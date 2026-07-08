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
