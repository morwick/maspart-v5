"""Telematics/GPS armada Sinotruk — service + 3 tool asisten admin-only.

Terverifikasi live 2026-07-22 (login 200, 257 unit). Yang dikunci:
- Auth RSA: getPublicKey(id) → encrypt → login(encryptId) → tokenKey; header
  Authorization = tokenKey MENTAH (tanpa Bearer); re-login saat token basi.
- Gerbang ADMIN-ONLY (bukan Menu Control): staf/pembeli tak pernah dapat.
- ganti_nama_unit = WRITE 2 langkah: tanpa konfirmasi TIDAK menulis.
- Data GPS ≠ EPC ≠ populasi (guardrail deskripsi).
"""
import pytest

import app.services.telematics as t
from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "toko", "role": "pembeli"}

_UNIT = {
    "cjh": "PC531850", "vin": "LZZ7CLXB5PC531850", "carNumber": None,
    "organizations": [{"id": 623, "organizationName": "MAS"}],
    "status": "running", "model": "ZZ4256V364HE1R", "brandEdition": "C7H",
    "gearboxType": "ZF16S2531TO", "tireType": "0.608", "driveForm": "6×4",
    "engine": "MC13.54-50", "mileage": 73469.9,
}
_UNIT2 = {**_UNIT, "cjh": "PJ264972", "carNumber": "Truk A",
          "vin": "SLGV0123456789888",  # dummy → harus disembunyikan
          "organizations": [{"id": 625, "organizationName": "JNT"}]}
_LOC = {"cjh": "PC531850", "status": "running", "totalMileage": 73470.0,
        "fuelLevel": 95, "isFaulty": False, "lat": -3.69, "lng": 121.05,
        "revdatetime": "2026-07-22 14:53"}


@pytest.fixture(autouse=True)
def _reset_token():
    t._token = None
    t._token_exp = 0.0
    yield


@pytest.fixture
def cfg_on(monkeypatch):
    class S:
        telematics_configured = True
        telematics_username = "PT_MAS_2026"
        telematics_password = "rahasia"
    monkeypatch.setattr(t, "get_settings", lambda: S())
    return S


# ── auth ─────────────────────────────────────────────────────────────
def test_login_flow_rsa_dan_header_tanpa_bearer(cfg_on, monkeypatch):
    monkeypatch.setattr(t, "_rsa_encrypt", lambda pk, pw: "ENC")
    calls = []

    class R:
        def __init__(self, payload):
            self._p = payload
            self.status_code = 200
        def json(self):
            return self._p

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append((url, data, headers))
        if url.endswith("/getPublicKey"):
            return R({"code": 200, "data": {"id": 2, "publicKey": "KEY"}})
        if url.endswith("/auth/login"):
            assert data["encryptId"] == 2 and data["password"] == "ENC"
            return R({"code": 200, "data": {"tokenKey": "HEXTOKEN"}})
        # panggilan berproteksi → header Authorization = token mentah
        assert headers["Authorization"] == "HEXTOKEN"
        return R({"code": 200, "data": {"ok": True}})
    monkeypatch.setattr(t.requests, "post", fake_post)
    assert t._post("/api/x", {}) == {"ok": True}
    assert any("getPublicKey" in c[0] for c in calls)


@pytest.mark.parametrize("kode_basi", [401, 350])
def test_relogin_saat_token_basi(cfg_on, monkeypatch, kode_basi):
    """Re-login pada 401 (umum) DAN 350 (telematics 'session invalid',
    single-session) — produksi 2026-07-22 gagal karena 350 tak ditangani."""
    monkeypatch.setattr(t, "_rsa_encrypt", lambda pk, pw: "ENC")
    seq = {"n": 0}

    class R:
        def __init__(self, payload, code=200):
            self._p = payload
            self.status_code = code
        def json(self):
            return self._p

    def fake_post(url, data=None, headers=None, timeout=None):
        if url.endswith("/getPublicKey"):
            return R({"data": {"id": 1, "publicKey": "K"}})
        if url.endswith("/auth/login"):
            return R({"code": 200, "data": {"tokenKey": f"TOK{seq['n']}"}})
        seq["n"] += 1
        if seq["n"] == 1:
            return R({"code": kode_basi, "message": "session invalid"})
        return R({"code": 200, "data": [1, 2]})
    monkeypatch.setattr(t.requests, "post", fake_post)
    assert t._post("/api/y", {}) == [1, 2]                   # sukses setelah re-login


