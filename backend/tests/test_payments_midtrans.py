"""Midtrans Snap: create (redirect_url), status, verifikasi signature webhook."""
import hashlib
from types import SimpleNamespace

import pytest

from app.services import payments

SK = "SB-Mid-server-TESTKEY"


def _cfg(server_key=SK):
    return SimpleNamespace(
        midtrans_server_key=server_key,
        midtrans_app_base="https://app.sandbox.midtrans.com",
        midtrans_api_base="https://api.sandbox.midtrans.com",
        public_base_url="https://maspart.tech",
        payment_configured=bool(server_key),
    )


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(payments, "get_settings", lambda: _cfg())


class FakeResp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


# ── status mapping ────────────────────────────────────────────────────────────

def test_norm_status_mapping():
    assert payments._norm_status("settlement") == "paid"
    assert payments._norm_status("capture", "accept") == "paid"
    assert payments._norm_status("capture", "challenge") == "pending"
    assert payments._norm_status("pending") == "pending"
    assert payments._norm_status("expire") == "failed"
    assert payments._norm_status("deny") == "failed"
    assert payments._norm_status("cancel") == "failed"


def test_amount_int():
    assert payments._amount_int("150000.00") == 150000
    assert payments._amount_int(150000) == 150000
    assert payments._amount_int(None) == 0


# ── available / methods ───────────────────────────────────────────────────────

def test_available(cfg):
    assert payments.available() is True


def test_not_available_without_key(monkeypatch):
    monkeypatch.setattr(payments, "get_settings", lambda: _cfg(server_key=""))
    assert payments.available() is False
    pay, err = payments.create_payment("ORD-1", 1000, "snap")
    assert pay is None and "belum diaktifkan" in err


def test_list_methods_snap(cfg):
    methods, err = payments.list_methods()
    assert err is None and len(methods) == 1 and methods[0]["code"] == "snap"


# ── create_payment ────────────────────────────────────────────────────────────

def test_create_payment(cfg, monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return FakeResp(201, {"token": "tok", "redirect_url": "https://app.sandbox.midtrans.com/snap/v2/vtweb/abc"})

    monkeypatch.setattr(payments.requests, "post", fake_post)
    pay, err = payments.create_payment("ORD-1", 150000, "snap",
                                       {"name": "Budi", "email": "b@x.com", "phone": "08123456789"})
    assert err is None
    assert pay["ref"] == "ORD-1" and pay["channel"] == "snap" and pay["status"] == "pending"
    assert pay["url"].startswith("https://app.sandbox.midtrans.com/snap/")
    # Batas bayar diisi (ISO Z) → order bisa auto-batal saat kedaluwarsa.
    assert pay["expiry"] and pay["expiry"].endswith("Z") and "T" in pay["expiry"]
    assert captured["url"].endswith("/snap/v1/transactions")
    assert captured["body"]["transaction_details"] == {"order_id": "ORD-1", "gross_amount": 150000}
    assert captured["body"]["callbacks"]["finish"] == "https://maspart.tech/pesanan/ORD-1"
    assert captured["headers"]["Authorization"].startswith("Basic ")


def test_create_payment_gateway_error(cfg, monkeypatch):
    monkeypatch.setattr(payments.requests, "post",
                        lambda *a, **k: FakeResp(401, text="unauthorized"))
    pay, err = payments.create_payment("ORD-1", 1000, "snap")
    assert pay is None and "401" in err


# ── get_status ────────────────────────────────────────────────────────────────

def test_get_status_paid(cfg, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/v2/ORD-1/status")
        return FakeResp(200, {"transaction_status": "settlement", "gross_amount": "150000.00", "order_id": "ORD-1"})

    monkeypatch.setattr(payments.requests, "get", fake_get)
    res, err = payments.get_status("ORD-1")
    assert err is None and res["status"] == "paid" and res["amount"] == 150000 and res["order_id"] == "ORD-1"


def test_get_status_404_pending(cfg, monkeypatch):
    monkeypatch.setattr(payments.requests, "get", lambda *a, **k: FakeResp(404, {}))
    res, err = payments.get_status("ORD-NEW")
    assert err is None and res["status"] == "pending"


# ── webhook signature ─────────────────────────────────────────────────────────

def _signed(order_id="ORD-1", status_code="200", gross="150000.00",
            transaction_status="settlement", key=SK):
    sig = hashlib.sha512(f"{order_id}{status_code}{gross}{key}".encode()).hexdigest()
    return {"order_id": order_id, "status_code": status_code, "gross_amount": gross,
            "transaction_status": transaction_status, "signature_key": sig}


def test_webhook_valid_signature(cfg):
    data, err = payments.parse_webhook({}, _signed())
    assert err is None
    assert data["status"] == "paid" and data["ref"] == "ORD-1" and data["amount"] == 150000


def test_webhook_bad_signature(cfg):
    payload = _signed()
    payload["signature_key"] = "0" * 128           # salah
    data, err = payments.parse_webhook({}, payload)
    assert data is None and "Signature" in err


def test_webhook_missing_signature(cfg):
    payload = _signed()
    del payload["signature_key"]
    data, err = payments.parse_webhook({}, payload)
    assert data is None and err


def test_webhook_signature_from_other_serverkey_rejected(cfg):
    # signature dibuat dgn key berbeda → ditolak (fail-closed).
    payload = _signed(key="SB-Mid-server-ATTACKER")
    data, err = payments.parse_webhook({}, payload)
    assert data is None and err
