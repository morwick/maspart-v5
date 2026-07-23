"""Sisipan `pengetahuan_terkait` di handler tool (Fase A program "nyambung"
2026-07-23): tautan lintas-store masuk hasil tool dgn invarian `catatan` tetap
KEY TERAKHIR; disaring peran; fail-open bila knowledge_links bermasalah."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "roni", "role": "pembeli"}

_ROWS = [
    {"store": "jadwal_perawatan", "judul": "Filter oli SD16 tiap 250 jam",
     "buka": "jadwal_perawatan(model='SD16')", "ref": "SD16|x"},
    {"store": "manual_teks", "judul": "Tekanan oli rendah",
     "buka": "cari_manual(topik='tekanan oli')", "ref": "bosch|12"},
]


@pytest.fixture
def links(monkeypatch):
    monkeypatch.setattr(ai.knowledge_links, "terkait",
                        lambda ents, exclude_store="", limit=5,
                        untuk_pembeli=False: list(_ROWS))
    monkeypatch.setattr(ai.knowledge_links, "entitas",
                        lambda **kw: ["pn:X"])
    monkeypatch.setattr(ai, "_allowed_tool_names",
                        lambda user, sheet_id="": {"jadwal_perawatan",
                                                   "cari_manual"})


def test_sisip_terkait_catatan_tetap_terakhir(links):
    out = ai._sisip_terkait({"found": True, "catatan": "asli."},
                            ["pn:X"], "dtc_codes", ADMIN)
    assert list(out)[-1] == "catatan"                  # invarian _cap_tool_content
    assert out["catatan"].startswith("asli.")
    assert "pengetahuan_terkait" in out
    assert out["pengetahuan_terkait"][0]["store"] == "jadwal_perawatan"
    # `ref` internal TIDAK ikut terkirim ke model
    assert all("ref" not in r for r in out["pengetahuan_terkait"])


def test_sisip_terkait_saring_tool_di_luar_peran(links, monkeypatch):
    monkeypatch.setattr(ai, "_allowed_tool_names",
                        lambda user, sheet_id="": {"cari_manual"})
    out = ai._sisip_terkait({"catatan": "x"}, ["pn:X"], "dtc_codes", PEMBELI)
    stores = [r["store"] for r in out.get("pengetahuan_terkait", [])]
    assert stores == ["manual_teks"]                   # jadwal tersaring


def test_sisip_terkait_fail_open(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("links rusak")

    monkeypatch.setattr(ai.knowledge_links, "terkait", boom)
    out = ai._sisip_terkait({"found": True, "catatan": "asli"},
                            ["pn:X"], "s", ADMIN)
    assert out == {"found": True, "catatan": "asli"}   # utuh tanpa field baru


def test_sisip_terkait_tanpa_entitas_noop(links):
    out = ai._sisip_terkait({"catatan": "a"}, [], "s", ADMIN)
    assert "pengetahuan_terkait" not in out


def test_handler_jadwal_membawa_terkait(links, monkeypatch):
    monkeypatch.setattr(ai.maintenance_ref, "available", lambda: True)
    monkeypatch.setattr(ai.maintenance_ref, "search",
                        lambda m, q, j, jn: [{"jenis": "dozer", "model": "SD16",
                                              "varian": "", "sistem": "mesin",
                                              "nama": "Filter oli",
                                              "part_number": "175-49-11580",
                                              "qty": 1, "ganti_jam": [250]}])
    monkeypatch.setattr(ai.maintenance_ref, "models_by_jenis", lambda: {})
    out = ai._t_jadwal_perawatan({"model": "SD16"}, ADMIN)
    assert out["pengetahuan_terkait"]
    assert list(out)[-1] == "catatan"


def test_handler_kode_kesalahan_membawa_terkait(links, monkeypatch):
    monkeypatch.setattr(ai.fault_codes, "count", lambda: 1)
    monkeypatch.setattr(ai.fault_codes, "search", lambda **kw: [])
    monkeypatch.setattr(ai.eol_dtc, "count", lambda: 1)
    monkeypatch.setattr(ai.eol_dtc, "search", lambda **kw: [])
    monkeypatch.setattr(ai.abs_scr_codes, "count", lambda: 1)
    monkeypatch.setattr(ai.abs_scr_codes, "search", lambda **kw: [])
    out = ai._t_cari_kode_kesalahan({"code": "P0087"}, ADMIN)
    assert out.get("pengetahuan_terkait")
    assert list(out)[-1] == "catatan"
