"""Test abs_scr_codes + integrasi tool cari_kode_kesalahan/diagnosa.

Fokus: (1) loader gzip data NYATA yang di-commit (abs_scr_codes.json.gz),
(2) lookup ABS via SPN/FMI + fallback jujur (FMI tak ada → semua FMI utk SPN),
(3) lookup SCR via kode P (eksak → prefix) + query Indonesia + filter unit,
(4) teks SUDAH Bahasa Indonesia (bukan English mentah),
(5) handler cari_kode_kesalahan & diagnosa memuat hasil_abs_scr.
"""
from app.services import abs_scr_codes as asc
from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}


# ── data nyata ter-commit ───────────────────────────────────────────────
def test_data_tersedia_dan_cakupan():
    assert asc.available()
    assert asc.count() >= 200
    u = asc.units()
    assert "ABS" in u and "SCR" in u


def test_abs_lookup_spn_fmi_ada_perbaikan():
    res = asc.search(spn=789, fmi=1)
    assert res and all(r["sistem"] == "ABS" for r in res)
    r = res[0]
    assert r["spn"] == 789 and r["fmi"] == 1
    assert r["perbaikan"].strip()          # ada langkah perbaikan
    assert "Periksa" in r["perbaikan"]      # sudah Bahasa Indonesia


def test_abs_fallback_fmi_tak_terdaftar():
    # FMI 99 tak valid → service balikan SEMUA FMI utk SPN itu (jujur, tak kosong)
    res = asc.search(spn=789, fmi=99)
    assert res and all(r["spn"] == 789 for r in res)


def test_scr_lookup_kode_p():
    res = asc.search(code="P0427")
    assert res and res[0]["sistem"] == "SCR" and res[0]["kode"] == "P0427"
    # SCR spesifik urea (bukan generik) juga ada
    assert asc.search(code="P1D06")


def test_scr_query_indonesia_dan_unit():
    res = asc.search(unit="SCR", query="urea")
    assert res and all(r["sistem"] == "SCR" for r in res)


def test_teks_sudah_indonesia():
    # tidak boleh ada frasa English mentah khas Repair Guide
    for r in asc.search(unit="ABS", limit=50):
        assert "Remark: A repair can be considered" not in r["perbaikan"]


# ── integrasi handler cari_kode_kesalahan ───────────────────────────────
def test_handler_abs_spn_fmi():
    r = ai._t_cari_kode_kesalahan({"spn": 789, "fmi": 1}, USER)
    assert r["found"]
    assert r["jumlah_cocok_abs_scr"] >= 1
    h = r["hasil_abs_scr"][0]
    assert h["sistem"] == "ABS" and h["perbaikan"].strip()


def test_handler_scr_code():
    r = ai._t_cari_kode_kesalahan({"code": "P1D06"}, USER)
    assert r["jumlah_cocok_abs_scr"] >= 1
    assert any(x["sistem"] == "SCR" for x in r["hasil_abs_scr"])


def test_handler_unit_abs():
    r = ai._t_cari_kode_kesalahan({"unit": "ABS", "query": "sensor kecepatan roda"}, USER)
    assert r["hasil_abs_scr"] and all(x["sistem"] == "ABS" for x in r["hasil_abs_scr"])


# ── integrasi handler diagnosa (SIMS di-stub agar offline & cepat) ──────
def test_handler_diagnosa_abs(monkeypatch):
    from app.services import sims_eol
    monkeypatch.setattr(sims_eol, "tanya", lambda q: {"found": False})
    r = ai._t_diagnosa({"spn": 789, "fmi": 1}, USER)
    assert r["found"]
    assert r["kode_kesalahan_abs_scr"] and r["kode_kesalahan_abs_scr"][0]["sistem"] == "ABS"


# ── spec/dispatch tak berubah (tetap satu tool) ─────────────────────────
def test_spec_dan_dispatch_tak_berubah():
    names = [s["function"]["name"] for s in ai._tool_specs(USER, "")]
    assert "cari_kode_kesalahan" in names and "diagnosa" in names
    assert "cari_kode_kesalahan" in ai._DISPATCH and "diagnosa" in ai._DISPATCH
