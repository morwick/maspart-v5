"""Test ai_knowledge — pengetahuan ter-derive dari data fakta (bukan hafalan).

Fokus: (1) miner menghitung prefix/kategori PERSIS dari catalog_bom, (2) blok
prompt tidak memuat PN utuh (anti-papagal), (3) daftar gudang disembunyikan
untuk pembeli, (4) file hilang → blok kosong tanpa error.
"""
import json

import pytest

from app.services import ai_knowledge


@pytest.fixture()
def fake_data(tmp_path, monkeypatch):
    """Arahkan data_path ke katalog mini buatan yang isinya diketahui pasti."""
    bom = {
        "kategori": {"01": "Kabin", "05": "Transmisi", "09": "Rem"},
        "units": {
            "UNIT A (LZZA)": {"kategori": {
                "05": {"assy_pn": "HW1970900001", "jumlah": 3, "parts": [
                    {"pn": "WG22290000A1", "nama": "Main shaft", "qty": "1"},
                    {"pn": "WG22290000B2", "nama": "Gear ring", "qty": "1"},
                    {"pn": "AZ16000000C3", "nama": "Bracket", "qty": "2"},
                ]},
                "01": {"assy_pn": "", "jumlah": 1, "parts": [
                    {"pn": "AZ16000000D4", "nama": "Door bracket", "qty": "1"},
                ]},
            }},
            "UNIT B (LZZB)": {"kategori": {
                "05": {"assy_pn": "", "jumlah": 1, "parts": [
                    # PN sama dgn Unit A — harus dihitung SEKALI (unik per PN)
                    {"pn": "WG22290000A1", "nama": "Main shaft", "qty": "1"},
                ]},
            }},
        },
    }
    (tmp_path / "catalog_bom.json").write_text(
        json.dumps(bom, ensure_ascii=False), encoding="utf-8")

    class _S:
        data_path = tmp_path

    monkeypatch.setattr(ai_knowledge, "get_settings", lambda: _S())
    monkeypatch.setattr(ai_knowledge, "_CACHE",
                        {"mtime": None, "data": None, "block": "", "block_no_gudang": ""})
    from app.services import gudang_config
    monkeypatch.setattr(gudang_config, "coords_map",
                        lambda: {"01.Jakarta": (0, 0), "04.Palembang": (0, 0)})
    return tmp_path


def test_build_menghitung_prefix_dari_data(fake_data):
    d = ai_knowledge.build(min_sub_count=1, min_sub_share=0.5)
    assert d["cakupan"]["pn_unik_bom"] == 4          # A1, B2, C3, D4 (A1 unik)
    by_pref = {r["prefix"]: r for r in d["prefix_pn"]}
    assert by_pref["WG"]["jumlah_pn"] == 2
    assert by_pref["AZ"]["jumlah_pn"] == 2
    # kategori dominan WG = Transmisi (label dari catalog_bom, bukan karangan)
    assert by_pref["WG"]["kategori_top"][0][0] == "Transmisi"
    sub = {r["prefix"]: r for r in d["sub_prefix_pn"]}
    assert sub["WG2229"]["kategori_dominan"] == "Transmisi"
    assert d["gudang"] == ["01.Jakarta", "04.Palembang"]


def test_blok_prompt_tanpa_pn_utuh(fake_data):
    ai_knowledge.build_and_save(min_sub_count=1, min_sub_share=0.5)
    blok = ai_knowledge.knowledge_block({"role": "admin"})
    assert "WG2229…" in blok and "PENGETAHUAN DARI DATA NYATA" in blok
    # Tidak boleh ada PN KATALOG utuh yang bisa dipapagalkan model sebagai
    # jawaban — satu-satunya token mirip-PN yang boleh = nama gudang kanonik
    # (dan itu dikecualikan guard produksi lewat _unit_name_tokens).
    from app.services.ai_assistant import _extract_pns
    gudang_toks = _extract_pns("01.Jakarta 04.Palembang")
    assert _extract_pns(blok) <= gudang_toks


def test_gudang_disembunyikan_untuk_pembeli(fake_data):
    ai_knowledge.build_and_save(min_sub_count=1, min_sub_share=0.5)
    admin = ai_knowledge.knowledge_block({"role": "admin"})
    pembeli = ai_knowledge.knowledge_block({"role": "pembeli"})
    assert "01.Jakarta" in admin
    assert "01.Jakarta" not in pembeli
    assert "PENGETAHUAN DARI DATA NYATA" in pembeli  # sisa blok tetap ada


def test_file_hilang_blok_kosong(fake_data):
    (fake_data / "ai_knowledge.json").unlink(missing_ok=True)
    assert ai_knowledge.knowledge_block({"role": "admin"}) == ""
