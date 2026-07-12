"""PPN aplikasi HARUS sama persis dengan Accurate: 12% INKLUSIF.

Bukti dari Penawaran Penjualan Accurate (MASPART-02, relay WG9725584002):
  Sub Total Rp 80.000 · PPN 12% Rp 8.571 · Total Rp 80.000
Artinya harga jual SUDAH mengandung PPN — pajak bukan tambahan. Aplikasi dulu
menambahkan PPN 11% di atas subtotal (80.000 → total 88.800), sehingga tagihan ke
pembeli tak pernah cocok dengan dokumen Accurate.

Aturan pemilik: subtotal & PPN ikut Accurate apa adanya; HANYA ongkir yang berasal
dari aplikasi dan ditambahkan di atas.
"""
import pytest

from app.services import orders as OS


def test_ppn_terkandung_sama_dengan_accurate():
    """80.000 × 12/112 = 8.571,43 → 8.571, persis seperti yang ditampilkan Accurate."""
    assert OS.ppn_included(80_000) == 8_571


def test_ppn_bukan_11_persen_di_atas_harga():
    assert OS.ppn_included(80_000) != 8_800     # ⛔ 11% eksklusif (perilaku lama)


@pytest.mark.parametrize("subtotal, ppn", [
    (0, 0),
    (1_120, 120),
    (112_000, 12_000),
    (1_000_000, 107_142),
])
def test_ppn_inklusif_berbagai_nilai(subtotal, ppn):
    assert OS.ppn_included(subtotal) == ppn


@pytest.fixture
def order_dibuat(monkeypatch):
    """Tangkap payload order yang dikirim ke Supabase."""
    terkirim = {}

    class _Resp:
        status_code = 201

        @staticmethod
        def json():
            return [{"id": 1, "order_code": "PO-1"}]

    def _post(url, headers=None, json=None, timeout=None):
        if "order_items" not in url:
            terkirim.update(json or {})
        return _Resp()

    monkeypatch.setattr(OS.requests, "post", _post)
    monkeypatch.setattr(OS.harga, "price_for_buyer", lambda pn: (80_000, "Relay 20A"))
    monkeypatch.setattr(OS.harga, "weight_for", lambda pn, allow_remote=False: 200)
    monkeypatch.setattr(OS.gudang, "gudang_label", lambda g: g)
    return terkirim


def test_total_order_sama_dengan_accurate_plus_ongkir(order_dibuat):
    """Total = harga Accurate (sudah termasuk PPN) + ongkir aplikasi. TANPA menambah PPN."""
    order, err = OS.create_order(
        "roni", "pembeli", "", [{"part_number": "WG9725584002", "qty": 1}],
        shipping_cost=12_000, gudang_label="02.Pekanbaru")
    assert err is None
    assert order_dibuat["subtotal"] == 80_000        # sama dengan Sub Total Accurate
    assert order_dibuat["tax"] == 8_571              # sama dengan PPN 12% Accurate
    assert order_dibuat["shipping_cost"] == 12_000   # satu-satunya angka dari aplikasi
    assert order_dibuat["total"] == 92_000           # 80.000 + 12.000 (BUKAN 100.800)
    assert order["total"] == 92_000


def test_tanpa_ongkir_total_persis_subtotal(order_dibuat):
    OS.create_order("roni", "pembeli", "", [{"part_number": "WG9725584002", "qty": 1}],
                    shipping_cost=0, gudang_label="02.Pekanbaru")
    assert order_dibuat["total"] == 80_000           # persis Total di Accurate