# ── data helpers ─────────────────────────────────────────────────────
def test_rangkum_unit_sembunyikan_dummy_vin():
    u = t.rangkum_unit(_UNIT2, _LOC)
    assert u["vin"] is None                                  # dummy dibuang
    assert u["nama"] == "Truk A"
    assert u["fleet"] == ["JNT"]


def test_rangkum_unit_status_gps():
    u = t.rangkum_unit(_UNIT, _LOC)
    assert u["status_gps"] == "Jalan"
    assert u["bbm_persen"] == 95 and u["rusak"] is False
    assert u["posisi"]["lat"] == -3.69


def test_fleet_breakdown():
    b = t.fleet_breakdown([_UNIT, _UNIT2, dict(_UNIT)])
    d = {x["fleet"]: x["jumlah"] for x in b}
    assert d["MAS"] == 2 and d["JNT"] == 1


def test_semua_unit_filter_fleet(cfg_on, monkeypatch):
    monkeypatch.setattr(t, "_semua_records", lambda **kw: [_UNIT, _UNIT2])
    d = t.semua_unit(fleet="jnt")
    assert d["total"] == 1 and d["records"][0]["cjh"] == "PJ264972"


# ── tool: lihat_unit_armada ──────────────────────────────────────────
@pytest.fixture
def tele_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.telematics, "semua_unit",
                        lambda fleet="": {"total": 2, "records": [_UNIT, _UNIT2]})
    monkeypatch.setattr(ai.telematics, "lokasi_semua", lambda *a, **k: {"PC531850": _LOC})


def test_lihat_unit_armada_ringkasan(tele_on):
    r = ai._t_lihat_unit_armada({}, ADMIN)
    assert r["found"] and r["total_armada"] == 2
    assert any(f["fleet"] == "MAS" for f in r["per_fleet"])
    assert "GPS" in r["catatan"]
    assert list(r.keys())[-1] == "catatan"


def test_lihat_unit_armada_hanya_rusak(tele_on, monkeypatch):
    loc_rusak = {**_LOC, "isFaulty": True}
    monkeypatch.setattr(ai.telematics, "lokasi_semua", lambda *a, **k: {"PC531850": loc_rusak})
    r = ai._t_lihat_unit_armada({"hanya_rusak": True}, ADMIN)
    assert all(u.get("rusak") for u in r["unit"])
    assert len(r["unit"]) == 1


def test_lihat_unit_armada_admin_only(tele_on):
    for u in (STAF, PEMBELI):
        assert "error" in ai._t_lihat_unit_armada({}, u)


def test_lihat_unit_lookup_satu_unit_nama(tele_on, monkeypatch):
    """Produksi: 'cek nama SJ398956' dijawab 'tidak ada' padahal unitnya ADA —
    lihat_unit_armada tak punya lookup per-frame (cuma daftar cap-60). Param
    'unit' menjawab nama unit spesifik."""
    rec = {**_UNIT2, "cjh": "SJ398956", "carNumber": "JNT-B 9526 UEY"}
    monkeypatch.setattr(ai.telematics, "cari_unit",
                        lambda q: rec if q == "SJ398956" else None)
    r = ai._t_lihat_unit_armada({"unit": "SJ398956"}, ADMIN)
    assert r["found"] is True
    assert r["nama"] == "JNT-B 9526 UEY"
    assert r["unit"]["frame"] == "SJ398956"
    assert list(r.keys())[-1] == "catatan"


def test_lihat_unit_lookup_tak_ada_jujur(tele_on, monkeypatch):
    monkeypatch.setattr(ai.telematics, "cari_unit", lambda q: None)
    r = ai._t_lihat_unit_armada({"unit": "XX000000"}, ADMIN)
    assert r["found"] is False and "tidak ditemukan" in r["catatan"].lower()


# ── tool: ganti_nama_unit (2 langkah) ────────────────────────────────
@pytest.fixture
def rename_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.telematics, "cari_unit", lambda q: dict(_UNIT))
    tulis = {"n": 0}

    def fake_ganti(cjh, nama):
        tulis["n"] += 1
        return {"cjh": cjh, "carNumber": nama}
    monkeypatch.setattr(ai.telematics, "ganti_nama", fake_ganti)
    return tulis


