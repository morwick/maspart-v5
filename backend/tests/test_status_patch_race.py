"""PATCH status BERSYARAT (anti-TOCTOU): cancel/expire/mark_paid menambah filter
`status=in.(…)` — 0 baris kena = kalah race → gagal TANPA menulis.

Regresi yang dijaga: dulu check-then-PATCH tanpa syarat → pembeli klik batal
berbarengan dgn webhook lunas bisa menimpa order 'diproses' jadi 'batal' (uang
tertahan, stok dilepas, tanpa jejak); atau webhook ganda double-commit reservasi.
"""
import pytest

from app.services import orders, reservations


class _Resp:
    def __init__(self, status_code, rows):
        self.status_code = status_code
        self._rows = rows

    def json(self):
        return self._rows


@pytest.fixture
def capture(monkeypatch):
    """Rekam params PATCH & kendalikan baris yang 'kena'. rows=[] → kalah race."""
    seen = {"params": None, "rows": [{"id": 1}]}

    def _patch(url, headers=None, params=None, json=None, timeout=None):
        seen["params"] = params
        return _Resp(200, seen["rows"])

    monkeypatch.setattr(orders.requests, "patch", _patch)
    return seen


# ── _patch low-level ────────────────────────────────────────────────────────
def test_patch_bersyarat_menambah_filter_status(capture):
    orders._patch("PO-1", {"status": "batal"}, expect_status={"menunggu_pembayaran"})
    assert "status" in capture["params"]
    assert capture["params"]["status"].startswith("in.(")
    assert "menunggu_pembayaran" in capture["params"]["status"]


def test_patch_bersyarat_nol_baris_return_false(capture):
    capture["rows"] = []                       # kalah race — tak ada baris cocok
    assert orders._patch("PO-1", {"status": "batal"},
                         expect_status={"menunggu_pembayaran"}) is False


def test_patch_bersyarat_ada_baris_return_true(capture):
    capture["rows"] = [{"id": 9}]
    assert orders._patch("PO-1", {"status": "batal"},
                         expect_status={"menunggu_pembayaran"}) is True


def test_patch_tanpa_expect_status_tak_ada_filter(capture):
    orders._patch("PO-1", {"status": "batal"})
    assert "status" not in capture["params"]   # perilaku lama tak berubah


# ── cancel_by_buyer kalah race ──────────────────────────────────────────────
def test_cancel_kalah_race_saat_sudah_diproses(monkeypatch, capture):
    """Order tampak cancelable saat dibaca, tapi keburu lunas → PATCH 0 baris →
    gagal & reservasi TAK dilepas (order lunas tetap utuh)."""
    capture["rows"] = []                       # PATCH tak kena baris apa pun
    monkeypatch.setattr(orders, "get_order",
                        lambda code, username=None: {"order_code": code, "status": "menunggu_pembayaran"})
    dilepas = []
    monkeypatch.setattr(reservations, "release", lambda code: dilepas.append(code))

    ok, err = orders.cancel_by_buyer("PO-1", "budi")
    assert ok is False and err
    assert dilepas == []                       # reservasi order lunas TAK dilepas


def test_cancel_sukses_saat_menang_race(monkeypatch, capture):
    capture["rows"] = [{"id": 1}]
    monkeypatch.setattr(orders, "get_order",
                        lambda code, username=None: {"order_code": code, "status": "menunggu_pembayaran"})
    dilepas = []
    monkeypatch.setattr(reservations, "release", lambda code: dilepas.append(code))

    ok, err = orders.cancel_by_buyer("PO-1", "budi")
    assert ok is True and err is None
    assert dilepas == ["PO-1"]


# ── mark_paid idempoten / anti-hidupkan-batal ───────────────────────────────
def test_mark_paid_kalah_race_tak_commit(monkeypatch, capture):
    """Order sudah 'batal'/'diproses' → PATCH 0 baris → tak commit reservasi,
    tak kirim notif (idempoten, tak menghidupkan order batal)."""
    capture["rows"] = []
    committed = []
    monkeypatch.setattr(reservations, "commit", lambda code: committed.append(code))
    monkeypatch.setattr(orders, "_notify_paid_async", lambda code: committed.append(("notif", code)))

    assert orders.mark_paid("PO-1", raw={}) is False
    assert committed == []                     # tak ada efek samping


def test_mark_paid_sukses_commit(monkeypatch, capture):
    capture["rows"] = [{"id": 1}]
    committed = []
    monkeypatch.setattr(reservations, "commit", lambda code: committed.append(code))
    monkeypatch.setattr(orders, "_notify_paid_async", lambda code: None)

    assert orders.mark_paid("PO-1", raw={}) is True
    assert committed == ["PO-1"]


# ── expire_order kalah race ─────────────────────────────────────────────────
def test_expire_kalah_race_tak_lepas_reservasi(monkeypatch, capture):
    """Order keburu dibayar saat sweeper mau meng-expire → 0 baris → tak batal,
    tak lepas reservasi (order lunas selamat)."""
    capture["rows"] = []
    dilepas = []
    monkeypatch.setattr(reservations, "release", lambda code: dilepas.append(code))

    assert orders.expire_order("PO-1") is False
    assert dilepas == []
