"""Prompt-cache & paham-konteks (2026-09-02) — DeepSeek DI-MOCK (tanpa network).

Latar (terukur dari ai_chat_log 841 giliran, 3 Agu–2 Sep 2026): panggilan
PERTAMA tiap giliran hanya kena cache ±20 rb token dari ±47 rb, walau giliran
sebelumnya baru 1 menit lalu dengan peran sama. Sebabnya: template DeepSeek
mengangkat SEMUA pesan `role:system` ke puncak prompt (sebelum spec tool), jadi
blok konteks dinamis yang dulu dikirim sbg pesan system kedua berdiri di depan
±100 rb char spec tool + riwayat → semuanya cache-miss tiap giliran.

Kontrak yang dijaga di sini:
  1. tepat SATU pesan system per permintaan; konteks dinamis digabung ke pesan
     user terakhir (_sisip_konteks);
  2. riwayat dirender STABIL: pesan lama identik antar giliran (plafon
     seragam, jendela bergeser per blok, penanda tanpa angka);
  3. hasil tool ronde lama TIDAK diciutkan selama masih di bawah anggaran
     (mengubah pesan lama = pesan sesudahnya dibayar penuh lagi);
  4. slot FIGUR (balon→PN gambar teknis terakhir) menjawab 'no 41 apa' tanpa
     memanggil ulang tool, dan PN-nya sah bagi guard.
"""
from __future__ import annotations

import json

import pytest

from app.services import ai_assistant as ai
from app.services import ai_session

USER = {"username": "tester", "role": "user"}
CONV = "sesi-cache-1234-abcd"


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda h: None)
    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", lambda **kw: True)
    # Katalog lokal KOSONG: PN hanya sah bila ter-ground dari tool/memo.
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    ai_session._reset()
    yield
    ai_session._reset()


