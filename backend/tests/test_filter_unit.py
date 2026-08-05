"""Tool asisten `filter_unit` — DAFTAR ELEMENT FILTER satu unit dari nomor rangka.

Kasus nyata RJ326978 (2026-08-05): 'cek filter <rangka>' lewat cari_part_di_unit
menjawab ASSEMBLY/HOUSING ('Air filter assembly', 'Engine oil module', cover) —
bukan ELEMENT yang benar-benar diganti saat servis. Tool ini membalik seleksinya:
sisir SELURUH baris katalog unit, ambil HANYA baris ber-penanda element
('element'/'cartridge'/'滤芯'), klasifikasikan per jenis lewat nama + assembly
induk, lalu — kalau element oli & solar MESIN tak ada di pohon Sinotruk (mesin
Weichai) — tambal dari EPC Weichai.

Semua sumber di-mock (offline). conftest hanya mematikan warm_items_index /
items_index_ready, TIDAK unit_items → tiap test WAJIB mem-patch
`ai.epc_bom.unit_items`, kalau tidak ia menembak EPC sungguhan.
"""
import copy

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "roni", "role": "pembeli"}

FRAME = "RJ326978"


def _row(pn, nama, parent_pn, parent_nama, nama_cn="", qty=1):
    """Satu baris bentuk epc_bom.unit_items()['rows']."""
    return {"pn": pn, "nama": nama, "nama_cn": nama_cn, "qty": qty,
            "dari_assembly": {"pn": parent_pn, "nama": parent_nama}}


