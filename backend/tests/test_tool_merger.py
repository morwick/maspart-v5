"""Fase 4 rombakan: 4 pasang tool kembar Sinotruk↔Weichai dilebur — nama sisi
Sinotruk + param `sumber` ('atlas'|'mesin'|auto) + AUTO-FALLBACK silang
(menyerang mode gagal produksi 33%: model salah pilih sisi). Shim nama lama
tetap jalan (dispatch + allow-list)."""
from __future__ import annotations

from app.services import ai_assistant as ai

U = {"username": "mas", "role": "admin"}


def test_spec_hanya_4_gabungan_dispatch_tetap_8():
    names = [t["function"]["name"] for t in ai._tool_specs(U)]
    for lama in ("gambar_exploded_mesin", "katalog_mesin", "uraikan_mesin", "repair_kit_mesin"):
        assert lama not in names           # spec lama tak ditawarkan lagi
        assert lama in ai._DISPATCH        # tapi tetap bisa dieksekusi (shim)
        assert lama in ai._allowed_tool_names(U)  # alias legacy tetap SAH
    for baru in ("gambar_exploded", "katalog_kategori", "uraikan_assembly", "repair_kit_transmisi"):
        assert baru in names
    # param sumber terdaftar di spec gabungan
    ge = next(t for t in ai._tool_specs(U) if t["function"]["name"] == "gambar_exploded")
    assert "sumber" in ge["function"]["parameters"]["properties"]


def test_router_gambar_sumber_mesin(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": True, "figures": [{"svg": "S", "balon": 1, "nama": "Turbo",
                                    "kategori": "", "jumlah_item": 1,
                                    "items_ringkas": []}]})
    r = ai._t_gambar_exploded({"rangka": "RJ1", "pn": "X", "sumber": "mesin"}, U)
    assert r["found"] and "MESIN" in r["catatan"]


def test_router_gambar_auto_fallback_silang(monkeypatch):
    # Atlas: PN tak ketemu → otomatis coba sisi mesin Weichai → ketemu.
    monkeypatch.setattr(ai.epc_bom, "exploded_figures", lambda r, p, k: {
        "found": False, "_err": "not_in_category", "message": "tak ada"})
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": True, "figures": [{"svg": "S", "balon": 3, "nama": "Blok",
                                    "kategori": "", "jumlah_item": 1,
                                    "items_ringkas": []}]})
    r = ai._t_gambar_exploded({"rangka": "RJ1", "pn": "X", "kategori": "mesin"}, U)
    assert r["found"] and r["sumber_dipakai"] == "mesin_weichai"
    assert "otomatis dialihkan" in r["catatan"]


def test_router_gambar_sumber_atlas_tanpa_fallback(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "exploded_figures", lambda r, p, k: {
        "found": False, "_err": "not_in_category", "message": "tak ada"})

    def _boom(r, p, k):
        raise AssertionError("sumber='atlas' tidak boleh fallback ke Weichai")

    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", _boom)
    r = ai._t_gambar_exploded(
        {"rangka": "RJ1", "pn": "X", "kategori": "rem", "sumber": "atlas"}, U)
    assert r["found"] is False


def test_router_uraikan_sumber_mesin_map_assembly_ke_part(monkeypatch):
    seen = {}

    def fake_find(r, t):
        seen["part"] = t
        return {"found": True, "engine": {"nama": "WP12", "model": "X", "order": "O"},
                "hasil": [{"pn": "1000000001", "nama": "Turbocharger", "group": "Turbo"}]}

    monkeypatch.setattr(ai.epc_weichai, "find_parts", fake_find)
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    r = ai._t_uraikan_assembly(
        {"rangka": "SJ346500", "assembly": "turbo", "sumber": "mesin"}, U)
    # arg assembly → part (impl mengekspansi istilah jadi daftar sinonim)
    assert r["found"] and "turbo" in seen["part"]


def test_router_repair_kit_sumber_mesin(monkeypatch):
    dipanggil = {}

    def fake_mesin(args, user):
        dipanggil["ok"] = True
        return {"found": True, "hasil": []}

    monkeypatch.setattr(ai, "_repair_kit_mesin_impl", fake_mesin)
    r = ai._t_repair_kit_transmisi({"rangka": "RJ1", "sumber": "mesin"}, U)
    assert r["found"] and dipanggil.get("ok")


def test_shim_nama_lama_tetap_jalan(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "exploded_figures", lambda r, p, k: {
        "found": False, "_err": "no_link"})
    r = ai._t_gambar_exploded_mesin({"rangka": "RJ1", "pn": "X"}, U)
    assert r["found"] is False and "Weichai" in r["error"]
