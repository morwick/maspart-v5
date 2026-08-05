"""Keluarga VARIAN PEMASOK — FASE 2: baris pencarian, endpoint /parts/varian,
dan jalur asisten (detail_part, cek_massal_part, buat_penawaran).

FAKTA LIVE 2026-08-05 yang dijaga di sini: satu part fisik dipecah jadi BEBERAPA
kartu barang Accurate per PEMASOK (PN 1000442956 — base 482 pc @Rp 300rb, '/SN'
1.069 pc @Rp 285rb, '/SH' 2 pc @Rp 455rb). Sebelum fase ini web & asisten
menjawab dari SATU kartu saja (kerap kartu mati 2 pc) atau '—'.

Aturan pemilik yang di-test, bukan sekadar bentuk data:
  • stok yang ditampilkan = TOTAL sekeluarga;
  • harga TIDAK dirata-rata/digabung — beda harga = RENTANG (label), dan angka
    mentah Excel (harga_num) sengaja None supaya tak ada angka menyesatkan;
  • penawaran tak pernah memilih varian diam-diam — PN ambigu = BATAL + tanya;
  • pembeli tak boleh melihat sebaran antar-gudang (diganti stok wilayahnya).

Semua offline: _index_cache disuntik & _search_retry/_ensure_session dipagari,
jadi tak ada satu pun panggilan ke Accurate.
"""
from __future__ import annotations

import pytest

from app.routers import parts as parts_router
from app.services import accurate as ac
from app.services import ai_assistant as ai
from app.services import gudang, part_index, permissions, reservations, sims

ADMIN = {"username": "admin", "role": "admin"}
WAWAN = {"username": "wawan", "role": "user"}        # col_stok saja (tanpa harga)
BUDI = {"username": "budi", "role": "user"}          # col_harga saja (tanpa stok)
AGUS = {"username": "agustiono", "role": "user"}     # dua-duanya
PEMBELI = {"username": "roni", "role": "pembeli"}


# ── Data nyata (dibaca dari Accurate 2026-08-05) ────────────────────────────
def _ent(no: str, pn: str, qty: float, price: float, id_: int) -> dict:
    return {"no": no, "pn": pn, "name": "Fuel Filter", "available_to_sell": float(qty),
            "quantity": float(qty), "unit": "Pc", "item_type": "Persediaan",
            "price": float(price), "accurate_id": id_}


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
# Keluarga berharga SAMA — batas penting: rentang hanya muncul bila harga BEDA.
_SEHARGA = {
    "AA1000": _ent("000001.AA1000", "AA1000", 4, 90_000, 1),
    "AA1000SN": _ent("000002.AA1000/SN", "AA1000/SN", 6, 90_000, 2),
}


@pytest.fixture
def isi_indeks(monkeypatch):
    """Suntik _index_cache sintetis (+ view `snap` seperti refresh() membangunnya)
    dan pagari jalur live — pola sama dengan tests/test_accurate_varian.py."""
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
            "snap": {k: {"stok": v["available_to_sell"], "harga": v["price"],
                         "unit": v["unit"]} for k, v in by_pn.items()},
        })
        return ac._index_cache

    return _isi


@pytest.fixture
def izin(monkeypatch):
    """Menu Control: agustiono lengkap, wawan tanpa harga, budi tanpa stok."""
    peta = {"agustiono": ["col_stok", "col_harga"],
            "wawan": ["col_stok"], "budi": ["col_harga"]}
    monkeypatch.setattr(permissions, "effective",
                        lambda kind, u, r: list(peta.get(u, [])))


@pytest.fixture
def acc_siap(monkeypatch):
    monkeypatch.setattr(ac, "available", lambda: True)


# ── 1. accurate.family_summary (ringkasan murah) ────────────────────────────
def test_family_summary_keluarga(isi_indeks):
    isi_indeks(_KELUARGA, _GUDANG)
    f = ac.family_summary("1000442956")
    assert f["n"] == 3 and f["stok_total"] == 1553.0
    assert (f["harga_min"], f["harga_max"]) == (285_000, 455_000)
    assert f["kunci"][0] == "1000442956SN"        # urut stok desc (dari by_base)


