"""Pegas daun/suspensi: Atlas otoritatif atas Loading List (kasus nyata PJ306941
WG9114520140 LL usang vs WG9525520641 Atlas — ground truth screenshot EPC user).
EPC di-mock."""
import pytest

from app.services import ai_assistant as ai

U = {"username": "mas", "role": "admin"}


def test_atlas_modules_pegas_bukan_axle():
    # Query pegas → is_axle False (tanpa posisi palsu & tanpa auto-gambar gardan).
    # Termasuk token EN pendek 'spring'/'leaf' yang kerap dipakai model — dulu
    # jatuh ke poros → gambar 'Drive device' nyasar (regresi screenshot user).
    for q in ("per daun", "pegas daun", "leaf spring", "plate spring", "suspensi",
              "spring", "leaf"):
        mods, is_axle = ai._atlas_modules_for(q)
        assert is_axle is False, q
    # Kontrol: query poros biasa tetap axle.
    assert ai._atlas_modules_for("kampas rem")[1] is True


def test_bom_pegas_bawa_pn_assembly_atlas(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "loading_list", lambda r: {
        "found": True, "frame_number": "PJ306941", "jumlah_part": 3,
        "parts": [{"pn": "WG9114520140", "nama_cn": "前钢板弹簧总成", "qty": 2},
                  {"pn": "WG9525525152", "nama_cn": "后钢板弹簧总成", "qty": 2}]})
    monkeypatch.setattr(ai.epc_bom, "atlas_find_in_tree", lambda r, kws: {
        "found": True, "parts": [
            {"pn": "WG9525520641", "nama": "Front right plate spring assembly", "nama_cn": ""},
            {"pn": "WG9525525152", "nama": "Rear plate spring assembly", "nama_cn": ""}]})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai.catalog_bom, "available", lambda: False)

    r = ai._t_bom_dari_rangka({"rangka": "PJ306941", "kata_kunci": "per daun"}, U)
    assert r["found"]
    aa = r["pn_assembly_atlas_otoritatif"]
    assert any(x["part_number"] == "WG9525520641" for x in aa)   # PN Atlas yg benar tersedia
    assert "peringatan_assembly_atlas" in r
    assert "USANG" in r["peringatan_assembly_atlas"]             # LL dilarang jadi PN utama


def test_bom_pegas_atlas_kosong_jangan_mengarang(monkeypatch):
    # Prinsip user: bila Atlas tak ketemu → jawab tak ketemu, JANGAN menambal dari LL.
    monkeypatch.setattr(ai.epc_bom, "loading_list", lambda r: {
        "found": True, "frame_number": "X", "jumlah_part": 1,
        "parts": [{"pn": "WG9114520140", "nama_cn": "前钢板弹簧总成", "qty": 2}]})
    monkeypatch.setattr(ai.epc_bom, "atlas_find_in_tree", lambda r, kws: {"found": False})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai.catalog_bom, "available", lambda: False)

    r = ai._t_bom_dari_rangka({"rangka": "X", "kata_kunci": "leaf spring"}, U)
    assert "pn_assembly_atlas_otoritatif" not in r
    assert "TIDAK ditemukan" in r["peringatan_assembly_atlas"]
    assert "JANGAN mengarang" in r["peringatan_assembly_atlas"]


def test_bom_nonpegas_tanpa_peringatan(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "loading_list", lambda r: {
        "found": True, "frame_number": "X", "jumlah_part": 1,
        "parts": [{"pn": "WG123", "nama_cn": "灯", "qty": 1}]})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai.catalog_bom, "available", lambda: False)
    r = ai._t_bom_dari_rangka({"rangka": "X", "kata_kunci": "lampu"}, U)
    assert "peringatan_assembly_atlas" not in r
