"""Integritas data `fault_codes.json` (hasil `tools/build_fault_codes.py` dari PDF).

Mengunci kualitas ekstraksi SPN/FMI. Catatan penting: banyak entri ber-`SPN=0,
FMI=0` (placeholder `P0000` modul AC + kode CAN/ECU-internal seperti P1083)
MEMANG tertulis `0` di PDF sumber — itu FAITHFUL, bukan cacat ekstraksi. Yang
dulu cacat = beberapa SPN korup (mis. 526512/526535) hasil salah-gabung baris
lintas-halaman → kini `null`.
"""
from app.services import fault_codes


def _all():
    return fault_codes._load()


def test_cakupan_wajar():
    assert fault_codes.count() > 2000


def test_tak_ada_spn_fmi_korup():
    for r in _all():
        if r["spn"] is not None:
            assert 0 <= r["spn"] <= 524287, f"SPN korup: {r['code']}={r['spn']}"
        if r["fmi"] is not None:
            assert 0 <= r["fmi"] <= 31, f"FMI di luar rentang: {r['code']}={r['fmi']}"
    # SPN kosong (None, hasil pembersihan korup) harus sedikit.
    assert sum(1 for r in _all() if r["spn"] is None) <= 30


def test_lookup_spn_fmi_terkenal():
    hits = fault_codes.search(spn=1551, fmi=5)
    assert any(h["code"] == "P0645" for h in hits)


def test_lookup_kode_pcode():
    hits = fault_codes.search(code="P0300")
    assert hits and any(h["spn"] == 1322 for h in hits)


def test_field_lengkap():
    for r in _all()[:200]:
        assert set(r) >= {"code", "english", "desc_cn", "spn", "fmi", "mil", "svs"}
        assert r["mil"] in ("ON", "OFF") and r["svs"] in ("ON", "OFF")
        assert r["code"] and r["code"][0] in "PU"
