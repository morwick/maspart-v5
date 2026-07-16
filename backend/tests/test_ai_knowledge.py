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
    # Sumber fakta tambahan dibuat DETERMINISTIK (tanpa file data nyata):
    # fault_codes angka pasti; filter/gearbox dimatikan (dirender-test terpisah).
    from app.services import fault_codes, filter_ref, maintenance_ref, repairkit
    monkeypatch.setattr(fault_codes, "count", lambda: 7)
    monkeypatch.setattr(filter_ref, "available", lambda: False)
    monkeypatch.setattr(maintenance_ref, "available", lambda: False)
    monkeypatch.setattr(repairkit, "available", lambda: False)
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
    assert d["fault_codes"]["jumlah"] == 7
    assert d["filter_shantui_units"] == [] and d["gearbox_repairkit"] == []


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


# ── P7: auto-rebuild saat catalog_bom.json lebih baru ───────────────────────

class _SyncThread:
    """Thread palsu yang menjalankan target SINKRON di .start() (untuk test)."""
    def __init__(self, target=None, daemon=None, name=None):
        self._t = target

    def start(self):
        if self._t:
            self._t()


def test_auto_rebuild_saat_catalog_lebih_baru(fake_data, monkeypatch):
    import os
    ai_knowledge.build_and_save(min_sub_count=1, min_sub_share=0.5)
    kpath = fake_data / "ai_knowledge.json"
    bom_mt = (fake_data / "catalog_bom.json").stat().st_mtime
    os.utime(kpath, (bom_mt - 100, bom_mt - 100))     # knowledge LEBIH TUA dari bom
    monkeypatch.setattr(ai_knowledge, "_CACHE",
                        {"mtime": None, "data": None, "block": "", "block_no_gudang": ""})
    monkeypatch.setattr(ai_knowledge, "_REBUILD", {"on": False, "last": 0.0})
    monkeypatch.setattr(ai_knowledge.threading, "Thread", _SyncThread)
    calls = {"n": 0}
    real = ai_knowledge.build_and_save
    monkeypatch.setattr(ai_knowledge, "build_and_save",
                        lambda **kw: (calls.__setitem__("n", calls["n"] + 1),
                                      real(min_sub_count=1, min_sub_share=0.5))[1])

    blok = ai_knowledge.knowledge_block({"role": "admin"})
    assert "PENGETAHUAN DARI DATA NYATA" in blok       # blok LAMA tetap disajikan
    assert calls["n"] == 1                              # rebuild dipicu tepat sekali


def test_tak_rebuild_bila_catalog_tak_lebih_baru(fake_data, monkeypatch):
    import os
    ai_knowledge.build_and_save(min_sub_count=1, min_sub_share=0.5)
    kpath = fake_data / "ai_knowledge.json"
    bom_mt = (fake_data / "catalog_bom.json").stat().st_mtime
    os.utime(kpath, (bom_mt + 100, bom_mt + 100))     # knowledge LEBIH BARU dari bom
    monkeypatch.setattr(ai_knowledge, "_CACHE",
                        {"mtime": None, "data": None, "block": "", "block_no_gudang": ""})
    monkeypatch.setattr(ai_knowledge, "_REBUILD", {"on": False, "last": 0.0})
    monkeypatch.setattr(ai_knowledge.threading, "Thread", _SyncThread)
    calls = {"n": 0}
    monkeypatch.setattr(ai_knowledge, "build_and_save",
                        lambda **kw: calls.__setitem__("n", calls["n"] + 1))

    ai_knowledge.knowledge_block({"role": "admin"})
    assert calls["n"] == 0                              # tak ada rebuild