def test_family_summary_lewat_kunci_atau_suffix_sama_saja(isi_indeks):
    isi_indeks(_KELUARGA, _GUDANG)
    assert ac.family_summary("1000442956/SH") == ac.family_summary("1000442956")
    assert ac.family_summary("1000442956SN") == ac.family_summary("1000442956")


def test_family_summary_none_bila_sendirian_atau_asing(isi_indeks):
    """None (bukan dict kosong) = pemanggil pakai jalur LAMA apa adanya."""
    isi_indeks(_TUNGGAL)
    assert ac.family_summary("WG9525160004") is None
    assert ac.family_summary("ZZ9999999999") is None


def test_family_summary_tak_menyusun_per_gudang(isi_indeks, monkeypatch):
    """Ia dipanggil ratusan kali per request → jangan sampai diam-diam memanggil
    stock_family (yang membangun daftar per-gudang)."""
    isi_indeks(_KELUARGA, _GUDANG)
    monkeypatch.setattr(ac, "stock_family",
                        lambda pn: (_ for _ in ()).throw(AssertionError("terlalu mahal")))
    assert ac.family_summary("1000442956")["n"] == 3


# ── 2. Baris pencarian: total keluarga + harga rentang ──────────────────────
def test_acc_fields_keluarga_total_dan_rentang(isi_indeks):
    isi_indeks(_KELUARGA, _GUDANG)
    f = part_index._acc_fields(ac.snapshot(), "1000442956")
    assert f["stok"] == "1.553" and f["stok_num"] == 1553      # bukan 2 pc kartu mati
    assert f["harga"] == "Rp 285.000 – 455.000"
    # ⛔ harga_num None: sel Excel tak boleh berisi angka yang seolah harga resmi
    # satu barang (_compact_result juga membuang None → tak makan token).
    assert f["harga_num"] is None
    assert f["varian_pemasok"] == 3


def test_acc_fields_keluarga_harga_sama_tetap_angka(isi_indeks):
    isi_indeks(_SEHARGA)
    f = part_index._acc_fields(ac.snapshot(), "AA1000")
    assert f["stok"] == "10" and f["harga"] == "Rp 90.000"     # tak ada rentang palsu
    assert f["harga_num"] == 90_000 and f["varian_pemasok"] == 2


def test_acc_fields_pn_tunggal_persis_perilaku_lama(isi_indeks):
    """Part tanpa keluarga: 4 field, tanpa key baru — ratusan pemanggil lama."""
    isi_indeks(_TUNGGAL)
    f = part_index._acc_fields(ac.snapshot(), "WG9525160004")
    assert f == {"stok": "11", "harga": "Rp 5.250.000",
                 "stok_num": 11, "harga_num": 5_250_000}


def test_acc_fields_pn_asing_tetap_strip(isi_indeks):
    isi_indeks(_TUNGGAL)
    assert part_index._acc_stok_harga(ac.snapshot(), "ZZ9999") == ("—", "—", None, None)


def test_acc_stok_harga_indeks_kosong_tak_berubah():
    """Tanpa indeks (by_base kosong) jalur keluarga harus benar-benar diam."""
    assert part_index._acc_stok_harga({}, "1000442956") == ("—", "—", None, None)


# ── 3. Router: _overlay_accurate (hasil pencarian web) ──────────────────────
def test_overlay_accurate_baris_keluarga(isi_indeks, acc_siap):
    isi_indeks(_KELUARGA, _GUDANG)
    r = parts_router._overlay_accurate(
        [{"part_number": "1000442956", "stok": "2", "harga": "Rp 999.999"}])[0]
    assert r["stok"] == "1.553"                        # total sekeluarga
    assert r["harga"] == "Rp 285.000 – 455.000"        # rentang, bukan rata-rata


def test_overlay_accurate_pn_tunggal_tak_berubah(isi_indeks, acc_siap):
    isi_indeks(_TUNGGAL)
    r = parts_router._overlay_accurate(
        [{"part_number": "WG9525160004", "stok": "0", "harga": "Rp 1"}])[0]
    assert r["stok"] == "11" and r["harga"] == "Rp 5.250.000"


