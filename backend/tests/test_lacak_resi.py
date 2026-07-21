"""Lacak resi: bacakan manifest kurir untuk resi yang diketik admin cabang.

⛔ BUKAN pemesanan pengiriman — resi tetap dibuat gerai ekspedisi. Endpoint ini
hanya menghemat langkah pembeli menyalin nomor ke situs kurir.
"""
import pytest
from fastapi import HTTPException

from app.core import ratelimit as RL
from app.routers import orders as R
from app.services import shipping

USER = {"username": "budi", "role": "pembeli"}


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = str(self._body)

    def json(self):
        return self._body


_MANIFEST = {
    "meta": {"code": 200, "status": "success"},
    "data": {
        "delivered": True,
        "summary": {"service_code": "REG", "status": "DELIVERED"},
        "delivery_status": {"status": "DELIVERED", "pod_receiver": "SATPAM",
                            "pod_date": "2026-07-20"},
        "manifest": [
            {"manifest_date": "2026-07-18", "manifest_time": "09:00",
             "manifest_description": "Diterima di gerai", "city_name": "JAKARTA"},
            {"manifest_date": "2026-07-20", "manifest_time": "14:10",
             "manifest_description": "Terkirim", "city_name": "PEKANBARU"},
        ],
    },
}


@pytest.fixture(autouse=True)
def bersih(monkeypatch):
    shipping.bersihkan_cache_lacak()
    RL._hits.clear()
    monkeypatch.setattr(shipping, "available", lambda: True)
    yield
    shipping.bersihkan_cache_lacak()
    RL._hits.clear()


def _order(**kw):
    o = {"order_code": "PO-1", "status": "dikirim", "courier": "jne",
         "tracking_no": "JNE123456789"}
    o.update(kw)
    return o


# ── Service ─────────────────────────────────────────────────────────────────

def test_riwayat_terbaru_di_atas(monkeypatch):
    """Kurir mengirim manifest dari yang TERLAMA; pembeli ingin yang terbaru dulu."""
    monkeypatch.setattr(shipping.requests, "post", lambda *a, **k: _Resp(200, _MANIFEST))
    hasil, err = shipping.track("jne123456789", "JNE")
    assert err is None
    assert hasil["delivered"] is True and hasil["status"] == "DELIVERED"
    assert hasil["resi"] == "JNE123456789"                 # dinormalkan huruf besar
    assert hasil["riwayat"][0]["keterangan"] == "Terkirim"  # terbaru dulu
    assert hasil["riwayat"][0]["lokasi"] == "PEKANBARU"
    assert hasil["penerima"] == "SATPAM"


def test_hasil_di_cache_agar_tak_menguras_kuota(monkeypatch):
    """Halaman pesanan memanggil tiap dibuka; paket tak berpindah semenit sekali."""
    n = {"panggil": 0}

    def _post(*a, **k):
        n["panggil"] += 1
        return _Resp(200, _MANIFEST)

    monkeypatch.setattr(shipping.requests, "post", _post)
    for _ in range(5):
        shipping.track("JNE1", "jne")
    assert n["panggil"] == 1


def test_resi_beda_tidak_ikut_cache(monkeypatch):
    monkeypatch.setattr(shipping.requests, "post", lambda *a, **k: _Resp(200, _MANIFEST))
    shipping.track("JNE1", "jne")
    n = {"panggil": 0}

    def _post(*a, **k):
        n["panggil"] += 1
        return _Resp(200, _MANIFEST)

    monkeypatch.setattr(shipping.requests, "post", _post)
    shipping.track("JNE2", "jne")
    assert n["panggil"] == 1


def test_pesan_kurir_diteruskan_apa_adanya(monkeypatch):
    """'Invalid Awb' jauh lebih berguna bagi admin daripada 'gagal'."""
    monkeypatch.setattr(shipping.requests, "post",
                        lambda *a, **k: _Resp(404, {"meta": {"message": "Invalid Awb"}}))
    hasil, err = shipping.track("SALAH", "jne")
    assert hasil is None and err == "Invalid Awb"


def test_gagal_jaringan_tak_melempar(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("jaringan mati")
    monkeypatch.setattr(shipping.requests, "post", _boom)
    hasil, err = shipping.track("JNE1", "jne")
    assert hasil is None and "Gagal menghubungi" in err


def test_kegagalan_tidak_ikut_di_cache(monkeypatch):
    """Kalau error ikut di-cache, paket yang baru saja discan tetap tampil gagal
    selama 10 menit."""
    monkeypatch.setattr(shipping.requests, "post",
                        lambda *a, **k: _Resp(404, {"meta": {"message": "Invalid Awb"}}))
    assert shipping.track("JNE1", "jne")[0] is None
    monkeypatch.setattr(shipping.requests, "post", lambda *a, **k: _Resp(200, _MANIFEST))
    hasil, err = shipping.track("JNE1", "jne")
    assert err is None and hasil["delivered"] is True


# ── Endpoint ────────────────────────────────────────────────────────────────

def test_endpoint_kembalikan_riwayat(monkeypatch):
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: _order())
    monkeypatch.setattr(shipping.requests, "post", lambda *a, **k: _Resp(200, _MANIFEST))
    out = R.order_tracking("PO-1", USER)
    assert out["ada_resi"] is True and out["delivered"] is True
    assert len(out["riwayat"]) == 2


def test_belum_ada_resi_bukan_error(monkeypatch):
    """Admin belum mengisi resi → halaman pesanan tetap tampil normal."""
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: _order(tracking_no=""))
    out = R.order_tracking("PO-1", USER)
    assert out["ada_resi"] is False and "belum diisi" in out["error"]


def test_layanan_mati_dikembalikan_sbg_error_bukan_exception(monkeypatch):
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: _order())
    monkeypatch.setattr(shipping, "available", lambda: False)
    out = R.order_tracking("PO-1", USER)
    assert out["ada_resi"] is True and out["error"]


def test_pesanan_orang_lain_tak_bisa_dilacak(monkeypatch):
    """get_order dipanggil dengan filter username untuk non-admin."""
    dilihat = {}

    def _get(code, username=None):
        dilihat["username"] = username
        return None

    monkeypatch.setattr(R.orders, "get_order", _get)
    with pytest.raises(HTTPException) as e:
        R.order_tracking("PO-9", USER)
    assert e.value.status_code == 404 and dilihat["username"] == "budi"


def test_dibatasi_per_akun(monkeypatch):
    """Tiap panggilan bisa menembak API kurir — jangan biarkan dipompa."""
    monkeypatch.setattr(R.orders, "get_order", lambda code, username=None: _order(tracking_no=""))
    for _ in range(20):
        R.order_tracking("PO-1", USER)
    with pytest.raises(HTTPException) as e:
        R.order_tracking("PO-1", USER)
    assert e.value.status_code == 429