def test_ganti_nama_langkah1_pratinjau_tak_menulis(rename_on):
    r = ai._t_ganti_nama_unit({"cjh": "PC531850", "nama_baru": "Dump 12"}, ADMIN)
    assert r["perlu_konfirmasi"] is True
    assert r["pratinjau"]["nama_baru"] == "Dump 12"
    assert rename_on["n"] == 0                               # BELUM menulis
    assert "KONFIRMASI" in r["catatan"]


def test_ganti_nama_langkah2_eksekusi(rename_on):
    r = ai._t_ganti_nama_unit(
        {"cjh": "PC531850", "nama_baru": "Dump 12", "konfirmasi": True}, ADMIN)
    assert r.get("berhasil") is True and r["nama_baru"] == "Dump 12"
    assert rename_on["n"] == 1                               # menulis sekali


def test_ganti_nama_admin_only(rename_on):
    r = ai._t_ganti_nama_unit(
        {"cjh": "X", "nama_baru": "Y", "konfirmasi": True}, STAF)
    assert "error" in r and rename_on["n"] == 0              # non-admin tak menulis


# ── tool: excel_unit_armada ──────────────────────────────────────────
def test_excel_unit_armada(tele_on, monkeypatch):
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda judul, kolom, baris: ("EXP1", "armada.xlsx"))
    r = ai._t_excel_unit_armada({}, ADMIN)
    assert r["found"] and r["export_id"] == "EXP1"
    assert r["jumlah_baris"] == 2
    assert list(r.keys())[-1] == "catatan"


def test_excel_unit_armada_admin_only(tele_on):
    assert "error" in ai._t_excel_unit_armada({}, STAF)


# ── gerbang & integrasi ──────────────────────────────────────────────
def test_spec_admin_only():
    na = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "")}
    ns = {s["function"]["name"] for s in ai._tool_specs(STAF, "")}
    npb = {s["function"]["name"] for s in ai._tool_specs(PEMBELI, "")}
    for tool in ("lihat_unit_armada", "excel_unit_armada", "ganti_nama_unit"):
        assert tool in na and tool not in ns and tool not in npb


def test_run_tool_menolak_non_admin(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    r = ai._run_tool("lihat_unit_armada", {}, STAF)
    assert r.get("denied") or "admin" in (r.get("error") or "").lower()


def test_excel_armada_terdaftar_kartu():
    # Part file dimuat via exec ke namespace ai (bukan modul mandiri) → baca
    # sumbernya dari path, bukan import. Pastikan kartu Excel armada terdaftar.
    from pathlib import Path
    p = (Path(ai.__file__).parent / "ai_parts" / "p9_chat_loop.py")
    assert "excel_unit_armada" in p.read_text(encoding="utf-8")


def test_telematics_off_pesan_jujur(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: False)
    r = ai._t_lihat_unit_armada({}, ADMIN)
    assert "belum dikonfigurasi" in r["error"]


# ── isi nama massal dari Excel (sheet_isi_nama_telematik) ────────────
_SHEET = {
    "headers": ["No Rangka", "Nama"],
    "_body": [
        ["SJ398957", "JNT - B 9542 UEY"],   # sudah sama
        ["SJ398958", "Nama Baru A"],         # berubah
        ["PC531850", "Truk C7H"],            # berubah (kosong→ada)
        ["XX000000", "Unit Hantu"],          # tak ada di telematics
    ],
}
_TELE_RECS = [
    {"cjh": "SJ398957", "vin": "V1", "carNumber": "JNT - B 9542 UEY",
     "organizations": [{"id": 625, "organizationName": "JNT"}]},
    {"cjh": "SJ398958", "vin": "V2", "carNumber": "Lama B",
     "organizations": [{"id": 625, "organizationName": "JNT"}]},
    {"cjh": "PC531850", "vin": "V3", "carNumber": None,
     "organizations": [{"id": 623, "organizationName": "MAS"}]},
]


@pytest.fixture
def massal_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: dict(_SHEET))
    monkeypatch.setattr(ai.telematics, "semua_unit",
                        lambda fleet="": {"total": 3, "records": _TELE_RECS})
    tulis = []
    monkeypatch.setattr(ai.telematics, "ganti_nama",
                        lambda cjh, nama: (tulis.append((cjh, nama)) or {"cjh": cjh, "carNumber": nama}))
    return tulis


