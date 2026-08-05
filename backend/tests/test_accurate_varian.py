"""Keluarga VARIAN PEMASOK + sapuan katalog anti-bocor (Accurate).

Dua bug produksi yang diuji di sini, dua-duanya terverifikasi LIVE 2026-08-05:

(A) INDEKS BOCOR — server melapor rowCount=5229 tapi fetch_all_items hanya
    memungut 5200 (persis 26×200). Barang yang BERTRANSAKSI selama sapuan
    berpindah posisi di paging offset → terlewat; dan satu halaman yang balas
    kosong sesaat memotong seluruh ekor katalog. Yang hilang justru yang paling
    laku (PN 1000442956 Fuel Filter 482 pc dan varian '/SN' 1.069 pc), sementara
    kartu mati ('/SH', 2 pc) tertangkap → halaman part & asisten menjawab
    "stok —" untuk part terlaris.

(B) index_key SATU ARAH — pemaafan suffix hanya query-bersuffix → PN dasar.
    Query PN dasar '1000442956' tak menemukan kunci '1000442956SN'/'…SH' yang
    ADA di indeks, padahal by_gudang (dari report) memuat ketiganya → tampilan
    campur: total '—' tapi tabel per-gudang terisi.

Semua offline: _index_cache disuntik & _search_retry/_ensure_session dipagari
supaya tak ada satu pun panggilan ke Accurate.
"""
from __future__ import annotations

import pytest

from app.services import accurate as ac


# ── Data nyata (dibaca dari Accurate 2026-08-05) ────────────────────────────
def _ent(no: str, pn: str, qty: float, price: float, id_: int) -> dict:
    return {"no": no, "pn": pn, "name": "Fuel Filter", "available_to_sell": float(qty),
            "quantity": float(qty), "unit": "Pc", "item_type": "Persediaan",
            "price": float(price), "accurate_id": id_}


# PN 1000442956 dipecah jadi 3 kartu barang per PEMASOK — part SAMA, harga beda.
_KELUARGA = {
    "1000442956": _ent("000969.1000442956", "1000442956", 482, 300_000, 4069),
    "1000442956SN": _ent("000951.1000442956/SN", "1000442956/SN", 1069, 285_000, 4051),
    "1000442956SH": _ent("000993.1000442956/SH", "1000442956/SH", 2, 455_000, 4093),
}
_GUDANG = {
    "1000442956": {"01.Jakarta": 400.0, "02.Pekanbaru": 82.0},
    "1000442956SN": {"01.Jakarta": 1069.0},
    "1000442956SH": {"04.Palembang": 2.0},
}
_TUNGGAL = {"WG9525160004": _ent("000123.WG9525160004", "WG9525160004", 11, 5_250_000, 777)}


@pytest.fixture
def isi_indeks(monkeypatch):
    """Suntik _index_cache sintetis (dipulihkan otomatis oleh monkeypatch —
    jangan sampai bocor ke test lain) + pagar anti-jaringan."""
    def _no_live(*a, **k):
        raise AssertionError("baca indeks DILARANG menembak Accurate live")

    monkeypatch.setattr(ac, "_search_retry", _no_live)
    monkeypatch.setattr(ac, "_ensure_session", _no_live)

    def _isi(by_pn: dict, by_gudang: dict | None = None) -> dict:
        monkeypatch.setattr(ac, "_index_cache", {
            "ts": 123.0, "gudang_ts": 123.0,
            "items": list(by_pn.values()),
            "by_pn": dict(by_pn),
            "by_gudang": dict(by_gudang or {}),
            "by_base": ac._build_by_base(by_pn),
            "snap": {},
        })
        return ac._index_cache

    return _isi


# ── 1. _pn_base_key: potong di '/' atau '+' pertama ─────────────────────────
def test_pn_base_key():
    assert ac._pn_base_key("1000442956/SN") == "1000442956"
    assert ac._pn_base_key("1000442956/SH") == "1000442956"
    assert ac._pn_base_key("X+2") == "X"
    assert ac._pn_base_key("WG9525160004") == "WG9525160004"   # tanpa suffix → dirinya
    assert ac._pn_base_key("") == ""


