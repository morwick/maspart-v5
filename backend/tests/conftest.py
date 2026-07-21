"""Pastikan package `app` bisa diimpor saat pytest dijalankan dari mana pun.

    cd backend && python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _jangan_tulis_observabilitas_prod(request, monkeypatch):
    """Test yang memanggil ai_assistant.chat() TIDAK boleh menulis ke Supabase
    PRODUKSI (kejadian nyata 2026-07-08: 150 baris 'tester' mengotori halaman
    Observabilitas AI). log_turn di-no-op untuk SEMUA test — kecuali modul
    test_ai_chat_log yang memang menguji fungsi itu (ia mock requests sendiri)."""
    if request.module.__name__ == "test_ai_chat_log":
        return
    try:
        from app.services import ai_chat_log
        monkeypatch.setattr(ai_chat_log, "log_turn", lambda **kw: True)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _bersihkan_cache_izin():
    """permissions menyimpan hasil perms_load di cache modul ber-TTL 30 dtk.
    Antar-test cache itu BOCOR: test yang me-monkeypatch perms_load justru
    membaca centang milik test sebelumnya. Buang sebelum & sesudah tiap test."""
    try:
        from app.services import permissions
    except Exception:
        yield
        return
    permissions.invalidate_cache()
    yield
    permissions.invalidate_cache()


@pytest.fixture(autouse=True)
def _jangan_bangun_indeks_epc_nyata(monkeypatch):
    """Indeks item EPC per-unit: warm_items_index menembak RATUSAN panggilan EPC
    nyata di thread latar & menulis cache disk — tak boleh terjadi dari test.
    items_index_ready dipaksa False agar handler tak terpengaruh file cache yang
    kebetulan ada di data/ (test yang mengujinya me-mock sendiri, menimpa ini)."""
    try:
        from app.services import epc_bom
        monkeypatch.setattr(epc_bom, "warm_items_index", lambda r: None)
        monkeypatch.setattr(epc_bom, "items_index_ready", lambda r: False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _bersihkan_idempotensi_order():
    """Cache idempotensi POST /orders (L5) proses-lokal → bersihkan antar-test
    agar hasil order satu test tak bocor ke test lain yang sidik jarinya sama."""
    try:
        from app.routers import orders as _o
        _o._order_idem.clear()
        _o._order_locks.clear()
    except Exception:
        pass
    yield
    try:
        from app.routers import orders as _o
        _o._order_idem.clear()
        _o._order_locks.clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _indeks_accurate_segar(monkeypatch):
    """Guard umur indeks (accurate.index_too_old_for_checkout) memblokir checkout
    bila ts=0 (default env test) — dianggap basi. Beri ts SEGAR agar test checkout
    yang tak menguji integritas indeks tetap jalan; test khusus H3 meng-override
    ts-nya sendiri (monkeypatch.setitem menang di scope test)."""
    try:
        import time
        from app.services import accurate
        monkeypatch.setitem(accurate._index_cache, "ts", time.time())
    except Exception:
        pass
