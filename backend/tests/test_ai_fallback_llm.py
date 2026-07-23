"""Provider LLM cadangan (H1 paket handal 2026-07-23): DeepSeek 402 saldo habis /
429 / 5xx / jaringan → alihkan ke provider OpenAI-compatible cadangan (env
AI_FALLBACK_*), cooldown 10 menit, lalu re-probe primary. Tanpa fallback
ter-konfigurasi = perilaku lama persis (kasus produksi: 402 = asisten mati total)."""
import time
from types import SimpleNamespace

import pytest
import requests as real_requests

from app.services import ai_assistant as ai

PRIMARY_URL = "https://api.deepseek.test"
FB_URL = "https://fallback.test"


def _settings(fb=True):
    return SimpleNamespace(
        ai_configured=True,
        deepseek_api_key="k1", deepseek_base_url=PRIMARY_URL,
        deepseek_model="deepseek-chat",
        ai_fallback_configured=fb,
        ai_fallback_base_url=FB_URL, ai_fallback_api_key="k2",
        ai_fallback_model="model-cadangan",
    )


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body if body is not None else {"error": {"message": f"err {status}"}}
        self.text = str(self._body)

    def json(self):
        return self._body


OK_BODY = {"choices": [{"message": {"content": "halo"}, "finish_reason": "stop"}]}


class _FakeRequests:
    """Pengganti global `requests` di namespace ai — skrip respons per-post.
    Item skrip: _Resp ATAU exception instance (dilempar)."""
    RequestException = real_requests.RequestException

    def __init__(self, script):
        self.script = list(script)
        self.posts = []          # (url, payload)

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, json))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _hermetik(monkeypatch):
    monkeypatch.setattr(ai, "_fallback_until", 0.0, raising=False)
    monkeypatch.setattr(ai.time, "sleep", lambda s: None)


def _wire(monkeypatch, script, fb=True):
    fake = _FakeRequests(script)
    monkeypatch.setattr(ai, "requests", fake)
    monkeypatch.setattr(ai, "get_settings", lambda: _settings(fb))
    return fake


def test_402_langsung_ke_fallback(monkeypatch):
    fake = _wire(monkeypatch, [_Resp(402), _Resp(200, OK_BODY)])
    out = ai._post_chat([{"role": "user", "content": "hai"}], [])
    assert out == OK_BODY
    (u1, p1), (u2, p2) = fake.posts
    assert u1.startswith(PRIMARY_URL) and u2.startswith(FB_URL)
    # payload identik kecuali model di-swap
    assert p1["model"] == "deepseek-chat" and p2["model"] == "model-cadangan"
    assert p1["messages"] == p2["messages"]
    assert ai._fallback_until > time.monotonic()   # cooldown menyala


def test_cooldown_langsung_fallback(monkeypatch):
    fake = _wire(monkeypatch, [_Resp(200, OK_BODY)])
    monkeypatch.setattr(ai, "_fallback_until", time.monotonic() + 600, raising=False)
    out = ai._post_chat([], [])
    assert out == OK_BODY
    assert len(fake.posts) == 1 and fake.posts[0][0].startswith(FB_URL)


def test_cooldown_kedaluwarsa_reprobe_primary(monkeypatch):
    fake = _wire(monkeypatch, [_Resp(200, OK_BODY)])
    monkeypatch.setattr(ai, "_fallback_until", time.monotonic() - 1, raising=False)
    out = ai._post_chat([], [])
    assert out == OK_BODY
    assert len(fake.posts) == 1 and fake.posts[0][0].startswith(PRIMARY_URL)


def test_dua_provider_mati_error_primary_muncul(monkeypatch):
    # primary 402; fallback 500 dua kali (retry internal) → error PRIMARY (402) naik
    _wire(monkeypatch, [_Resp(402), _Resp(500), _Resp(500)])
    with pytest.raises(RuntimeError) as ei:
        ai._post_chat([], [])
    assert "402" in str(ei.value)
    assert ai._fallback_until == 0.0   # tak terkunci ke fallback yang mati


def test_tanpa_fallback_perilaku_lama(monkeypatch):
    _wire(monkeypatch, [_Resp(402)], fb=False)
    with pytest.raises(RuntimeError) as ei:
        ai._post_chat([], [])
    assert "402" in str(ei.value)


def test_jaringan_2x_lalu_fallback(monkeypatch):
    exc = real_requests.ConnectionError("putus")
    fake = _wire(monkeypatch, [exc, exc, _Resp(200, OK_BODY)])
    out = ai._post_chat([], [])
    assert out == OK_BODY
    assert len(fake.posts) == 3 and fake.posts[2][0].startswith(FB_URL)


def test_429_retry_lalu_fallback(monkeypatch):
    fake = _wire(monkeypatch, [_Resp(429), _Resp(429), _Resp(200, OK_BODY)])
    out = ai._post_chat([], [])
    assert out == OK_BODY
    urls = [u for u, _ in fake.posts]
    assert urls[0].startswith(PRIMARY_URL) and urls[1].startswith(PRIMARY_URL)
    assert urls[2].startswith(FB_URL)