def test_overlay_accurate_pn_asing_harga_excel_dibuang(isi_indeks, acc_siap):
    isi_indeks(_TUNGGAL)
    r = parts_router._overlay_accurate(
        [{"part_number": "ZZ9999", "stok": "3", "harga": "Rp 777"}])[0]
    assert r["harga"] == "—" and r["stok"] == "3"      # perilaku lama


# ── 4. Endpoint GET /api/parts/varian ──────────────────────────────────────
def test_varian_admin_lihat_semua_kartu(isi_indeks, acc_siap, izin):
    isi_indeks(_KELUARGA, _GUDANG)
    r = parts_router.parts_varian(pn="1000442956", user=ADMIN)
    assert r["configured"] is True and r["found"] is True and r["base"] == "1000442956"
    assert [v["kode"] for v in r["varian"]] == [
        "1000442956/SN", "1000442956", "1000442956/SH"]        # stok desc
    assert r["total_available"] == 1553.0
    assert (r["harga_min"], r["harga_max"]) == (285_000, 455_000)
    sn = r["varian"][0]
    assert sn["no"] == "000951.1000442956/SN" and sn["nama"] == "Fuel Filter"
    assert sn["stok"] == 1069.0 and sn["harga"] == 285_000 and sn["unit"] == "Pc"
    assert sn["per_gudang"] == [{"gudang": "01.Jakarta", "qty": 1069.0}]


def test_varian_staf_tanpa_col_harga(isi_indeks, acc_siap, izin):
    r = (isi_indeks(_KELUARGA, _GUDANG),
         parts_router.parts_varian(pn="1000442956", user=WAWAN))[1]
    assert "harga_min" not in r and "harga_max" not in r
    assert all("harga" not in v for v in r["varian"])
    assert r["varian"][0]["stok"] == 1069.0                    # stok tetap (col_stok ON)


def test_varian_staf_tanpa_col_stok(isi_indeks, acc_siap, izin):
    isi_indeks(_KELUARGA, _GUDANG)
    r = parts_router.parts_varian(pn="1000442956", user=BUDI)
    assert "total_available" not in r
    assert all("stok" not in v and "per_gudang" not in v for v in r["varian"])
    assert r["harga_min"] == 285_000                           # col_harga ON
    assert r["varian"][0]["kode"] == "1000442956/SN"           # identitas tetap


def test_varian_staf_lengkap_sama_dgn_admin(isi_indeks, acc_siap, izin):
    isi_indeks(_KELUARGA, _GUDANG)
    assert parts_router.parts_varian(pn="1000442956", user=AGUS) == \
        parts_router.parts_varian(pn="1000442956", user=ADMIN)


@pytest.fixture
def dunia_pembeli(monkeypatch):
    """Wilayah pembeli: gudang terpilih Jakarta + 69 pc kartu '/SN' direservasi."""
    monkeypatch.setattr(part_index, "gudang_names",
                        lambda: ["01.Jakarta", "02.Pekanbaru", "04.Palembang"])
    monkeypatch.setattr(parts_router, "get_user_gudang", lambda u: "jakarta")
    monkeypatch.setattr(gudang, "buyer_label", lambda k: "01.Jakarta")
    monkeypatch.setattr(gudang, "shippable", lambda bd: bd)
    monkeypatch.setattr(reservations, "reserved_map",
                        lambda: {("1000442956/SN", "01.Jakarta"): 69})


def test_varian_pembeli_tanpa_sebaran_gudang(isi_indeks, acc_siap, izin, dunia_pembeli):
    """Pembeli: harga & stok boleh, sebaran antar-cabang TIDAK — diganti satu
    angka wilayahnya, lewat aturan scoping yang sama dgn hasil pencarian."""
    isi_indeks(_KELUARGA, _GUDANG)
    r = parts_router.parts_varian(pn="1000442956", user=PEMBELI)
    assert all("per_gudang" not in v for v in r["varian"])
    wil = {v["kode"]: v["stok_wilayah"] for v in r["varian"]}
    assert wil["1000442956/SN"] == 1000.0      # 1069 − 69 direservasi orang lain
    assert wil["1000442956"] == 400.0          # Jakarta saja, bukan 482 (+Pekanbaru)
    assert wil["1000442956/SH"] == 2.0         # fallback gudang terdekat (Palembang)
    assert r["varian"][0]["harga"] == 285_000  # harga jual tetap boleh dilihat