# Baris bentukan unit RJ326978 — ringkas tapi memuat SEMUA kelas kasus nyata.
ROWS = [
    # ── SATU element oli mesin, dua varian INDUK (E265 & E381): barangnya sama,
    # harus tampil sekali saja.
    _row("200V05504-0122", "Engine oil filter element with O-ring",
         "201V05000-7043/1", "Engine oil module (E265)"),
    _row("200V05504-0122", "Engine oil filter element with O-ring",
         "201V05000-7043/3", "Engine oil module (E381)"),
    # cover: ikut kata 'filter' tapi BUKAN barang servis (tanpa penanda element)
    _row("200V05505-0011", "Engine oil filter cover, with sealing ring",
         "201V05000-7043/1", "Engine oil module (E265)"),

    # ── ASSEMBLY yang MENYAMAR element: kata 'element' hanya di keterangan nama.
    # Cirinya tegas dari data — ia justru INDUK dari element yang terpilih.
    _row("082V12501-7293/1",
         "Fuel filter (Hand oil pump cancelled, Filter element upgrade) (0475)",
         "082V12501-7293",
         "Fuel filter (Hand oil pump cancelled, Upgraded filter element)"),
    # element solar halus ASLI — induknya persis baris di atas
    _row("201V12503-0062", "Fuel filter element With O-ring",
         "082V12501-7293/1",
         "Fuel filter (Hand oil pump cancelled, Filter element upgrade) (0475)"),

    # ── solar kasar: 'coarse' harus menang atas 'fuel' (urutan aturan)
    _row("WG9925550182/1", "Fuel coarse filter element (Parker)",
         "WG9925550182", "Fuel coarse filter"),
    # element GENERIK: namanya tak menyebut fungsi sama sekali — hanya INDUK yang
    # menyebut ('… fuel coarse filter …'); penandanya pun cuma CN 滤芯.
    _row("WG9925550966/1", "Filter element",
         "WG9925550966",
         "Electrical heater fuel coarse filter of electric pump "
         "(Left in and right out / Long service life)", nama_cn="滤芯"),

    # ── udara: main + safety, masing-masing DUA pemasok (/1 Mann-Hummel vs /2
    # Shandong Taiquan) untuk SLOT yang sama.
    _row("WG9525195201/1", "Main filter element - Flame retardant (Mann- Hummel)",
         "WG9525195010/1", "Air filter assembly"),
    _row("WG9525195201/2", "Main filter element (Shandong Taiquan)",
         "WG9525195010/2", "Air filter assembly"),
    _row("710W08405-0017/1", "Safety filter element - Flame retardant (Mann- Hummel)",
         "WG9525195010/1", "Air filter assembly"),
    _row("710W08405-0017/2", "Safety filter element (Shandong Taiquan)",
         "WG9525195010/2", "Air filter assembly"),
    # rumah & bracket filter udara — jawaban SALAH yang dulu keluar
    _row("WG9525195010", "Air filter assembly", "WG9525190000", "Air intake system"),
    _row("WG9525195025", "Air filter bracket assembly", "WG9525190000",
         "Air intake system"),

    _row("WG9525470325+002/1", "Steering oil tank filter element",
         "WG9525470325", "Power steering oil tank assembly (Xishui TSUNG)"),
    _row("YZ167182100260+004/1", "Filter cartridge (Shanghai Yida)",
         "YZ167182100260", "Evaporator assembly"),
    _row("WG1034130181+008/1", "Urea filter element",
         "WG1034130181", "Urea pump box integrated system (China V non-heating)"),
    # saringan kawat urea: bukan element servis
    _row("WG1034130181+014/1", "3D filter mesh",
         "WG1034130181", "Urea pump box integrated system (China V non-heating)"),

    # ── SPECIAL CASE gardan: 'Oil filter' polos TANPA penanda element, tapi di EPC
    # baris itulah yang dijual & diganti (rumahnya tak dijual satuan).
    _row("810W32118-0010", "Oil filter", "810W35100-0058",
         "MCY13BGS rear axle housing and wheel hub attachment "
         "(Wheel-side planetary reducer)"),
    # …tapi special-case itu dicocokkan dari AWAL nama: rumahnya sendiri, yang
    # kebetulan memuat frasa 'oil filter' di tengah, tetap harus keluar.
    _row("810W35100-0121", "Partition ring oil filter assembly", "810W35100-0058",
         "MCY13BGS rear axle housing and wheel hub attachment "
         "(Wheel-side planetary reducer)"),

    # element ber-induk 'Filter assembly' polos → fallback JUJUR 'lainnya'
    _row("WG2203240200", "Filter element assembly", "WG2203240100", "Filter assembly"),

    # ── pembanding non-filter
    _row("082V11640-0287/1", "ECU bracket (E050)", "080-#0211-0477",
         "MC07H High-pressure common rail system"),
    # 'element' TANPA sinyal filter — penjaga false-positive penanda
    _row("WG9100590010", "Heating element", "WG9100590000", "Cab heater assembly"),
]

# Unit bermesin Weichai: pohon Sinotruk berhenti di engine assembly → element oli
# & solar MESIN memang tak ada di sana (sisanya chassis tetap lengkap).
ROWS_MESIN_KOSONG = [r for r in ROWS if r["pn"] not in
                     ("200V05504-0122", "200V05505-0011",
                      "082V12501-7293/1", "201V12503-0062")]

# Indeks inventori tiruan (rows_for_pns PEMAAF suffix varian — lihat
# test_pn_suffix_varian.py; di sini cukup cocok persis atas 2 PN).
_LOKAL = {
    "200V05504-0122": {"part_name": "FILTER OLI MESIN MC11", "stok": "42",
                       "harga": "Rp 250.000", "gudang": {"02.Pekanbaru": 42}},
    "WG9525195201/1": {"part_name": "FILTER UDARA LUAR HOWO", "stok": "8",
                       "harga": "Rp 310.000", "gudang": {"01.Medan": 8}},
}

