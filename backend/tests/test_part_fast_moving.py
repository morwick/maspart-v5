"""Tool part_fast_moving: daftar part aus per MODEL dari dataset fast_moving.

Invarian: label pasaran terurai (NX400 = NX + 400 HP), ambigu → kandidat +
instruksi tanya (bukan memilih diam-diam), tak ketemu → daftar 'tersedia'
(bukan mengarang), ko_eksis & porsi n_unit diteruskan, harga tergerbang
_boleh_harga, stok disilang part_index. Semua sumber di-mock (offline).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import fast_moving

ADMIN = {"username": "admin", "role": "admin"}

_DATA = {"model": {
    "ZZ3257V404JF1": {
        "jenis": "HOWO-NX 6X4", "hp": 400, "unit_populasi": 51,
        "unit_sampel": ["SJ000001", "SJ000002", "SJ000003"], "n_sampel": 3,
        "slot": [
            {"kategori": "filter", "slot": "oil filter", "varian": [
                {"pn": "VG61000070005", "nama": "Oil filter", "qty": 1,
                 "n_unit": 3, "tahun": ["2023", "2024"], "pn_sub": [],
                 "pengganti": []}]},
            {"kategori": "rem", "slot": "brake shoe — drum brake",
             "ko_eksis": True, "varian": [
                {"pn": "AZ450045001160", "nama": "Brake shoe", "qty": 2,
                 "n_unit": 3, "tahun": [], "pn_sub": [], "pengganti": []},
                {"pn": "AZ450045001161", "nama": "Brake shoe", "qty": 2,
                 "n_unit": 3, "tahun": [], "pn_sub": [], "pengganti": []}]},
            {"kategori": "karet", "slot": "suspension rubber mount", "varian": [
                {"pn": "AZ9T3152200011", "nama": "Suspension rubber mount",
                 "qty": 4, "n_unit": 2, "tahun": ["2024"], "pn_sub": [],
                 "pengganti": []},
                {"pn": "AZ9725520683", "nama": "Suspension rubber mount",
                 "qty": 4, "n_unit": 1, "tahun": ["2022"], "pn_sub": [],
                 "pengganti": ["AZ9725520683TP0003"]}]},
        ]},
    "ZZ3312V404JB1": {
        "jenis": "HOWO-NX 8X4", "hp": 400, "unit_populasi": 12,
        "unit_sampel": ["SJ000009"], "n_sampel": 1, "slot": []},
    "ZZ4256V324HF1B": {
        "jenis": "SITRAK C7H 6X4", "hp": 320, "unit_populasi": 30,
        "unit_sampel": ["SJ000005"], "n_sampel": 1, "slot": []},
}}

_LOKAL = {"VG61000070005": {"part_name": "Oil Filter", "stok": 120,
                            "harga": "Rp 55.000", "gudang": {"01.Jakarta": 100}}}


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(fast_moving, "data", lambda: dict(_DATA))
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: {p: dict(_LOKAL[p]) for p in pns if p in _LOKAL})
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: True)


def test_fm_match_variasi_label():
    m64 = _DATA["model"]["ZZ3257V404JF1"]
    assert ai._fm_match("NX400", "ZZ3257V404JF1", m64)
    assert ai._fm_match("nx 400", "ZZ3257V404JF1", m64)          # _fm_norm meng-upper
    assert ai._fm_match("HOWO NX 6X4", "ZZ3257V404JF1", m64)
    assert ai._fm_match("ZZ3257V404JF1", "ZZ3257V404JF1", m64)
    assert not ai._fm_match("NX460", "ZZ3257V404JF1", m64)       # HP beda
    m_c7h = _DATA["model"]["ZZ4256V324HF1B"]
    assert ai._fm_match("SITRAK C7H", "ZZ4256V324HF1B", m_c7h)
    assert not ai._fm_match("SITRAK C9H", "ZZ4256V324HF1B", m_c7h)


def test_nx400_ambigu_tawarkan_kandidat(patched):
    r = ai._t_part_fast_moving({"model": "NX400"}, ADMIN)
    assert r["found"] and r["ambigu"]
    # urut populasi terbanyak; tak memilih diam-diam
    assert [k["model"] for k in r["kandidat"]] == ["ZZ3257V404JF1", "ZZ3312V404JB1"]
    assert "TANYAKAN" in r["catatan"]


def test_detail_model_lengkap(patched):
    r = ai._t_part_fast_moving({"model": "NX400 6X4"}, ADMIN)
    assert r["found"] and r["model"] == "ZZ3257V404JF1" and r["hp"] == 400
    assert r["unit_sampel_epc"] == 3 and r["unit_populasi"] == 51
    slots = {s["slot"]: s for s in r["slot"]}
    # porsi + stok + harga tersilang
    of = slots["oil filter"]["varian"][0]
    assert of["dipakai_di_unit"] == "3/3" and of["stok_total"] == 120
    assert of["harga"] == "Rp 55.000"
    # ko_eksis diteruskan
    assert slots["brake shoe — drum brake"]["ko_eksis"] is True
    # varian terbelah bawa tahun + pengganti; PN di luar inventori ditandai
    rm = slots["suspension rubber mount"]["varian"]
    assert [v["dipakai_di_unit"] for v in rm] == ["2/3", "1/3"]
    assert rm[0]["tahun_unit"] == ["2024"]
    assert rm[1]["pengganti"] == ["AZ9725520683TP0003"]
    assert rm[1]["ada_di_inventori"] is False


def test_harga_tergerbang(patched, monkeypatch):
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: False)
    r = ai._t_part_fast_moving({"model": "ZZ3257V404JF1"}, ADMIN)
    of = [s for s in r["slot"] if s["slot"] == "oil filter"][0]["varian"][0]
    assert of["stok_total"] == 120 and "harga" not in of


def test_filter_kategori(patched):
    r = ai._t_part_fast_moving({"model": "ZZ3257V404JF1", "kategori": "rem"}, ADMIN)
    assert [s["kategori"] for s in r["slot"]] == ["rem"]
    assert r["kategori_difilter"] == "rem"


def test_tak_ketemu_daftarkan_tersedia(patched):
    r = ai._t_part_fast_moving({"model": "NX999"}, ADMIN)
    assert r["found"] is False
    jenis = {t["jenis"] for t in r["tersedia"]}
    assert "HOWO-NX 6X4" in jenis and "SITRAK C7H 6X4" in jenis
    assert "JANGAN mengarang" in r["catatan"]


def test_dataset_kosong(monkeypatch):
    monkeypatch.setattr(fast_moving, "data", lambda: {})
    r = ai._t_part_fast_moving({"model": "NX400"}, ADMIN)
    assert "belum terbangun" in r["error"]


# ── Jalur NOMOR RANGKA, 1 unit atau lebih (permintaan pemilik 2026-08-06) ────
VIN_A = "LZZPBXSF5NJ248278"      # ZZ3257V404JF1 (ada di dataset)
VIN_B = "LZZPBXSF2PJ264974"      # ZZ3257V404JF1 juga → se-model
VIN_C = "LZZ5EXSF9RJ373319"      # ZZ4256V324HF1B → model lain
VIN_X = "LZZ0000000000000"       # tak dikenal EPC & populasi

_CFG = {
    VIN_A: {"model": "ZZ3257V404JF1", "jenis": "Cargo", "mesin": "WP12.400E201"},
    VIN_B: {"model": "ZZ3257V404JF1", "jenis": "Cargo", "mesin": "WP12.400E201"},
    VIN_C: {"model": "ZZ4256V324HF1B", "jenis": "Tractor (kepala)",
            "mesin": "MC11.42-50"},
    VIN_X: {},
}


@pytest.fixture
def per_vin(patched, monkeypatch):
    """EPC getVehicleConfig & populasi ditiru — tak ada jaringan."""
    monkeypatch.setattr(ai, "_configs_rangka",
                        lambda rgs: {r: dict(_CFG.get(r, {})) for r in rgs})
    monkeypatch.setattr(fast_moving, "peta_populasi", lambda: {})


def test_dua_unit_se_model_jadi_satu_daftar(per_vin):
    r = ai._t_part_fast_moving({"rangka": [VIN_A, VIN_B]}, ADMIN)
    assert r["found"] and r["unit_se_model"] is True
    assert r["model"] == "ZZ3257V404JF1" and r["jumlah_rangka"] == 2
    assert [u["rangka"] for u in r["unit"]] == [VIN_A, VIN_B]
    assert {u["model"] for u in r["unit"]} == {"ZZ3257V404JF1"}
    # bentuknya SAMA dengan jalur 'model' → penyaji tak perlu logika baru
    of = [s for s in r["slot"] if s["slot"] == "oil filter"][0]["varian"][0]
    assert of["dipakai_di_unit"] == "3/3" and of["stok_total"] == 120
    assert "SEMUA bermodel" in r["catatan"]


def test_vin_koma_dalam_satu_string_juga_diterima(per_vin):
    """Model kerap meneruskan apa adanya: 'VIN1,VIN2'."""
    r = ai._t_part_fast_moving({"rangka": f"{VIN_A},{VIN_B}"}, ADMIN)
    assert r["found"] and r["jumlah_rangka"] == 2 and r["unit_se_model"] is True


def test_unit_beda_model_digabung_dan_ditandai(per_vin, monkeypatch):
    data2 = {"model": dict(_DATA["model"])}
    data2["model"]["ZZ4256V324HF1B"] = {
        "jenis": "SITRAK C7H 6X4", "hp": 320, "unit_populasi": 30,
        "unit_sampel": ["SJ000005", "SJ000006"], "n_sampel": 2,
        "slot": [
            {"kategori": "filter", "slot": "oil filter", "varian": [
                {"pn": "VG61000070005", "nama": "Oil filter", "qty": 1,
                 "n_unit": 2, "tahun": [], "pn_sub": [], "pengganti": []}]},
            {"kategori": "belt", "slot": "v-belt", "varian": [
                {"pn": "VG1246060003", "nama": "V-belt", "qty": 1,
                 "n_unit": 2, "tahun": [], "pn_sub": [], "pengganti": []}]},
        ]}
    monkeypatch.setattr(fast_moving, "data", lambda: data2)

    r = ai._t_part_fast_moving({"rangka": [VIN_A, VIN_C]}, ADMIN)
    assert r["found"] and r["gabungan_beberapa_model"] is True
    assert {m["model"] for m in r["model_terlibat"]} == {"ZZ3257V404JF1", "ZZ4256V324HF1B"}
    slot = {s["slot"]: s for s in r["slot"]}
    # Slot yang dipakai SEMUA model didahulukan & ditandai (prioritas stok)
    assert r["slot"][0]["slot"] == "oil filter"
    assert slot["oil filter"]["di_semua_model"] is True
    assert slot["oil filter"]["varian"][0]["dipakai_di_unit"] == {
        "ZZ3257V404JF1": "3/3", "ZZ4256V324HF1B": "2/2"}   # porsi PER MODEL
    # Slot khusus satu model menyebutkan modelnya
    assert slot["v-belt"]["di_semua_model"] is False
    assert slot["v-belt"]["model"] == ["ZZ4256V324HF1B"]
    assert "di_semua_model" in r["catatan"] and "jangan dijumlahkan" in r["catatan"]


def test_gabungan_excel_memuat_semua_baris(per_vin, monkeypatch):
    monkeypatch.setattr(fast_moving, "data", lambda: {"model": {
        "ZZ3257V404JF1": _DATA["model"]["ZZ3257V404JF1"],
        "ZZ4256V324HF1B": {**_DATA["model"]["ZZ4256V324HF1B"], "n_sampel": 1,
                           "slot": [{"kategori": "belt", "slot": "v-belt", "varian": [
                               {"pn": "VG1246060003", "nama": "V-belt", "qty": 1,
                                "n_unit": 1, "tahun": [], "pn_sub": [],
                                "pengganti": []}]}]},
    }})
    r = ai._t_part_fast_moving({"rangka": [VIN_A, VIN_C], "excel": True}, ADMIN)
    assert r["export_id"] and r["filename"].endswith(".xlsx")
    # 5 varian model A (1 filter + 2 sepatu rem + 2 karet) + 1 belt model C
    assert r["jumlah_baris"] == 6


def test_unit_tak_dikenal_dilaporkan_apa_adanya(per_vin):
    r = ai._t_part_fast_moving({"rangka": [VIN_A, VIN_X]}, ADMIN)
    assert r["found"] is True and r["jumlah_rangka"] == 2
    x = [u for u in r["unit"] if u["rangka"] == VIN_X][0]
    assert x["model"] is None and "tak dikenal" in x["catatan"]


def test_semua_unit_gagal_tak_mengarang(per_vin):
    r = ai._t_part_fast_moving({"rangka": [VIN_X]}, ADMIN)
    assert r["found"] is False and "JANGAN mengarang" in r["catatan"]
    assert "part_aus_dari_rangka" in r["catatan"]      # tawarkan jalur per-VIN


def test_populasi_didahulukan_karena_kunci_dataset(patched, monkeypatch):
    """Dataset ber-kunci kode model POPULASI. Kode EPC memakai sandi lain yang
    menyertakan konfigurasi ('ZZ1317V466JE1R/27F7Q46-BZ' — kasus nyata pemilik):
    kalau EPC didahulukan, unit yang datanya ADA justru dijawab 'belum ada'."""
    monkeypatch.setattr(ai, "_configs_rangka",
                        lambda rgs: {r: {"model": "ZZ1317V466JE1R/27F7Q46-BZ"} for r in rgs})
    monkeypatch.setattr(fast_moving, "peta_populasi",
                        lambda: {VIN_A[-8:]: {"model": "ZZ3257V404JF1",
                                              "jenis": "HOWO-NX 6X4", "tahun": "2022"}})
    r = ai._t_part_fast_moving({"rangka": [VIN_A]}, ADMIN)
    assert r["found"] and r["model"] == "ZZ3257V404JF1"
    assert "populasi" in r["unit"][0]["sumber_model"]


def test_epc_jadi_jaring_bila_unit_tak_ada_di_populasi(patched, monkeypatch):
    """Unit di luar populasi (mis. milik pihak lain) masih tertolong EPC bila
    kode dasarnya kebetulan sama dengan kunci dataset."""
    monkeypatch.setattr(ai, "_configs_rangka",
                        lambda rgs: {r: {"model": "ZZ3257V404JF1/27F7Q46-BZ"} for r in rgs})
    monkeypatch.setattr(fast_moving, "peta_populasi", lambda: {})
    r = ai._t_part_fast_moving({"rangka": [VIN_A]}, ADMIN)
    assert r["found"] and r["model"] == "ZZ3257V404JF1"
    assert "EPC" in r["unit"][0]["sumber_model"]


def test_kategori_tetap_menyaring_di_jalur_rangka(per_vin):
    r = ai._t_part_fast_moving({"rangka": [VIN_A], "kategori": "rem"}, ADMIN)
    assert [s["kategori"] for s in r["slot"]] == ["rem"]


def test_hp_dari_kode_mesin_bukan_tebakan_kode_model(per_vin):
    """Pemilik memergoki 'HOWO-NX 8X4, 480 HP' untuk SJ346500 padahal mesinnya
    WP12.400E201 = 400 HP — digit kode model BUKAN tenaga."""
    r = ai._t_part_fast_moving({"rangka": [VIN_A, VIN_B]}, ADMIN)
    assert r["hp"] == 400 and "kode mesin" in r["hp_sumber"]
    assert r["unit"][0]["mesin"] == "WP12.400E201"
    assert r["unit"][0]["hp"] == 400
    # tebakan kode model tetap dibawa, TAPI berlabel & dilarang disebut
    assert r["hp_perkiraan_kode_model"] == 400          # kebetulan sama di fixture
    assert "JANGAN sebut" in r["hp_perkiraan_catatan"]


def test_hp_beda_antar_unit_disebut_per_unit(per_vin, monkeypatch):
    monkeypatch.setattr(ai, "_configs_rangka", lambda rgs: {
        VIN_A: {"model": "ZZ3257V404JF1", "mesin": "WP12.400E201"},
        VIN_B: {"model": "ZZ3257V404JF1", "mesin": "WP13.480E501"}})
    r = ai._t_part_fast_moving({"rangka": [VIN_A, VIN_B]}, ADMIN)
    assert "hp" not in r                                # ⛔ jangan pilih salah satu
    assert r["hp_per_unit"] == {VIN_A: 400, VIN_B: 480}
    assert "BEDA antar unit" in r["hp_sumber"]


def test_hp_model_tanpa_rangka_ditandai_perkiraan(patched):
    r = ai._t_part_fast_moving({"model": "ZZ3257V404JF1"}, ADMIN)
    assert r["hp"] == 400 and "perkiraan" in r["hp_sumber"]
    assert "PERKIRAAN" in r["catatan"] and "kode mesin" in r["catatan"]


def test_hp_dari_mesin_parser():
    assert fast_moving.hp_dari_mesin("WP12.400E201发动机") == 400
    assert fast_moving.hp_dari_mesin("WP13.530E501") == 530
    assert fast_moving.hp_dari_mesin("MC11.42-50") == 420
    assert fast_moving.hp_dari_mesin("MT13.54") == 540
    assert fast_moving.hp_dari_mesin("HW19709XST变速箱") is None    # gearbox ≠ mesin
    assert fast_moving.hp_dari_mesin("") is None


def test_tanpa_model_dan_tanpa_rangka_minta_keduanya(patched):
    r = ai._t_part_fast_moving({}, ADMIN)
    assert "nomor rangka" in r["error"] and "NX400" in r["error"]


def test_slot_dipotong_agar_hasil_tak_terpangkas_diam_diam(monkeypatch, patched):
    """Model terbesar produksi = 31 KB JSON, plafon isi tool 24 KB → tanpa
    plafon slot, hasilnya dipotong di TENGAH dan slot hilang tanpa jejak."""
    besar = {"jenis": "HOWO NX 8X4", "hp": 460, "unit_populasi": 77,
             "unit_sampel": ["SJ1"], "n_sampel": 1,
             "slot": [{"kategori": "filter", "slot": f"filter {i:03d}", "varian": [
                 {"pn": f"VG61000{i:06d}", "nama": f"Filter {i}", "qty": 1,
                  "n_unit": 1, "tahun": [], "pn_sub": [], "pengganti": []}]}
                 for i in range(ai._FM_MAX_SLOT + 12)]}
    monkeypatch.setattr(fast_moving, "data", lambda: {"model": {"ZZ1315N4666E1": besar}})
    r = ai._t_part_fast_moving({"model": "ZZ1315N4666E1"}, ADMIN)
    assert r["jumlah_slot"] == ai._FM_MAX_SLOT + 12          # total tetap jujur
    assert len(r["slot"]) == ai._FM_MAX_SLOT
    assert r["slot_tak_ditampilkan"] == 12
    assert "TIDAK ditampilkan" in r["catatan"] and "excel=true" in r["catatan"]
    # Excel memuat SEMUANYA (itulah jalan keluar yang ditawarkan catatan).
    r2 = ai._t_part_fast_moving({"model": "ZZ1315N4666E1", "excel": True}, ADMIN)
    assert r2["jumlah_baris"] == ai._FM_MAX_SLOT + 12 and r2["export_id"]


def test_urutan_kategori_ikut_frekuensi_servis(patched):
    """Yang tampil duluan di chat harus yang paling sering diservis."""
    r = ai._t_part_fast_moving({"model": "ZZ3257V404JF1"}, ADMIN)
    assert [s["kategori"] for s in r["slot"]] == ["filter", "rem", "karet"]


# ── Nama lapangan: urutan tampil & peleburan slot kembar ────────────────────
_DATA_ID = {"model": {"ZZ9": {
    "jenis": "HOWO NX 8X4", "hp": 460, "unit_populasi": 77,
    "unit_sampel": ["A", "B"], "n_sampel": 2, "slot": [
        # sengaja diacak & dipecah per assembly seperti keluaran builder asli
        {"kategori": "filter", "slot": "air drying chamber",
         "nama_id": "tabung air dryer (pengering angin)", "varian": [
             {"pn": "WG9000360521+001", "nama": "Air drying chamber", "qty": 1,
              "n_unit": 2, "tahun": [], "pn_sub": [], "pengganti": []}]},
        {"kategori": "filter", "slot": "fuel coarse filter — fuel tank",
         "nama_id": "filter solar kasar (bawah)", "varian": [
             {"pn": "WG9925550182", "nama": "Fuel coarse filter element", "qty": 1,
              "n_unit": 2, "tahun": [], "pn_sub": [], "pengganti": []}]},
        {"kategori": "filter", "slot": "fuel coarse filter — engine",
         "nama_id": "filter solar kasar (bawah)", "varian": [
             {"pn": "WG9925550182", "nama": "Fuel coarse filter element", "qty": 1,
              "n_unit": 2, "tahun": [], "pn_sub": [], "pengganti": []},
             {"pn": "WG9925550180", "nama": "Fuel coarse filter", "qty": 1,
              "n_unit": 1, "tahun": [], "pn_sub": [], "pengganti": []}]},
        {"kategori": "filter", "slot": "oil filter element component",
         "nama_id": "filter oli mesin", "varian": [
             {"pn": "080V05504-6096", "nama": "Oil filter element component",
              "qty": 1, "n_unit": 2, "tahun": [], "pn_sub": [], "pengganti": []}]},
    ]}}}


@pytest.fixture
def patched_id(monkeypatch):
    monkeypatch.setattr(fast_moving, "data", lambda: dict(_DATA_ID))
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: True)


def test_nama_lapangan_urut_kepentingan_servis(patched_id):
    """Filter oli & solar HARUS di atas air dryer — itu yang dicari user."""
    r = ai._t_part_fast_moving({"model": "ZZ9"}, ADMIN)
    assert [s.get("nama_lapangan") for s in r["slot"]] == [
        "filter oli mesin", "filter solar kasar (bawah)",
        "tabung air dryer (pengering angin)"]


def test_slot_kembar_dilebur_jadi_satu_baris(patched_id):
    """Builder memecah slot per assembly induk; di chat itu tampak seperti
    daftar berulang. Dilebur jadi satu baris + PN unik."""
    r = ai._t_part_fast_moving({"model": "ZZ9"}, ADMIN)
    solar = [s for s in r["slot"] if s.get("nama_lapangan") == "filter solar kasar (bawah)"][0]
    assert [v["pn"] for v in solar["varian"]] == ["WG9925550182", "WG9925550180"]
    assert len(solar["slot_epc"]) == 2      # jejak slot EPC aslinya tetap ada
    assert r["jumlah_slot"] == 3            # 4 slot dataset → 3 baris tampil
