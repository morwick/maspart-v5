"""Test etalase pembeli (buyer_catalog): build produk, scope stok lokasi,
pencarian, sort, pagination, kategori, terlaris. Semua sumber di-mock —
tidak menyentuh Accurate/Supabase/galeri foto sungguhan."""
import pytest

from app.services import accurate, buyer_catalog, gudang, harga, image_search, part_index, reservations


# ── Data uji ─────────────────────────────────────────────────────────────────
_CATALOG = [
    ("WG9100443050", "BRAKE PAD"),
    ("VG1560080012", "FUEL FILTER ASSEMBLY"),
    ("AZ9725520276", "SPRING FRONT"),
    ("WG1642770001", "MIRROR ASSY LH"),          # tanpa harga → tak dipajang
    ("202V09100-7926", "OIL FILTER ELEMENT"),
]
_ACC_ITEMS = [
    {"pn": "WG9100443050", "name": "BRAKE PAD SINOTRUK", "price": 250_000, "available_to_sell": 12},
    {"pn": "VG1560080012", "name": "FUEL FILTER", "price": 150_000, "available_to_sell": 4},
    {"pn": "KACA-SPION-88", "name": "KACA SPION UNIVERSAL", "price": 90_000, "available_to_sell": 7},  # aftermarket (di luar katalog)
    {"pn": "202V09100-7926", "name": "OIL FILTER", "price": 75_000, "available_to_sell": 3},
    {"pn": "AZ9725520276", "name": "SPRING", "price": 1_200_000, "available_to_sell": 0},
    {"pn": "NO-WEIGHT-01", "name": "TANPA BERAT", "price": 50_000, "available_to_sell": 9},  # berat 0 → tak dipajang
    {"pn": "HARGA-NOL-01", "name": "HARGA NOL DI ACCURATE", "price": 0, "available_to_sell": 5},
]
# harga.xlsx sengaja diisi harga BERBEDA + satu PN yang HANYA ada di sini — keduanya
# harus DIABAIKAN: harga jual = Accurate saja (aturan pemilik).
_HARGA_EXCEL = {"202V09100-7926": "Rp 999.000", "EXCEL-ONLY-01": "Rp 300.000",
                "HARGA-NOL-01": "Rp 400.000"}
_GUDANG_BD = {
    "WG9100443050": {"01.Jakarta": 5, "23.Medan": 2},
    "VG1560080012": {"23.Medan": 3},
    "202V09100-7926": {"01.Jakarta": 8},
    "AZ9725520276": {},
}
_FOTOS = {"WG9100443050": "http://sims/brakepad.jpg", "KACA-SPION-88": "learned://spion.jpg"}
_BERAT = {"WG9100443050": 1200, "VG1560080012": 800, "KACA-SPION-88": 300,
          "202V09100-7926": 500, "AZ9725520276": 45_000, "NO-WEIGHT-01": 0}


@pytest.fixture(autouse=True)
def _mock_sumber(monkeypatch):
    monkeypatch.setattr(part_index, "all_parts_min", lambda: list(_CATALOG))
    monkeypatch.setattr(part_index, "harga_map", lambda: dict(_HARGA_EXCEL))
    monkeypatch.setattr(part_index, "gudang_breakdown", lambda pn: dict(_GUDANG_BD.get(pn.upper(), {})))
    monkeypatch.setattr(part_index, "gudang_names", lambda: ["01.Jakarta", "23.Medan"])
    monkeypatch.setattr(part_index, "status", lambda: {"indexed_at": "T1"})
    monkeypatch.setattr(accurate, "all_items", lambda force=False: list(_ACC_ITEMS))
    monkeypatch.setattr(accurate, "snapshot", lambda: {"x": 1})
    monkeypatch.setattr(accurate, "gudang_breakdown", lambda pn: {})
    monkeypatch.setattr(image_search, "photo_url_map", lambda: dict(_FOTOS))
    monkeypatch.setattr(harga, "weight_for", lambda pn: _BERAT.get((pn or "").upper(), 0))
    monkeypatch.setattr(reservations, "reserved_map", lambda force=False: {})
    monkeypatch.setattr(gudang, "buyer_label", lambda key: {"jakarta": "01.Jakarta", "medan": "23.Medan"}.get(key or ""))
    buyer_catalog.invalidate_cache()
    yield
    buyer_catalog.invalidate_cache()


def _page(**kw):
    args = {"username": "budi", "buyer_gudang_key": "jakarta"}
    args.update(kw)
    return buyer_catalog.catalog_page(**args)


