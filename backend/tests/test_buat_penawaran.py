"""Tool buat_penawaran: admin-only berlapis + alur pelanggan/barang/PDF (Accurate mock)."""
import pytest

from app.services import ai_assistant as ai
from app.services import ai_export

ADMIN = {"username": "admin", "role": "admin"}
USER = {"username": "budi", "role": "user"}
PEMBELI = {"username": "toko", "role": "pembeli"}


@pytest.fixture
def acc(monkeypatch):
    """Accurate palsu: 1 pelanggan, 2 barang dikenal, buat+PDF sukses."""
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=15: [{"id": 2751, "no": "001-199", "name": "CV ANUGERAH"}]
                        if "anugerah" in q.lower() else [])
    katalog = {
        "WG9925520270": {"id": 3052, "no": "x", "pn": "WG9925520270", "name": "Spring",
                         "unit_id": 100, "unit": "Pc", "price": 1500000, "available": 5},
        "AZ9925520271": {"id": 3054, "no": "y", "pn": "AZ9925520271", "name": "Leaf",
                         "unit_id": 100, "unit": "Pc", "price": 90000, "available": 3},
    }
    monkeypatch.setattr(ai.accurate, "item_for_quotation",
                        lambda pn: katalog.get(pn.strip().upper()))
    dibuat = {}

    def _create(*, number, customer_id, lines, transdate, taxable=True, inclusive=True, description=""):
        dibuat.update(number=number, customer_id=customer_id, lines=lines, transdate=transdate)
        return {"id": 22964, "number": number, "total": sum(l["qty"] * l["unit_price"] for l in lines)}

    monkeypatch.setattr(ai.accurate, "ensure_session_force",
                        lambda: {"jsessionid": "J", "dsi": "D"})   # aksi user: sesi siap
    monkeypatch.setattr(ai.accurate, "create_sales_quotation", _create)
    monkeypatch.setattr(ai.accurate, "next_quotation_number", lambda: "MASPART-01")
    monkeypatch.setattr(ai.accurate, "sales_quotation_pdf", lambda qid, layout_id=50: b"%PDF-1.4 fake")
    monkeypatch.setattr(ai.accurate, "AccurateError", Exception, raising=False)
    dibuat["logout_calls"] = 0

    def _logout():
        dibuat["logout_calls"] += 1
        return True

    monkeypatch.setattr(ai.accurate, "logout", _logout)
    return dibuat


# ── Scoping admin-only (berlapis) ────────────────────────────────────────────

def test_tool_hanya_ditawarkan_ke_admin():
    assert "buat_penawaran" in ai._allowed_tool_names(ADMIN)
    assert "buat_penawaran" not in ai._allowed_tool_names(USER)
    assert "buat_penawaran" not in ai._allowed_tool_names(PEMBELI)


def test_handler_tolak_non_admin(acc):
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, USER)
    assert r["denied"] is True


def test_run_tool_tolak_non_admin_terpusat(acc):
    r = ai._run_tool("buat_penawaran", {"pelanggan": "x", "barang": []}, USER)
    assert r["denied"] is True


# ── Validasi input ───────────────────────────────────────────────────────────

def test_pelanggan_tak_ditemukan(acc):
    r = ai._t_buat_penawaran({"pelanggan": "TIDAK ADA",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert r["found"] is False and "tidak ditemukan" in r["error"].lower()


def test_part_tak_ada_batalkan_semua(acc):
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1},
                                         {"part_number": "ZZZ000", "qty": 2}]}, ADMIN)
    # Satu PN tak ada → TIDAK buat penawaran sebagian.
    assert r["found"] is False and r["part_tidak_ditemukan"] == ["ZZZ000"]
    assert "number" not in acc      # create_sales_quotation TIDAK dipanggil


def test_barang_tanpa_harga_dibatalkan(monkeypatch, acc):
    """Harga jual Accurate 0 → batal (tak menawar/mengarang harga)."""
    monkeypatch.setattr(ai.accurate, "item_for_quotation",
                        lambda pn: {"id": 9, "no": "z", "pn": pn.upper(), "name": "X",
                                    "unit_id": 100, "unit": "Pc", "price": 0, "available": 1})
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert r["found"] is False and r["part_tanpa_harga"] == ["WG9925520270"]
    assert "number" not in acc


# ── Nomor otomatis MASPART-NN (bukan penomoran otomatis Accurate) ────────────