def test_isi_nama_massal_pratinjau_tak_menulis(massal_on):
    r = ai._t_sheet_isi_nama_telematik({"_sheet_id": "s1"}, ADMIN)
    assert r["perlu_konfirmasi"] is True
    assert r["ringkasan"] == {"total_baris": 4, "akan_berubah": 2,
                              "sudah_sama": 1, "tak_ada_di_telematics": 1}
    assert massal_on == []                         # BELUM menulis
    assert "KONFIRMASI" in r["catatan"]


def test_isi_nama_massal_terapkan_hanya_berubah(massal_on):
    r = ai._t_sheet_isi_nama_telematik({"_sheet_id": "s1", "konfirmasi": True}, ADMIN)
    assert r["selesai"] and r["diterapkan"] == 2
    assert r["dilewati_sama"] == 1 and r["dilewati_tak_ada"] == 1
    # hanya 2 yang berbeda ditulis; yang sudah sama & tak ada TIDAK
    assert set(massal_on) == {("SJ398958", "Nama Baru A"), ("PC531850", "Truk C7H")}


def test_isi_nama_massal_admin_only(massal_on):
    r = ai._t_sheet_isi_nama_telematik({"_sheet_id": "s1", "konfirmasi": True}, STAF)
    assert "error" in r and massal_on == []        # non-admin tak menulis


def test_isi_nama_massal_tanpa_lampiran(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: None)
    r = ai._t_sheet_isi_nama_telematik({"_sheet_id": ""}, ADMIN)
    assert r["found"] is False and "terlampir" in r["error"]


def test_isi_nama_massal_kolom_tak_terdeteksi(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: {
        "headers": ["Kolom X", "Kolom Y"], "_body": [["a", "b"]]})
    r = ai._t_sheet_isi_nama_telematik({"_sheet_id": "s1"}, ADMIN)
    assert r["found"] is False and "tidak terdeteksi" in r["catatan"]


def test_spec_isi_nama_massal_admin_only_saat_ada_sheet():
    na = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "sheet-1")}
    ns = {s["function"]["name"] for s in ai._tool_specs(STAF, "sheet-1")}
    assert "sheet_isi_nama_telematik" in na
    assert "sheet_isi_nama_telematik" not in ns


# ── daftarkan unit BARU (recordingVehicle) ───────────────────────────
@pytest.fixture
def daftar_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.telematics, "cari_unit", lambda q: None)  # belum ada
    tulis = []
    monkeypatch.setattr(ai.telematics, "daftarkan",
                        lambda sbh, vin, km=0, euro2=False:
                        (tulis.append((sbh, vin, km, euro2)) or {"cjh": vin[-8:]}))
    return tulis


def test_daftarkan_unit_langkah1_pratinjau_tak_menulis(daftar_on):
    r = ai._t_daftarkan_unit({"vin": "LZZ1BG3H1SJ399331", "sbh": "80741773",
                              "km": 100}, ADMIN)
    assert r["perlu_konfirmasi"] is True
    assert r["pratinjau"]["frame"] == "SJ399331"
    assert r["pratinjau"]["serial_gps"] == "80741773"
    assert r["pratinjau"]["sudah_terdaftar"] is False
    assert daftar_on == []                          # BELUM menulis


def test_daftarkan_unit_langkah2_eksekusi(daftar_on):
    r = ai._t_daftarkan_unit({"vin": "LZZ1BG3H1SJ399331", "sbh": "80741773",
                              "km": 100, "konfirmasi": True}, ADMIN)
    assert r.get("berhasil") is True and r["frame"] == "SJ399331"
    assert daftar_on == [("80741773", "LZZ1BG3H1SJ399331", 100, False)]


def test_daftarkan_unit_wajib_vin_dan_sbh(daftar_on):
    assert "error" in ai._t_daftarkan_unit({"vin": "X"}, ADMIN)   # tanpa sbh
    assert "error" in ai._t_daftarkan_unit({"sbh": "1"}, ADMIN)   # tanpa vin
    assert daftar_on == []


def test_daftarkan_unit_admin_only(daftar_on):
    r = ai._t_daftarkan_unit({"vin": "V", "sbh": "S", "konfirmasi": True}, STAF)
    assert "error" in r and daftar_on == []


