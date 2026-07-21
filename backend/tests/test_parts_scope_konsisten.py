"""Konsistensi stok pembeli antara ETALASE (buyer_catalog) & DETAIL (parts).

Model (keputusan pemilik 2026-07-12, final): gudang pembeli dulu → FALLBACK
gudang terdekat yang masih ada stok (pembeli tetap bisa membeli; badge
'READY · <gudang>' = gudang pengirim; ongkir dari gudang pemenuh). Regresi
bug: reservasi HARUS dikurangkan SEBELUM scoping — kalau scope dulu, fallback
tak jalan → katalog READY tapi detail 'habis'.
"""
from app.routers import parts
from app.services import buyer_catalog, orders, part_index, reservations, gudang
from app.services import supabase_client as sb


def _mock_common(monkeypatch):
    monkeypatch.setattr(parts.part_index, "gudang_names", lambda: ["01.Jakarta", "23.Medan"])
    monkeypatch.setattr(parts, "get_user_gudang", lambda u: "jakarta")
    monkeypatch.setattr(parts.gudang, "buyer_label",
                        lambda key: {"jakarta": "01.Jakarta", "medan": "23.Medan"}.get(key or ""))


def test_detail_scope_reserve_sebelum_scope(monkeypatch):
    _mock_common(monkeypatch)
    monkeypatch.setattr(parts.reservations, "reserved_map",
                        lambda: {("WG9100443050", "01.Jakarta"): 5})
    results = [{"part_number": "WG9100443050", "gudang": {"01.Jakarta": 5, "23.Medan": 2}}]
    out = parts._scope_gudang(results, {"username": "budi", "role": "pembeli"})
    # Jakarta habis direservasi → fallback Medan 2 (BUKAN kosong). Label pembeli
    # kini nama KOTA (tanpa nomor gudang) — rincian internal disembunyikan.
    assert out[0]["gudang"] == {"Medan": 2}


def test_detail_scope_tanpa_reservasi_pakai_gudang_sendiri(monkeypatch):
    _mock_common(monkeypatch)
    monkeypatch.setattr(parts.reservations, "reserved_map", lambda: {})
    results = [{"part_number": "WG9100443050", "gudang": {"01.Jakarta": 5, "23.Medan": 2}}]
    out = parts._scope_gudang(results, {"username": "budi", "role": "pembeli"})
    assert out[0]["gudang"] == {"Jakarta": 5}      # label kota (M5)


def test_katalog_dan_detail_sepakat_saat_reservasi(monkeypatch):
    """Etalase & detail HARUS menunjuk gudang pemenuh yang SAMA (Medan)."""
    _mock_common(monkeypatch)
    resv = {("WG9100443050", "01.Jakarta"): 5}
    bd = {"01.Jakarta": 5, "23.Medan": 2}

    # Detail (parts)
    monkeypatch.setattr(parts.reservations, "reserved_map", lambda: dict(resv))
    detail = parts._scope_gudang(
        [{"part_number": "WG9100443050", "gudang": dict(bd)}],
        {"username": "budi", "role": "pembeli"},
    )[0]["gudang"]

    # Etalase (buyer_catalog._scoped_stock)
    qty, label = buyer_catalog._scoped_stock(
        {"part_number": "WG9100443050", "gudang": dict(bd)},
        "budi", own="01.Jakarta", all_names=["01.Jakarta", "23.Medan"], resv=dict(resv),
    )
    # Detail → {'Medan': 2} (label kota); etalase → (2, 'Medan') — konsisten.
    assert detail == {"Medan": 2}
    assert qty == 2 and "Medan" in label


def test_fulfillment_gudang_utk_asal_ongkir(monkeypatch):
    """Gudang pemenuh (asal ongkir) = gudang sendiri bila ada stok, fallback bila
    habis direservasi — logika SAMA dgn pemilihan gudang di create_order."""
    monkeypatch.setattr(part_index, "gudang_names", lambda: ["01.Jakarta", "23.Medan"])
    monkeypatch.setattr(sb, "get_user_gudang", lambda u: "jakarta")
    monkeypatch.setattr(gudang, "buyer_label",
                        lambda k: {"jakarta": "01.Jakarta", "medan": "23.Medan"}.get(k or ""))
    monkeypatch.setattr(part_index, "gudang_breakdown",
                        lambda pn: {"01.Jakarta": 5, "23.Medan": 2})
    items = [{"part_number": "PN1", "qty": 1}]

    monkeypatch.setattr(reservations, "reserved_map", lambda force=False: {})
    assert orders.fulfillment_gudang("budi", items) == "01.Jakarta"

    monkeypatch.setattr(reservations, "reserved_map",
                        lambda force=False: {("PN1", "01.Jakarta"): 5})
    assert orders.fulfillment_gudang("budi", items) == "23.Medan"

    assert orders.fulfillment_gudang("budi", []) == ""
