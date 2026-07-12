"""Penawaran Accurate otomatis saat order lunas (create_for_order).
Semua panggilan Accurate di-mock — tak menyentuh Accurate sungguhan."""
import pytest

from app.services import accurate, accurate_quotation as aq


@pytest.fixture
def acc(monkeypatch):
    """Accurate palsu: 1 customer 'PT ARGCIO', 2 item berharga."""
    monkeypatch.setattr(aq.accurate, "available", lambda: True)
    monkeypatch.setattr(aq.accurate, "ensure_session_force", lambda: None)
    monkeypatch.setattr(aq.accurate, "next_quotation_number", lambda: "MASPART-07")
    items = {
        "WG9925520270": {"id": 11, "pn": "WG9925520270", "name": "Spring bracket",
                         "unit_id": 1, "price": 1_500_000},
        "AZ9925520271": {"id": 12, "pn": "AZ9925520271", "name": "Leaf spring",
                         "unit_id": 1, "price": 0},          # tanpa harga
    }
    monkeypatch.setattr(aq.accurate, "item_for_quotation", lambda pn: items.get(pn.upper()))
    monkeypatch.setattr(aq.accurate, "search_customers",
                        lambda name, limit=20: [{"id": 99, "no": "C.1", "name": "PT ARGCIO"}]
                        if "argcio" in name.lower() else [])
    created = {}
    def _create(**kw):
        created.update(kw)
        return {"id": 555, "number": kw["number"], "total": 1_500_000}
    monkeypatch.setattr(aq.accurate, "create_sales_quotation", _create)
    return created


def _order(**kw):
    o = {"order_code": "ORD-1", "recipient_name": "PT ARGCIO",
         "items": [{"part_number": "WG9925520270", "qty": 2}]}
    o.update(kw)
    return o


def test_created_happy_path(acc):
    r = aq.create_for_order(_order())
    assert r["status"] == "created" and r["number"] == "MASPART-07"
    assert r["id"] == 555 and r["customer"] == "PT ARGCIO"
    assert acc["customer_id"] == 99
    assert acc["lines"][0]["item_id"] == 11 and acc["lines"][0]["qty"] == 2.0


def test_skip_customer_tak_ditemukan(acc):
    r = aq.create_for_order(_order(recipient_name="Budi Perorangan"))
    assert r["status"] == "skip" and "tak ditemukan" in r["note"]


def test_skip_customer_ambigu(acc, monkeypatch):
    monkeypatch.setattr(aq.accurate, "search_customers",
                        lambda name, limit=20: [{"id": 1, "name": "PT JAYA A"},
                                                {"id": 2, "name": "PT JAYA B"}])
    r = aq.create_for_order(_order(recipient_name="jaya"))
    assert r["status"] == "skip" and "ambigu" in r["note"]


def test_skip_pn_tak_ada(acc):
    r = aq.create_for_order(_order(items=[{"part_number": "ZZZ0000", "qty": 1}]))
    assert r["status"] == "skip" and "tak ada di Accurate" in r["note"]


def test_skip_pn_tanpa_harga(acc):
    # AZ9925520271 harga 0 → seluruh penawaran di-skip (jangan buat sebagian).
    r = aq.create_for_order(_order(items=[{"part_number": "AZ9925520271", "qty": 1}]))
    assert r["status"] == "skip" and "tanpa harga" in r["note"]


def test_skip_nama_kosong(acc):
    r = aq.create_for_order(_order(recipient_name="", username=""))
    assert r["status"] == "skip" and "kosong" in r["note"]


def test_skip_accurate_tak_aktif(monkeypatch):
    monkeypatch.setattr(aq.accurate, "available", lambda: False)
    r = aq.create_for_order(_order())
    assert r["status"] == "skip"


def test_failed_login_gagal(acc, monkeypatch):
    def _boom():
        raise accurate.AccurateError("akun dipakai di tempat lain")
    monkeypatch.setattr(aq.accurate, "ensure_session_force", _boom)
    r = aq.create_for_order(_order())
    assert r["status"] == "failed" and "login" in r["note"].lower()


def test_failed_create_exception_tak_bocor(acc, monkeypatch):
    def _boom(**kw):
        raise RuntimeError("Accurate down")
    monkeypatch.setattr(aq.accurate, "create_sales_quotation", _boom)
    r = aq.create_for_order(_order())            # tak boleh raise
    assert r["status"] == "failed"