def test_daftarkan_unit_sudah_ada_diperingatkan(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.telematics, "cari_unit", lambda q: {"cjh": "SJ399331"})
    r = ai._t_daftarkan_unit({"vin": "LZZ1BG3H1SJ399331", "sbh": "807"}, ADMIN)
    assert r["pratinjau"]["sudah_terdaftar"] is True
    assert "SUDAH ADA" in r["catatan"]


# ── daftar unit MASSAL dari Excel ────────────────────────────────────
_SHEET_DAFTAR = {
    "headers": ["VIN", "Serial GPS", "KM", "Euro2"],
    "_body": [
        ["LZZ1BG3H1SJ399331", "80741773", "100", ""],       # baru
        ["LZZ7CLXB5PC531850", "80615204", "500", "ya"],      # sudah ada (PC531850)
        ["LZZ1BG3H9SJ399999", "80741999", "", ""],           # baru
    ],
}


@pytest.fixture
def daftar_massal_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: dict(_SHEET_DAFTAR))
    monkeypatch.setattr(ai.telematics, "semua_unit", lambda fleet="": {
        "records": [{"cjh": "PC531850", "vin": "LZZ7CLXB5PC531850"}]})
    tulis = []
    monkeypatch.setattr(ai.telematics, "daftarkan",
                        lambda sbh, vin, km=0, euro2=False:
                        (tulis.append((vin, sbh, km, euro2)) or {"cjh": vin[-8:]}))
    return tulis


def test_sheet_daftar_pratinjau_tak_menulis(daftar_massal_on):
    r = ai._t_sheet_daftar_unit({"_sheet_id": "s1"}, ADMIN)
    assert r["perlu_konfirmasi"] is True
    assert r["ringkasan"] == {"total_baris": 3, "akan_didaftar": 2, "sudah_terdaftar": 1}
    assert daftar_massal_on == []                    # BELUM menulis


def test_sheet_daftar_eksekusi_hanya_baru(daftar_massal_on):
    r = ai._t_sheet_daftar_unit({"_sheet_id": "s1", "konfirmasi": True}, ADMIN)
    assert r["selesai"] and r["didaftar"] == 2 and r["dilewati_sudah_ada"] == 1
    vins = {t[0] for t in daftar_massal_on}
    assert vins == {"LZZ1BG3H1SJ399331", "LZZ1BG3H9SJ399999"}   # yang sudah ada tidak
    # euro2 & km terbaca dari kolom
    assert daftar_massal_on[0][2] == 100


def test_sheet_daftar_admin_only(daftar_massal_on):
    r = ai._t_sheet_daftar_unit({"_sheet_id": "s1", "konfirmasi": True}, STAF)
    assert "error" in r and daftar_massal_on == []


def test_spec_daftar_unit_admin_only():
    na = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "")}
    ns = {s["function"]["name"] for s in ai._tool_specs(STAF, "")}
    assert "daftarkan_unit" in na and "daftarkan_unit" not in ns
    na_sheet = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "s1")}
    assert "sheet_daftar_unit" in na_sheet


# ── masukkan unit ke FLEET (updateVehicleOrganization) ───────────────
_REC_FLEET = {"cjh": "NJ248278", "vin": "VNJ", "model": "HOWO",
              "organizations": [{"id": 623, "organizationName": "MAS"}]}
_FLEETS = [{"id": 623, "nama": "MAS", "parent_id": 1687},
           {"id": 625, "nama": "JNT", "parent_id": 623},
           {"id": 2313, "nama": "MITRAANGKUTAN", "parent_id": 623}]


@pytest.fixture
def fleet_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.telematics, "cari_unit",
                        lambda q: dict(_REC_FLEET) if q else None)
    monkeypatch.setattr(ai.telematics, "cari_fleet", lambda q: (
        {"id": 2313, "nama": "MITRAANGKUTAN"}
        if str(q).lower() in ("mitraangkutan", "2313") else None))
    tulis = []
    monkeypatch.setattr(ai.telematics, "masukkan_ke_fleet",
                        lambda tid, cars: (tulis.append((tid, cars)) or True))
    return tulis


