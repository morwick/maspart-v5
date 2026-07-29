"""Pencarian SPN/FMI lintas-store + akhir dari pesan yang saling bertentangan.

Log produksi mencatat empat kode dijawab "tidak ada di semua database". Setelah
ditelusuri, DUA di antaranya datanya SUDAH KITA MILIKI — yang bocor adalah
logikanya:

  • SPN 444 FMI 14  → baris EOL menyimpan SPN/FMI di dalam string kode
    ("SPN444/FMI14") dan kolom spn/fmi-nya dibiarkan None oleh builder.
  • SPN 520264 FMI 11 → baris `sumber="kartu"` tak pernah keluar lewat proyeksi
    legacy, sehingga tool balas found=True (dari PDF) SEKALIGUS memberi flag
    "FMI 11 tak terdaftar" — dua pesan yang bertentangan bagi model.

Nol jaringan, nol panggilan model.
"""
from __future__ import annotations

import pytest

from app.services import dtc_codes
from app.services import ai_assistant as ai

# Store hasil rebuild; kalau belum di-rebuild, sebagian test tak relevan.
_EOL_BER_SPN = any(r.get("spn") is not None for r in dtc_codes.rows("eol"))
butuh_rebuild = pytest.mark.skipif(
    not _EOL_BER_SPN, reason="store DTC belum di-rebuild (jalankan tools/build_dtc_store.py)")


# ── parser builder (murni, tak butuh store) ────────────────────────────────

def test_parser_kode_eol():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "tools" / "build_dtc_store.py"
    spec = importlib.util.spec_from_file_location("bds_uji", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    assert m._spn_fmi_dari_kode("SPN444/FMI14") == (444, 14)
    assert m._spn_fmi_dari_kode("spn 520264 / fmi 11") == (520264, 11)
    # Bukan pola SPN/FMI → jangan dipaksakan
    assert m._spn_fmi_dari_kode("P0100F7") == (None, None)
    assert m._spn_fmi_dari_kode("") == (None, None)
    # Di luar rentang J1939 → tolak, jangan cemari store dgn angka mustahil
    assert m._spn_fmi_dari_kode("SPN9999999/FMI3") == (None, None)
    assert m._spn_fmi_dari_kode("SPN444/FMI99") == (None, None)


# ── pencarian kanonik ──────────────────────────────────────────────────────

def test_spn_tak_ada_balas_kosong():
    assert dtc_codes.search_spn_fmi(999999, 1) == []
    assert dtc_codes.search_spn_fmi(None) == []


@butuh_rebuild
def test_spn444_fmi14_kini_terjangkau():
    """Sebelumnya: "tidak ada di semua database". Datanya ada sejak awal."""
    hits = dtc_codes.search_spn_fmi(444, 14)
    assert hits, "SPN 444 FMI 14 harus ketemu setelah kode EOL di-parse"
    assert any(h["spn"] == 444 and h["fmi"] == 14 for h in hits)
    assert any(h.get("deskripsi") for h in hits), "harus punya deskripsi Indonesia"


@butuh_rebuild
def test_baris_kartu_ikut_terjangkau():
    """32 baris sumber='kartu' dulu tak pernah keluar ke konsumen mana pun."""
    hits = dtc_codes.search_spn_fmi(520264, 11)
    assert any(h["sumber"] == "kartu" for h in hits)


def test_fallback_spn_saja_saat_fmi_tak_cocok():
    """FMI yang diminta tak ada → kembalikan FMI lain untuk SPN itu, jangan
    kosong; pemanggil menandainya jujur."""
    semua = dtc_codes.search_spn_fmi(520208)
    if not semua:
        pytest.skip("SPN contoh tak ada di store ini")
    fmi_ada = {h["fmi"] for h in semua}
    fmi_tak_ada = next(i for i in range(32) if i not in fmi_ada)
    hits = dtc_codes.search_spn_fmi(520208, fmi_tak_ada)
    assert hits, "fallback SPN-saja harus tetap memberi konteks"
    assert all(h["fmi"] != fmi_tak_ada for h in hits)


def test_fmi_tersedia():
    semua = dtc_codes.search_spn_fmi(520208)
    if not semua:
        pytest.skip("SPN contoh tak ada di store ini")
    daftar = dtc_codes.fmi_tersedia(520208)
    assert daftar == sorted(set(daftar))
    assert set(daftar) >= {h["fmi"] for h in semua if h["fmi"] is not None}


# ── tool: pesan tak boleh bertentangan ─────────────────────────────────────

@butuh_rebuild
def test_tool_tak_memberi_flag_kontradiktif():
    """Bila ADA pasangan persis di sumber mana pun, jangan sekaligus bilang
    'FMI itu tak terdaftar'."""
    out = ai._t_cari_kode_kesalahan({"spn": 520264, "fmi": 11}, {"role": "admin"})
    assert out["found"] is True
    assert "fmi_diminta_tak_terdaftar" not in out
    assert "tak terdaftar di database" not in out["catatan"].lower()


def test_tool_menjelaskan_spn_di_luar_rentang():
    """SPN 5205252 (>524287) dulu berujung nihil bisu — user tak pernah tahu
    bahwa masalahnya di angka yang ia bacakan."""
    out = ai._t_cari_kode_kesalahan({"spn": 5205252, "fmi": 5}, {"role": "admin"})
    assert out.get("spn_di_luar_rentang") == 5205252
    assert "J1939" in out["catatan"]


@butuh_rebuild
def test_tool_menyertakan_hasil_kanonik():
    out = ai._t_cari_kode_kesalahan({"spn": 444, "fmi": 14}, {"role": "admin"})
    assert out["found"] is True
    assert out["jumlah_cocok_kanonik"] >= 1
    assert any(h["spn"] == 444 for h in out["hasil_kanonik"])