def _stub_model(monkeypatch, seq):
    """`seq` = daftar balasan model: str (jawaban) atau dict (message utuh)."""
    calls = {"n": 0, "semua": []}

    def fake(messages, tools, max_tokens=6000):
        calls["semua"].append([dict(m) for m in messages])
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        if isinstance(c, dict):
            return {"choices": [{"message": c, "finish_reason": "tool_calls"}]}
        return {"choices": [{"message": {"content": c}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake)
    return calls


# ── 1. Satu pesan system; konteks digabung ke pesan user terakhir ────────────

def test_satu_pesan_system_konteks_di_pesan_user_terakhir(monkeypatch):
    calls = _stub_model(monkeypatch, ["Baik."])
    hist = [{"role": "user", "content": "cek kampas rem"},
            {"role": "assistant", "content": "Ada 3 varian."},
            {"role": "user", "content": "yang pertama harganya?"}]
    ai.chat(USER, hist, conversation_id=CONV)
    msgs = calls["semua"][0]
    assert [m["role"] for m in msgs].count("system") == 1
    assert msgs[0] == {"role": "system", "content": "system uji"}
    akhir = msgs[-1]
    assert akhir["role"] == "user"
    assert akhir["content"].startswith(ai._CTX_BUKA)
    assert "KONTEKS AKTIF" in akhir["content"]
    assert "[PENGGUNA] Username: tester." in akhir["content"]
    assert akhir["content"].endswith(ai._CTX_TUTUP + "\n\nyang pertama harganya?")
    # Pesan riwayat sebelumnya dikirim POLOS (tanpa catatan sistem menempel).
    assert msgs[1] == {"role": "user", "content": "cek kampas rem"}
    assert msgs[2] == {"role": "assistant", "content": "Ada 3 varian."}


def test_prefix_antar_giliran_hanya_bertambah(monkeypatch):
    """Giliran k+1 harus memuat SEMUA pesan giliran k (kecuali pesan user
    terakhirnya yang kini polos) byte-per-byte — itulah syarat cache-hit."""
    calls = _stub_model(monkeypatch, ["Baik."])
    hist: list[dict] = []
    for k in range(6):
        hist.append({"role": "user", "content": f"pertanyaan {k} " + "x" * 3000})
        ai.chat(USER, hist, conversation_id=CONV)
        hist.append({"role": "assistant", "content": f"jawaban {k} " + "y" * 3000})
    kirim = calls["semua"]
    for k in range(1, len(kirim)):
        lama, baru = kirim[k - 1], kirim[k]
        # semua pesan sebelum pesan user terakhir giliran lalu: identik
        assert baru[:len(lama) - 1] == lama[:-1]
        # pesan user terakhir giliran lalu kini POLOS (tanpa catatan sistem)
        assert baru[len(lama) - 1] == {"role": "user",
                                       "content": hist[2 * (k - 1)]["content"]}


# ── 2. _sanitize_history: render stabil ─────────────────────────────────────

def test_sanitize_history_plafon_seragam_semua_posisi():
    hist = [{"role": "assistant" if i % 2 else "user", "content": f"m{i} " + "z" * 6000}
            for i in range(10)]
    out = ai._sanitize_history(hist)
    panjang = {len(m["content"]) for m in out[:-1]}
    assert len(panjang) == 1                      # pesan lama & baru: plafon SAMA
    assert all(m["content"].endswith(" …(dipangkas)") for m in out[:-1])
    assert list(panjang)[0] == ai._HIST_CHARS + len(" …(dipangkas)")


def test_sanitize_history_jendela_bergeser_per_blok():
    hist = [{"role": "assistant" if i % 2 else "user", "content": f"pesan {i}"}
            for i in range(30)]
    # ≤16 pesan: utuh
    assert len(ai._sanitize_history(hist[:16])) == 16
    # 17..22 pesan: 6 tertua dibuang sekaligus → awal jendela TETAP pesan ke-6
    for n in range(17, 23):
        out = ai._sanitize_history(hist[:n])
        assert len(out) == n - 6
        assert out[0]["content"].endswith("pesan 6")
        assert out[0]["content"].startswith(ai._HIST_NOTE_PANGKAS)
    # 23 pesan: blok berikutnya (12 dibuang) → awal jendela pesan ke-12
    out = ai._sanitize_history(hist[:23])
    assert len(out) == 11 and out[0]["content"].endswith("pesan 12")


def test_sanitize_history_render_identik_antar_giliran():
    """Simulasi 22 giliran: keluaran giliran k+1 selalu DIAWALI keluaran
    giliran k (minus pesan terakhir) — kecuali saat blok bergeser."""
    hist = [{"role": "assistant" if i % 2 else "user", "content": f"isi {i} " + "q" * 2000}
            for i in range(44)]
    geser = 0
    for n in range(1, 44):
        a, b = ai._sanitize_history(hist[:n]), ai._sanitize_history(hist[:n + 1])
        if b[:len(a)] != a:
            geser += 1
            assert n >= 16                    # hanya saat jendela penuh (16 → 17)
    assert geser <= 5                         # 44 pesan → maks 5 pergeseran blok


def test_penanda_pangkas_tanpa_angka():
    """Angka jumlah pesan yang dipangkas berubah tiap giliran dan menempel di
    pesan PERTAMA → seluruh riwayat cache-miss. Penanda wajib bebas angka."""
    assert not any(ch.isdigit() for ch in ai._HIST_NOTE_PANGKAS)
    hist = [{"role": "user", "content": f"p{i}"} for i in range(40)]
    a, b = ai._sanitize_history(hist[:18]), ai._sanitize_history(hist[:20])
    assert a[0] == b[0]                       # penanda + pesan pertama identik


# ── 3. Stub hasil tool ronde lama: hanya bila lewat anggaran ────────────────

def _msgs_tool():
    return [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "HASIL RONDE 1 " + "x" * 600},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "b"}]},
        {"role": "tool", "tool_call_id": "b", "content": "HASIL RONDE 2 " + "y" * 600},
        {"role": "tool", "tool_call_id": "c", "content": "HASIL RONDE 3 " + "z" * 600},
    ]


def _idx_tool():
    return [{"i": 2, "round": 1, "name": "bom_dari_rangka"},
            {"i": 4, "round": 2, "name": "stok_gudang"},
            {"i": 5, "round": 3, "name": "detail_part"}]


def test_trim_tak_menyentuh_di_bawah_anggaran():
    m, idx = _msgs_tool(), _idx_tool()
    ai._trim_old_tool_messages(m, idx, cur_round=3, budget_chars=5000)
    assert "HASIL RONDE 1" in m[2]["content"] and "stubbed" not in idx[0]


def test_trim_menyala_dan_berhenti_di_anggaran():
    m, idx = _msgs_tool(), _idx_tool()
    # total ≈1.840 > 1.500 → ronde 1 diciutkan; sesudahnya ≈1.300 ≤ 1.500 → stop
    ai._trim_old_tool_messages(m, idx, cur_round=4, budget_chars=1500)
    assert "diringkas" in m[2]["content"] and idx[0]["stubbed"] is True
    assert "HASIL RONDE 2" in m[4]["content"] and "stubbed" not in idx[1]


def test_trim_tanpa_anggaran_perilaku_lama():
    m, idx = _msgs_tool(), _idx_tool()
    ai._trim_old_tool_messages(m, idx, cur_round=4)
    assert "diringkas" in m[2]["content"] and "diringkas" in m[4]["content"]


