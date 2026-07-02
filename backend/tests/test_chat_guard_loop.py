"""Loop guard anti-halusinasi di chat() end-to-end — DeepSeek DI-MOCK
(tanpa network): model 'bandel' yang terus mengarang PN harus berujung
pesan jujur 'tidak ditemukan', bukan tabel palsu.
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    # Hindari membangun system prompt besar / daftar tool / index nyata — bukan fokus test.
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user: [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())


def _stub_model(monkeypatch, content):
    """_post_chat palsu tanpa tool_calls. `content` = str (selalu sama) atau
    list[str] (jawaban berurutan; elemen terakhir dipakai seterusnya)."""
    seq = [content] if isinstance(content, str) else list(content)
    calls = {"n": 0}

    def fake(messages, tools):
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return {"choices": [{"message": {"content": c}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


def test_pn_karangan_membandel_diganti_pesan_jujur(monkeypatch):
    calls = _stub_model(monkeypatch, "Part yang cocok: AZ9998887776, stok 5, Rp 1.200.000.")
    out = ai.chat(USER, [{"role": "user", "content": "cari tierod dong"}])
    # 1 jawaban awal + 2 retry koreksi = 3 panggilan model, lalu jaring terakhir.
    assert calls["n"] == 1 + ai._MAX_GUARD_RETRIES
    assert out["reply"] == ai._NOT_FOUND_REPLY
    assert "AZ9998887776" not in out["reply"]


def test_pn_yang_user_sebut_dianggap_sah(monkeypatch):
    calls = _stub_model(monkeypatch, "Stok WG2210040097 saat ini 5 pcs.")
    out = ai.chat(USER, [{"role": "user", "content": "stok WG2210040097 berapa?"}])
    assert calls["n"] == 1                       # tanpa retry — langsung lolos
    assert out["reply"] == "Stok WG2210040097 saat ini 5 pcs."


def test_pn_dari_jawaban_asisten_sebelumnya_dianggap_sah(monkeypatch):
    # Follow-up tanpa tool: PN dari turn asisten sebelumnya (sudah lolos guard) sah.
    _stub_model(monkeypatch, "Ya, HW19709XST237036 itu transmisi assy NX400.")
    history = [
        {"role": "user", "content": "transmisi NX400 apa?"},
        {"role": "assistant", "content": "Transmisi NX400 adalah HW19709XST237036."},
        {"role": "user", "content": "yang tadi itu assy ya?"},
    ]
    out = ai.chat(USER, history)
    assert "HW19709XST237036" in out["reply"]
    assert "tak terverifikasi" not in out["reply"]


def test_jawaban_tanpa_pn_lolos_apa_adanya(monkeypatch):
    _stub_model(monkeypatch, "Halo! Ada yang bisa saya bantu soal spare part?")
    out = ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert out["reply"].startswith("Halo!")
    assert out["tools_used"] == []


# ── Jawaban final kosong (model berhenti di [PIKIR]) → paksa tulis ulang ─────

def test_jawaban_kosong_diretry_lalu_dapat_jawaban(monkeypatch):
    calls = _stub_model(monkeypatch, [
        "[PIKIR] mikir panjang tapi lupa nulis jawaban final",       # kosong stlh strip
        "[PIKIR] oke [/PIKIR] Repair kit HW tersedia, mau tingkat apa?",
    ])
    out = ai.chat(USER, [{"role": "user", "content": "repair kit hw19710?"}])
    assert calls["n"] == 2                                # 1 gagal + 1 retry sukses
    assert out["reply"] == "Repair kit HW tersedia, mau tingkat apa?"


def test_jawaban_kosong_membandel_berujung_pesan_aman(monkeypatch):
    calls = _stub_model(monkeypatch, "[PIKIR] nalar terus tanpa jawaban")
    out = ai.chat(USER, [{"role": "user", "content": "halo"}])
    assert calls["n"] == 1 + ai._MAX_EMPTY_RETRIES
    assert out["reply"] == ai._EMPTY_FINAL_MSG
    assert "nalar" not in out["reply"]                    # isi [PIKIR] tak bocor


# ── Kode unit/seri sah tidak disamarkan guard ────────────────────────────────

def test_kode_seri_unit_tidak_disamarkan(monkeypatch):
    # 'NX400HP' mirip PN (7 char huruf+angka) tapi itu nama seri katalog — guard
    # tidak boleh menyamarkannya (kasus nyata isi-kategori-kopling-nx400).
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: {"NX400HP", "HOWO400"})
    calls = _stub_model(monkeypatch, "Unit seri NX400HP dan HOWO400 tersedia di katalog.")
    out = ai.chat(USER, [{"role": "user", "content": "seri nx400 ada?"}])
    assert calls["n"] == 1                                # tanpa retry guard
    assert "NX400HP" in out["reply"] and "HOWO400" in out["reply"]
    assert "tak terverifikasi" not in out["reply"]


def test_pn_karangan_tetap_tertangkap_meski_ada_unit_token(monkeypatch):
    # Filter unit token TIDAK boleh meloloskan PN karangan sungguhan.
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: {"NX400HP"})
    _stub_model(monkeypatch, "Di NX400HP pakai part AZ9998887776 stok 3.")
    out = ai.chat(USER, [{"role": "user", "content": "part kopling nx400hp?"}])
    assert "AZ9998887776" not in out["reply"]


# ── _strip_reasoning: perilaku baru return "" ────────────────────────────────

def test_strip_reasoning_kosong_bila_hanya_nalar():
    assert ai._strip_reasoning("[PIKIR] cuma nalar tanpa penutup") == ""
    assert ai._strip_reasoning("[PIKIR] nalar [/PIKIR]") == ""
    assert ai._strip_reasoning("") == ""


def test_strip_reasoning_ambil_jawaban_setelah_penutup():
    assert ai._strip_reasoning("[PIKIR] a [/PIKIR] Jawaban.") == "Jawaban."
    assert ai._strip_reasoning("nalar bocor [/PIKIR] Jawaban.") == "Jawaban."
