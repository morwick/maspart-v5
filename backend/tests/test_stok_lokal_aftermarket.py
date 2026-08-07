"""Barang STOK GUDANG di luar katalog Sinotruk (aftermarket, indeks Accurate)
harus terjangkau — kasus nyata log prod 2026-07-08: 'ic regulator', 'spring
assembly di stok', F00M425212 dijawab 'tidak ada' padahal barang sejenis DIJUAL
(Alternator Regulator 2915YNZ-3000-W, dst; hanya ada di indeks Accurate).

- accurate.search_index : cari nama/PN di indeks bersama — non-blocking, tanpa tarikan.
- cari_part             : 'stok_lokal_tambahan' terisi + MISS kamus TIDAK dicatat.
- detail_part           : PN Accurate-only → found=True bersumber stok gudang.
- /search-name (web)    : fallback ekspansi sinonim (kasus miss 'spion'→mirror).

Semua tanpa jaringan: indeks Accurate & part_index di-monkeypatch.
"""
from __future__ import annotations

import pytest

from app.routers import parts as parts_router
from app.services import accurate, part_index, search_log, sims
from app.services import ai_assistant as ai

U = {"username": "mas", "role": "admin"}

REGULATOR = {"no": "004001.2915YNZ-3000-W", "pn": "2915YNZ-3000-W",
             "name": "Alternator Regulator", "available_to_sell": 4.0,
             "quantity": 4.0, "unit": "Pc", "item_type": "Persediaan",
             "price": 350000.0, "accurate_id": 11}
SPION = {"no": "004002.8202015-E02", "pn": "8202015-E02",
         "name": "Kaca Spion LH", "available_to_sell": 2.0,
         "quantity": 2.0, "unit": "Pc", "item_type": "Persediaan",
         "price": 0.0, "accurate_id": 12}
CUCUK = {"no": "004003.2902010-Q367-X1L", "pn": "2902010-Q367-X1L",
         "name": "Cucuk Per Depan Faw", "available_to_sell": 8.0,
         "quantity": 8.0, "unit": "Pc", "item_type": "Persediaan",
         "price": 125000.0, "accurate_id": 13}
NOISE = {"no": "004004.XYZ", "pn": "XYZ", "name": "Oli Mesin 10L",
         "available_to_sell": 30.0, "quantity": 30.0, "unit": "Liter",
         "item_type": "Persediaan", "price": 500000.0, "accurate_id": 14}


def _seed_index(monkeypatch, items, by_gudang=None):
    monkeypatch.setattr(accurate, "_index_cache", {
        "ts": 123.0, "items": list(items),
        "by_pn": {accurate.norm_pn(i["pn"]): i for i in items},
        # per-gudang HANYA dari indeks (enrichment 5-jam) — tak ada panggilan live.
        "by_gudang": by_gudang or {}, "snap": {},
    })


# ── accurate.search_index ────────────────────────────────────────────────────

def test_search_index_frasa_dan_nonblocking(monkeypatch):
    _seed_index(monkeypatch, [REGULATOR, SPION, CUCUK, NOISE])

    def _boom(**kw):
        raise AssertionError("search_index tak boleh memicu tarikan Accurate")

    monkeypatch.setattr(accurate, "fetch_all_items", _boom)
    hits = accurate.search_index(["alternator regulator"])
    assert [h["pn"] for h in hits] == ["2915YNZ-3000-W"]


def test_search_index_kata_tunggal_generik_tak_banjir(monkeypatch):
    _seed_index(monkeypatch, [REGULATOR, SPION, CUCUK, NOISE])
    # 1 kata cocok saja (skor lemah) → dibuang; frasa 'cucuk per' → tetap ketemu.
    assert accurate.search_index(["per"]) == []
    hits = accurate.search_index(["cucuk per"])
    assert [h["pn"] for h in hits] == ["2902010-Q367-X1L"]


def test_search_index_kosong_saat_cold_start(monkeypatch):
    _seed_index(monkeypatch, [])
    assert accurate.search_index(["regulator"]) == []


