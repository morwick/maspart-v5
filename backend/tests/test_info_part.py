"""Tool `info_part` (Fase C 2026-07-23): pengetahuan mendalam keluarga part +
tautan lintas-store; jujur saat belum terkurasi; spec ditawarkan semua peran."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "roni", "role": "pembeli"}

_ROW = {"keluarga": "filter oli", "sistem": "mesin", "sub_sistem": "pelumasan",
        "nama_kunci": ["OIL FILTER"], "contoh_pn": ["VG61000070005"],
        "jumlah_pn": 214, "fungsi": "Menyaring oli mesin.",
        "gejala_umum": "Tekanan oli turun.", "catatan": ""}


@pytest.fixture
def taks(monkeypatch):
    monkeypatch.setattr(ai.part_taxonomy, "available", lambda: True)
    monkeypatch.setattr(ai.part_taxonomy, "cari",
                        lambda q, limit=3: [dict(_ROW)] if "filter" in q.lower() else [])
    monkeypatch.setattr(ai.part_taxonomy, "for_pn", lambda pn: None)
    monkeypatch.setattr(ai.knowledge_links, "entitas", lambda **kw: [])
    monkeypatch.setattr(ai.knowledge_links, "terkait",
                        lambda *a, **k: [])


def test_info_part_dasar(taks):
    out = ai._t_info_part({"nama": "filter oli"}, ADMIN)
    assert out["found"] and out["keluarga"] == "filter oli"
    assert out["fungsi"] == "Menyaring oli mesin."
    assert list(out)[-1] == "catatan"
    assert "JANGAN mengarang PN" in out["catatan"]


def test_info_part_belum_terkurasi_jujur(taks, monkeypatch):
    row = {**_ROW, "fungsi": "", "gejala_umum": ""}
    monkeypatch.setattr(ai.part_taxonomy, "cari", lambda q, limit=3: [row])
    out = ai._t_info_part({"nama": "filter oli"}, ADMIN)
    assert out["found"] and "fungsi" not in out
    assert "BELUM dikurasi" in out["catatan"]


def test_info_part_tak_terklasifikasi(taks):
    out = ai._t_info_part({"nama": "zzz"}, ADMIN)
    assert out["found"] is False
    assert "Jangan mengarang" in out["catatan"]


def test_info_part_butuh_input(taks):
    assert "error" in ai._t_info_part({}, ADMIN)


def test_info_part_ditawarkan_semua_peran():
    for u in (ADMIN, PEMBELI, {"username": "b", "role": "user"}):
        names = {s["function"]["name"] for s in ai._tool_specs(u)}
        assert "info_part" in names, f"info_part hilang utk {u['role']}"
    assert ai._DISPATCH["info_part"] is ai._t_info_part


def test_info_part_membawa_terkait(taks, monkeypatch):
    monkeypatch.setattr(ai.knowledge_links, "entitas", lambda **kw: ["pn:X"])
    monkeypatch.setattr(ai.knowledge_links, "terkait", lambda *a, **k: [
        {"store": "jadwal_perawatan", "judul": "Filter oli SD16",
         "buka": "jadwal_perawatan(model='SD16')", "ref": "x"}])
    monkeypatch.setattr(ai, "_allowed_tool_names",
                        lambda user, sheet_id="": {"jadwal_perawatan"})
    out = ai._t_info_part({"nama": "filter oli", "pn": "VG61000070005"}, ADMIN)
    assert out["pengetahuan_terkait"][0]["store"] == "jadwal_perawatan"
    assert list(out)[-1] == "catatan"
