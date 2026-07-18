"""Penjaga STOK TERPUSAT asisten — mengikuti Menu Control 'Kolom Stok'.

Bug nyata (2026-07-18): akun 'wawan' dengan centang Harga & Stok DIMATIKAN tetap
melihat kolom Stok berisi angka di asisten AI (harga sudah tersembunyi dengan
benar). Sebabnya kunci izin `col_stok` ditulis admin di Menu Control tapi TAK
PERNAH dibaca server — satu-satunya penyaringan stok adalah _hide_gudang_for_buyer
yang cuma membuang rincian per-gudang untuk pembeli, sehingga stok_total lolos ke
model lewat semua tool ber-EPC. Kini bentuknya kembar dengan harga: satu helper
_boleh_stok + strip TERPUSAT di _run_tool.

Kembaran: tests/test_harga_gate_asisten.py.
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
WAWAN = {"username": "wawan", "role": "user"}       # col_stok OFF
AGUS = {"username": "agustiono", "role": "user"}    # col_stok ON
PEMBELI = {"username": "roni", "role": "pembeli"}


@pytest.fixture
def perms(monkeypatch):
    """Menu Control: agustiono punya col_stok, wawan tidak."""
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda kind, u, r: ["col_stok", "col_harga"] if u == "agustiono"
                        else ["col_harga"])


# ── _boleh_stok (gerbang tunggal) ────────────────────────────────────────────
def test_boleh_stok_per_peran(perms):
    assert ai._boleh_stok(ADMIN) is True
    assert ai._boleh_stok(PEMBELI) is True           # wajib tahu ketersediaan utk beli
    assert ai._boleh_stok(AGUS) is True              # col_stok ON
    assert ai._boleh_stok(WAWAN) is False            # col_stok OFF


# ── _strip_stok (rekursif) ───────────────────────────────────────────────────
def test_strip_stok_dict_dan_list_bersarang():
    r = {"parts": [{"pn": "X", "stok_total": 2810, "harga_lokal": "Rp 5"},
                   {"pn": "Y", "stok_per_gudang": {"01.Jakarta": 9}}],
         "stok_dapat_dijual": 12, "nama": "z"}
    ai._strip_stok(r)
    assert r["parts"][0] == {"pn": "X", "harga_lokal": "Rp 5"}   # harga tetap
    assert "stok_per_gudang" not in r["parts"][1]
    assert "stok_dapat_dijual" not in r
    assert r["nama"] == "z"                          # non-stok utuh


def test_strip_stok_gudang_hanya_dibuang_bila_peta_qty():
    """'gudang' bisa berarti NAMA gudang (string, mis. filter query) — itu bukan
    data stok & harus tetap ada; hanya peta {gudang: qty} yang dibuang."""
    r = {"gudang": "Jakarta", "hasil": [{"pn": "X", "gudang": {"01.Jakarta": 7}}]}
    ai._strip_stok(r)
    assert r["gudang"] == "Jakarta"
    assert "gudang" not in r["hasil"][0]


# ── Penjaga terpusat di _run_tool ────────────────────────────────────────────
def test_run_tool_strip_stok_utk_staf_tanpa_izin(perms, monkeypatch):
    monkeypatch.setattr(ai, "_DISPATCH", {"dummy": lambda a, u: {
        "found": True,
        "parts": [{"pn": "AZ450045000042", "stok_total": 2810, "harga_lokal": "Rp 100"}]}})
    monkeypatch.setattr(ai, "_allowed_tool_names", lambda u, sid="": {"dummy"})
    out = ai._run_tool("dummy", {}, WAWAN)
    assert "stok_total" not in out["parts"][0]       # ⛔ tak bocor
    assert out["parts"][0]["harga_lokal"] == "Rp 100"  # col_harga ON → harga tetap


def test_run_tool_pertahankan_stok_utk_yang_berhak(perms, monkeypatch):
    monkeypatch.setattr(ai, "_DISPATCH", {"dummy": lambda a, u: {
        "parts": [{"pn": "X", "stok_total": 5}]}})
    monkeypatch.setattr(ai, "_allowed_tool_names", lambda u, sid="": {"dummy"})
    assert ai._run_tool("dummy", {}, AGUS)["parts"][0]["stok_total"] == 5
    assert ai._run_tool("dummy", {}, ADMIN)["parts"][0]["stok_total"] == 5
    assert ai._run_tool("dummy", {}, PEMBELI)["parts"][0]["stok_total"] == 5


# ── Tool khusus stok tak ditawarkan & tak boleh dieksekusi ───────────────────
def test_tool_stok_tak_ditawarkan_tanpa_izin(perms):
    nama_wawan = {f["function"]["name"] for f in ai._tool_specs(WAWAN)}
    nama_agus = {f["function"]["name"] for f in ai._tool_specs(AGUS)}
    nama_admin = {f["function"]["name"] for f in ai._tool_specs(ADMIN)}
    for t in ("stok_accurate", "stok_gudang", "stok_tertahan", "alternatif_ready"):
        assert t not in nama_wawan
    # staf ber-izin dapat tool stok umum; 'stok_tertahan' & 'alternatif_ready'
    # tetap admin-only (gerbang peran lama, tak diubah oleh gerbang stok ini).
    for t in ("stok_accurate", "stok_gudang"):
        assert t in nama_agus
    for t in ("stok_tertahan", "alternatif_ready"):
        assert t in nama_admin and t not in nama_agus
    assert "cari_part" in nama_wawan          # tool umum tetap ditawarkan


def test_eksekusi_tool_stok_ditolak_tanpa_izin(perms):
    assert "stok_gudang" not in ai._allowed_tool_names(WAWAN)
    assert ai._run_tool("stok_gudang", {"kata_kunci": "kopling"}, WAWAN)["denied"] is True


# ── Excel unggahan: gerbang ditegakkan di FILE, bukan cuma hasil model ───────
def test_sheet_isi_kolom_stok_ditolak_tanpa_izin():
    from app.services import ai_sheet
    r = ai_sheet.fill_columns("sid", WAWAN, permintaan=[{"isi": ai_sheet.ISI_STOK}],
                              boleh_stok=False)
    assert r["denied"] is True


def test_sheet_tandai_status_ditolak_tanpa_izin():
    """Warna baris READY/STOK KOSONG = pengungkapan stok lewat pintu belakang."""
    from app.services import ai_sheet
    r = ai_sheet.fill_columns("sid", WAWAN, permintaan=[{"isi": ai_sheet.ISI_NAMA}],
                              tandai_status=True, boleh_stok=False)
    assert r["denied"] is True


# ── Baris konteks per-user memberi tahu model bahwa stok tak tersedia ────────
def test_baris_konteks_sebut_larangan_stok(perms):
    assert "TIDAK berhak melihat STOK" in ai._user_context_line(WAWAN)
    assert "TIDAK berhak melihat STOK" not in ai._user_context_line(AGUS)
