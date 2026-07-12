"""Menu Control benar-benar MEMATIKAN fiturnya, bukan cuma menyembunyikan menunya.

Regresi yang dijaga: izin menu (permissions kind 'menu') dulu hanya dikirim ke
frontend untuk menyembunyikan item sidebar — tak ada satu pun endpoint yang
mengeceknya. Jadi mematikan centang "Asisten AI" tak berpengaruh: user tinggal
membuka /asisten atau memanggil /api/ai/chat langsung. Sidebar PEMBELI bahkan tak
pernah mengambil izin sama sekali (menu selalu tampil).
"""
import pytest
from fastapi import HTTPException

from app.deps import require_menu
from app.routers import ai as ai_router
from app.services import permissions

PEMBELI = {"username": "roni", "role": "pembeli"}
STAF = {"username": "budi", "role": "user"}
ADMIN = {"username": "mas", "role": "admin"}


@pytest.fixture
def ai_dimatikan(monkeypatch):
    """Baris izin: 'roni' & 'budi' TANPA key 'ai' (admin mematikan centangnya)."""
    monkeypatch.setattr(permissions, "perms_load",
                        lambda perm_type: {"roni": ["search"], "budi": ["search"]})


def _panggil(user):
    return require_menu("ai")(user=user)


def test_pembeli_tanpa_izin_ai_ditolak(ai_dimatikan):
    with pytest.raises(HTTPException) as e:
        _panggil(PEMBELI)
    assert e.value.status_code == 403
    assert "Asisten AI" in str(e.value.detail)


def test_staf_tanpa_izin_ai_ditolak(ai_dimatikan):
    with pytest.raises(HTTPException) as e:
        _panggil(STAF)
    assert e.value.status_code == 403


def test_admin_selalu_lolos(ai_dimatikan):
    assert _panggil(ADMIN) == ADMIN


def test_user_dengan_izin_ai_lolos(monkeypatch):
    monkeypatch.setattr(permissions, "perms_load", lambda perm_type: {"roni": ["search", "ai"]})
    assert _panggil(PEMBELI) == PEMBELI


def test_tanpa_baris_izin_default_semua_menu_aktif(monkeypatch):
    """Tanpa baris di tabel permissions, aturan lama tetap: semua menu aktif."""
    monkeypatch.setattr(permissions, "perms_load", lambda perm_type: {})
    assert _panggil(PEMBELI) == PEMBELI


def test_status_ai_melaporkan_menu_dimatikan(ai_dimatikan, monkeypatch):
    """/api/ai/status → available False + allowed False, supaya halaman /asisten
    memberi alasan yang benar ('dimatikan admin', bukan 'API key kosong')."""
    monkeypatch.setattr(ai_router.get_settings(), "deepseek_api_key", "sk-ada", raising=False)
    out = ai_router.ai_status(PEMBELI)
    assert out["allowed"] is False and out["available"] is False


def _pakai_require_ai(route) -> bool:
    """Route ini memakai dependency require_ai? Bandingkan OBJEK fungsinya —
    mencocokkan nama ('_dep') tak bisa dipakai: rate limiter juga memakai closure
    bernama sama, sehingga endpoint tanpa penjaga ikut lolos."""
    return any(d.call is ai_router.require_ai for d in route.dependant.dependencies)


def test_semua_jalur_pemakaian_asisten_dijaga_izin():
    dijaga = {"ai_chat", "ai_chat_image", "ai_chat_sheet", "export_ai_excel",
              "export_banding_rangka"}
    ketemu = {getattr(r, "name", ""): _pakai_require_ai(r) for r in ai_router.router.routes
              if getattr(r, "name", "") in dijaga}
    assert set(ketemu) == dijaga, f"endpoint hilang/berganti nama: {set(ketemu) ^ dijaga}"
    assert all(ketemu.values()), f"tak dijaga izin menu: {[n for n, ok in ketemu.items() if not ok]}"


def test_status_dan_feedback_tetap_terbuka():
    """Kontrol negatif — memastikan pemeriksaan di atas tidak lolos-kosong:
    /status & /feedback sengaja TIDAK dijaga (status dipakai halaman untuk tahu
    dirinya dimatikan; feedback boleh dari user mana pun)."""
    terbuka = {"ai_status", "submit_feedback"}
    for r in ai_router.router.routes:
        if getattr(r, "name", "") in terbuka:
            assert not _pakai_require_ai(r)