def test_chat_loop_mengirim_anggaran():
    """Loop chat WAJIB memakai anggaran: konstanta ada & masuk akal terhadap
    konteks model (±130 rb char ≈ 40 rb token)."""
    assert 60_000 <= ai._TRIM_BUDGET_CHARS <= 200_000
    src = open(ai.__file__.replace("ai_assistant.py", "ai_parts/p9_chat_loop.py"),
               encoding="utf-8").read()
    assert "budget_chars=_TRIM_BUDGET_CHARS" in src


# ── 4. Slot FIGUR: balon → PN gambar teknis terakhir ────────────────────────

_HASIL_GAMBAR = {
    "found": True,
    "gambar": [{"image_id": "img1", "pn": "WG1642110019", "balon": 41,
                "nama_figure": "Cowl panel and radiator cover", "kategori": "kabin",
                "jumlah_item": 58}],
    "daftar_balon_gambar": [
        {"balon": "40", "pn": "WG1642110018", "nama": "Right A column trim panel"},
        {"balon": "41", "pn": "wg1642110019", "nama": "Left A column trim panel " + "x" * 80},
        {"balon": "42", "pn": "", "nama": "tanpa pn — dilewati"},
    ],
    "daftar_balon_figure": "Cowl panel and radiator cover",
    "daftar_balon_cakupan": {"ditampilkan": 2, "total_item_figure": 58,
                             "figure": "Cowl panel and radiator cover"},
}


def test_figur_dari_hasil_bentuk_dan_pembersihan():
    f = ai._figur_dari_hasil("gambar_exploded", {"rangka": "RT108966"}, _HASIL_GAMBAR)
    assert f["figure"] == "Cowl panel and radiator cover"
    assert f["ctx"] == "RT108966" and f["total"] == 58
    assert f["balon"][0] == ["40", "WG1642110018", "Right A column trim panel"]
    assert f["balon"][1][1] == "WG1642110019"                 # PN di-uppercase
    assert len(f["balon"][1][2]) <= ai._FIGUR_NAMA_CAP        # nama dipotong
    assert len(f["balon"]) == 2                               # balon tanpa PN dibuang
    assert ai._figur_dari_hasil("cari_part", {}, {"found": True, "hasil": []}) == {}


def test_session_figur_disimpan_diganti_figure_terbaru():
    ai_session.merge(USER["username"], CONV,
                     figur={"figure": "F1", "ctx": "RT1", "total": 10,
                            "balon": [["1", "wg100", "A"], ["2", "WG200", ""]]})
    m = ai_session.get(USER["username"], CONV)
    assert m["figur"]["figure"] == "F1" and m["figur"]["balon"][0] == ["1", "WG100", "A"]
    # merge tanpa figur → figur lama TETAP
    ai_session.merge(USER["username"], CONV, pn=["WG300"])
    assert ai_session.get(USER["username"], CONV)["figur"]["figure"] == "F1"
    # figure baru MENGGANTI (hanya satu figure — yang terakhir dilihat user)
    ai_session.merge(USER["username"], CONV,
                     figur={"figure": "F2", "balon": [["7", "WG700", "B"]]})
    f = ai_session.get(USER["username"], CONV)["figur"]
    assert f["figure"] == "F2" and f["balon"] == [["7", "WG700", "B"]]
    # figur cacat diabaikan, tak melempar
    ai_session.merge(USER["username"], CONV, figur={"figure": "F3", "balon": [["", ""]]})
    assert ai_session.get(USER["username"], CONV)["figur"]["figure"] == "F2"


def test_blok_balon_hanya_saat_user_merujuk_nomor():
    memo = {"figur": {"figure": "Cowl panel", "ctx": "RT108966", "total": 58,
                      "balon": [["41", "WG1642110019", "Left A column trim panel"]]}}
    dasar = [{"role": "user", "content": "cek panel kabin RT108966"},
             {"role": "assistant", "content": "Gambar sudah tampil."}]
    ya = ai._active_context_block(dasar + [{"role": "user", "content": "No 41 apa"}], memo)
    assert "BALON GAMBAR TEKNIS TERAKHIR" in ya and "41=WG1642110019" in ya
    assert "1 dari 58 balon" in ya
    for q in ("harganya berapa?", "no 612630010054 ada?", "nomor rangka RT108966"):
        tidak = ai._active_context_block(dasar + [{"role": "user", "content": q}], memo)
        assert "BALON GAMBAR TEKNIS TERAKHIR" not in tidak