# ── cari_part: stok_lokal_tambahan ───────────────────────────────────────────

@pytest.fixture
def mock_catalog_kosong(monkeypatch):
    monkeypatch.setattr(part_index, "search_part_name", lambda t: [])
    monkeypatch.setattr(part_index, "search_part_number", lambda t: [])
    monkeypatch.setattr(part_index, "correct_typos", lambda t: (t, []))
    monkeypatch.setattr(part_index, "suggest_names", lambda q, limit=6: [])
    monkeypatch.setattr(part_index, "suggest_pns", lambda q: [])
    monkeypatch.setattr(part_index, "pn_tokens", lambda q: [])
    monkeypatch.setattr(sims, "available", lambda: False)


def test_cari_part_menemukan_barang_stok_lokal(mock_catalog_kosong, monkeypatch):
    _seed_index(monkeypatch, [REGULATOR, SPION, CUCUK, NOISE])
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    dicatat = []
    monkeypatch.setattr(search_log, "record_miss", lambda *a, **k: dicatat.append(a))

    res = ai._t_cari_part({"query": "alternator regulator"}, U)
    rows = res["stok_lokal_tambahan"]
    assert rows and rows[0]["part_number"] == "2915YNZ-3000-W"
    assert rows[0]["stok_total"] == "4 Pc"
    assert rows[0]["harga_lokal"] == "Rp 350.000"
    assert "stok_lokal_tambahan" in (res["catatan"] or "")
    # Barang DITEMUKAN di stok lokal → bukan celah kamus, MISS tak dicatat.
    assert dicatat == []


def test_cari_part_stok_lokal_dedup_terhadap_katalog(mock_catalog_kosong, monkeypatch):
    _seed_index(monkeypatch, [REGULATOR])
    # Katalog SUDAH menemukan PN yang sama → jangan dobel di stok_lokal_tambahan.
    row = {"part_number": "2915YNZ-3000-W", "part_name": "REGULATOR",
           "file": "NX400", "path": "Sinotruk/NX400"}
    monkeypatch.setattr(part_index, "search_part_name",
                        lambda t: [row] if "regulator" in (t or "").lower() else [])
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    res = ai._t_cari_part({"query": "alternator regulator"}, U)
    assert {it["part_number"] for it in res["hasil"]} == {"2915YNZ-3000-W"}
    assert res["stok_lokal_tambahan"] == []


def test_cari_part_miss_tetap_dicatat_bila_stok_lokal_nihil(mock_catalog_kosong, monkeypatch):
    _seed_index(monkeypatch, [NOISE])
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    # SIMS HIDUP = syarat "benar-benar nihil di mana-mana". Sejak audit telemetri
    # 2026-08-07, nihil saat SIMS MATI tak lagi dicatat sbg celah kamus (jaring
    # terakhirnya memang tak pernah ditarik) — lihat test_klaim_jujur_stok_excel.
    monkeypatch.setattr(sims, "available", lambda: True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})
    dicatat = []
    monkeypatch.setattr(search_log, "record_miss", lambda *a, **k: dicatat.append(a))
    ai._t_cari_part({"query": "flux capacitor"}, U)
    assert dicatat  # benar-benar nihil di mana-mana → tetap masuk Pencarian Nihil


# ── detail_part: fallback PN Accurate-only ───────────────────────────────────

# Rincian per-gudang datang dari INDEKS (enrichment 5-jam), BUKAN panggilan live.
_REG_GUDANG = {accurate.norm_pn(REGULATOR["pn"]): {"01.Jakarta": 4}}


