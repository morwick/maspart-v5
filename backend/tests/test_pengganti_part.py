"""Supersession/pengganti part: SIMS partEquivalentQuery (Sinotruk sasis) +
EPC Weichai (mesin) digabung di tool pengganti_part. Jaringan di-mock.
"""
from app.services import sims, ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


# ── sims.get_part_equivalents: klasifikasi ARAH (lama→pengganti) ─────────────
def test_sims_equivalents_arah(monkeypatch):
    recs = [
        # OLD1 sbg SEBELUM → penggantinya NEW1
        {"preSpGoodsNo": "OLD1+001/1", "preGoodsName": "Old One",
         "afterSpGoodsNo": "NEW1+121/1", "afterGoodsName": "New One"},
        # OLD1 sbg SESUDAH → ia menggantikan OLDX
        {"preSpGoodsNo": "OLDX", "preGoodsName": "X",
         "afterSpGoodsNo": "OLD1+001/1", "afterGoodsName": "Old One"},
    ]

    class FakeSF:
        @staticmethod
        def fetch_part_equivalents(pn, page_size=50):
            return recs

    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "_sf", FakeSF)
    r = sims.get_part_equivalents("OLD1+001/1")
    assert r["found"]
    assert [x["pn"] for x in r["digantikan_oleh"]] == ["NEW1+121/1"]
    assert [x["pn"] for x in r["menggantikan"]] == ["OLDX"]


def test_sims_equivalents_kosong(monkeypatch):
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "_sf", type("F", (), {"fetch_part_equivalents": staticmethod(lambda pn, page_size=50: [])}))
    assert sims.get_part_equivalents("ZZ")["found"] is False


# ── tool _t_pengganti_part: gabung SIMS + Weichai, silang stok lokal ─────────
def test_pengganti_gabung_dua_sumber(monkeypatch):
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {
        "found": True, "digantikan_oleh": [{"pn": "NEW1", "nama": "New One"}], "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {
        "found": True, "digantikan_oleh": [{"pn": "NEWENG", "tanggal": "2024", "tipe": "searah"}],
        "menggantikan": []})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [
        {"part_number": "NEW1", "part_name": "New One Local", "stok": "5", "harga": "Rp 10"}])
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert r["found"]
    pns = [x["part_number"] for x in r["digantikan_oleh"]]
    assert "NEW1" in pns and "NEWENG" in pns          # kedua sumber tergabung
    new1 = next(x for x in r["digantikan_oleh"] if x["part_number"] == "NEW1")
    assert new1["sumber"] == "SIMS" and new1["stok_total"] == "5"   # silang stok lokal
    eng = next(x for x in r["digantikan_oleh"] if x["part_number"] == "NEWENG")
    assert eng["sumber"] == "Weichai" and eng.get("catatan")        # tak ada di katalog lokal
    assert set(r["sumber"]) == {"SIMS", "Weichai"}


def test_pengganti_dedup(monkeypatch):
    # PN pengganti sama muncul di SIMS & Weichai → hanya sekali.
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {
        "found": True, "digantikan_oleh": [{"pn": "SAME", "nama": "S"}], "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {
        "found": True, "digantikan_oleh": [{"pn": "SAME"}], "menggantikan": []})
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])
    r = ai._t_pengganti_part({"part_number": "OLD"}, ADMIN)
    assert [x["part_number"] for x in r["digantikan_oleh"]] == ["SAME"]


def test_pengganti_kosong(monkeypatch):
    monkeypatch.setattr(ai.sims, "get_part_equivalents", lambda pn: {"found": False})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    r = ai._t_pengganti_part({"part_number": "XX"}, ADMIN)
    assert not r["found"] and "persamaan" in r["error"].lower()


def test_pengganti_butuh_pn():
    assert "error" in ai._t_pengganti_part({}, ADMIN)
