"""Tool topik_gagal — gap miner tersambung ke pipa mengajar.

Lingkaran yang dijaga: pertanyaan berulang gagal (≥3×, penambang ai_belajar)
→ ditawarkan ke yang BOLEH mengajar (layar pembuka via /api/ai/status +
tool saat ditanya) → diajarkan lewat alur ajarkan_pengetahuan → ditandai
selesai → keluar dari daftar. Gerbang sama dgn mengajar (_can_mengajar):
daftar kegagalan = peta kelemahan asisten.

Nol jaringan/model: ai_belajar.gaps/resolve_gap & permissions di-mock.
"""
from __future__ import annotations

import pytest

from app.routers import ai as ai_router
from app.services import ai_assistant as ai

ADMIN = {"username": "agus", "role": "admin"}
STAF = {"username": "budi", "role": "user"}          # dicentang ai_mengajar
POLOS = {"username": "polos", "role": "user"}
PEMBELI = {"username": "toko", "role": "pembeli"}

GAPS = [
    {"topik": "bushing front spring", "jumlah": 5, "contoh": "carikan bushing front spring"},
    {"topik": "seal pompa hidrolik", "jumlah": 3, "contoh": "kode seal pompa hidrolik howo"},
]


@pytest.fixture
def perms(monkeypatch):
    def fake(kind, u, r):
        if kind == "asisten":
            return ["ai_mengajar"] if u == "budi" else []
        return ["ai"] if kind == "menu" else []
    monkeypatch.setattr("app.services.permissions.effective", fake)


# ── daftar ──────────────────────────────────────────────────────────────────

def test_daftar_urut_tersering_dengan_contoh(monkeypatch):
    monkeypatch.setattr(ai.ai_belajar, "gaps", lambda: list(reversed(GAPS)))
    r = ai._t_topik_gagal({}, ADMIN)
    assert r["found"] is True and r["jumlah"] == 2
    assert r["topik"][0]["topik"] == "bushing front spring"      # jumlah 5 duluan
    assert r["topik"][0]["contoh_pertanyaan"] == "carikan bushing front spring"
    assert "tandai_selesai" in r["catatan"]                      # alur menutup lingkaran


def test_daftar_kosong_jujur(monkeypatch):
    monkeypatch.setattr(ai.ai_belajar, "gaps", lambda: [])
    r = ai._t_topik_gagal({}, ADMIN)
    assert r["found"] is False and "kabar" in r["catatan"].lower()


# ── tandai selesai / bukan gap ──────────────────────────────────────────────

def test_tandai_selesai_memanggil_resolve(monkeypatch):
    dipanggil = {}
    monkeypatch.setattr(ai.ai_belajar, "resolve_gap",
                        lambda t: dipanggil.setdefault("topik", t) or True)
    r = ai._t_topik_gagal({"aksi": "tandai_selesai",
                           "topik": "Bushing Front Spring"}, ADMIN)
    assert r["found"] is True
    assert dipanggil["topik"] == "bushing front spring"          # dinormalkan lower
    assert "SELESAI" in r["catatan"]


def test_bukan_gap_label_beda(monkeypatch):
    monkeypatch.setattr(ai.ai_belajar, "resolve_gap", lambda t: True)
    r = ai._t_topik_gagal({"aksi": "bukan_gap", "topik": "seal pompa hidrolik"}, ADMIN)
    assert "BUKAN gap" in r["catatan"]


def test_topik_tak_dikenal_dijawab_jujur(monkeypatch):
    monkeypatch.setattr(ai.ai_belajar, "resolve_gap", lambda t: False)
    monkeypatch.setattr(ai.ai_belajar, "gaps", lambda: GAPS)
    r = ai._t_topik_gagal({"aksi": "tandai_selesai", "topik": "ngasal"}, ADMIN)
    assert r["found"] is False
    assert "bushing front spring" in r["topik_tersisa"]
    assert "jangan menebak" in r["catatan"]


# ── gerbang ─────────────────────────────────────────────────────────────────

def test_gerbang_spec_dan_handler(perms, monkeypatch):
    monkeypatch.setattr(ai.ai_belajar, "gaps", lambda: GAPS)
    for u in (ADMIN, STAF):
        nama = {s["function"]["name"] for s in ai._tool_specs(u)}
        assert "topik_gagal" in nama
        assert ai._t_topik_gagal({}, u)["found"] is True
    for u in (POLOS, PEMBELI):
        nama = {s["function"]["name"] for s in ai._tool_specs(u)}
        assert "topik_gagal" not in nama
        assert ai._t_topik_gagal({}, u).get("denied") is True


def test_terdaftar_di_dispatch_dan_label():
    assert ai._DISPATCH["topik_gagal"] is ai._t_topik_gagal
    assert ai._tool_label("topik_gagal") == "Melihat topik yang gagal dijawab"


# ── /api/ai/status: tawaran di layar pembuka ────────────────────────────────

def _status(user, monkeypatch, gaps=GAPS):
    monkeypatch.setattr("app.services.ai_belajar.gaps", lambda: list(gaps))
    monkeypatch.setattr(ai_router, "_perbaikan_untuk", lambda u: False)
    monkeypatch.setattr(ai_router, "get_settings",
                        lambda: type("S", (), {"ai_configured": True})())
    return ai_router.ai_status(user)


def test_status_admin_dapat_tawaran(perms, monkeypatch):
    out = _status(ADMIN, monkeypatch)
    assert out["gap_ajar"]["jumlah"] == 2
    assert out["gap_ajar"]["topik"][0] == "bushing front spring"


def test_status_staf_dicentang_dapat_tawaran(perms, monkeypatch):
    assert "gap_ajar" in _status(STAF, monkeypatch)


def test_status_yang_tak_berhak_tidak_bocor(perms, monkeypatch):
    """Peta kelemahan asisten tak boleh sampai ke yang tak bisa memperbaikinya."""
    assert "gap_ajar" not in _status(POLOS, monkeypatch)
    assert "gap_ajar" not in _status(PEMBELI, monkeypatch)


def test_status_tanpa_gap_tanpa_field(perms, monkeypatch):
    assert "gap_ajar" not in _status(ADMIN, monkeypatch, gaps=[])


def test_status_gagal_baca_gap_tak_menjatuhkan(perms, monkeypatch):
    def meledak():
        raise RuntimeError("disk")
    monkeypatch.setattr("app.services.ai_belajar.gaps", meledak)
    monkeypatch.setattr(ai_router, "_perbaikan_untuk", lambda u: False)
    monkeypatch.setattr(ai_router, "get_settings",
                        lambda: type("S", (), {"ai_configured": True})())
    out = ai_router.ai_status(ADMIN)
    assert out["available"] is True and "gap_ajar" not in out