def test_detail_part_fallback_stok_lokal(monkeypatch):
    monkeypatch.setattr(part_index, "search_part_number", lambda t: [])
    _seed_index(monkeypatch, [REGULATOR], by_gudang=_REG_GUDANG)
    monkeypatch.setattr(accurate, "available", lambda: True)
    # ⛔ stock_full TAK BOLEH login live: bila _warehouse_retry terpanggil → gagal test.
    monkeypatch.setattr(accurate, "_warehouse_retry",
                        lambda item_id: (_ for _ in ()).throw(AssertionError("dilarang login live")))

    r = ai._t_detail_part({"part_number": "2915YNZ-3000-W"}, U)
    assert r["found"] and r["part_name"] == "Alternator Regulator"
    assert r["stok_total"] == "4 Pc"
    assert r["stok_per_gudang"] == {"01.Jakarta": 4}   # dari indeks
    assert r["harga_lokal"] == "Rp 350.000"
    assert "TIDAK ada di katalog Sinotruk" in r["sumber"]


def test_detail_part_fallback_pembeli_tanpa_rincian_gudang(monkeypatch):
    monkeypatch.setattr(part_index, "search_part_number", lambda t: [])
    _seed_index(monkeypatch, [REGULATOR], by_gudang=_REG_GUDANG)
    monkeypatch.setattr(accurate, "available", lambda: True)

    r = ai._t_detail_part({"part_number": "2915YNZ-3000-W"},
                          {"username": "b", "role": "pembeli"})
    assert r["found"]
    assert not r.get("stok_per_gudang")  # pembeli: tanpa breakdown antar-cabang


def test_stock_full_tak_pernah_login_live(monkeypatch):
    """Aturan keras: baca stok = HANYA cache indeks, tak pernah panggilan live."""
    _seed_index(monkeypatch, [REGULATOR], by_gudang={})   # per-gudang belum ter-enrich
    monkeypatch.setattr(accurate, "_warehouse_retry",
                        lambda item_id: (_ for _ in ()).throw(AssertionError("dilarang login live")))
    hit = accurate.stock_full("2915YNZ-3000-W")
    assert hit is not None
    assert hit["per_gudang"] == []        # belum ter-enrich → kosong, TANPA login


def test_detail_part_tetap_not_found_tanpa_accurate(monkeypatch):
    monkeypatch.setattr(part_index, "search_part_number", lambda t: [])
    monkeypatch.setattr(accurate, "available", lambda: False)
    r = ai._t_detail_part({"part_number": "TIDAKADA123"}, U)
    assert r["found"] is False


# ── /search-name web: fallback ekspansi sinonim ──────────────────────────────

def test_search_name_web_pakai_sinonim(monkeypatch):
    mirror_row = {"file": "NX360 6X4", "path": "Sinotruk/NX360 6X4", "sheet": "",
                  "part_number": "WG1642770101", "part_name": "Left Rear View Mirror",
                  "quantity": "1", "stok": "2", "harga": "—", "gudang": {},
                  "excel_row": 5, "source": "excel"}

    def sp_name(t):
        return [dict(mirror_row)] if "mirror" in (t or "").lower() else []

    monkeypatch.setattr(part_index, "search_part_name", sp_name)
    monkeypatch.setattr(part_index, "gudang_names", lambda: [])
    monkeypatch.setattr(ai, "_expand_query",
                        lambda q: (["spion", "mirror", "rear view mirror"], ["spion"]))
    dicatat = []
    monkeypatch.setattr(search_log, "record_miss", lambda *a, **k: dicatat.append(a))

    res = parts_router.search_name(q="spion", page=1, page_size=20, user=dict(U))
    assert res.count >= 1
    assert res.results[0].part_number == "WG1642770101"
    assert dicatat == []  # ketemu via kamus → bukan miss


def test_search_name_web_tetap_miss_bila_kamus_tak_kenal(monkeypatch):
    monkeypatch.setattr(part_index, "search_part_name", lambda t: [])
    monkeypatch.setattr(part_index, "suggest_names", lambda q, limit=6: [])
    monkeypatch.setattr(part_index, "gudang_names", lambda: [])
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    dicatat = []
    monkeypatch.setattr(search_log, "record_miss", lambda *a, **k: dicatat.append(a))

    res = parts_router.search_name(q="flux capacitor", page=1, page_size=20, user=dict(U))
    assert res.count == 0
    assert dicatat  # tetap tercatat di Pencarian Nihil
