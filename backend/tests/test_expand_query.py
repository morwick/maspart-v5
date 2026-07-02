"""Ekspansi sinonim bahasa bengkel (§3.5.3) — _expand_query & _spelling_variants.

Kamus di-mock agar test tidak bergantung isi data/sinonim/sinonim.json.
"""
import pytest

from app.services import ai_assistant as ai

ENTRIES = [
    {"grup": "kopling", "triggers": ["kampas kopling", "plat kopling"],
     "keywords": ["driven disc", "driven plate"]},
    {"grup": "per", "triggers": ["per"], "keywords": ["spring"]},
    {"grup": "laher", "triggers": ["laher", "klaher"], "keywords": ["bearing"]},
]


@pytest.fixture(autouse=True)
def _mock_kamus(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: ENTRIES)


def test_trigger_frasa_menambah_keyword_katalog():
    terms, matched = ai._expand_query("cari kampas kopling untuk NX400")
    assert matched == ["kampas kopling"]
    assert "driven disc" in terms and "driven plate" in terms
    assert terms[0] == "cari kampas kopling untuk NX400"  # query asli tetap ikut


def test_trigger_hanya_cocok_sebagai_kata_utuh():
    # 'per' TIDAK boleh cocok di dalam 'persneling' (aturan batas kata).
    terms, matched = ai._expand_query("gigi persneling susah masuk")
    assert matched == []
    assert "spring" not in terms


def test_trigger_kata_utuh_tetap_cocok():
    _, matched = ai._expand_query("per depan patah")
    assert matched == ["per"]


def test_tanpa_trigger_tidak_ada_ekspansi():
    terms, matched = ai._expand_query("filter oli mesin")
    assert matched == []
    assert terms == ["filter oli mesin"]


def test_dua_grup_sekaligus():
    terms, matched = ai._expand_query("laher dan kampas kopling")
    assert set(matched) == {"laher", "kampas kopling"}
    assert "bearing" in terms and "driven disc" in terms


# ── _spelling_variants: disc ↔ disk ─────────────────────────────────────────

def test_varian_disc_disk_dua_arah():
    out = ai._spelling_variants(["driven disc", "brake disk"])
    assert "driven disk" in out and "brake disc" in out


def test_discharge_tidak_diubah():
    # Batas kata: 'discharge' bukan 'disc'.
    assert ai._spelling_variants(["discharge valve"]) == ["discharge valve"]