def test_chat_menyimpan_figur_lalu_menjawab_balon_tanpa_tool(monkeypatch):
    """Giliran 1: tool bergambar → figur tersimpan. Giliran 2 'no 41 apa':
    konteks memuat daftar balon & PN-nya lolos guard tanpa tool apa pun."""
    monkeypatch.setattr(ai, "_run_tool",
                        lambda n, a, u, sheet_id="": dict(_HASIL_GAMBAR))
    panggil_tool = {"content": "[PIKIR]cek gambar[/PIKIR]",
                    "tool_calls": [{"id": "c1", "type": "function",
                                    "function": {"name": "gambar_exploded",
                                                 "arguments": json.dumps(
                                                     {"rangka": "RT108966",
                                                      "pn": "WG1642110019"})}}]}
    _stub_model(monkeypatch, [panggil_tool, "Gambar figure Cowl panel sudah tampil."])
    hist = [{"role": "user", "content": "gambar teknis panel kabin RT108966"}]
    out1 = ai.chat(USER, hist, conversation_id=CONV)
    assert "gambar_exploded" in out1["tools_used"]
    f = ai_session.get(USER["username"], CONV)["figur"]
    assert f["figure"] == "Cowl panel and radiator cover"
    assert ["41", "WG1642110019", "Left A column trim panel " + "x" * 80][:2] == f["balon"][1][:2]

    hist += [{"role": "assistant", "content": out1["reply"]},
             {"role": "user", "content": "no 41 apa"}]
    calls = _stub_model(monkeypatch,
                        ["Balon 41 = WG1642110019 — Left A column trim panel."])
    out2 = ai.chat(USER, hist, conversation_id=CONV)
    assert calls["n"] == 1                                    # tanpa retry/koreksi
    assert "BALON GAMBAR TEKNIS TERAKHIR" in calls["semua"][0][-1]["content"]
    assert "WG1642110019" in out2["reply"]
    assert "tak terverifikasi" not in out2["reply"]
    assert out2["tools_used"] == []


# ── 4b. Guard PN: potongan digit dari PN alfanumerik bukan PN tersendiri ─────

def test_potongan_digit_pn_memo_tak_dicap_karangan():
    """'WG1642110019 ' (diikuti spasi) dulu melahirkan token '1642110019' yang
    tak ada di memo (memo hanya simpan bentuk utuh) → jawaban sah disamarkan."""
    reply = "Balon 41 = WG1642110019 — Left A column trim panel."
    assert ai._pns_jawaban(reply) == {"WG1642110019"}
    assert ai._ungrounded_pns(reply, {"WG1642110019"}) == []
    # PN yang benar-benar tak ada tetap tertangkap — termasuk PN numerik Weichai
    # (di tengah kalimat; di ujung kalimat _PN_NUMERIC_RE memang melewatkannya,
    # perilaku lama yang tak disentuh di sini).
    assert ai._ungrounded_pns("Pakai AZ9998887776 atau 612630010054 ya", set()) \
        == ["612630010054", "AZ9998887776"]
    # angka saja dari PN yang SUDAH grounded (model lupa awalan) tak dicurigai
    assert ai._ungrounded_pns("nomornya 1642110019 ya", {"WG1642110019"}) == []
    # semua-karangan tetap jadi pesan jujur (bukan tabel palsu tersamar)
    salah = "Berikut:\n- AZ9998887776 stok 3\n- AZ9998887777 stok 2"
    assert ai._sanitize_ungrounded(salah, ai._ungrounded_pns(salah, set())) == ai._NOT_FOUND_REPLY


# ── 5. Hasil tool BOCOR: satu pesan user per ronde, berapa pun tool-nya ──────

def test_hasil_bocor_dua_tool_jadi_satu_pesan_user(monkeypatch):
    monkeypatch.setattr(ai, "_run_tool",
                        lambda n, a, u, sheet_id="": {"found": True, "units": [n]})
    calls = _stub_model(monkeypatch, [
        '<invoke name="daftar_unit"></invoke><invoke name="info_aplikasi"></invoke>',
        "Ada beberapa unit.",
    ])
    ai.chat(USER, [{"role": "user", "content": "unit apa saja?"}])
    msgs = calls["semua"][-1]
    hasil = [m for m in msgs if "[HASIL TOOL" in (m.get("content") or "")]
    assert len(hasil) == 1 and hasil[0]["role"] == "user"
    assert "[HASIL TOOL daftar_unit]" in hasil[0]["content"]
    assert "[HASIL TOOL info_aplikasi]" in hasil[0]["content"]
    assert [m["role"] for m in msgs].count("system") == 1
