"""Tool asisten `pesanan_bermasalah` — ADMIN-ONLY.

Sinyal masalah sudah lama dicatat (payment_note dari jaring pengaman pembayaran,
penawaran_status dari Penawaran Accurate otomatis) tapi belum pernah ada yang
membacanya. Yang paling penting: 'uang_perlu_dicek' = uang pembeli yang sudah masuk
ke gateway tapi pesanannya batal / nominalnya beda → menunggu REFUND.
"""
import time

from app.services import ai_assistant as A
from app.services import orders as O

ADMIN = {"username": "mas", "role": "admin"}
CABANG = {"username": "jkt", "role": "user"}
PEMBELI = {"username": "budi", "role": "pembeli"}


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _rows():
    lama = _iso(time.time() - 10 * 86400)
    return [
        # dibayar setelah batal → perlu refund
        {"order_code": "PO-REFUND", "username": "budi", "gudang": "01.Jakarta", "total": 500_000,
         "status": "batal", "payment_method": "gateway", "created_at": lama,
         "payment_note": "Dibayar di gateway setelah pesanan BATAL (Rp500.000) — perlu refund."},
        # lunas tapi penawaran Accurate gagal
        {"order_code": "PO-QUOT", "username": "andi", "gudang": "04.Palembang", "total": 200_000,
         "status": "diproses", "payment_method": "gateway", "paid_at": _iso(time.time() - 3600),
         "created_at": lama, "penawaran_status": "failed", "penawaran_note": "Accurate 401"},
        # lunas 10 hari, belum dikirim
        {"order_code": "PO-STUCK", "username": "cici", "gudang": "01.Jakarta", "total": 300_000,
         "status": "diproses", "payment_method": "gateway", "paid_at": lama, "created_at": lama},
        # menunggu bayar & sudah lewat tenggat (rekonsiliasi tak bisa memutuskan)
        {"order_code": "PO-MACET", "username": "dodi", "gudang": "01.Jakarta", "total": 100_000,
         "status": "menunggu_pembayaran", "payment_method": "gateway", "created_at": lama,
         "payment_expiry": _iso(time.time() - 86400)},
        # sehat — tak boleh muncul di mana pun
        {"order_code": "PO-OK", "username": "eka", "gudang": "01.Jakarta", "total": 50_000,
         "status": "selesai", "payment_method": "gateway", "created_at": lama},
    ]


class _Resp:
    status_code = 200

    def raise_for_status(self):
        pass

    @staticmethod
    def json():
        return _rows()


def _setup(monkeypatch):
    monkeypatch.setattr(O.requests, "get", lambda *a, **k: _Resp())


# ── Pengelompokan ────────────────────────────────────────────────────────────
def test_uang_yang_perlu_refund_dikedepankan(monkeypatch):
    _setup(monkeypatch)

    res = O.problem_orders()

    assert [o["order_code"] for o in res["uang_perlu_dicek"]] == ["PO-REFUND"]
    assert "refund" in res["uang_perlu_dicek"][0]["catatan"].lower()
    assert res["ringkasan"]["uang_perlu_dicek"] == 1


def test_penawaran_accurate_gagal_terdeteksi(monkeypatch):
    _setup(monkeypatch)

    res = O.problem_orders()

    assert [o["order_code"] for o in res["penawaran_gagal"]] == ["PO-QUOT"]


def test_lunas_lama_belum_dikirim(monkeypatch):
    _setup(monkeypatch)

    res = O.problem_orders(stuck_days=3)

    codes = [o["order_code"] for o in res["lunas_belum_dikirim"]]
    assert codes == ["PO-STUCK"]            # PO-QUOT baru lunas 1 jam → belum 'nyangkut'
    assert res["lunas_belum_dikirim"][0]["umur_hari"] >= 10


def test_ambang_hari_dihormati(monkeypatch):
    _setup(monkeypatch)

    assert O.problem_orders(stuck_days=30)["lunas_belum_dikirim"] == []


def test_bayar_lewat_tenggat_tapi_belum_beres(monkeypatch):
    _setup(monkeypatch)

    res = O.problem_orders()

    assert [o["order_code"] for o in res["bayar_macet"]] == ["PO-MACET"]


def test_pesanan_sehat_tak_pernah_muncul(monkeypatch):
    _setup(monkeypatch)

    res = O.problem_orders()
    semua = [o["order_code"] for k in ("uang_perlu_dicek", "penawaran_gagal",
                                       "lunas_belum_dikirim", "bayar_macet") for o in res[k]]

    assert "PO-OK" not in semua


def test_tanpa_masalah_katakan_bersih(monkeypatch):
    class _Kosong(_Resp):
        @staticmethod
        def json():
            return [{"order_code": "PO-OK", "status": "selesai", "total": 1,
                     "payment_method": "gateway", "created_at": _iso(time.time())}]

    monkeypatch.setattr(O.requests, "get", lambda *a, **k: _Kosong())

    out = A._t_pesanan_bermasalah({}, ADMIN)

    assert out["ada_masalah"] is False
    assert "Tidak ada pesanan bermasalah" in out["jawaban_wajib"]


def test_skema_lama_tanpa_kolom_penanda_tidak_meledak(monkeypatch):
    """Migrasi 018/019 belum jalan → select penuh gagal; jangan gagal total."""
    calls = {"n": 0}

    def _get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("column orders.payment_note does not exist")
        return _Resp()

    monkeypatch.setattr(O.requests, "get", _get)

    res = O.problem_orders()

    assert calls["n"] == 2                  # jatuh ke select minimal
    assert res["ringkasan"]["diperiksa"] == 5


# ── Peran: ADMIN-ONLY ────────────────────────────────────────────────────────
def test_non_admin_ditolak(monkeypatch):
    _setup(monkeypatch)

    for u in (PEMBELI, CABANG):
        assert A._t_pesanan_bermasalah({}, u).get("denied") is True
        assert A._run_tool("pesanan_bermasalah", {}, u).get("denied") is True


def test_hanya_admin_ditawari_toolnya():
    assert "pesanan_bermasalah" in {s["function"]["name"] for s in A._tool_specs(ADMIN)}
    for u in (PEMBELI, CABANG):
        assert "pesanan_bermasalah" not in {s["function"]["name"] for s in A._tool_specs(u)}