# ── Build produk ─────────────────────────────────────────────────────────────
def test_build_hanya_produk_berharga_dan_berberat():
    pns = {i["part_number"] for i in _page(page_size=100)["items"]}
    assert "WG9100443050" in pns            # harga Accurate
    assert "KACA-SPION-88" in pns           # aftermarket di luar katalog Excel
    assert "202V09100-7926" in pns          # harga Accurate
    assert "WG1642770001" not in pns        # tanpa harga sama sekali
    assert "NO-WEIGHT-01" not in pns        # tanpa berat → keranjang menolak


def test_harga_hanya_dari_accurate_bukan_harga_xlsx():
    """⛔ Aturan pemilik: harga.xlsx TAK PERNAH jadi harga jual. Part yang berharga
    hanya di sana (atau berharga 0 di Accurate) TIDAK dijual — kalau dipajang, pembeli
    akan ditolak saat checkout karena order memakai harga Accurate."""
    items = {i["part_number"]: i for i in _page(page_size=100)["items"]}
    assert "EXCEL-ONLY-01" not in items      # hanya ada di harga.xlsx
    assert "HARGA-NOL-01" not in items       # harga 0 di Accurate; Excel TIDAK menambal
    # Harga yang dipajang = harga Accurate, bukan angka lain di harga.xlsx.
    assert items["202V09100-7926"]["harga"] == 75_000
    assert items["202V09100-7926"]["harga_display"] == "Rp 75.000"


def test_nama_utamakan_katalog_lokal_dan_fallback_accurate():
    items = {i["part_number"]: i for i in _page(page_size=100)["items"]}
    assert items["WG9100443050"]["name"] == "BRAKE PAD"          # nama katalog menang
    assert items["KACA-SPION-88"]["name"] == "KACA SPION UNIVERSAL"  # fallback Accurate


def test_foto_dan_kategori():
    items = {i["part_number"]: i for i in _page(page_size=100)["items"]}
    assert items["WG9100443050"]["foto"] == "http://sims/brakepad.jpg"
    assert "rem" in items["WG9100443050"]["kategori"]
    assert "filter" in items["VG1560080012"]["kategori"]
    assert "suspensi" in items["AZ9725520276"]["kategori"]
    assert items["VG1560080012"]["foto"] is None


# ── Scope stok lokasi pembeli (gudang sendiri dulu → fallback terdekat) ──────
def test_stok_discope_ke_lokasi_pembeli():
    items = {i["part_number"]: i for i in _page(page_size=100)["items"]}
    # Pembeli Jakarta: brake pad 5 (bukan 7 total), fuel filter fallback Medan
    # (keputusan pemilik 2026-07-12: pembeli TETAP bisa beli dari gudang lain,
    # badge 'READY · <gudang>' menunjukkan gudang pengirim).
    assert items["WG9100443050"]["stok"] == 5
    assert items["WG9100443050"]["gudang"] == "Jakarta"
    assert items["VG1560080012"]["stok"] == 3
    assert items["VG1560080012"]["gudang"] == "Medan"
    assert items["AZ9725520276"]["ready"] is False


def test_reservasi_mengurangi_stok(monkeypatch):
    monkeypatch.setattr(reservations, "reserved_map",
                        lambda force=False: {("WG9100443050", "01.Jakarta"): 5})
    items = {i["part_number"]: i for i in _page(page_size=100)["items"]}
    # Jakarta habis direservasi → fallback gudang lain yang masih ada (Medan 2).
    assert items["WG9100443050"]["stok"] == 2
    assert items["WG9100443050"]["gudang"] == "Medan"


def test_ready_only_menyaring_yang_habis():
    pns = {i["part_number"] for i in _page(ready_only=True, page_size=100)["items"]}
    assert "AZ9725520276" not in pns
    assert "WG9100443050" in pns


# ── Fingerprint ikut TIMESTAMP indeks Accurate (harga dipajang == dibayar) ────
def test_fingerprint_berubah_saat_timestamp_indeks_berubah(monkeypatch):
    """Refresh yang UBAH HARGA tapi jumlah item sama → timestamp indeks bergeser →
    fingerprint berubah → etalase rebuild → harga pajang sinkron dgn checkout."""
    monkeypatch.setattr(accurate, "index_stamp", lambda: (100.0, 50.0))
    fp1 = buyer_catalog._fingerprint()
    monkeypatch.setattr(accurate, "index_stamp", lambda: (200.0, 50.0))  # ts agregat naik
    fp2 = buyer_catalog._fingerprint()
    assert fp1 != fp2

    # Tanpa perubahan ts → fingerprint stabil (tak rebuild sia-sia).
    monkeypatch.setattr(accurate, "index_stamp", lambda: (200.0, 50.0))
    assert buyer_catalog._fingerprint() == fp2


