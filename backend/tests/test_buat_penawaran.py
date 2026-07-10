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

    monkeypatch.setattr(ai.accurate, "create_sales_quotation", _create)
    monkeypatch.setattr(ai.accurate, "sales_quotation_pdf", lambda qid, layout_id=50: b"%PDF-1.4 fake")
    monkeypatch.setattr(ai.accurate, "AccurateError", Exception, raising=False)
    return dibuat


# ── Scoping admin-only (berlapis) ────────────────────────────────────────────

def test_tool_hanya_ditawarkan_ke_admin():
    assert "buat_penawaran" in ai._allowed_tool_names(ADMIN)
    assert "buat_penawaran" not in ai._allowed_tool_names(USER)
    assert "buat_penawaran" not in ai._allowed_tool_names(PEMBELI)


def test_handler_tolak_non_admin(acc):
    r = ai._t_buat_penawaran({"nomor": "PN-1", "pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, USER)
    assert r["denied"] is True


def test_run_tool_tolak_non_admin_terpusat(acc):
    r = ai._run_tool("buat_penawaran", {"nomor": "PN-1", "pelanggan": "x", "barang": []}, USER)
    assert r["denied"] is True


# ── Validasi input ───────────────────────────────────────────────────────────

def test_nomor_wajib(acc):
    r = ai._t_buat_penawaran({"nomor": "  ", "pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert "error" in r and "omor" in r["error"]


def test_pelanggan_tak_ditemukan(acc):
    r = ai._t_buat_penawaran({"nomor": "PN-1", "pelanggan": "TIDAK ADA",
                              "barang": [{"part_number": "WG9925520270", "qty": 1}]}, ADMIN)
    assert r["found"] is False and "tidak ditemukan" in r["error"].lower()


def test_part_tak_ada_batalkan_semua(acc):
    r = ai._t_buat_penawaran({"nomor": "PN-1", "pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "WG9925520270", "qty": 1},
                                         {"part_number": "ZZZ000", "qty": 2}]}, ADMIN)
    # Satu PN tak ada → TIDAK buat penawaran sebagian.
    assert r["found"] is False and r["part_tidak_ditemukan"] == ["ZZZ000"]
    assert "number" not in acc      # create_sales_quotation TIDAK dipanggil


# ── Alur sukses ──────────────────────────────────────────────────────────────

def test_buat_sukses_hasilkan_kartu_pdf(acc):
    r = ai._t_buat_penawaran({
        "nomor": "PN-2026-001", "pelanggan": "CV ANUGERAH", "tanggal": "10/07/2026",
        "barang": [{"part_number": "WG9925520270", "qty": 2},
                   {"part_number": "AZ9925520271", "qty": 3, "harga": 100000}],
    }, ADMIN)
    assert r["found"] is True
    assert r["nomor"] == "PN-2026-001" and r["pelanggan"] == "CV ANUGERAH"
    assert r["jumlah_barang"] == 2
    # harga baris 1 = default Accurate; baris 2 = harga user (100000)
    assert acc["lines"][0]["unit_price"] == 1500000
    assert acc["lines"][1]["unit_price"] == 100000
    assert acc["number"] == "PN-2026-001"      # nomor MANUAL diteruskan apa adanya
    # kartu unduh PDF benar-benar tersimpan & bisa diambil
    data, fname = ai_export.generic_excel(r["export_id"])
    assert data == b"%PDF-1.4 fake" and fname.endswith(".pdf")


def test_harga_default_dari_accurate_bila_tak_disebut(acc):
    ai._t_buat_penawaran({"nomor": "PN-9", "pelanggan": "CV ANUGERAH",
                          "barang": [{"part_number": "AZ9925520271", "qty": 1}]}, ADMIN)
    assert acc["lines"][0]["unit_price"] == 90000      # harga Accurate


def test_capture_meta_munculkan_kartu_di_frontend():
    """Hasil buat_penawaran harus masuk daftar excel_exports (kartu unduh)."""
    import inspect
    src = inspect.getsource(ai.chat)
    assert "buat_penawaran" in src   # terdaftar di _capture_meta
