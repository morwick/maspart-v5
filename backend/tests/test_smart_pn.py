"""Pencarian PN 'pemaaf' — smart_pn_search, suggest_pns, dan wiring cari_part.

Pola diambil dari log Pencarian Nihil nyata: PN + suffix qty/halaman
('WG9925680011/7', '+01', ' 1/3'), PN varian panjang yang basisnya ada di
katalog ('WG9525930223TQF717'), PN salah satu digit yang dicoba berulang,
dan beberapa PN dalam satu pertanyaan. Index dipalsukan → tanpa data/network.
"""
from datetime import datetime

import pandas as pd
import pytest

from app.services import ai_assistant as ai
from app.services import part_index

U = {"username": "mas", "role": "admin"}

PARTS = [
    ("WG9925680011", "OIL FILTER"),
    ("WG9525930223", "COMBINATION SWITCH"),
    ("AZ400745400160", "CYLINDER HEAD GASKET"),
    ("AZ450045000026", "BRAKE CHAMBER"),
    ("WG9100032311", "STARTER"),
    ("202V09100-7926", "INJECTOR ASSEMBLY"),
]


@pytest.fixture
def fake_index(monkeypatch):
    df = pd.DataFrame(
        [{"part_number": pn, "part_name": nm, "remark": "", "quantity": 1}
         for pn, nm in PARTS]
    )
    fi = {
        "simple_name": "NX400 6X4", "relative_path": "Sinotruk/NX400 6X4",
        "sheet": "S1", "dataframe": df,
        "part_number_index": {pn.upper(): [i] for i, (pn, _) in enumerate(PARTS)},
        "part_name_index": {},
    }
    at = datetime(2026, 1, 1)
    monkeypatch.setitem(part_index._state, "excel_files", [fi])
    monkeypatch.setitem(part_index._state, "indexed_at", at)
    monkeypatch.setitem(part_index._state, "stok_cache", {})
    monkeypatch.setitem(part_index._state, "harga_lookup", {})
    monkeypatch.setitem(part_index._state, "gudang_cache", {})
    monkeypatch.setattr(part_index, "ensure_index", lambda: None)
    monkeypatch.setattr(part_index.harga, "weight_for", lambda pn: None)
    # cache turunan ikut segar utk index palsu ini
    monkeypatch.setattr(part_index, "_ALLPARTS_CACHE", {"at": None, "rows": []})
    monkeypatch.setattr(part_index, "_PN_FLAT_CACHE", {"at": None, "map": {}})


# ── deteksi token PN ─────────────────────────────────────────────────────────
def test_pn_tokens_dari_kalimat():
    assert part_index.pn_tokens("cari WG9925680011/7 dong") == ["WG9925680011"]
    assert part_index.pn_tokens("AZ450045000026 AZ450045000027 WG9100032311") == [
        "AZ450045000026", "AZ450045000027", "WG9100032311"]
    # kata biasa & kode unit pendek bukan PN
    assert part_index.pn_tokens("kampas kopling NX400 6X4") == []


def test_looks_like_pn():
    assert part_index.looks_like_pn("WD615.47")
    assert part_index.looks_like_pn("202V09100-7926")
    assert not part_index.looks_like_pn("NX400")      # terlalu pendek
    assert not part_index.looks_like_pn("FILTER")     # tanpa digit


# ── smart_pn_search ──────────────────────────────────────────────────────────
def test_suffix_qty_halaman_dibuang(fake_index):
    for q in ("WG9925680011/7", "WG9925680011+01", "WG9925680011 1/3"):
        rows, note = part_index.smart_pn_search(q)
        assert [r["part_number"] for r in rows] == ["WG9925680011"], q
        assert note


def test_basis_pn_dari_varian_panjang(fake_index):
    rows, note = part_index.smart_pn_search("WG9525930223TQF717")
    assert [r["part_number"] for r in rows] == ["WG9525930223"]
    assert "BASIS" in note.upper()


def test_bebas_pemisah(fake_index):
    rows, _ = part_index.smart_pn_search("202V091007926")  # tanpa '-'
    assert [r["part_number"] for r in rows] == ["202V09100-7926"]


def test_tetap_nihil_tanpa_asal_cocok(fake_index):
    rows, note = part_index.smart_pn_search("ZQ36151")
    assert rows == [] and note is None


# ── suggest_pns: user kurang/tertukar satu digit ─────────────────────────────
def test_saran_pn_mirip(fake_index):
    saran = part_index.suggest_pns("AZ40074540160")  # kurang satu '0'
    assert any(s["part_number"] == "AZ400745400160" for s in saran)


def test_saran_pn_tidak_menyaran_diri_sendiri(fake_index):
    assert part_index.suggest_pns("AZ450045000026") == []


# ── wiring _t_cari_part ──────────────────────────────────────────────────────
def _pns(res):
    return {it["part_number"] for it in (res.get("hasil") or [])}


@pytest.fixture
def quiet_ai(monkeypatch):
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    monkeypatch.setattr(part_index, "search_part_name", lambda t: [])
    monkeypatch.setattr(part_index, "correct_typos", lambda t: (t, []))
    monkeypatch.setattr(part_index, "suggest_names", lambda q, limit=6: [])
    monkeypatch.setattr(part_index, "assembly_context", lambda pn, f="": {})


def test_cari_part_pn_suffix(fake_index, quiet_ai):
    res = ai._t_cari_part({"query": "WG9925680011/7"}, U)
    assert _pns(res) == {"WG9925680011"}
    assert "dibersihkan" in (res.get("catatan") or "")


def test_cari_part_multi_pn(fake_index, quiet_ai):
    res = ai._t_cari_part(
        {"query": "AZ450045000026 AZ450045000027 WG9100032311"}, U)
    assert {"AZ450045000026", "WG9100032311"} <= _pns(res)
    catatan = res.get("catatan") or ""
    assert "AZ450045000027" in catatan and "TIDAK ditemukan" in catatan


def test_cari_part_saran_pn_mirip(fake_index, quiet_ai):
    res = ai._t_cari_part({"query": "AZ40074540160"}, U)
    assert not res.get("hasil")
    assert any(s["part_number"] == "AZ400745400160"
               for s in res.get("saran_mungkin_maksud") or [])