def test_masukkan_fleet_langkah1_pratinjau_tak_menulis(fleet_on):
    r = ai._t_masukkan_unit_fleet({"unit": "NJ248278", "fleet": "MITRAANGKUTAN"}, ADMIN)
    assert r["perlu_konfirmasi"] is True
    assert r["pratinjau"]["fleet_tujuan"] == "MITRAANGKUTAN"
    assert r["pratinjau"]["fleet_sekarang"] == ["MAS"]
    assert fleet_on == []


def test_masukkan_fleet_langkah2_eksekusi(fleet_on):
    r = ai._t_masukkan_unit_fleet(
        {"unit": "NJ248278", "fleet": "MITRAANGKUTAN", "konfirmasi": True}, ADMIN)
    assert r.get("berhasil") is True
    assert fleet_on == [(2313, [{"cjh": "NJ248278", "organizationIds": [623]}])]


def test_masukkan_fleet_fleet_tak_ada(fleet_on):
    r = ai._t_masukkan_unit_fleet(
        {"unit": "NJ248278", "fleet": "TIDAKADA", "konfirmasi": True}, ADMIN)
    assert r["found"] is False and fleet_on == []


def test_masukkan_fleet_admin_only(fleet_on):
    r = ai._t_masukkan_unit_fleet(
        {"unit": "X", "fleet": "MITRAANGKUTAN", "konfirmasi": True}, STAF)
    assert "error" in r and fleet_on == []


# ── masukkan fleet MASSAL dari Excel ─────────────────────────────────
@pytest.fixture
def fleet_massal_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: {
        "headers": ["No Rangka", "Fleet"],
        "_body": [["NJ248278", "MITRAANGKUTAN"],   # ok
                  ["PC531850", "JNT"],              # ok
                  ["ZZ999999", "MITRAANGKUTAN"],    # unit tak ada
                  ["NJ248278", "FLEETHANTU"]]})     # fleet tak ada
    recs = [{"cjh": "NJ248278", "vin": "V1", "organizations": [{"id": 623, "organizationName": "MAS"}]},
            {"cjh": "PC531850", "vin": "V2", "organizations": [{"id": 623, "organizationName": "MAS"}]}]
    monkeypatch.setattr(ai.telematics, "semua_unit", lambda fleet="": {"records": recs})
    monkeypatch.setattr(ai.telematics, "daftar_fleet", lambda: _FLEETS)
    tulis = []
    monkeypatch.setattr(ai.telematics, "masukkan_ke_fleet",
                        lambda tid, cars: (tulis.append((tid, len(cars))) or True))
    return tulis


def test_sheet_fleet_pratinjau(fleet_massal_on):
    r = ai._t_sheet_masukkan_fleet({"_sheet_id": "s1"}, ADMIN)
    assert r["perlu_konfirmasi"] is True
    assert r["ringkasan"] == {"total_baris": 4, "akan_dipindah": 2,
                              "unit_tak_ada": 1, "fleet_tak_ada": 1}
    assert fleet_massal_on == []


def test_sheet_fleet_eksekusi_per_fleet(fleet_massal_on):
    r = ai._t_sheet_masukkan_fleet({"_sheet_id": "s1", "konfirmasi": True}, ADMIN)
    assert r["selesai"] and r["dipindah"] == 2
    # 2 fleet berbeda → 2 panggilan (masing-masing 1 unit)
    assert sorted(fleet_massal_on) == [(625, 1), (2313, 1)]


def test_sheet_fleet_admin_only(fleet_massal_on):
    r = ai._t_sheet_masukkan_fleet({"_sheet_id": "s1", "konfirmasi": True}, STAF)
    assert "error" in r and fleet_massal_on == []


def test_spec_masukkan_fleet_admin_only():
    na = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "s1")}
    ns = {s["function"]["name"] for s in ai._tool_specs(STAF, "s1")}
    assert "masukkan_unit_fleet" in na and "sheet_masukkan_fleet" in na
    assert "masukkan_unit_fleet" not in ns and "sheet_masukkan_fleet" not in ns


