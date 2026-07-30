"""Mode perbaikan Asisten AI (saklar global admin di Menu Control).

Disimpan di app_config.config['asisten_perbaikan']. Kontrak yang dijaga:
- /api/ai/status mengirim `perbaikan` per-user → klien (web & mobile)
  menampilkan popup "sedang perbaikan" + mengunci input (available=false).
- ADMIN KEBAL: tetap bisa memakai asisten untuk menguji selagi mode nyala.
- SEMUA endpoint pemakaian menolak (503) — popup UI saja bisa dilewati
  lewat URL/API langsung.
- Config rusak → dianggap mati (jangan mematikan asisten tanpa sengaja).
"""
import pytest
from fastapi import HTTPException

from app.routers import ai as ai_router
from app.services import app_config, permissions

ADMIN = {"username": "mas", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "roni", "role": "pembeli"}


@pytest.fixture
def perbaikan_nyala(monkeypatch):
    monkeypatch.setattr(app_config, "load",
                        lambda: {"version": {}, "config": {"asisten_perbaikan": True}})


@pytest.fixture
def perbaikan_mati(monkeypatch):
    monkeypatch.setattr(app_config, "load",
                        lambda: {"version": {}, "config": {}})


@pytest.fixture
def semua_menu_aktif(monkeypatch):
    monkeypatch.setattr(permissions, "perms_load", lambda perm_type: {})


def test_status_kirim_flag_perbaikan(perbaikan_nyala, semua_menu_aktif, monkeypatch):
    monkeypatch.setattr(ai_router.get_settings(), "deepseek_api_key", "sk-ada",
                        raising=False)
    out = ai_router.ai_status(STAF)
    assert out["perbaikan"] is True
    assert out["available"] is False          # input klien ikut terkunci


def test_admin_kebal_mode_perbaikan(perbaikan_nyala, semua_menu_aktif, monkeypatch):
    monkeypatch.setattr(ai_router.get_settings(), "deepseek_api_key", "sk-ada",
                        raising=False)
    out = ai_router.ai_status(ADMIN)
    assert out["perbaikan"] is False
    assert out["available"] is True
    ai_router._tolak_bila_perbaikan(ADMIN)    # tidak melempar


def test_guard_menolak_non_admin_503(perbaikan_nyala):
    for user in (STAF, PEMBELI):
        with pytest.raises(HTTPException) as e:
            ai_router._tolak_bila_perbaikan(user)
        assert e.value.status_code == 503
        assert "perbaikan" in str(e.value.detail).lower()


def test_mode_mati_semua_lolos(perbaikan_mati):
    for user in (ADMIN, STAF, PEMBELI):
        ai_router._tolak_bila_perbaikan(user)  # tidak melempar
        assert ai_router._perbaikan_untuk(user) is False


def test_config_rusak_dianggap_mati(monkeypatch):
    def boom():
        raise RuntimeError("file rusak")
    monkeypatch.setattr(app_config, "load", boom)
    assert ai_router._perbaikan_untuk(STAF) is False


def test_semua_jalur_pemakaian_dijaga_perbaikan():
    """Endpoint pemakaian asisten WAJIB memanggil _tolak_bila_perbaikan —
    dicek dari sumbernya supaya endpoint baru tak lolos diam-diam."""
    import inspect
    for nama in ("ai_chat", "ai_chat_stream", "ai_chat_sheet"):
        fn = getattr(ai_router, nama, None)
        assert fn is not None, f"endpoint {nama} tidak ditemukan"
        assert "_tolak_bila_perbaikan" in inspect.getsource(fn), nama
