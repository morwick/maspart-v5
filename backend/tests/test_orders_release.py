"""Reservasi stok dilepas saat DIKIRIM/SELESAI/BATAL (bukan saat diproses) —
agar app 'ikut Accurate' setelah admin memproses penawaran (kurangi stok Accurate)
di titik pengiriman, tanpa double-potong. Anti-oversell tetap sepanjang order aktif."""
import pytest

from app.services import orders, reservations


@pytest.fixture
def patched(monkeypatch):
    calls = {"released": []}
    monkeypatch.setattr(orders, "_patch", lambda code, data, **kw: True)
    monkeypatch.setattr(reservations, "release",
                        lambda code: calls["released"].append(code) or True)
    return calls


def _from(monkeypatch, status):
    monkeypatch.setattr(orders, "current_status", lambda code, gudang=None: status)


@pytest.mark.parametrize("new", ["dikirim", "selesai", "batal"])
def test_set_status_melepas_reservasi(monkeypatch, patched, new):
    _from(monkeypatch, "diproses" if new != "batal" else "diproses")
    assert orders.set_status("PO-1", new) is True
    assert patched["released"] == ["PO-1"]


def test_set_status_diproses_tidak_melepas(monkeypatch, patched):
    _from(monkeypatch, "menunggu_pembayaran")
    assert orders.set_status("PO-1", "diproses") is True
    assert patched["released"] == []          # masih ditahan (anti-oversell)


def test_set_status_branch_dikirim_melepas(monkeypatch, patched):
    _from(monkeypatch, "diproses")
    assert orders.set_status_branch("PO-1", "01.Jakarta", "dikirim", tracking_no="JNE123") is True
    assert patched["released"] == ["PO-1"]


def test_transisi_ilegal_tidak_melepas(monkeypatch, patched):
    _from(monkeypatch, "selesai")             # terminal → tak boleh pindah
    assert orders.set_status("PO-1", "dikirim") is False
    assert patched["released"] == []
