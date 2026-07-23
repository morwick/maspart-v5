"""Retry jaringan transien fetcher SIMS + epc.get_config (H2 paket handal
2026-07-23). Dulu satu blip DNS/koneksi = hasil {} yang terbaca asisten sebagai
'tidak ditemukan' (bohong). Retry HANYA utk RequestException — status HTTP
(termasuk 401/403 refresh token) diteruskan apa adanya."""
import json

import pytest
import requests as real_requests

from shared import sims_fetcher
from app.services import epc


class DummyResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise real_requests.HTTPError(f"{self.status_code}")


def _flaky(script):
    """requests.get palsu: item Exception dilempar, selain itu dikembalikan."""
    calls = {"n": 0}

    def get(url, params=None, headers=None, timeout=None, **kw):
        item = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return get, calls


@pytest.fixture(autouse=True)
def _cepat(monkeypatch):
    monkeypatch.setattr(sims_fetcher.time, "sleep", lambda s: None)


def test_get_retry_flaky_sekali_lalu_sukses(monkeypatch):
    ok = DummyResp(200)
    get, calls = _flaky([real_requests.ConnectionError("blip"), ok])
    monkeypatch.setattr(sims_fetcher.requests, "get", get)
    assert sims_fetcher._get_retry("http://x") is ok
    assert calls["n"] == 2


def test_get_retry_habis_lempar_error(monkeypatch):
    get, calls = _flaky([real_requests.ConnectionError("mati")])
    monkeypatch.setattr(sims_fetcher.requests, "get", get)
    with pytest.raises(real_requests.ConnectionError):
        sims_fetcher._get_retry("http://x")
    assert calls["n"] == 2  # 2 attempt lalu menyerah


def test_get_retry_status_http_diteruskan(monkeypatch):
    """401 TIDAK di-retry oleh helper — refresh token urusan pemanggil."""
    get, calls = _flaky([DummyResp(401)])
    monkeypatch.setattr(sims_fetcher.requests, "get", get)
    r = sims_fetcher._get_retry("http://x")
    assert r.status_code == 401 and calls["n"] == 1


def test_fetch_part_info_lolos_blip(monkeypatch, tmp_path):
    """Wiring: fetch_sims_part_info selamat dari 1 blip jaringan."""
    monkeypatch.setattr(sims_fetcher, "_get_token", lambda: "Bearer tok")
    monkeypatch.setattr(sims_fetcher, "PART_INFO_JSON", tmp_path / "part_info.json")
    ok = DummyResp(200, {"data": {"rows": [{"partName": "MIRROR", "partCode": "WG1"}]}})
    get, calls = _flaky([real_requests.ConnectionError("blip"), ok])
    monkeypatch.setattr(sims_fetcher.requests, "get", get)
    info = sims_fetcher.fetch_sims_part_info("WG1", force_refresh=True)
    assert info.get("partName") == "MIRROR"
    assert calls["n"] == 2


def test_epc_get_config_lolos_blip(monkeypatch):
    ok = DummyResp(200, {"success": True, "data": {"brandName": "HOWO"}})
    get, calls = _flaky([real_requests.ConnectionError("blip"), ok])
    monkeypatch.setattr(epc.requests, "get", get)
    monkeypatch.setattr(epc.time, "sleep", lambda s: None)
    epc._cache.clear()
    out = epc.get_config("RT108966")
    assert out.get("brandName") == "HOWO" and calls["n"] == 2
    assert epc._cache  # hit sah di-cache


def test_epc_get_config_gagal_total_tak_dicache(monkeypatch):
    get, calls = _flaky([real_requests.ConnectionError("mati")])
    monkeypatch.setattr(epc.requests, "get", get)
    monkeypatch.setattr(epc.time, "sleep", lambda s: None)
    epc._cache.clear()
    assert epc.get_config("RT108966") == {}
    assert calls["n"] == 2 and not epc._cache
