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


# ── INDEKS persamaan penuh (paginasi) + lookup instan ───────────────────────
def test_equivalents_index(monkeypatch):
    page1 = [
        {"preSpGoodsNo": "OLD1+001/1", "preGoodsName": "Old", "afterSpGoodsNo": "NEW1", "afterGoodsName": "New"},
        {"preSpGoodsNo": "OLD2", "preGoodsName": "O2", "afterSpGoodsNo": "NEW2", "afterGoodsName": "N2"},
    ]

    def fake_page(page, page_size=500):
        return (page1, 2) if page == 1 else ([], 2)

    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "_sf", type("F", (), {"fetch_equivalents_page": staticmethod(fake_page)}))
    monkeypatch.setattr(sims, "_equiv_index", {"ts": 0.0, "by_pn": {}})
    n = sims.refresh_equivalents(force=True)
    assert n > 0
    # exact + base ('OLD1' tanpa suffix) sama-sama cocok
    assert [x["pn"] for x in sims.equivalents_for("OLD1+001/1")["digantikan_oleh"]] == ["NEW1"]
    assert [x["pn"] for x in sims.equivalents_for("OLD1")["digantikan_oleh"]] == ["NEW1"]
    # arah balik
    assert [x["pn"] for x in sims.equivalents_for("NEW1")["menggantikan"]] == ["OLD1+001/1"]
    assert sims.equivalents_for("TAKADA") == {}


# ── fix produksi 2026-07-17: tool pakai INDEKS hangat, bukan query live ─────
def test_pengganti_pakai_indeks_bukan_live(monkeypatch):
    # indeks siap → query live TIDAK boleh dipanggil (rentan timeout = gagal 45%)
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {
        "digantikan_oleh": [{"pn": "NEWIDX", "nama": "Baru"}], "menggantikan": []})

    def _boom(pn):
        raise AssertionError("query live tidak boleh dipanggil saat indeks siap")

    monkeypatch.setattr(ai.sims, "get_part_equivalents", _boom)
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    r = ai._t_pengganti_part({"part_number": "OLD1"}, ADMIN)
    assert r["found"]
    assert [x["part_number"] for x in r["digantikan_oleh"]] == ["NEWIDX"]


def test_pengganti_kosong_tetap_beri_info_pn(monkeypatch):
    # tak ada supersession TAPI PN dikenal katalog → sertakan info (bukan buntu).
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "WG9100443050": {"part_name": "Brake Disc", "stok": "3", "harga": "Rp 1.000"}})
    r = ai._t_pengganti_part({"part_number": "WG9100443050"}, ADMIN)
    assert r["found"] is False
    assert r["info_part_ditanya"]["stok_total"] == "3"
    assert "aktif" in r["info_part_ditanya"]["catatan"]


# ── cari_part menyisipkan 'pengganti' dari indeks ───────────────────────────
def test_cari_part_sisip_pengganti(monkeypatch):
    ROW = {"part_number": "OLDPN", "part_name": "Brake Pedal", "file": "NX360", "path": "Sinotruk/NX360"}
    monkeypatch.setattr(ai.part_index, "search_part_name", lambda t: [ROW])
    monkeypatch.setattr(ai.part_index, "search_part_number", lambda t: [])
    monkeypatch.setattr(ai.part_index, "correct_typos", lambda t: (t, []))
    monkeypatch.setattr(ai.part_index, "suggest_names", lambda q, limit=6: [])
    monkeypatch.setattr(ai.part_index, "assembly_context", lambda pn, f="": {})
    monkeypatch.setattr(ai, "_expand_query", lambda q: (["brake pedal"], []))
    monkeypatch.setattr(ai.sims, "equivalents_for",
                        lambda pn: {"digantikan_oleh": [{"pn": "NEWPN", "nama": "New Pedal"}],
                                    "menggantikan": []} if pn == "OLDPN" else {})
    res = ai._t_cari_part({"query": "brake pedal"}, ADMIN)
    it = next(x for x in res["hasil"] if x["part_number"] == "OLDPN")
    assert it.get("pengganti") == [{"pn": "NEWPN", "nama": "New Pedal"}]
    assert "info_pengganti" in res