def test_varian_pn_tunggal_tetap_found_satu_kartu(isi_indeks, acc_siap, izin):
    """Keputusan bentuk: keluarga beranggota SATU tetap found=True dgn 1 varian —
    frontend punya SATU jalur render, tak perlu cabang 'tak punya varian'."""
    isi_indeks(_TUNGGAL, {"WG9525160004": {"02.Pekanbaru": 11.0}})
    r = parts_router.parts_varian(pn="WG9525160004", user=ADMIN)
    assert r["found"] is True and len(r["varian"]) == 1
    assert r["varian"][0]["kode"] == "WG9525160004" and r["total_available"] == 11.0
    assert r["harga_min"] == r["harga_max"] == 5_250_000


def test_varian_pn_asing_dan_accurate_mati(isi_indeks, acc_siap, izin, monkeypatch):
    isi_indeks(_KELUARGA, _GUDANG)
    assert parts_router.parts_varian(pn="ZZ9999", user=ADMIN) == \
        {"configured": True, "found": False}
    monkeypatch.setattr(ac, "available", lambda: False)
    assert parts_router.parts_varian(pn="1000442956", user=ADMIN)["configured"] is False


# ── 5. /accurate-stock: kartu saudara ikut lewat (bentuk lama utuh) ─────────
def test_accurate_stock_bawa_varian_lain(isi_indeks, acc_siap, izin):
    isi_indeks(_KELUARGA, _GUDANG)
    s = parts_router.accurate_stock(pn="1000442956", user=ADMIN)["stock"]
    assert s["available_to_sell"] == 482.0 and s["stok_semua_varian"] == 1553.0
    assert {v["kode"]: v["harga"] for v in s["varian_lain"]} == {
        "1000442956/SN": 285_000, "1000442956/SH": 455_000}


def test_accurate_stock_varian_lain_kena_gerbang(isi_indeks, acc_siap, izin):
    """Key baru wajib terjangkau strip harga/stok — kalau tidak, angka bocor
    lewat pintu belakang ke staf yang centangnya dimatikan."""
    isi_indeks(_KELUARGA, _GUDANG)
    tanpa_harga = parts_router.accurate_stock(pn="1000442956", user=WAWAN)["stock"]
    assert all("harga" not in v for v in tanpa_harga["varian_lain"])
    assert tanpa_harga["stok_semua_varian"] == 1553.0
    tanpa_stok = parts_router.accurate_stock(pn="1000442956", user=BUDI)["stock"]
    assert "stok_semua_varian" not in tanpa_stok
    assert all("stok" not in v for v in tanpa_stok["varian_lain"])


def test_accurate_stock_pembeli_tetap_dapat_varian_tanpa_gudang(isi_indeks, acc_siap, izin):
    """varian_lain = stok/harga per kartu (BUKAN sebaran gudang) → pembeli boleh."""
    s = (isi_indeks(_KELUARGA, _GUDANG),
         parts_router.accurate_stock(pn="1000442956", user=PEMBELI))[1]["stock"]
    assert "per_gudang" not in s and len(s["varian_lain"]) == 2


