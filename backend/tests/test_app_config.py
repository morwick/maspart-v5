"""Config aplikasi mobile: GET /api/app/meta (publik) + panel admin.

Aplikasi memanggil /api/app/meta setiap kali dibuka untuk (a) notifikasi update
in-app dan (b) feature-flag server-driven. Sebelum ini endpointnya TIDAK ADA
(404 di produksi) sehingga pengguna lama tak pernah diberi tahu ada APK baru.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import app_config

ADMIN = {"username": "admin", "role": "admin"}
USER = {"username": "budi", "role": "user"}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Arahkan penyimpanan ke folder sementara + bersihkan cache proses."""
    s = app_config.get_settings()
    monkeypatch.setattr(type(s), "data_path", property(lambda _self: tmp_path))
    app_config.reset_cache()
    yield tmp_path
    app_config.reset_cache()


@pytest.fixture
def klien(monkeypatch):
    from app import deps
    app.dependency_overrides[deps.require_admin] = lambda: ADMIN
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Endpoint publik yang dipanggil aplikasi ─────────────────────────────────

def test_app_meta_terbuka_tanpa_login(data_dir, klien):
    """Aplikasi memanggilnya di luar sesi login — 401 akan mematikan fitur."""
    r = klien.get("/api/app/meta")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"version", "config"}
    assert body["version"]["download_url"] == "https://maspart.tech/download"
    assert body["version"]["force"] is False          # jangan memaksa update tanpa diminta


def test_app_meta_tetap_melayani_walau_file_rusak(data_dir, klien):
    """⛔ Tak boleh 500: dipanggil tiap kali aplikasi dibuka."""
    (data_dir / "app_config.json").write_text("{ini bukan json", encoding="utf-8")
    app_config.reset_cache()
    r = klien.get("/api/app/meta")
    assert r.status_code == 200 and r.json()["version"]["latest_name"]


# ── Panel admin ─────────────────────────────────────────────────────────────

def test_admin_simpan_versi_lalu_terbaca_aplikasi(data_dir, klien):
    r = klien.put("/api/admin/app-config", json={
        "version": {"latest_name": "2.2.0", "min_name": "2.1.0", "force": True,
                    "latest_code": 8, "min_code": 7,
                    "download_url": "https://maspart.tech/download"},
        "config": {"asisten_suggestions": ["stok filter oli"],
                   "foto": {"tta_default": False, "top_k": 30, "threshold": 0.25}},
    })
    assert r.status_code == 200
    # Yang dilihat APLIKASI harus ikut berubah — bukan hanya balasan panel.
    meta = klien.get("/api/app/meta").json()
    assert meta["version"]["latest_name"] == "2.2.0"
    assert meta["version"]["force"] is True
    assert meta["config"]["asisten_suggestions"] == ["stok filter oli"]
    assert meta["config"]["foto"]["top_k"] == 30
    # Persisten di disk (restart container tak menghapusnya).
    tersimpan = json.loads((data_dir / "app_config.json").read_text(encoding="utf-8"))
    assert tersimpan["version"]["latest_name"] == "2.2.0"


def test_simpan_config_saja_tidak_menghapus_versi(data_dir, klien):
    klien.put("/api/admin/app-config", json={"version": {"latest_name": "2.2.0"}})
    klien.put("/api/admin/app-config", json={"config": {"search": {"page_size": 50}}})
    meta = klien.get("/api/app/meta").json()
    assert meta["version"]["latest_name"] == "2.2.0"     # tak ikut terhapus
    assert meta["config"]["search"]["page_size"] == 50


def test_tipe_ngawur_dari_panel_tidak_bocor_ke_aplikasi(data_dir, klien):
    """Panel mengirim JSON mentah; aplikasi mem-parse dgn tipe ketat."""
    klien.put("/api/admin/app-config", json={
        "version": {"latest_code": "bukan angka", "force": 1,
                    "latest_name": "  2.3.0  ", "download_url": ""}})
    v = klien.get("/api/app/meta").json()["version"]
    assert v["latest_code"] == 0                  # gagal parse → nilai lama dipertahankan
    assert v["force"] is True                     # 1 → bool
    assert v["latest_name"] == "2.3.0"            # spasi dipangkas
    assert v["download_url"] == "https://maspart.tech/download"   # kosong → default


def test_app_config_admin_only(data_dir, monkeypatch):
    """Versi & flag mengendalikan aplikasi semua orang — jangan bisa diubah user biasa."""
    from app import deps

    def _tolak():
        from fastapi import HTTPException
        raise HTTPException(403, "admin only")

    app.dependency_overrides[deps.require_admin] = _tolak
    try:
        c = TestClient(app)
        assert c.get("/api/admin/app-config").status_code == 403
        assert c.put("/api/admin/app-config", json={"config": {}}).status_code == 403
    finally:
        app.dependency_overrides.clear()