def test_nomor_dibuat_maspart_bukan_dari_user(acc):
    r = ai._t_buat_penawaran({
        "pelanggan": "CV ANUGERAH", "tanggal": "10/07/2026",
        "barang": [{"part_number": "WG9925520270", "qty": 2}],
    }, ADMIN)
    assert r["found"] is True
    assert r["nomor"] == "MASPART-01"        # dari next_quotation_number, bukan user
    assert acc["number"] == "MASPART-01"     # nomor yg dikirim ke Accurate


def test_next_quotation_number_format():
    """MASPART-NN dari nomor tertinggi + 1, zero-pad 2 digit."""
    from app.services import accurate as a
    import re
    rx = a._MASPART_NUM_RE
    assert rx.match("MASPART-01").group(1) == "01" or int(rx.match("MASPART-01").group(1)) == 1
    assert int(rx.match("MASPART-07").group(1)) == 7
    assert int(rx.match("MASPART-123").group(1)) == 123
    assert rx.match("PQ2607000171") is None          # nomor Accurate diabaikan
    assert rx.match("TEST-CLD-002") is None


# ── Alur sukses ──────────────────────────────────────────────────────────────

def test_buat_sukses_hasilkan_kartu_pdf(acc):
    r = ai._t_buat_penawaran({
        "pelanggan": "CV ANUGERAH", "tanggal": "10/07/2026",
        "barang": [{"part_number": "WG9925520270", "qty": 2},
                   {"part_number": "AZ9925520271", "qty": 3}],
    }, ADMIN)
    assert r["found"] is True
    assert r["nomor"] == "MASPART-01" and r["pelanggan"] == "CV ANUGERAH"
    assert r["jumlah_barang"] == 2
    # HARGA selalu dari Accurate (tak ada override) — aturan pemilik.
    assert acc["lines"][0]["unit_price"] == 1500000
    assert acc["lines"][1]["unit_price"] == 90000
    # kartu unduh PDF benar-benar tersimpan & bisa diambil
    data, fname = ai_export.generic_excel(r["export_id"])
    assert data == b"%PDF-1.4 fake" and fname.endswith(".pdf")


def test_harga_selalu_dari_accurate_abaikan_override(acc):
    """Walau user menyisipkan 'harga', DIABAIKAN — pakai harga Accurate."""
    ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                          "barang": [{"part_number": "AZ9925520271", "qty": 1, "harga": 5}]}, ADMIN)
    assert acc["lines"][0]["unit_price"] == 90000      # harga Accurate, bukan 5


# ── Logout sesi setelah penawaran (akun Accurate 1 sesi) ─────────────────────

def test_logout_setelah_penawaran_dibuat(acc):
    ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                          "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert acc["logout_calls"] == 1      # sesi dilepas setelah buat


def test_tak_logout_bila_penawaran_tak_jadi(acc):
    # Pelanggan tak ada → penawaran tak dibuat → TIDAK perlu logout (tak ada sesi
    # write yang dipegang; logout hanya setelah benar-benar membuat penawaran).
    ai._t_buat_penawaran({"pelanggan": "TIDAK ADA",
                          "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert acc["logout_calls"] == 0


def test_login_gagal_pesan_jelas_bukan_cooldown(monkeypatch, acc):
    """Login Accurate gagal (mis. akun dipakai di tempat lain) → pesan jelas,
    penawaran tak dibuat, tak logout (tak ada sesi yg dipegang)."""
    def _fail():
        raise ai.accurate.AccurateError("login gagal")
    monkeypatch.setattr(ai.accurate, "ensure_session_force", _fail)
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert r["found"] is False
    assert "1 sesi" in r["error"] or "login gagal" in r["error"].lower()
    assert acc["logout_calls"] == 0 and "number" not in acc


def test_logout_walau_pdf_gagal(monkeypatch, acc):
    """PDF gagal setelah penawaran dibuat → sesi TETAP dilepas (finally)."""
    def _boom(qid, layout_id=50):
        raise ai.accurate.AccurateError("pdf gagal")
    monkeypatch.setattr(ai.accurate, "sales_quotation_pdf", _boom)
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert r["found"] is False
    assert acc["logout_calls"] == 1      # tetap logout walau PDF gagal


def test_capture_meta_munculkan_kartu_di_frontend():
    """Hasil buat_penawaran harus masuk daftar excel_exports (kartu unduh)."""
    import inspect
    src = inspect.getsource(ai.chat)
    assert "buat_penawaran" in src   # terdaftar di _capture_meta