# ── 2. _build_by_base: peta keluarga, urut stok desc ────────────────────────
def test_build_by_base_keluarga_urut_stok():
    bb = ac._build_by_base(_KELUARGA)
    # 1.069 (/SN) → 482 (base) → 2 (/SH): kartu berstok terbanyak dulu.
    assert bb["1000442956"] == ["1000442956SN", "1000442956", "1000442956SH"]


def test_build_by_base_keluarga_tunggal_tetap_masuk():
    bb = ac._build_by_base(_TUNGGAL)
    assert bb == {"WG9525160004": ["WG9525160004"]}


# ── 3. index_key: dua arah ──────────────────────────────────────────────────
def test_index_key_cocok_persis_menang(isi_indeks):
    isi_indeks(_KELUARGA)
    assert ac.index_key("1000442956/SN") == "1000442956SN"
    assert ac.index_key("1000442956") == "1000442956"


def test_index_key_bersuffix_ke_dasar_regresi(isi_indeks):
    """Perilaku LAMA wajib utuh: PN EPC ber-suffix varian → kunci PN dasar."""
    isi_indeks(_TUNGGAL)
    assert ac.index_key("WG9525160004/2") == "WG9525160004"
    assert ac.index_key("ZZ0000000000/9") == ""                # asing tetap miss


def test_index_key_dasar_ke_varian_tunggal(isi_indeks):
    """Arah baru: query PN DASAR sementara indeks hanya punya kartu bersuffix."""
    isi_indeks({"1000442956SN": _KELUARGA["1000442956SN"]})
    assert ac.index_key("1000442956") == "1000442956SN"


def test_index_key_dasar_ambigu_tak_menebak(isi_indeks):
    """≥2 kartu pemasok → harga/stok mana yang benar TAK BOLEH ditebak."""
    isi_indeks({k: _KELUARGA[k] for k in ("1000442956SN", "1000442956SH")})
    assert ac.index_key("1000442956") == ""                    # stock_family yang menyajikan


# ── 4. stock_family: sajikan SEMUA kartu apa adanya ─────────────────────────
def test_stock_family_lengkap(isi_indeks):
    isi_indeks(_KELUARGA, _GUDANG)
    fam = ac.stock_family("1000442956")
    assert fam["found"] is True and fam["base"] == "1000442956"
    assert [v["pn"] for v in fam["varian"]] == ["1000442956/SN", "1000442956", "1000442956/SH"]
    assert fam["total_available"] == 1553.0                    # 1069 + 482 + 2
    assert (fam["harga_min"], fam["harga_max"]) == (285_000, 455_000)
    per = fam["varian"][1]["per_gudang"]                       # kartu base
    assert [g["gudang"] for g in per] == ["01.Jakarta", "02.Pekanbaru"]   # qty desc
    assert fam["varian"][0]["per_gudang"] == [{"gudang": "01.Jakarta", "qty": 1069.0}]


def test_stock_family_query_lewat_salah_satu_varian(isi_indeks):
    isi_indeks(_KELUARGA, _GUDANG)
    lewat_suffix = ac.stock_family("1000442956/SH")
    lewat_kunci = ac.stock_family("1000442956SN")   # kunci ternormalisasi (tanpa '/')
    utuh = ac.stock_family("1000442956")
    assert lewat_suffix == utuh and lewat_kunci == utuh


def test_stock_family_indeks_kosong(isi_indeks):
    isi_indeks({})
    assert ac.stock_family("1000442956")["found"] is False


def test_stock_family_pn_asing(isi_indeks):
    isi_indeks(_KELUARGA)
    assert ac.stock_family("ZZ9999999999")["found"] is False