# ── 6. Asisten: detail_part ────────────────────────────────────────────────
@pytest.fixture
def katalog_kosong(monkeypatch):
    """PN di luar katalog Sinotruk → detail_part jatuh ke jalur stok Accurate."""
    monkeypatch.setattr(part_index, "search_part_number", lambda t: [])
    monkeypatch.setattr(part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(part_index, "smart_pn_search", lambda q: ([], None))
    monkeypatch.setattr(part_index, "suggest_pns", lambda q: [])
    monkeypatch.setattr(sims, "available", lambda: False)
    monkeypatch.setattr(sims, "get_part_spec", lambda pn: {})


def test_detail_part_sodorkan_kartu_varian(isi_indeks, acc_siap, izin, katalog_kosong):
    isi_indeks(_KELUARGA, _GUDANG)
    r = ai._t_detail_part({"part_number": "1000442956"}, ADMIN)
    assert r["found"] is True
    vp = r["varian_pemasok"]
    assert vp["jumlah_varian"] == 3 and vp["total"] == 1553.0
    assert [v["kode"] for v in vp["varian"]] == [
        "1000442956/SN", "1000442956", "1000442956/SH"]
    assert vp["varian"][0]["harga"] == 285_000 and vp["varian"][0]["stok"] == 1069.0
    assert vp["varian"][0]["per_gudang"] == {"01.Jakarta": 1069.0}
    assert (vp["harga_min"], vp["harga_max"]) == (285_000, 455_000)
    assert "JANGAN merata-rata" in r["catatan_varian"]
    assert "TANYAKAN dulu varian mana" in r["catatan_varian"]


def test_detail_part_pn_tunggal_tanpa_kartu_varian(isi_indeks, acc_siap, izin, katalog_kosong):
    isi_indeks(_TUNGGAL, {"WG9525160004": {"02.Pekanbaru": 11.0}})
    r = ai._t_detail_part({"part_number": "WG9525160004"}, ADMIN)
    assert r["found"] is True
    assert "varian_pemasok" not in r and "catatan_varian" not in r


def test_detail_part_varian_gate_harga_dan_pembeli(isi_indeks, acc_siap, izin, katalog_kosong):
    isi_indeks(_KELUARGA, _GUDANG)
    staf = ai._t_detail_part({"part_number": "1000442956"}, WAWAN)   # tanpa col_harga
    assert all("harga" not in v for v in staf["varian_pemasok"]["varian"])
    assert staf["varian_pemasok"]["varian"][0]["stok"] == 1069.0
    beli = ai._t_detail_part({"part_number": "1000442956"}, PEMBELI)
    v0 = beli["varian_pemasok"]["varian"][0]
    assert "per_gudang" not in v0 and "stok" not in v0    # sebaran & total tak terscope
    assert v0["harga"] == 285_000                        # harga jual tetap


# ── 7. Asisten: cek_massal_part ────────────────────────────────────────────
@pytest.fixture
def massal(monkeypatch):
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "1000442956": {"part_name": "Fuel Filter"},
        "WG9525160004": {"part_name": "Clutch Plate"}})
    monkeypatch.setattr(ai.harga, "shipping_weight_for",
                        lambda pn, allow_remote=False: 0)


def test_cek_massal_kartu_varian_per_pn(isi_indeks, acc_siap, izin, massal):
    isi_indeks({**_KELUARGA, **_TUNGGAL}, _GUDANG)
    r = ai._t_cek_massal_part({"daftar_pn": "1000442956, WG9525160004"}, ADMIN)
    baris = {it["pn"]: it for it in r["part"]}
    vp = baris["1000442956"]["varian_pemasok"]
    assert vp["jumlah_varian"] == 3
    assert vp["varian"][0] == {"kode": "1000442956/SN", "stok": 1069.0, "harga": 285_000}
    # ⛔ per-gudang sengaja TIDAK ikut di jalur massal (payload token).
    assert all("per_gudang" not in v for v in vp["varian"])
    assert "varian_pemasok" not in baris["WG9525160004"]
    assert "JANGAN merata-rata harga" in r["catatan_varian"]
    assert list(r.keys())[-1] == "catatan"        # catatan tetap key TERAKHIR


def test_cek_massal_varian_gate_harga_dan_pembeli(isi_indeks, acc_siap, izin, massal):
    isi_indeks(_KELUARGA, _GUDANG)
    staf = ai._t_cek_massal_part({"daftar_pn": "1000442956"}, WAWAN)["part"][0]
    assert all("harga" not in v for v in staf["varian_pemasok"]["varian"])
    beli = ai._t_cek_massal_part({"daftar_pn": "1000442956"}, PEMBELI)["part"][0]
    # Jalur massal memang tak memberi angka stok ke pembeli (tak terscope wilayah)
    # → kartu variannya pun hanya kode + harga.
    assert all("stok" not in v for v in beli["varian_pemasok"]["varian"])
    assert beli["varian_pemasok"]["varian"][0]["harga"] == 285_000


def test_cek_massal_tanpa_keluarga_tak_menambah_catatan(isi_indeks, acc_siap, izin, massal):
    isi_indeks(_TUNGGAL)
    r = ai._t_cek_massal_part({"daftar_pn": "WG9525160004"}, ADMIN)
    assert "catatan_varian" not in r
    assert "varian_pemasok" not in r["part"][0]


