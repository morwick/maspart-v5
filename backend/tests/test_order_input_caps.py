"""Plafon input pembeli di router orders (audit 2026-07-21).

/shipping/weight, /cart/gudang & /orders menghitung berat per item dengan
allow_remote=True — tiap PN asing = fetch SIMS live. Tanpa plafon `items`,
satu akun pembeli bisa mengirim ribuan PN sampah per request dan membanjiri
SIMS (sesi bersama seluruh toko). String juga dibatasi agar note/alamat
megabyte tak tersimpan ke DB.
"""
import pytest
from pydantic import ValidationError

from app.routers import orders as R


def _item(pn="WG9725190102", qty=1):
    return {"part_number": pn, "qty": qty}


def test_items_lebih_dari_plafon_ditolak():
    banyak = [_item(f"PN{i:05d}") for i in range(R._MAX_ITEMS + 1)]
    for model in (R.CreateOrderRequest, R.WeightRequest, R.RatesRequest,
                  R.CartGudangRequest):
        with pytest.raises(ValidationError):
            model(items=banyak)


def test_items_pas_plafon_diterima():
    pas = [_item(f"PN{i:05d}") for i in range(R._MAX_ITEMS)]
    assert len(R.WeightRequest(items=pas).items) == R._MAX_ITEMS


def test_string_panjang_ditolak():
    with pytest.raises(ValidationError):
        R.CreateOrderRequest(items=[_item()], note="x" * 2001)
    with pytest.raises(ValidationError):
        R.CreateOrderRequest(items=[_item()], recipient_address="x" * 501)
    with pytest.raises(ValidationError):
        R.OrderItemIn(part_number="P" * 65)


def test_qty_raksasa_ditolak_qty_nol_lolos_model():
    """Batas bawah qty SENGAJA tidak di model — jalur 400 berpesan di
    _create_order_impl yang menanganinya (pesan menyebut part-nya)."""
    with pytest.raises(ValidationError):
        R.OrderItemIn(part_number="PN1", qty=100_000)
    assert R.OrderItemIn(part_number="PN1", qty=0).qty == 0


def test_weight_grams_raksasa_ditolak():
    with pytest.raises(ValidationError):
        R.RatesRequest(weight_grams=2_000_001)
