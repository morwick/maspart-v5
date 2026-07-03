"""Routing domain query → modul Atlas (_atlas_modules_for) — khususnya AKSESORI
TERPASANG DI MESIN (air compressor, alternator, starter): harus jatuh ke modul
MESIN (FDJ/FDJFJ), bukan default poros. Kasus nyata: 'air compressor unit
RJ345233' dulu jatuh ke poros → cuma ketemu pipa outlet, padahal Air Compressor
Group lengkap ada di EPC Weichai (butuh uraikan_mesin).
"""
from app.services import ai_assistant as ai


def _route(query: str):
    """Tiru alur _t_part_aus_dari_rangka: ekspansi sinonim → pilih modul."""
    terms, _ = ai._expand_query(query)
    ql = (query + " " + " ".join(terms)).lower()
    return ai._atlas_modules_for(ql)


def test_air_compressor_ke_modul_mesin():
    mods, is_axle = _route("air compressor")
    assert "FDJ" in mods and not is_axle


def test_kompresor_angin_ke_modul_mesin():
    # via sinonim.json: 'kompresor angin' → 'air compressor'
    mods, is_axle = _route("kompresor angin")
    assert "FDJ" in mods and not is_axle


def test_alternator_dan_starter_ke_modul_mesin():
    for q in ("alternator", "dinamo starter"):
        mods, is_axle = _route(q)
        assert "FDJ" in mods and not is_axle, q


def test_part_poros_tetap_ke_axle():
    for q in ("kampas rem", "baut roda"):
        mods, is_axle = _route(q)
        assert mods == ("CDQ", "QDQ") and is_axle, q


def test_aus_keywords_air_compressor_punya_istilah_china():
    # istilah China 空压机 penting agar match nama part EPC berbahasa China
    assert "空压机" in ai._AUS_KEYWORDS.get("air compressor", [])