# ── 8. Asisten: buat_penawaran ambigu = BATAL + tanya ──────────────────────
@pytest.fixture
def penawaran(monkeypatch):
    """Accurate palsu untuk penawaran; pembuatan SQ dipagari (harus tak terpanggil
    di jalur ambigu)."""
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "ensure_session_force",
                        lambda: {"jsessionid": "J", "dsi": "D"})
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 1, "no": "001", "name": "CV ANUGERAH"}])
    dibuat: dict = {"sq": 0}

    def _create(**kw):
        dibuat["sq"] += 1
        dibuat.update(kw)
        return {"id": 9, "number": kw["number"],
                "total": sum(l["qty"] * l["unit_price"] for l in kw["lines"])}

    monkeypatch.setattr(ai.accurate, "create_sales_quotation", _create)
    monkeypatch.setattr(ai.accurate, "next_quotation_number", lambda: "MASPART-01")
    monkeypatch.setattr(ai.accurate, "sales_quotation_pdf", lambda qid, layout_id=50: b"%PDF")
    monkeypatch.setattr(ai.accurate, "logout", lambda: True)
    monkeypatch.setattr(ai.accurate, "suppress_autologin", lambda *a, **k: None)
    return dibuat


def test_penawaran_pn_ambigu_dibatalkan_dan_menuntun_bertanya(isi_indeks, penawaran, monkeypatch):
    """Indeks hanya punya kartu bersuffix → 'PN dasar' tak bisa di-resolve ke satu
    kartu. ⛔ Jangan menebak, jangan bikin SQ: kembalikan daftar variannya."""
    isi_indeks({k: _KELUARGA[k] for k in ("1000442956SN", "1000442956SH")}, _GUDANG)
    monkeypatch.setattr(ai.accurate, "item_for_quotation",
                        lambda pn: (_ for _ in ()).throw(AssertionError("tak boleh resolve")))
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "1000442956", "qty": 2}]}, ADMIN)
    assert r["found"] is False and r["perlu_pilih_varian"] is True
    assert penawaran["sq"] == 0                       # ⛔ tak ada penawaran terbuat
    amb = r["part_ambigu"][0]
    assert amb["pn_diminta"] == "1000442956"
    assert [v["kode"] for v in amb["varian"]] == ["1000442956/SN", "1000442956/SH"]
    assert [v["harga"] for v in amb["varian"]] == [285_000, 455_000]
    assert [v["stok"] for v in amb["varian"]] == [1069.0, 2.0]
    assert "JANGAN memilihkan sendiri" in r["error"]


def test_penawaran_kode_varian_persis_tetap_jalan(isi_indeks, penawaran, monkeypatch):
    """Kode yang diketik user cocok PERSIS ke satu kartu = pilihan eksplisit →
    alur penawaran lama TIDAK boleh terganggu."""
    isi_indeks(_KELUARGA, _GUDANG)
    monkeypatch.setattr(ai.accurate, "item_for_quotation",
                        lambda pn: {"id": 4051, "no": "000951.1000442956/SN",
                                    "pn": "1000442956/SN", "name": "Fuel Filter",
                                    "unit_id": 100, "unit": "Pc", "price": 285_000,
                                    "available": 1069})
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "1000442956/SN", "qty": 2}]}, ADMIN)
    assert r["found"] is True and penawaran["sq"] == 1
    assert penawaran["lines"][0]["unit_price"] == 285_000


def test_penawaran_pn_asing_tetap_pesan_lama(isi_indeks, penawaran, monkeypatch):
    """PN yang memang tak ada ≠ ambigu — pesan 'part_tidak_ditemukan' tetap yang
    bicara (jangan menyeret semua kegagalan ke pesan varian)."""
    isi_indeks(_KELUARGA, _GUDANG)
    monkeypatch.setattr(ai.accurate, "item_for_quotation", lambda pn: None)
    r = ai._t_buat_penawaran({"pelanggan": "CV ANUGERAH",
                              "barang": [{"part_number": "ZZZ000", "qty": 1}]}, ADMIN)
    assert r["found"] is False and r["part_tidak_ditemukan"] == ["ZZZ000"]
    assert "perlu_pilih_varian" not in r and penawaran["sq"] == 0