# ── 5. stock_full: bentuk lama utuh + info keluarga bila ada ────────────────
def test_stock_full_menambahkan_varian_lain(isi_indeks):
    isi_indeks(_KELUARGA, _GUDANG)
    sf = ac.stock_full("1000442956")
    assert sf["available_to_sell"] == 482.0                    # kartu yang diminta
    assert sf["per_gudang"][0] == {"gudang": "01.Jakarta", "qty": 400.0}
    lain = {v["pn"]: v["available_to_sell"] for v in sf["varian_lain"]}
    assert lain == {"1000442956/SN": 1069.0, "1000442956/SH": 2.0}
    assert sf["stok_semua_varian"] == 1553.0


def test_stock_full_pn_tunggal_bentuk_lama_utuh(isi_indeks):
    """Part tanpa keluarga TIDAK boleh berubah bentuk (pemakai lama tak kaget)."""
    isi_indeks(_TUNGGAL, {"WG9525160004": {"02.Pekanbaru": 11.0}})
    sf = ac.stock_full("WG9525160004")
    assert "varian_lain" not in sf and "stok_semua_varian" not in sf
    assert set(sf) == set(_TUNGGAL["WG9525160004"]) | {"per_gudang"}


# ── 6. fetch_all_items: sapuan berulang + dedup (bug INDEKS BOCOR) ──────────
def _fake_pages(peta_per_sweep: list[dict[int, list[int]]], row_count: int):
    """Fake _search_retry stateful: tiap kali start=0 dianggap sapuan baru."""
    state = {"sweep": -1, "calls": []}

    def _fake(*, keywords: str = "", start: int = 0, limit: int = 0):
        state["calls"].append(start)
        if start == 0:
            state["sweep"] += 1
        peta = peta_per_sweep[min(state["sweep"], len(peta_per_sweep) - 1)]
        ids = peta.get(start)
        if callable(ids):
            ids = ids()
        return {"sp": {"rowCount": row_count},
                "d": [{"id": i, "no": f"00000{i}.PN{i}"} for i in (ids or [])]}

    return _fake, state


def test_fetch_all_items_sapuan_kedua_menambal_yang_bergeser(monkeypatch):
    """Sapuan-1 melewatkan 2 barang karena posisinya bergeser (mereka bertransaksi
    saat sapuan berjalan) → sapuan-2 menambal, hasil di-DEDUP by id."""
    monkeypatch.setattr(ac, "_PAGE_SIZE", 4)
    fake, state = _fake_pages([
        # sapuan-1: id 5 & 6 terlewat, id 1 & 2 malah terbaca dua kali
        {0: [1, 2, 3, 4], 4: [7, 8, 9, 1], 8: [2]},
        # sapuan-2: katalog tenang → lengkap
        {0: [1, 2, 3, 4], 4: [5, 6, 7, 8], 8: [9]},
    ], row_count=9)
    monkeypatch.setattr(ac, "_search_retry", fake)

    items = ac.fetch_all_items()
    assert sorted(i["id"] for i in items) == [1, 2, 3, 4, 5, 6, 7, 8, 9]   # unik & genap
    assert state["calls"].count(0) == 2                        # berhenti begitu genap


def test_fetch_all_items_halaman_kosong_di_tengah_tak_memotong_ekor(monkeypatch):
    """⛔ `if not batch: break` yang lama membuang SELURUH ekor katalog hanya
    karena satu halaman balas kosong sesaat. Kini: retry sekali lalu lanjut."""
    monkeypatch.setattr(ac, "_PAGE_SIZE", 4)
    kosong_sekali = {"n": 0}

    def _tengah():
        kosong_sekali["n"] += 1
        return [] if kosong_sekali["n"] == 1 else [5, 6, 7, 8]

    fake, state = _fake_pages([{0: [1, 2, 3, 4], 4: _tengah, 8: [9]}], row_count=9)
    monkeypatch.setattr(ac, "_search_retry", fake)

    items = ac.fetch_all_items()
    assert sorted(i["id"] for i in items) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert kosong_sekali["n"] == 2                             # retry sekali, lalu isi


