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


def test_relogin_saat_token_basi(cfg_on, monkeypatch):
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
            return R({"code": 401, "message": "expired"})   # token basi
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
