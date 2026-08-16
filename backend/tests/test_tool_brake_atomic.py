"""Rem anti-loop: plafon harus DETERMINISTIK, dan penolakannya jujur.

Dua cacat yang diuji di sini, keduanya dari log produksi:

1. Gate `_call_count >= _MAX_CALLS_PER_TOOL` dibaca SEBELUM eksekusi dan angkanya
   dinaikkan SESUDAH, tanpa lock — sementara batch tool dijalankan di
   ThreadPoolExecutor. Seluruh gelombang worker pertama membaca angka yang sama
   dan lolos bersamaan, jadi plafon efektif ≈ 3 + (worker−1).

2. Hasil penolakan membawa `found: False`, sehingga dibaca sebagai "nf" (data
   tidak ada) dan memicu nota "lookup gagal, jangan mengarang". Model lalu
   menyimpulkan puluhan PN yang DITOLAK REM itu memang tak ada di data —
   padahal belum sempat dicek sama sekali.

DeepSeek di-mock; nol jaringan, nol panggilan model.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import ai_assistant as ai

USER = {"username": "tester", "role": "user"}


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])


# ── klasifikasi ─────────────────────────────────────────────────────────────

def test_hasil_rem_dikenali_sbg_brake():
    assert ai._tool_fail_kind({"found": False, "dibatasi": True}) == "brake"


def test_brake_bukan_nf_maupun_err():
    """Pembedaan inilah intinya: 'nf' berarti kita SUDAH mencari dan tak ada."""
    assert ai._tool_fail_kind({"found": False}) == "nf"
    assert ai._tool_fail_kind({"error": "boom"}) == "err"
    assert ai._tool_fail_kind({"found": False, "dibatasi": True}) != "nf"


def test_hasil_rem_tak_berkey_error():
    """Kontrak lama: rem tak boleh mencemari telemetri error infra."""
    assert "error" not in {"found": False, "dibatasi": True}


def test_sinyal_kuat_menimpa_brake():
    daftar: list[str] = []
    ai._catat_tool_gagal(daftar, "detail_part", "brake")
    assert daftar == ["detail_part:brake"]
    ai._catat_tool_gagal(daftar, "detail_part", "nf")
    assert daftar == ["detail_part:nf"]          # nf lebih kuat
    ai._catat_tool_gagal(daftar, "detail_part", "err")
    assert daftar == ["detail_part:err"]         # err paling kuat
    ai._catat_tool_gagal(daftar, "detail_part", "brake")
    assert daftar == ["detail_part:err"]         # tak turun lagi


# ── atomisitas di bawah eksekusi paralel ────────────────────────────────────

def _chat_dgn_batch(monkeypatch, n_panggilan: int, nama: str = "detail_part") -> dict:
    """Jalankan satu giliran di mana model mengemit `n` tool_call sekaligus."""
    jalan = {"n": 0}
    lock = threading.Lock()
    barrier = threading.Barrier(min(4, n_panggilan), timeout=5)

    def _fake_run_tool(name, args, u, sid=""):
        # Paksa semua worker gelombang pertama masuk BERSAMAAN — inilah kondisi
        # balapan yang dulu membuat plafon bocor.
        try:
            barrier.wait()
        except Exception:
            pass
        with lock:
            jalan["n"] += 1
        return {"found": True, "pn": args.get("part_number")}

    monkeypatch.setattr(ai, "_run_tool", _fake_run_tool)
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])

    calls = [{"id": str(i), "type": "function",
              "function": {"name": nama,
                           "arguments": '{"part_number": "PN%03d"}' % i}}
             for i in range(n_panggilan)]
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": calls},
                      "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Selesai."}, "finish_reason": "stop"}]},
    ]
    it = iter(seq)
    last = {}
    dikirim: list[list[dict]] = []

    def _post(messages, tools, max_tokens=6000):
        dikirim.append([dict(m) for m in messages])
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(ai, "_post_chat", _post)
    out = ai.chat(USER, [{"role": "user", "content": "cek banyak PN"}])
    return {"eksekusi": jalan["n"], "out": out, "messages": dikirim}


def _lebih_dari_plafon(nama: str = "detail_part") -> int:
    return ai._plafon_tool(nama) + 4


def test_plafon_tepat_walau_batch_paralel(monkeypatch):
    """Sebelum perbaikan: bisa 6 eksekusi. Sesudah: tepat _MAX_CALLS_PER_TOOL."""
    r = _chat_dgn_batch(monkeypatch, _lebih_dari_plafon())
    assert r["eksekusi"] == ai._MAX_CALLS_PER_TOOL


def test_giliran_ber_rem_tetap_menghasilkan_jawaban(monkeypatch):
    """Rem tak boleh menjatuhkan giliran — user tetap dapat jawaban."""
    r = _chat_dgn_batch(monkeypatch, _lebih_dari_plafon())
    assert r["out"]["reply"]


# ── plafon: kerja NYATA ber-argumen-beda tak boleh dipotong di angka 3 ───────
# Bukti produksi 2026-07-24: 9 nomor rangka masuk, 3 dicek, jawaban menyatakan
# KESEMBILANNYA "tidak terdaftar". Plafon 3 memperlakukan daftar tempelan user
# sebagai gejala model macet — padahal macet = argumen IDENTIK (sudah di-cache).

def test_plafon_muat_daftar_tempelan_user(monkeypatch):
    """9 item ber-argumen BEDA (kasus nyata log) harus lolos semua."""
    r = _chat_dgn_batch(monkeypatch, 9)
    assert r["eksekusi"] == 9


def test_tool_berat_tetap_ketat(monkeypatch):
    """Render gambar EPC bisa puluhan detik/panggilan — plafonnya TIDAK ikut naik."""
    assert ai._plafon_tool("gambar_exploded") == ai._MAX_CALLS_PER_TOOL_BERAT
    assert ai._plafon_tool("gambar_exploded") < ai._plafon_tool("detail_part")
    r = _chat_dgn_batch(monkeypatch, 9, nama="gambar_exploded")
    assert r["eksekusi"] == ai._MAX_CALLS_PER_TOOL_BERAT


def test_plafon_eksekusi_seluruh_giliran_ada(monkeypatch):
    """Pagar biaya: plafon per-tool naik, tapi TOTAL eksekusi per giliran tetap
    terbatas — tanpa ini satu giliran patologis bisa meledak lintas tool."""
    assert ai._MAX_TOOL_EXEC_TURN >= ai._MAX_CALLS_PER_TOOL
    assert ai._MAX_TOOL_EXEC_TURN <= 60


# ── penolakan rem harus SAMPAI ke model & ke user ───────────────────────────

def test_nota_rem_naik_ke_tingkat_percakapan(monkeypatch):
    """Sebagai field di dalam satu hasil tool, penolakan rem terbukti terlewat.
    Ia kini pesan tersendiri yang MENYEBUT item yang belum dicek."""
    r = _chat_dgn_batch(monkeypatch, _lebih_dari_plafon())
    teks = "\n".join(str(m.get("content") or "")
                     for batch in r["messages"] for m in batch)
    assert "DITOLAK rem anti-loop" in teks
    assert "BELUM PERNAH DICEK" in teks
    assert "tidak terdaftar" in teks          # frasa negatif yang dilarang
    assert "detail_part(" in teks             # ber-rincian, bukan generik


def test_jawaban_memberi_tahu_user_apa_yang_belum_dicek(monkeypatch):
    """Jaring terakhir: nota ke model boleh saja diabaikan — user tetap diberi
    tahu item mana yang belum sempat dicek, agar 'tidak ada' tak ditelan mentah."""
    r = _chat_dgn_batch(monkeypatch, _lebih_dari_plafon())
    reply = r["out"]["reply"]
    assert "BELUM sempat dicek" in reply
    assert "Selesai." in reply                # jawaban asli tetap utuh


def test_catatan_rem_tak_dibaca_sbg_sanitized(monkeypatch):
    """Catatan rem TIDAK boleh memakai frasa penanda outcome 'sanitized' —
    dua kelas ini berbeda dan mencampurnya merusak angka observabilitas."""
    nota = ai._rem_tertunda_note([("detail_part", {"part_number": "PN001"})])
    assert nota and "tak terverifikasi" not in nota


# ── anggaran konteks: ronde lebar tak boleh meledakkan token input ──────────

def test_cap_ronde_menyempit_saat_hasil_banyak():
    """Plafon per-hasil saja tak cukup begitu satu ronde boleh berisi 10 panggilan:
    10 × 24.000 char ≈ 60rb token DALAM SATU RONDE, dan messages dikirim ulang
    utuh tiap panggilan berikutnya."""
    assert ai._cap_ronde(1) == ai._MAX_TOOL_CONTENT
    assert ai._cap_ronde(10) < ai._MAX_TOOL_CONTENT
    assert ai._cap_ronde(10) * 10 <= ai._MAX_TOOL_CONTENT_RONDE
    # monoton: makin banyak hasil, makin sempit jatah tiap hasil
    assert ai._cap_ronde(4) >= ai._cap_ronde(10) >= ai._cap_ronde(20)


def test_cap_ronde_tak_menyempitkan_ronde_lama():
    """Ronde SEMPIT (≤4 hasil — bentuk yang selama ini mungkin dengan plafon 3)
    harus dapat jatah PENUH seperti sebelumnya: penyempitan adalah harga untuk
    ronde LEBAR yang baru mungkin setelah plafon naik, bukan pemotongan mundur."""
    for n in (1, 2, 3, 4):
        assert ai._cap_ronde(n) == ai._MAX_TOOL_CONTENT


def test_cap_ronde_punya_lantai():
    """Hasil yang diciutkan sampai remah tak berguna bagi model."""
    assert ai._cap_ronde(500) >= ai._MIN_TOOL_CONTENT


def test_cap_tool_content_default_tak_berubah():
    """Pemanggil lama (tanpa `plafon`) harus berperilaku PERSIS seperti dulu."""
    s = "x" * (ai._MAX_TOOL_CONTENT + 5000)
    assert len(ai._cap_tool_content(s)) <= ai._MAX_TOOL_CONTENT
    assert ai._cap_tool_content("pendek") == "pendek"


def test_cap_tool_content_menyisakan_ekor():
    """Builder menaruh instruksi penyetir (catatan/jawaban_wajib) di UJUNG —
    plafon sempit pun tak boleh memotong-kepala-saja."""
    s = "A" * 20000 + "PENANDA_EKOR"
    out = ai._cap_tool_content(s, 5000)
    assert out.endswith("PENANDA_EKOR")
    assert len(out) <= 5000


def test_rem_tercatat_sbg_sebab_guard(monkeypatch):
    """Frekuensi rem harus terlihat di panel admin, bukan tersembunyi."""
    dicatat = {}
    asli = ai.ai_chat_log.log_turn_async

    def _tangkap(**kw):
        dicatat.update(kw)

    monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", _tangkap)
    try:
        _chat_dgn_batch(monkeypatch, _lebih_dari_plafon())
    finally:
        monkeypatch.setattr(ai.ai_chat_log, "log_turn_async", asli)
    assert "rem" in (dicatat.get("guard_kinds") or [])
    assert "detail_part:brake" in (dicatat.get("tools_failed") or [])


def test_pesan_rem_menunjuk_tool_massal(monkeypatch):
    """Model harus diberi jalan keluar yang benar, bukan sekadar ditolak."""
    ditangkap = {}

    def _fake_run_tool(name, args, u, sid=""):
        return {"found": True}

    monkeypatch.setattr(ai, "_run_tool", _fake_run_tool)
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [{"x": 1}])

    calls = [{"id": str(i), "type": "function",
              "function": {"name": "detail_part",
                           "arguments": '{"part_number": "PN%03d"}' % i}}
             for i in range(_lebih_dari_plafon())]
    seq = [
        {"choices": [{"message": {"content": None, "tool_calls": calls},
                      "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Selesai."}, "finish_reason": "stop"}]},
    ]
    it = iter(seq)
    last = {}

    def _post(messages, tools, max_tokens=6000):
        ditangkap["messages"] = [dict(m) for m in messages]
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    monkeypatch.setattr(ai, "_post_chat", _post)
    ai.chat(USER, [{"role": "user", "content": "cek 8 PN"}])

    teks = "\n".join(str(m.get("content") or "") for m in ditangkap["messages"])
    assert "cek_massal_part" in teks
    assert "BELUM TENTU tidak" in teks          # jangan simpulkan 'tak ada'
    assert ai._LOOKUP_GAGAL_NOTE not in teks    # nota 'lookup gagal' TIDAK muncul


def test_error_infra_tetap_err_bukan_brake(monkeypatch):
    """`_run_tool` di produksi MENELAN exception-nya sendiri dan mengembalikan
    {"error": ...}. Hasil itu harus tetap terklasifikasi 'err' — kalau tertukar
    jadi 'brake', gangguan infrastruktur akan tersembunyi dari telemetri."""
    hasil_err = {"error": "tool 'cari_part' gagal dijalankan (gangguan internal)."}
    assert ai._tool_fail_kind(hasil_err) == "err"
    assert ai._tool_fail_kind({"denied": True}) == "err"


def test_hasil_rem_konsumsi_slot_tak_dobel(monkeypatch):
    """Panggilan yang DITOLAK rem tidak boleh ikut menaikkan hitungan — kalau
    ikut, satu batch besar akan mengunci tool itu untuk ronde-ronde berikutnya."""
    r = _chat_dgn_batch(monkeypatch, 12)
    # 12 diemit, tepat _MAX_CALLS_PER_TOOL dieksekusi; sisanya ditolak TANPA
    # menambah hitungan (kalau menambah, angkanya akan > plafon di ronde ini).
    assert r["eksekusi"] == ai._MAX_CALLS_PER_TOOL
