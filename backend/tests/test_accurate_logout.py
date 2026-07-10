"""Logout Accurate + idle-logout: sesi dilepas agar akun 1-sesi tak bentrok."""
import time

from app.services import accurate as a


def test_logout_hapus_file_sesi(monkeypatch, tmp_path):
    fp = tmp_path / "accurate_session.json"
    fp.write_text('{"jsessionid":"J","dsi":"D"}', encoding="utf-8")
    monkeypatch.setattr(a, "_session_file", lambda: fp)
    posted = {}
    monkeypatch.setattr(a.requests, "post",
                        lambda url, **k: posted.update(url=url) or type("R", (), {"status_code": 200})())
    assert a.logout() is True
    assert not fp.exists()                       # file sesi dihapus
    assert "close-database.do" in posted["url"]  # server sesi ditutup


def test_logout_aman_saat_tak_ada_sesi(monkeypatch, tmp_path):
    fp = tmp_path / "nihil.json"
    monkeypatch.setattr(a, "_session_file", lambda: fp)
    assert a.logout() is True                    # tak ada sesi → sukses, tak melempar


def test_logout_tak_melempar_saat_server_error(monkeypatch, tmp_path):
    fp = tmp_path / "accurate_session.json"
    fp.write_text('{"jsessionid":"J","dsi":"D"}', encoding="utf-8")
    monkeypatch.setattr(a, "_session_file", lambda: fp)

    def _boom(*x, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(a.requests, "post", _boom)
    assert a.logout() is True                    # best-effort: tetap hapus file
    assert not fp.exists()


def test_idle_logout_dipicu_saat_idle(monkeypatch, tmp_path):
    """Simulasikan satu iterasi logika idle: idle melewati ambang → logout."""
    fp = tmp_path / "accurate_session.json"
    fp.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(a, "_session_file", lambda: fp)
    dipanggil = {"n": 0}
    monkeypatch.setattr(a, "logout", lambda: dipanggil.update(n=dipanggil["n"] + 1))

    # Aktivitas terakhir jauh di masa lampau → idle > ambang.
    a._last_activity = time.monotonic() - (a._IDLE_LOGOUT_SEC + 5)
    idle = time.monotonic() - a._last_activity
    if idle > a._IDLE_LOGOUT_SEC and a._session_file().exists():
        a.logout()
    assert dipanggil["n"] == 1


def test_mark_activity_menunda_logout(monkeypatch, tmp_path):
    fp = tmp_path / "accurate_session.json"
    fp.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(a, "_session_file", lambda: fp)
    a._mark_activity()                            # aktivitas BARU saja
    idle = time.monotonic() - a._last_activity
    assert idle < a._IDLE_LOGOUT_SEC              # belum idle → tak logout


def test_start_idle_logout_idempoten(monkeypatch):
    # Reset flag agar deterministik.
    monkeypatch.setattr(a, "_idle_started", False)
    started = []
    monkeypatch.setattr(a.threading, "Thread",
                        lambda *args, **k: type("T", (), {"start": lambda self: started.append(1)})())
    assert a.start_idle_logout() is True          # pertama → mulai
    assert a.start_idle_logout() is False         # kedua → tak dobel
    assert len(started) == 1