# Hasil bentukan epc_weichai.find_parts untuk RJ345233 (WP12) — find_parts
# otomatis mengurai turunan part yang cocok (Oil Filter → Filter Element).
WEICHAI_OK = {
    "found": True,
    "engine": {"model": "1424K059853", "nomor_mesin": "1424K059853",
               "nama": "WP12S430E201卡车用柴油机", "order": "DHP12Q4566-900*01"},
    "hasil": [
        {"pn": "1000428261", "nama": "Oil Filter", "group": "Oil Filter Group"},
        {"pn": "1000428205", "nama": "Filter Element", "group": "Oil Filter Group",
         "keterangan": "komponen di dalam part di atas"},
        {"pn": "612600080933", "nama": "Fuel Filter", "group": "Fuel Filter Group"},
        {"pn": "612600080934", "nama": "Fuel Filter Element",
         "group": "Fuel Filter Group", "keterangan": "komponen di dalam part di atas"},
        {"pn": "1000424916", "nama": "Fuel Coarse Filter Element",
         "group": "parts kit Group", "keterangan": "komponen di dalam part di atas"},
    ],
}


@pytest.fixture
def dunia(monkeypatch):
    dunia_state = {"rows": list(ROWS), "incomplete": False}

    def _unit_items(rangka):
        return {"found": True, "frame_number": rangka.upper(),
                "rows": copy.deepcopy(dunia_state["rows"]),
                "incomplete": dunia_state["incomplete"]}

    monkeypatch.setattr(ai.epc_bom, "unit_items", _unit_items)
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: {p: dict(_LOKAL[p]) for p in pns if p in _LOKAL})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])

    def _tak_boleh(rangka, terms):
        raise AssertionError("fallback tak boleh terpanggil")

    monkeypatch.setattr(ai.epc_weichai, "find_parts", _tak_boleh)
    return dunia_state


def _semua_pn(r):
    return [p["part_number"] for rs in r["filter_per_jenis"].values() for p in rs]


def _jenis_of(r, pn):
    for jenis, rs in r["filter_per_jenis"].items():
        if any(p["part_number"] == pn for p in rs):
            return jenis
    return None


def _baris(r, pn):
    for rs in r["filter_per_jenis"].values():
        for p in rs:
            if p["part_number"] == pn:
                return p
    return None


# ── seleksi: element MASUK, rumah/cover/bracket/mesh KELUAR ──────────────────
def test_hanya_element_yang_masuk(dunia):
    """Inti tool: yang keluar HARUS barang servis. Termasuk membuang ASSEMBLY
    yang menyamar element ('…Upgraded filter element' 082V12501-7293)."""
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert r["found"] is True and r["frame_number"] == FRAME
    pns = _semua_pn(r)
    for keluar in ("082V12501-7293/1",        # assembly menyamar element
                   "200V05505-0011",           # cover
                   "WG9525195010",             # rumah filter udara
                   "WG9525195025",             # bracket
                   "WG1034130181+014/1",       # saringan kawat urea
                   "810W35100-0121",           # 'oil filter' di TENGAH nama rumah
                   "082V11640-0287/1",         # non-filter
                   "WG9100590010"):            # 'element' tanpa sinyal filter
        assert keluar not in pns, keluar
    for masuk in ("200V05504-0122", "201V12503-0062", "WG9925550182/1",
                  "WG9925550966/1", "WG9525195201/1", "WG9525195201/2",
                  "710W08405-0017/1", "710W08405-0017/2", "WG9525470325+002/1",
                  "YZ167182100260+004/1", "WG1034130181+008/1",
                  "810W32118-0010", "WG2203240200"):
        assert masuk in pns, masuk
    assert r["jumlah_element"] == 13 == len(pns)