def test_fetch_all_items_kosong_permanen_ekor_tetap_terbaca(monkeypatch):
    """Halaman tengah kosong terus (2× coba) → offset itu dilewati, TAPI ekor
    (offset sesudahnya) tetap ditarik."""
    monkeypatch.setattr(ac, "_PAGE_SIZE", 4)
    fake, state = _fake_pages([{0: [1, 2, 3, 4], 4: [], 8: [9]}], row_count=9)
    monkeypatch.setattr(ac, "_search_retry", fake)

    ids = sorted(i["id"] for i in ac.fetch_all_items())
    assert 9 in ids                                            # ⛔ ekor tak dipotong
    assert ids == [1, 2, 3, 4, 9]
    assert state["calls"].count(0) == 2                        # sapuan ulang tak menambah → sudahi


def test_fetch_all_items_maksimal_3_sapuan(monkeypatch):
    """Guard: katalog yang tak pernah genap tak boleh membuat sapuan tanpa batas."""
    monkeypatch.setattr(ac, "_PAGE_SIZE", 100)                 # 1 halaman/sapuan
    n = {"i": 0}

    def _page0():
        n["i"] += 1
        return [1, 2, 3, 100 + n["i"]]                         # +1 barang baru tiap sapuan

    fake, state = _fake_pages([{0: _page0}], row_count=9)      # tak pernah mencapai 9
    monkeypatch.setattr(ac, "_search_retry", fake)

    items = ac.fetch_all_items()
    assert state["calls"].count(0) == ac._MAX_SWEEPS == 3
    assert len(items) == 6                                     # 3 tetap + 3 tambahan


# ── 7. _reconcile_report_keys: tambal barang yang lolos sapuan ──────────────
def _hit(pn: str, qty: float = 5.0, price: float = 1000.0, id_: int = 1) -> dict:
    return ac.normalize_item({"no": f"000001.{pn}", "name": "Tambalan",
                              "availableToSell": qty, "quantity": qty,
                              "unit1": {"name": "Pc"}, "unitPrice": price, "id": id_})


def test_reconcile_menambal_kunci_report_yang_hilang(isi_indeks, monkeypatch):
    """Report per-gudang = satu query server-side (tak ikut bocor spt paging) →
    kunci yang ada di sana tapi absen di by_pn = bukti barang hilang dari indeks."""
    isi_indeks({"BBB": _ent("000002.BBB", "BBB", 3, 500, 2)})
    monkeypatch.setattr(ac, "search_by_keyword", lambda k, limit=50: [_hit("AAA")] if k == "AAA" else [])

    n = ac._reconcile_report_keys({"AAA": {"01.Jakarta": 5.0}, "BBB": {"01.Jakarta": 3.0}})
    assert n == 1
    assert ac._index_cache["by_pn"]["AAA"]["available_to_sell"] == 5.0
    assert ac._index_cache["snap"]["AAA"] == {"stok": 5.0, "harga": 1000.0, "unit": "Pc"}
    assert ac._index_cache["by_base"]["AAA"] == ["AAA"]        # keluarga ikut dibangun ulang
    assert any(i["pn"] == "AAA" for i in ac._index_cache["items"])


def test_reconcile_kata_kunci_cadangan_tanpa_ekor_huruf(isi_indeks, monkeypatch):
    """Kunci indeks sudah tanpa '/' ('1000442956SN') sedangkan kode barang di
    Accurate masih '000951.1000442956/SN' → cari apa adanya bisa 0 hasil; kata
    kunci cadangan '1000442956' menemukan seluruh keluarga, lalu disaring PERSIS."""
    isi_indeks({"BBB": _ent("000002.BBB", "BBB", 3, 500, 2)})
    dicari: list[str] = []

    def _cari(k, limit=50):
        dicari.append(k)
        if k != "1000442956":
            return []                    # kunci ber-ekor huruf tak dikenal server
        return [ac.normalize_item({"no": "000951.1000442956/SN", "name": "Fuel Filter",
                                   "availableToSell": 1069, "unit1": {"name": "Pc"},
                                   "unitPrice": 285000, "id": 4051}),
                ac.normalize_item({"no": "000993.1000442956/SH", "name": "Fuel Filter",
                                   "availableToSell": 2, "unit1": {"name": "Pc"},
                                   "unitPrice": 455000, "id": 4093})]

    monkeypatch.setattr(ac, "search_by_keyword", _cari)
    assert ac._reconcile_report_keys({"1000442956SN": {"01.Jakarta": 1069.0}}) == 1
    assert dicari == ["1000442956SN", "1000442956"]            # tepat dulu, baru cadangan
    assert ac._index_cache["by_pn"]["1000442956SN"]["pn"] == "1000442956/SN"   # bukan '/SH'


