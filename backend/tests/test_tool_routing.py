"""Routing tool — kunci disambiguasi deskripsi pasangan tool yang tumpang-tindih
(2026-07-22). Test mengunci TEKS pointer 'pakai X bukan Y' agar tak terhapus;
pilihan tool sesungguhnya diputuskan LLM (tak bisa di-unit-test)."""
from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


def _desc():
    return {s["function"]["name"]: (s["function"].get("description") or "")
            for s in ai._tool_specs(ADMIN, "s1")}


def test_isi_assy_vs_uraikan_assembly():
    d = _desc()
    assert "uraikan_assembly" in d["isi_assy"] and "TANPA" in d["isi_assy"].upper()
    assert "isi_assy" in d["uraikan_assembly"]


def test_diagnosa_vs_cari_manual():
    d = _desc()
    assert "cari_manual" in d["diagnosa"]
    assert "diagnosa" in d["cari_manual"]


def test_isi_kategori_per_model_bukan_vin():
    d = _desc()
    assert "per-MODEL" in d["isi_kategori"] or "per-model" in d["isi_kategori"]
    assert "RANGKA" in d["isi_kategori"].upper()


def test_semua_tool_punya_deskripsi_unik():
    specs = ai._tool_specs(ADMIN, "s1")
    nama = [s["function"]["name"] for s in specs]
    assert len(nama) == len(set(nama)), "nama tool duplikat"
    for s in specs:
        assert (s["function"].get("description") or "").strip(), \
            f"{s['function']['name']} tanpa deskripsi"
