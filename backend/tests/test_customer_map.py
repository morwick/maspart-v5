"""Tautan akun → pelanggan Accurate, dan pemakaiannya oleh penawaran otomatis.

Dulu pelanggan dicocokkan dari `orders.recipient_name` — teks BEBAS yang diketik
pembeli di form pengiriman. Akun yang sama menulis 'rizki' hari ini dan
'PT ARGCIO JAYA ABADI' besok: penawaran kadang jadi, kadang di-skip diam-diam,
dan bisa menempel ke pelanggan SALAH bila namanya mirip.
"""
import pytest

from app.services import accurate_quotation as AQ
from app.services import customer_map


@pytest.fixture
def taut(monkeypatch):
    """Peta tautan palsu: username → {id,name,no}."""
    peta: dict[str, dict] = {}
    monkeypatch.setattr(AQ.customer_map, "untuk", lambda u: peta.get((u or "").strip().lower()))
    return peta


@pytest.fixture
def accurate_siap(monkeypatch):
    """Accurate 'hidup' + 1 item berharga, supaya tes fokus ke sisi pelanggan."""
    dibuat = {}
    monkeypatch.setattr(AQ.accurate, "available", lambda: True)
    monkeypatch.setattr(AQ.accurate, "ensure_session_force", lambda: None)
    monkeypatch.setattr(AQ.accurate, "item_for_quotation",
                        lambda pn: {"id": 11, "name": "Relay", "unit_id": 1, "price": 80000, "pn": pn})
    monkeypatch.setattr(AQ.accurate, "next_quotation_number", lambda: "MASPART-01")
    monkeypatch.setattr(AQ.accurate, "create_sales_quotation",
                        lambda **kw: dibuat.update(kw) or {"id": 99, "number": kw["number"]})
    return dibuat


def _order(**kw):
    d = {"order_code": "PO-1", "username": "roni", "recipient_name": "rizki",
         "items": [{"part_number": "WG9725584002", "qty": 2, "price": 80000}]}
    d.update(kw)
    return d


def test_pelanggan_diambil_dari_tautan_akun(taut, accurate_siap):
    """⛔ BUKAN dari recipient_name — di order ini namanya 'rizki', tapi yang
    dipakai harus pelanggan yang ditautkan ke akun 'roni'."""
    taut["roni"] = {"id": 4242, "name": "PT ARGCIO JAYA ABADI", "no": "C-001"}
    r = AQ.create_for_order(_order())
    assert r["status"] == "created"
    assert accurate_siap["customer_id"] == 4242          # id tautan, bukan hasil cari nama
    assert r["customer"] == "PT ARGCIO JAYA ABADI"


def test_recipient_name_tidak_lagi_dipakai(taut, accurate_siap, monkeypatch):
    """Nama penerima boleh apa saja — bahkan nama pelanggan LAIN — tautan tetap menang."""
    taut["roni"] = {"id": 4242, "name": "PT ARGCIO JAYA ABADI", "no": ""}
    # Bila kode diam-diam kembali mencari nama, ini akan meledak.
    def _jangan_dipanggil(*a, **k):
        raise AssertionError("search_customers tak boleh dipanggil lagi")
    monkeypatch.setattr(AQ.accurate, "search_customers", _jangan_dipanggil)
    r = AQ.create_for_order(_order(recipient_name="PT PERUSAHAAN LAIN"))
    assert r["status"] == "created" and accurate_siap["customer_id"] == 4242


def test_akun_belum_ditautkan_di_skip_dengan_alasan_jelas(taut, accurate_siap):
    """Skip, BUKAN error: pesanan tetap lunas & diproses."""
    r = AQ.create_for_order(_order())
    assert r["status"] == "skip"
    assert "belum ditautkan" in r["note"] and "roni" in r["note"]


def test_order_tanpa_username_di_skip(taut, accurate_siap):
    r = AQ.create_for_order(_order(username=""))
    assert r["status"] == "skip" and "username" in r["note"]


# ── Lapisan penyimpanan ─────────────────────────────────────────────────────

def test_untuk_tak_pernah_melempar_saat_db_bermasalah(monkeypatch):
    """Dipanggil dari jalur pembayaran — kegagalan DB tak boleh naik ke atas."""
    def _meledak(*a, **k):
        raise RuntimeError("DB down")
    monkeypatch.setattr(customer_map.requests, "get", _meledak)
    assert customer_map.untuk("roni") is None


def test_pesan_jelas_bila_migrasi_belum_jalan(monkeypatch):
    class _Resp:
        status_code = 400
        text = '{"code":"42703","message":"column users.accurate_customer_id does not exist"}'
    monkeypatch.setattr(customer_map.requests, "patch", lambda *a, **k: _Resp())
    ok, msg = customer_map.tautkan("roni", 1)
    assert ok is False and "migrations/024" in msg


def test_lepas_tautan_mengirim_null(monkeypatch):
    terkirim = {}

    class _Resp:
        status_code = 204
        text = ""
    monkeypatch.setattr(customer_map.requests, "patch",
                        lambda *a, **k: terkirim.update(k.get("json") or {}) or _Resp())
    ok, _ = customer_map.tautkan("roni", None)
    assert ok is True
    assert terkirim["accurate_customer_id"] is None
    assert terkirim["accurate_customer_name"] is None
