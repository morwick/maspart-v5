"""SALVAGE jawaban kosong — DeepSeek DI-MOCK (tanpa network, nol biaya model).

Bukti produksi (844 giliran, 15-31 Jul 2026): 5 giliran mentok ronde 8 dgn tool
SUKSES & data ada, tapi model gagal menyusun jawaban final → user menunggu
54-180 detik lalu menerima _EMPTY_FINAL_MSG (121 char). Retry lama mengirim
ulang konteks yang PERSIS SAMA, jadi gagal lagi ("bushing front spring" empty
dua kali berturut-turut).

Yang diuji di sini: selama ada SATU hasil tool yang berhasil, user tak boleh
lagi menerima permintaan maaf kosong.
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}

# Model yang "rusak": hanya menulis nalar, tak pernah jawaban final. _strip_reasoning
# mengubahnya jadi "" — persis gejala 5 giliran produksi itu.
PIKIR_SAJA = "[PIKIR] menimbang hasil tool, banyak sekali datanya…"

HASIL_TOOL = {
    "found": True,
    "jumlah_part_unik": 2,
    "parts": [
        {"part_number": "WG9725520274", "nama": "Front spring bushing", "stok": 4,
         "harga": "Rp 250.000"},
        {"part_number": "AZ9925520277", "nama": "Rear spring bushing", "stok": 0},
    ],
}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    # Prompt besar & daftar tool nyata bukan fokus test (dan bikin lambat).
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())


def _tool_call(name="cari_part", args='{"query":"bushing"}'):
    return {"id": "call_1", "type": "function",
            "function": {"name": name, "arguments": args}}


def _stub(monkeypatch, urutan, hasil_tool=HASIL_TOOL):
    """`urutan` = list respons model berurutan. Tiap elemen str (jadi content)
    atau dict (dipakai apa adanya sbg `message`). Elemen terakhir dipakai
    seterusnya. Merekam SEMUA panggilan (messages, tools) untuk diperiksa."""
    seq = list(urutan)
    rec = {"n": 0, "calls": []}

    def fake_post(messages, tools, max_tokens=6000):
        m = seq[min(rec["n"], len(seq) - 1)]
        rec["n"] += 1
        rec["calls"].append({"messages": list(messages), "tools": tools,
                             "max_tokens": max_tokens})
        msg = {"content": m} if isinstance(m, str) else dict(m)
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake_post)
    if hasil_tool is not None:
        monkeypatch.setattr(ai, "_run_tool", lambda name, args, u, sid="": hasil_tool)
    return rec


def test_salvage_llm_menyelamatkan_jawaban(monkeypatch):
    """Model buntu di konteks penuh, tapi menjawab saat konteksnya BERSIH."""
    JAWAB = "Bushing front spring: WG9725520274, stok 4, Rp 250.000."
    rec = _stub(monkeypatch, [
        {"content": "", "tool_calls": [_tool_call()]},   # ronde tool
        PIKIR_SAJA,                                      # jawaban final -> kosong
        PIKIR_SAJA,                                      # retry konteks penuh -> kosong
        JAWAB,                                           # panggilan SALVAGE -> berhasil
    ])
    out = ai.chat(USER, [{"role": "user", "content": "carikan bushing front spring"}])

    assert out["reply"] == JAWAB
    assert out["reply"] != ai._EMPTY_FINAL_MSG

    # Panggilan terakhir = salvage: konteks BERSIH (2 pesan, tanpa tool).
    salvage = rec["calls"][-1]
    assert len(salvage["messages"]) == 2
    assert salvage["tools"] == []
    assert salvage["messages"][0]["role"] == "system"
    assert "JANGAN menulis [PIKIR]" in salvage["messages"][0]["content"]
    # Data hasil tool ikut serta — itulah bahan jawabannya.
    assert "WG9725520274" in salvage["messages"][1]["content"]
    # Dan system prompt besar TIDAK ikut (inti perbaikannya).
    assert "system uji" not in salvage["messages"][0]["content"]


def test_lantai_deterministik_saat_model_buntu_total(monkeypatch):
    """SEMUA panggilan model kosong → tetap ada jawaban berisi, tanpa maaf."""
    rec = _stub(monkeypatch, [
        {"content": "", "tool_calls": [_tool_call()]},
        PIKIR_SAJA,   # dan seterusnya: model tak pernah menulis jawaban final
    ])
    out = ai.chat(USER, [{"role": "user", "content": "carikan bushing front spring"}])

    assert out["reply"] != ai._EMPTY_FINAL_MSG
    assert "WG9725520274" in out["reply"]      # data sungguhan dari hasil tool
    assert "AZ9925520277" in out["reply"]
    assert "sudah ketemu" in out["reply"]
    assert rec["n"] >= 3


def test_tanpa_hasil_tool_tetap_jujur_menyerah(monkeypatch):
    """Pool kosong (model tak pernah memanggil tool) → perilaku LAMA utuh."""
    _stub(monkeypatch, [PIKIR_SAJA], hasil_tool=None)
    out = ai.chat(USER, [{"role": "user", "content": "halo apa kabar"}])
    assert out["reply"] == ai._EMPTY_FINAL_MSG


def test_hasil_tool_GAGAL_tidak_masuk_kolam(monkeypatch):
    """Hasil found:False tak berguna diselamatkan — jangan disajikan sbg data."""
    _stub(monkeypatch, [
        {"content": "", "tool_calls": [_tool_call()]},
        PIKIR_SAJA,
    ], hasil_tool={"found": False, "catatan": "tidak ditemukan"})
    out = ai.chat(USER, [{"role": "user", "content": "carikan part xyz"}])
    assert out["reply"] == ai._EMPTY_FINAL_MSG


def test_jumlah_panggilan_model_tidak_bertambah(monkeypatch):
    """Netral biaya: slot retry KEDUA yang dulu identik kini dipakai salvage,
    bukan panggilan tambahan. _MAX_EMPTY_RETRIES turun 2 -> 1 sbg gantinya."""
    assert ai._MAX_EMPTY_RETRIES == 1
    rec = _stub(monkeypatch, [
        {"content": "", "tool_calls": [_tool_call()]},
        PIKIR_SAJA,
    ])
    ai.chat(USER, [{"role": "user", "content": "carikan bushing front spring"}])
    # 1 ronde tool + 1 jawaban kosong + 1 retry + 1 salvage = 4.
    # (Sebelum perubahan: 1 + 1 + 2 retry = 4 juga, tapi berujung pesan maaf.)
    assert rec["n"] == 4


def test_render_deterministik_tanpa_model(monkeypatch):
    """_render_hasil_tool murni Python — dipanggil langsung, tanpa network."""
    teks = ai._render_hasil_tool([{"name": "cari_part", "args": {},
                                   "result": HASIL_TOOL}])
    assert "WG9725520274" in teks
    assert "Front spring bushing" in teks
    assert "cari_part" in teks
    assert ai._render_hasil_tool([]) == ""


def test_salvage_membatasi_ukuran_kolam():
    """Kolam terkurung agar giliran panjang tak menahan memori tanpa batas."""
    assert ai._SALVAGE_POOL_MAX == 12
    assert ai._SALVAGE_MAX_CHARS > 0
