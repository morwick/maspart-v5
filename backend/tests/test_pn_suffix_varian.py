"""Silang PN EPC → inventori harus PEMAAF terhadap SUFFIX VARIAN.

Kasus nyata: asisten menjawab kampas kopling WG9525160004/2 'stok —' padahal
barangnya ADA (11 pc, Rp 5.250.000) — indeks kita menyimpannya sebagai
WG9525160004 (tanpa '/2'). EPC memakai suffix varian pemasok/halaman; pencocokan
PERSIS meleset dan part yang tersedia dilaporkan tak berstok.
"""
import pytest

from app.services import ai_assistant as ai, part_index

_INDEKS = {
    "WG9525160004": {"part_number": "WG9525160004", "part_name": "Driven disk assembly",
                     "stok": "11", "harga": "Rp 5.250.000", "gudang": {"02.Pekanbaru": 3}},
    "202V09100-7926": {"part_number": "202V09100-7926", "part_name": "Oil filter",
                       "stok": "5", "harga": "Rp 90.000", "gudang": {}},
}


@pytest.fixture
def indeks(monkeypatch):
    monkeypatch.setattr(part_index, "search_exact_pns",
                        lambda pns: [_INDEKS[p] for p in pns if p in _INDEKS])
    monkeypatch.setattr(part_index, "_pn_flat_map",
                        lambda: {"202V091007926": ["202V09100-7926"]})


def test_suffix_varian_dicocokkan_ke_pn_dasar(indeks):
    out = part_index.rows_for_pns(["WG9525160004/2"])
    assert out["WG9525160004/2"]["stok"] == "11"          # ⛔ bukan 'tak ketemu'
    assert out["WG9525160004/2"]["harga"] == "Rp 5.250.000"


def test_cocok_persis_tetap_menang(indeks):
    out = part_index.rows_for_pns(["WG9525160004"])
    assert out["WG9525160004"]["part_number"] == "WG9525160004"


def test_suffix_plus_dan_halaman(indeks):
    out = part_index.rows_for_pns(["WG9525160004+003/1"])
    assert out["WG9525160004+003/1"]["stok"] == "11"


def test_tanpa_tanda_pemisah(indeks):
    out = part_index.rows_for_pns(["202V091007926"])
    assert out["202V091007926"]["part_number"] == "202V09100-7926"


def test_pn_asing_tetap_tak_ketemu(indeks):
    assert part_index.rows_for_pns(["ZZ9999999999/9"]) == {}


def test_kunci_hasil_adalah_pn_asli_yang_diminta(indeks):
    """Pemanggil memakai PN EPC apa adanya sebagai kunci — jangan dipaksa tahu
    bentuk dasarnya."""
    out = part_index.rows_for_pns(["WG9525160004/2", "202V091007926"])
    assert set(out) == {"WG9525160004/2", "202V091007926"}


# ── Integrasi: tool asisten yang menyilangkan PN EPC ──────────────────────────
def test_cari_part_di_unit_melaporkan_stok_pn_bersuffix(monkeypatch, indeks):
    monkeypatch.setattr(ai.epc_bom, "search_in_unit", lambda r, k: {
        "found": True, "frame_number": "LZZTEST", "hasil": [
            {"pn": "WG9525160004/2", "nama": "430 pull clutch driven disc assembly",
             "kata_kunci": "clutch driven disc"}]})
    monkeypatch.setattr(ai.epc_bom, "reverse_find_in_unit",
                        lambda r, pn: {"found": True, "instances": [
                            {"parent_pn": "WG9525160001/2", "parent_nama": "CH430 Clutch Assembly"}]})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "clutch driven disc"], []))
    r = ai._t_cari_part_di_unit({"rangka": "LZZTEST", "kata_kunci": "kampas kopling"},
                                {"username": "admin", "role": "admin"})
    p0 = r["parts"][0]
    assert p0["part_number"] == "WG9525160004/2"        # PN EPC apa adanya
    assert p0["ada_di_inventori"] is True
    assert p0["stok_total"] == "11" and p0["harga_lokal"] == "Rp 5.250.000"