def test_mapping_jenis_benar(dunia):
    """Termasuk element GENERIK yang jenisnya hanya terbaca dari nama INDUK."""
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert _jenis_of(r, "200V05504-0122") == "oli_mesin"
    assert _jenis_of(r, "201V12503-0062") == "solar_halus"
    assert _jenis_of(r, "WG9925550182/1") == "solar_kasar"
    assert _jenis_of(r, "WG9925550966/1") == "solar_kasar"     # via nama INDUK
    assert _jenis_of(r, "WG9525195201/1") == "udara"
    assert _jenis_of(r, "710W08405-0017/1") == "udara"
    assert _jenis_of(r, "WG9525470325+002/1") == "power_steering"
    assert _jenis_of(r, "YZ167182100260+004/1") == "ac_kabin"
    assert _jenis_of(r, "WG1034130181+008/1") == "urea"
    assert _jenis_of(r, "WG2203240200") == "lainnya"
    # penyajian mengikuti _JENIS_FILTER_URUT (montir dulu, 'lainnya' terakhir)
    assert list(r["filter_per_jenis"]) == [
        "oli_mesin", "solar_halus", "solar_kasar", "udara", "power_steering",
        "ac_kabin", "urea", "gardan", "lainnya"]
    assert "filter oli mesin" in r["catatan"]                  # urutan ikut dinarasikan


def test_oil_filter_gardan_special_case(dunia):
    """'Oil filter' polos di rumah gardan: tanpa penanda element, tapi baris ITU
    yang dijual & diganti — dan jangan sampai tercap filter oli MESIN."""
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert _jenis_of(r, "810W32118-0010") == "gardan"
    assert r["filter_per_jenis"]["gardan"] == [_baris(r, "810W32118-0010")]


def test_duplikat_lintas_varian_induk_digabung(dunia):
    """Element yang sama muncul di 'Engine oil module' E265 DAN E381 — barangnya
    SATU, cukup tampil sekali (induk pertama dipakai sebagai konteks)."""
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    oli = r["filter_per_jenis"]["oli_mesin"]
    assert [p["part_number"] for p in oli] == ["200V05504-0122"]
    assert oli[0]["di_dalam_assembly"] == "Engine oil module (E265)"
    assert oli[0]["assembly_pn"] == "201V05000-7043/1"


def test_varian_pemasok_keduanya_disebut(dunia):
    """'…/1' Mann-Hummel vs '…/2' Shandong Taiquan = dua PEMASOK untuk slot yang
    SAMA. Hanya satu terpasang, tapi keduanya wajib disebut — model tak boleh
    memilih diam-diam."""
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    udara = {p["part_number"]: p for p in r["filter_per_jenis"]["udara"]}
    assert set(udara) == {"WG9525195201/1", "WG9525195201/2",
                          "710W08405-0017/1", "710W08405-0017/2"}
    assert all(p.get("varian_pemasok") is True for p in udara.values())
    assert "JANGAN memilih diam-diam" in r["catatan_varian"]
    # element tunggal TIDAK ikut ditandai varian
    assert "varian_pemasok" not in _baris(r, "WG9925550182/1")


# ── gerbang harga/stok ───────────────────────────────────────────────────────
def test_admin_lihat_harga_dan_rincian_gudang(dunia):
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    p = _baris(r, "200V05504-0122")
    assert p["ada_di_inventori"] is True and p["stok_total"] == "42"
    assert p["harga_lokal"] == "Rp 250.000"
    assert p["stok_per_gudang"] == {"02.Pekanbaru": 42}
    # nama katalog LOKAL diutamakan atas nama EPC
    assert p["nama"] == "FILTER OLI MESIN MC11"
    # PN di luar indeks lokal jujur ditandai
    assert _baris(r, "201V12503-0062")["ada_di_inventori"] is False


def test_harga_disembunyikan_dari_staf_tanpa_izin(dunia, monkeypatch):
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: False)
    r = ai._t_filter_unit({"rangka": FRAME}, STAF)
    p = _baris(r, "200V05504-0122")
    assert "harga_lokal" not in p and p["stok_total"] == "42"


def test_pembeli_tak_lihat_rincian_gudang(dunia):
    r = ai._t_filter_unit({"rangka": FRAME}, PEMBELI)
    p = _baris(r, "200V05504-0122")
    assert "stok_per_gudang" not in p and p["stok_total"] == "42"


