"""Hapus observabilitas AI: delete_before (>N hari) / delete_all + retensi 30 hari.
HTTP Supabase di-mock (tanpa jaringan)."""
from app.services import ai_chat_log as cl


class _Resp:
    def __init__(self, status=204, count=None):
        self.status_code = status
        self.headers = {"Content-Range": f"*/{count}"} if count is not None else {}


def _capture(monkeypatch):
    calls = {}

    def fake_delete(url, headers=None, params=None, timeout=None):
        calls["url"] = url
        calls["params"] = params
        calls["prefer"] = (headers or {}).get("Prefer")
        return _Resp(204, count=calls.get("_count", 5))

    monkeypatch.setattr(cl.requests, "delete", fake_delete)
    return calls


def test_delete_before_filter_dan_count(monkeypatch):
    calls = _capture(monkeypatch)
    ok, n = cl.delete_before(30)
    assert ok and n == 5
    # filter created_at < cutoff (lebih tua dari), count=exact utk hitung
    assert calls["params"]["created_at"].startswith("lt.")
    assert "count=exact" in (calls["prefer"] or "")


def test_delete_all_cocokkan_semua(monkeypatch):
    calls = _capture(monkeypatch)
    ok, n = cl.delete_all()
    assert ok and n == 5
    assert calls["params"]["created_at"].startswith("lte.")   # <=now = semua baris


def test_delete_before_nol_hari_jadi_semua(monkeypatch):
    calls = _capture(monkeypatch)
    cl.delete_before(0)
    assert calls["params"]["created_at"].startswith("lte.")    # days<=0 → delete_all


def test_delete_gagal_non_2xx(monkeypatch):
    monkeypatch.setattr(cl.requests, "delete",
                        lambda *a, **k: _Resp(500))
    ok, n = cl.delete_all()
    assert ok is False and n == -1


def test_delete_count_tak_terbaca(monkeypatch):
    monkeypatch.setattr(cl.requests, "delete", lambda *a, **k: _Resp(204, count=None))
    ok, n = cl.delete_before(7)
    assert ok is True and n == -1        # tak ada Content-Range → -1 (tetap sukses)


def test_cutoff_lebih_awal_dari_now():
    assert cl._cutoff(30) < cl._now()


def test_retensi_idempoten(monkeypatch):
    monkeypatch.setattr(cl, "_retention_started", False)
    monkeypatch.setattr(cl, "delete_before", lambda d: (True, 0))
    # start pertama True; panggilan kedua False (sudah jalan). Thread daemon aman.
    assert cl.start_retention() is True
    assert cl.start_retention() is False