# ── BUAT fleet baru (adjustOrganization) ─────────────────────────────
def test_to_adjust_transform_schema():
    """queryOrganization node → adjustOrganization node (change=unchanged)."""
    node = {"organization": {"id": 625, "organizationName": "JNT", "parentId": 623,
                             "organizationType": 0, "locked": False},
            "isOnceUnlock": False, "isLockedPid": False, "canBeUnlock": True,
            "children": []}
    a = t._to_adjust(node)
    assert a["id"] == 625 and a["change"] == "unchanged"
    nn = a["detail"]["newNode"]
    assert nn == {"id": 625, "pid": 623, "label": "JNT", "organizationType": 0,
                  "isLockedPid": False, "isOnceUnlock": False, "isLocked": False,
                  "canBeUnlock": True}
    assert a["detail"]["oldNode"] == nn


def test_buat_fleet_service_kirim_tree_diff(cfg_on, monkeypatch):
    tree = {"organization": {"id": 623, "organizationName": "MAS", "parentId": 1687,
                             "organizationType": 0, "locked": False},
            "isOnceUnlock": False, "isLockedPid": None, "canBeUnlock": True,
            "children": [{"organization": {"id": 625, "organizationName": "JNT",
                          "parentId": 623, "organizationType": 0, "locked": False},
                          "isOnceUnlock": False, "isLockedPid": False,
                          "canBeUnlock": True, "children": []}]}
    monkeypatch.setattr(t, "_org_tree", lambda: tree)
    monkeypatch.setattr(t, "daftar_fleet", lambda: [
        {"id": 9001, "nama": "MITRAANGKUTAN", "parent_id": 623}])
    kirim = {}

    def fake_json(path, obj):
        kirim["path"] = path
        kirim["obj"] = obj
        return {"code": 200, "message": "ok"}
    monkeypatch.setattr(t, "_post_json", fake_json)
    r = t.buat_fleet("MITRAANGKUTAN")           # induk default = akar 623
    assert r == {"id": 9001, "nama": "MITRAANGKUTAN", "parent_id": 623}
    assert kirim["path"].endswith("adjustOrganization")
    # node baru 'added' tersisip di bawah 623 dgn id negatif
    added = kirim["obj"]["children"][-1]
    assert added["change"] == "added"
    assert added["detail"]["newNode"]["label"] == "MITRAANGKUTAN"
    assert added["detail"]["newNode"]["pid"] == 623
    assert added["id"] < 0
    # node lama tetap 'unchanged'
    assert kirim["obj"]["change"] == "unchanged"


@pytest.fixture
def buat_on(monkeypatch):
    monkeypatch.setattr(ai.telematics, "available", lambda: True)
    monkeypatch.setattr(ai.telematics, "cari_fleet", lambda q: (
        {"id": 623, "nama": "MAS"} if str(q).lower() == "mas" else None))
    monkeypatch.setattr(ai.telematics, "daftar_fleet", lambda: [
        {"id": 623, "nama": "MAS", "parent_id": 1687}])
    dibuat = []
    monkeypatch.setattr(ai.telematics, "buat_fleet",
                        lambda nama, pid=None: (dibuat.append((nama, pid)) or
                                                {"id": 9002, "nama": nama, "parent_id": pid}))
    return dibuat


def test_buat_fleet_langkah1_pratinjau_tak_menulis(buat_on):
    r = ai._t_buat_fleet({"nama": "CABANG BARU"}, ADMIN)
    assert r["perlu_konfirmasi"] is True
    assert r["pratinjau"]["nama_fleet"] == "CABANG BARU"
    assert r["pratinjau"]["sudah_ada"] is False
    assert buat_on == []


def test_buat_fleet_langkah2_eksekusi(buat_on):
    r = ai._t_buat_fleet({"nama": "CABANG BARU", "induk": "MAS", "konfirmasi": True}, ADMIN)
    assert r.get("berhasil") is True and r["id_fleet"] == 9002
    assert buat_on == [("CABANG BARU", 623)]


def test_buat_fleet_sudah_ada_diperingatkan(buat_on):
    r = ai._t_buat_fleet({"nama": "MAS"}, ADMIN)   # MAS sudah ada
    assert r["pratinjau"]["sudah_ada"] is True
    assert "SUDAH ADA" in r["catatan"]


def test_buat_fleet_admin_only(buat_on):
    r = ai._t_buat_fleet({"nama": "X", "konfirmasi": True}, STAF)
    assert "error" in r and buat_on == []


def test_spec_buat_fleet_admin_only():
    na = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "")}
    ns = {s["function"]["name"] for s in ai._tool_specs(STAF, "")}
    assert "buat_fleet" in na and "buat_fleet" not in ns