# ── fallback EPC Weichai (element oli & solar MESIN) ─────────────────────────
def test_weichai_tak_dipanggil_saat_pohon_sinotruk_lengkap(dunia):
    """Bridge SSO Weichai lambat pada panggilan pertama — jangan disentuh bila
    pohon Sinotruk sudah punya element mesin. (fixture: find_parts = AssertionError)"""
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert r["found"] is True and "mesin" not in r


def test_fallback_weichai_saat_element_mesin_kosong(dunia, monkeypatch):
    dunia["rows"] = ROWS_MESIN_KOSONG
    dipanggil = []

    def _find(rangka, terms):
        dipanggil.append((rangka, list(terms)))
        return copy.deepcopy(WEICHAI_OK)

    monkeypatch.setattr(ai.epc_weichai, "find_parts", _find)
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [
        {"part_number": "1000428205", "part_name": "FILTER OLI MESIN WP12",
         "stok": "7", "harga": "Rp 180.000", "gudang": {"02.Pekanbaru": 7}}])

    r = ai._t_filter_unit({"rangka": "RJ345233"}, ADMIN)

    assert dipanggil and dipanggil[0][0] == "RJ345233"
    assert _jenis_of(r, "1000428205") == "oli_mesin"
    assert _jenis_of(r, "612600080934") == "solar_halus"
    assert _jenis_of(r, "1000424916") == "solar_kasar"
    # rumah/assembly filter mesin BUKAN barang servis → hanya konteks
    assert _jenis_of(r, "1000428261") is None
    assert {k["part_number"] for k in r["konteks_mesin_weichai"]} == \
        {"1000428261", "612600080933"}
    assert r["mesin"]["nomor_mesin"] == "1424K059853"
    assert r["mesin"]["model"] == "WP12S430E201卡车用柴油机"
    assert "EPC Weichai" in r["sumber"]
    # sumber per baris ditandai + gerbang harga/stok SAMA dgn baris Sinotruk
    p = _baris(r, "1000428205")
    assert p["sumber"] == "EPC Weichai"
    assert p["ada_di_inventori"] is True and p["harga_lokal"] == "Rp 180.000"
    assert p["nama"] == "FILTER OLI MESIN WP12"          # nama lokal menang
    assert _baris(r, "612600080934")["ada_di_inventori"] is False
    # hasil chassis tetap utuh
    assert _jenis_of(r, "WG9525195201/1") == "udara"


def test_weichai_no_link_catatan_jujur(dunia, monkeypatch):
    """Bukan unit bermesin Weichai (atau tak terhubung) = BUKAN kegagalan teknis —
    jangan disamarkan, dan hasil chassis tetap disajikan."""
    dunia["rows"] = ROWS_MESIN_KOSONG
    monkeypatch.setattr(ai.epc_weichai, "find_parts",
                        lambda r, t: {"found": False, "reason": "no_link",
                                      "message": "VIN tak terhubung ke EPC Weichai"})
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert r["found"] is True and "mesin" not in r
    assert "tidak terhubung ke EPC Weichai" in r["catatan_mesin"]
    assert "JANGAN mengarang" in r["catatan_mesin"]


def test_weichai_gagal_jaringan_belum_pasti(dunia, monkeypatch):
    dunia["rows"] = ROWS_MESIN_KOSONG
    monkeypatch.setattr(ai.epc_weichai, "find_parts",
                        lambda r, t: {"found": False, "reason": "network",
                                      "message": "timeout bridge"})
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert r["found"] is True
    assert "BELUM pasti" in r["catatan_mesin"] and "timeout bridge" in r["catatan_mesin"]


# ── kegagalan EPC & jawaban jujur ────────────────────────────────────────────
def test_rangka_wajib_disebut():
    assert "error" in ai._t_filter_unit({}, ADMIN)


def test_token_kedaluwarsa_ditandai(dunia, monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "unit_items",
                        lambda r: {"found": False, "_err": "token_expired"})
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert r["found"] is False and r.get("_token_issue") is True


def test_error_jaringan_bukan_jawaban_kosong(dunia, monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "unit_items",
                        lambda r: {"found": False, "_err": "network"})
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert r["found"] is False and "jaringan" in r["error"].lower()