# ── Pencarian, sort, pagination ──────────────────────────────────────────────
def test_cari_pn_dan_nama():
    assert {i["part_number"] for i in _page(q="WG9100", page_size=100)["items"]} == {"WG9100443050"}
    assert "VG1560080012" in {i["part_number"] for i in _page(q="fuel filter", page_size=100)["items"]}


def test_cari_pn_tanpa_pemisah():
    hits = _page(q="202V091007926", page_size=100)["items"]
    assert [i["part_number"] for i in hits] == ["202V09100-7926"]


def test_sort_harga():
    asc = [i["harga"] for i in _page(sort="harga_asc", page_size=100)["items"]]
    assert asc == sorted(asc)
    desc = [i["harga"] for i in _page(sort="harga_desc", page_size=100)["items"]]
    assert desc == sorted(desc, reverse=True)


def test_pagination():
    r = _page(page_size=2, page=1)
    assert len(r["items"]) == 2 and r["total_pages"] >= 2 and r["count"] >= 4
    r2 = _page(page_size=2, page=2)
    assert r2["items"] and r2["items"][0]["part_number"] != r["items"][0]["part_number"]


def test_filter_kategori():
    pns = {i["part_number"] for i in _page(kategori="filter", page_size=100)["items"]}
    assert "VG1560080012" in pns and "202V09100-7926" in pns
    assert "WG9100443050" not in pns


# ── Home payload ─────────────────────────────────────────────────────────────
def test_home_payload_kategori_dan_unggulan(monkeypatch):
    monkeypatch.setattr(buyer_catalog, "_terlaris_map",
                        lambda: {"VG1560080012": 30, "WG9100443050": 10, "TIDAK-DIJUAL": 99})
    h = buyer_catalog.home_payload("budi", "jakarta")
    assert h["lokasi"] == "Jakarta"
    assert h["total_produk"] >= 4
    keys = {k["key"] for k in h["kategori"]}
    assert {"filter", "rem", "suspensi"} <= keys
    # Terlaris urut qty terjual & hanya produk etalase (PN asing dibuang).
    assert [i["part_number"] for i in h["terlaris"]][:2] == ["VG1560080012", "WG9100443050"]
    # Unggulan hanya produk BERFOTO.
    assert all(i["foto"] for i in h["unggulan"])


def test_cache_dibangun_sekali_per_fingerprint(monkeypatch):
    calls = {"n": 0}
    orig = buyer_catalog._build_products

    def counted():
        calls["n"] += 1
        return orig()

    monkeypatch.setattr(buyer_catalog, "_build_products", counted)
    buyer_catalog.invalidate_cache()
    _page()
    _page()
    assert calls["n"] == 1


# ── Build KOSONG = transien (jangan nyangkut) — kejadian nyata 2026-07-15 ─────
def test_build_kosong_tidak_disimpan_sah_lalu_sembuh(monkeypatch):
    """Build KOSONG (sumber dingin pasca-restart) TIDAK di-cache sebagai sah;
    begitu sumber siap (build berikutnya berisi), etalase terisi SENDIRI tanpa
    menunggu refresh terjadwal."""
    calls = {"n": 0}
    seq = [[], [{"part_number": "WG9100443050", "kategori": ["rem"]}]]  # kosong → berisi

    def build():
        r = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return list(r)

    monkeypatch.setattr(buyer_catalog, "_build_products", build)
    buyer_catalog.invalidate_cache()

    assert buyer_catalog._products() == []                 # build pertama kosong
    assert buyer_catalog._cache["fp"] is None              # ⛔ tak di-cache sebagai sah

    # Lewati cooldown → build ulang; sumber kini berisi → sembuh sendiri.
    buyer_catalog._cache["ts"] -= buyer_catalog._EMPTY_RETRY_SEC + 1
    prods = buyer_catalog._products()
    assert [p["part_number"] for p in prods] == ["WG9100443050"]
    assert buyer_catalog._cache["fp"] is not None          # kini di-cache sah


def test_build_kosong_cooldown_tak_rebuild_beruntun(monkeypatch):
    """Dalam masa cooldown, hasil kosong tak membangun ulang tiap request (hindari
    badai build saat sumber memang benar-benar kosong)."""
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return []

    monkeypatch.setattr(buyer_catalog, "_build_products", build)
    buyer_catalog.invalidate_cache()

    buyer_catalog._products()          # build #1 (kosong)
    buyer_catalog._products()          # dalam cooldown → TIDAK rebuild
    buyer_catalog._products()
    assert calls["n"] == 1
