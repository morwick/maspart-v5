"""Penawaran Accurate otomatis saat order lunas (create_for_order).
Semua panggilan Accurate di-mock — tak menyentuh Accurate sungguhan."""
import pytest

from app.services import accurate, accurate_quotation as aq


@pytest.fixture
def acc(monkeypatch):
    """Accurate palsu: 2 item berharga + akun 'roni' TERTAUT ke pelanggan.

    Pelanggan datang dari tautan akun (users.accurate_customer_id) yang diatur
    admin — bukan lagi dari `recipient_name`. Pencocokan nama sudah dihapus;
    lihat test_customer_map.py."""
    monkeypatch.setattr(aq.accurate, "available", lambda: True)
    monkeypatch.setattr(aq.accurate, "ensure_session_force", lambda: None)
    monkeypatch.setattr(aq.accurate, "next_quotation_number", lambda: "MASPART-07")
    monkeypatch.setattr(aq.customer_map, "untuk",
                        lambda u: {"id": 99, "no": "C.1", "name": "PT ARGCIO"}
                        if (u or "").strip().lower() == "roni" else None)
    items = {
        "WG9925520270": {"id": 11, "pn": "WG9925520270", "name": "Spring bracket",
                         "unit_id": 1, "price": 1_500_000},
        "AZ9925520271": {"id": 12, "pn": "AZ9925520271", "name": "Leaf spring",
                         "unit_id": 1, "price": 0},          # tanpa harga
    }
    monkeypatch.setattr(aq.accurate, "item_for_quotation", lambda pn: items.get(pn.upper()))
    created = {}
    def _create(**kw):
        created.update(kw)
        return {"id": 555, "number": kw["number"], "total": 1_500_000}
    monkeypatch.setattr(aq.accurate, "create_sales_quotation", _create)
    return created


def _order(**kw):
    # `recipient_name` sengaja BUKAN nama pelanggan: ia hanya alamat kirim dan
    # tak boleh lagi mempengaruhi pelanggan pada penawaran.
    o = {"order_code": "ORD-1", "username": "roni", "recipient_name": "rizki",
         "items": [{"part_number": "WG9925520270", "qty": 2}]}
    o.update(kw)
    return o


def test_created_happy_path(acc):
    r = aq.create_for_order(_order())
    assert r["status"] == "created" and r["number"] == "MASPART-07"
    assert r["id"] == 555 and r["customer"] == "PT ARGCIO"
    assert acc["customer_id"] == 99
    assert acc["lines"][0]["item_id"] == 11 and acc["lines"][0]["qty"] == 2.0


def test_skip_akun_belum_ditautkan(acc):
    """Akun tanpa tautan → skip SEBELUM login Accurate (jangan buang sesi)."""
    r = aq.create_for_order(_order(username="pembeli_baru"))
    assert r["status"] == "skip" and "belum ditautkan" in r["note"]


def test_skip_pn_tak_ada(acc):
    r = aq.create_for_order(_order(items=[{"part_number": "ZZZ0000", "qty": 1}]))
    assert r["status"] == "skip" and "tak ada di Accurate" in r["note"]


def test_skip_pn_tanpa_harga(acc):
    # AZ9925520271 harga 0 → seluruh penawaran di-skip (jangan buat sebagian).
    r = aq.create_for_order(_order(items=[{"part_number": "AZ9925520271", "qty": 1}]))
    assert r["status"] == "skip" and "tanpa harga" in r["note"]


def test_skip_order_tanpa_username(acc):
    r = aq.create_for_order(_order(username=""))
    assert r["status"] == "skip" and "username" in r["note"]


def test_harga_baris_ikut_yang_DIBAYAR_bukan_live_accurate(acc):
    """Dokumen = transaksi. Pembeli membayar harga indeks saat order; kalau admin
    keburu mengubah harga di Accurate sebelum pelunasan, penawaran TETAP memakai
    harga yang dibayar — kalau tidak, invoice hasil penawaran menagih angka lain
    dari uang yang sudah masuk."""
    r = aq.create_for_order(_order(items=[
        {"part_number": "WG9925520270", "qty": 2, "price": 1_400_000},  # dibayar
    ]))                                                                  # live: 1.5jt
    assert r["status"] == "created"
    assert acc["lines"][0]["unit_price"] == 1_400_000       # bukan 1_500_000


def test_tanpa_harga_order_jatuh_ke_live_accurate(acc):
    """Order lama (belum menyimpan price per item) tetap terbuat memakai harga live."""
    r = aq.create_for_order(_order())          # item tanpa field 'price'
    assert r["status"] == "created"
    assert acc["lines"][0]["unit_price"] == 1_500_000


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