def test_nihil_total_jawab_jujur(dunia, monkeypatch):
    """Tak satu pun element di pohon DAN mesin tak terhubung Weichai → jangan
    memaksakan daftar."""
    dunia["rows"] = [
        _row("082V11640-0287/1", "ECU bracket (E050)", "080-#0211-0477",
             "MC07H High-pressure common rail system"),
        _row("WG9525195010", "Air filter assembly", "WG9525190000",
             "Air intake system"),
    ]
    monkeypatch.setattr(ai.epc_weichai, "find_parts",
                        lambda r, t: {"found": False, "reason": "no_link",
                                      "message": ""})
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert r["found"] is False and "filter_per_jenis" not in r
    assert "JANGAN mengarang" in r["jawaban_wajib"]


def test_pohon_parsial_diberi_peringatan(dunia):
    """Sebagian node gagal dibuka → hasil BUKAN vonis; catatan tak boleh menyuruh
    model menyimpulkan 'tidak ada'."""
    dunia["incomplete"] = True
    r = ai._t_filter_unit({"rangka": FRAME}, ADMIN)
    assert "gagal dibuka" in r["peringatan"]
    assert "jangan vonis pasti tidak ada" in r["catatan"]
    # tanpa peringatan, kalimat vonis itu memang tak muncul
    dunia["incomplete"] = False
    assert "jangan vonis pasti" not in ai._t_filter_unit({"rangka": FRAME}, ADMIN)["catatan"]


# ── klasifikasi jenis: unit test langsung ────────────────────────────────────
def test_jenis_filter_scr_kata_utuh():
    """'scr' sebagai SUBSTRING ikut kena 'screw'/'description' yang bertebaran di
    nama part — semua element bisa salah masuk urea."""
    assert ai._jenis_filter("Filter element fixing screw", "",
                            "Fuel filter assembly") == "solar_halus"
    assert ai._jenis_filter("Filter element", "", "SCR system assembly") == "urea"


def test_jenis_filter_urutan_aturan():
    # gardan WAJIB pertama: element gardan bernama 'Oil filter'
    assert ai._jenis_filter("Oil filter", "", "Rear axle housing") == "gardan"
    assert ai._jenis_filter("Oil filter element", "", "Engine oil module") == "oli_mesin"
    # kasar sebelum halus: 'Fuel coarse filter' memuat 'fuel' juga
    assert ai._jenis_filter("Fuel coarse filter element", "",
                            "Fuel coarse filter") == "solar_kasar"
    assert ai._jenis_filter("Fuel filter element", "", "Fuel filter") == "solar_halus"
    assert ai._jenis_filter("Filter element", "滤芯", "Air filter assembly") == "udara"
    assert ai._jenis_filter("Filter cartridge", "", "Evaporator assembly") == "ac_kabin"
    assert ai._jenis_filter("Filter element", "", "Gearbox assembly") == "transmisi"
    assert ai._jenis_filter("Filter element assembly", "", "Filter assembly") == "lainnya"


def test_pn_base_buang_suffix_varian():
    assert ai._pn_base("WG9525195201/2") == "WG9525195201"
    assert ai._pn_base("WG9525470325+002/1") == "WG9525470325+002"
    assert ai._pn_base("") == ""


# ── registrasi (pola test_cari_part_di_unit.py) ──────────────────────────────
def test_terdaftar_sebagai_tool_epc_per_vin():
    assert ai._DISPATCH["filter_unit"] is ai._t_filter_unit
    # hasilnya PN per-VIN otoritatif → wajib ikut guard substitusi katalog-lokal
    assert "filter_unit" in ai._EPC_VIN_PART_TOOLS
    assert "filter_unit" in ai._TOOL_LABEL
    for user in (ADMIN, PEMBELI):
        names = {s["function"]["name"] for s in ai._tool_specs(user)}
        assert "filter_unit" in names, user["role"]