def test_reconcile_kandidat_nihil_dilewati(isi_indeks, monkeypatch):
    isi_indeks({"BBB": _ent("000002.BBB", "BBB", 3, 500, 2)})
    monkeypatch.setattr(ac, "search_by_keyword", lambda k, limit=50: [])      # 0 hasil
    assert ac._reconcile_report_keys({"ZZZ": {"01.Jakarta": 1.0}}) == 0
    assert "ZZZ" not in ac._index_cache["by_pn"]


def test_reconcile_hasil_tak_persis_tak_dipakai(isi_indeks, monkeypatch):
    """search keyword bisa membalas barang MIRIP — hanya yang PN-nya persis
    sama dengan kunci report yang boleh masuk indeks."""
    isi_indeks({"BBB": _ent("000002.BBB", "BBB", 3, 500, 2)})
    monkeypatch.setattr(ac, "search_by_keyword", lambda k, limit=50: [_hit("AAA-LAIN")])
    assert ac._reconcile_report_keys({"AAA": {"01.Jakarta": 1.0}}) == 0


def test_reconcile_diam_saat_indeks_belum_dibangun(isi_indeks, monkeypatch):
    """by_pn kosong = indeks memang belum ada; menambal satu-satu bukan obatnya
    (bisa ribuan panggilan) → jangan sentuh jaringan sama sekali."""
    isi_indeks({})
    monkeypatch.setattr(ac, "search_by_keyword",
                        lambda k, limit=50: (_ for _ in ()).throw(AssertionError("dilarang")))
    assert ac._reconcile_report_keys({"AAA": {"01.Jakarta": 1.0}}) == 0


def test_reconcile_gagal_jaringan_tak_menjatuhkan(isi_indeks, monkeypatch):
    isi_indeks({"BBB": _ent("000002.BBB", "BBB", 3, 500, 2)})

    def _boom(k, limit=50):
        raise ac.AccurateError("sesi mati")

    monkeypatch.setattr(ac, "search_by_keyword", _boom)
    assert ac._reconcile_report_keys({"A1": {"g": 1.0}, "A2": {"g": 1.0}}) == 0


def test_reconcile_dipanggil_dari_enrichment_report(isi_indeks, monkeypatch):
    """Kabel: rekonsiliasi HANYA jalan di jalur enrichment terjadwal — dan
    hasilnya masuk indeks sebelum _save_index dipanggil scheduler."""
    isi_indeks({"BBB": _ent("000002.BBB", "BBB", 3, 500, 2)})
    monkeypatch.setattr(ac, "_ensure_session", lambda: {"jsessionid": "J", "dsi": "D"})
    monkeypatch.setattr(ac, "_stock_report_xls", lambda s: b"PK")
    monkeypatch.setattr(ac, "_parse_stock_report",
                        lambda x: {"AAA": {"01.Jakarta": 5.0}, "BBB": {"01.Jakarta": 3.0}})
    monkeypatch.setattr(ac, "search_by_keyword", lambda k, limit=50: [_hit("AAA")] if k == "AAA" else [])

    assert ac.enrich_warehouses_via_report() == 2
    assert "AAA" in ac._index_cache["by_pn"]                   # ikut tertambal
    assert ac.gudang_breakdown("AAA") == {"01.Jakarta": 5.0}
