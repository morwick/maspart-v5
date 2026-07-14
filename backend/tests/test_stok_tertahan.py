"""Tool asisten `stok_tertahan` — membongkar SELISIH stok Accurate vs yang bisa dibeli.

Stok yang dipajang = stok Accurate − reservasi aktif. Kalau angkanya terlihat 'kurang',
penyebabnya hampir selalu pesanan lain yang menahan barang itu. Tool ini menunjuk
pesanan mana. Karena membuka kode pesanan & penahan stok LINTAS CABANG → ADMIN-ONLY:
pembeli TIDAK boleh, akun cabang juga TIDAK.
"""
import time

from app.services import ai_assistant as A
from app.services import orders as O
from app.services import reservations as RES

ADMIN = {"username": "mas", "role": "admin"}
CABANG = {"username": "jkt", "role": "user"}
PEMBELI = {"username": "budi", "role": "pembeli"}

PN = "WG9725190070"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _setup(monkeypatch, rows, breakdown=None, statuses=None):
    monkeypatch.setattr(A.reservations, "active_rows",
                        lambda part_number="", gudang_label="", limit=500: [
                            r for r in rows
                            if not part_number or r["part_number"] == part_number
                        ])
    monkeypatch.setattr(A.part_index, "gudang_breakdown",
                        lambda pn: dict(breakdown or {}))
    monkeypatch.setattr(A.orders, "status_map", lambda codes: dict(statuses or {}))
    monkeypatch.setattr(A.harga, "price_for_buyer", lambda pn: (1_000_000, "Kampas Rem"))


def _row(order="PO-1", gudang="04.Palembang", qty=2, expires=None):
    return {"order_code": order, "part_number": PN, "gudang_label": gudang,
            "qty": qty, "expires_at": expires}


# ── Inti: menjelaskan selisih 3 → 1 ──────────────────────────────────────────
def test_menjelaskan_selisih_stok_accurate_vs_bisa_dibeli(monkeypatch):
    _setup(
        monkeypatch,
        rows=[_row(qty=2, expires=_iso(time.time() + 3600))],
        breakdown={"04.Palembang": 3},
        statuses={"PO-1": {"order_code": "PO-1", "status": "menunggu_pembayaran"}},
    )

    out = A._t_stok_tertahan({"part_number": PN}, ADMIN)

    assert out["total_tertahan"] == 2
    assert out["per_gudang"] == [
        {"gudang": "04.Palembang", "stok_accurate": 3, "tertahan": 2, "bisa_dibeli": 1},
    ]
    # Pesanan penahannya ditunjuk, bukan cuma angkanya.
    assert out["penahan"][0]["order_code"] == "PO-1"
    assert out["penahan"][0]["status_pesanan"] == "menunggu_pembayaran"


def test_reservasi_permanen_berarti_pesanan_sudah_lunas(monkeypatch):
    """expires_at NULL = order lunas (dicommit) → ditahan sampai dikirim."""
    _setup(
        monkeypatch,
        rows=[_row(expires=None)],
        breakdown={"04.Palembang": 2},
        statuses={"PO-1": {"order_code": "PO-1", "status": "diproses"}},
    )

    out = A._t_stok_tertahan({"part_number": PN}, ADMIN)

    assert "sampai dikirim" in out["penahan"][0]["ditahan_sampai"]
    assert out["per_gudang"][0]["bisa_dibeli"] == 0


def test_tanpa_reservasi_katakan_stok_apa_adanya(monkeypatch):
    _setup(monkeypatch, rows=[], breakdown={"04.Palembang": 3})

    out = A._t_stok_tertahan({"part_number": PN}, ADMIN)

    assert out["ada_reservasi"] is False
    assert out["total_tertahan"] == 0
    assert "sama persis dengan stok Accurate" in out["jawaban_wajib"]


def test_gudang_tanpa_reservasi_tetap_tampil_utuh(monkeypatch):
    """Gudang lain yang tak tertahan jangan hilang dari tabel."""
    _setup(
        monkeypatch,
        rows=[_row(gudang="04.Palembang", qty=2, expires=_iso(time.time() + 3600))],
        breakdown={"04.Palembang": 3, "01.Jakarta": 5},
        statuses={"PO-1": {"order_code": "PO-1", "status": "menunggu_pembayaran"}},
    )

    out = A._t_stok_tertahan({"part_number": PN}, ADMIN)
    per_g = {g["gudang"]: g for g in out["per_gudang"]}

    assert per_g["01.Jakarta"]["bisa_dibeli"] == 5     # utuh, tak ada yang menahan
    assert per_g["04.Palembang"]["bisa_dibeli"] == 1


def test_saring_per_gudang(monkeypatch):
    _setup(
        monkeypatch,
        rows=[_row(gudang="04.Palembang", qty=2, expires=_iso(time.time() + 3600)),
              _row(order="PO-2", gudang="01.Jakarta", qty=1, expires=_iso(time.time() + 3600))],
        breakdown={"04.Palembang": 3, "01.Jakarta": 5},
        statuses={"PO-1": {"status": "menunggu_pembayaran"}, "PO-2": {"status": "diproses"}},
    )

    out = A._t_stok_tertahan({"part_number": PN, "gudang": "palembang"}, ADMIN)

    assert [p["order_code"] for p in out["penahan"]] == ["PO-1"]
    assert [g["gudang"] for g in out["per_gudang"]] == ["04.Palembang"]
    assert out["total_tertahan"] == 2


def test_gudang_tak_dikenal_ditolak_dengan_saran(monkeypatch):
    _setup(monkeypatch, rows=[_row()])
    monkeypatch.setattr(A, "_gudang_list", lambda: ["04.Palembang", "01.Jakarta"])

    out = A._t_stok_tertahan({"part_number": PN, "gudang": "bulan"}, ADMIN)

    assert out["found"] is False
    assert "palembang" in out["gudang_tersedia"]


def test_tanpa_part_number_daftar_semua_reservasi_aktif(monkeypatch):
    _setup(
        monkeypatch,
        rows=[_row(qty=2, expires=_iso(time.time() + 3600)),
              _row(order="PO-2", qty=1, expires=_iso(time.time() + 3600))],
        statuses={"PO-1": {"status": "menunggu_pembayaran"}, "PO-2": {"status": "diproses"}},
    )

    out = A._t_stok_tertahan({}, ADMIN)

    assert out["total_tertahan"] == 3
    assert "per_gudang" not in out          # tanpa PN tak ada tabel stok per gudang
    assert len(out["penahan"]) == 2


# ── Peran: ADMIN-ONLY (pembeli DAN cabang ditolak) ───────────────────────────
def test_non_admin_ditolak_di_handler(monkeypatch):
    _setup(monkeypatch, rows=[_row()])

    for u in (PEMBELI, CABANG):
        out = A._t_stok_tertahan({"part_number": PN}, u)
        assert out.get("denied") is True
        assert "penahan" not in out         # kode pesanan penahan tak pernah bocor


def test_hanya_admin_yang_ditawari_toolnya():
    assert "stok_tertahan" in {s["function"]["name"] for s in A._tool_specs(ADMIN)}
    for u in (PEMBELI, CABANG):
        assert "stok_tertahan" not in {s["function"]["name"] for s in A._tool_specs(u)}


def test_penjaga_terpusat_menolak_non_admin_walau_dipaksa(monkeypatch):
    """Penjaga di _run_tool: tool sensitif tak bisa dipanggil lintas-peran walau
    model dipaksa memanggilnya (prompt-injection / riwayat palsu)."""
    _setup(monkeypatch, rows=[_row()])

    for u in (PEMBELI, CABANG):
        assert A._run_tool("stok_tertahan", {"part_number": PN}, u).get("denied") is True


# ── Service: reservasi kedaluwarsa tak boleh dihitung ────────────────────────
def test_active_rows_buang_reservasi_kedaluwarsa(monkeypatch):
    """Harus sinkron dengan reserved_map — kalau tidak, angka 'tertahan' di tool
    berbeda dari stok yang benar-benar dipajang ke pembeli."""
    class _R:
        status_code = 200

        @staticmethod
        def json():
            return [
                {"order_code": "PO-LAMA", "part_number": PN, "gudang_label": "04.Palembang",
                 "qty": 9, "expires_at": _iso(time.time() - 60)},      # sudah lewat
                {"order_code": "PO-BARU", "part_number": PN, "gudang_label": "04.Palembang",
                 "qty": 2, "expires_at": _iso(time.time() + 3600)},    # masih aktif
            ]

    monkeypatch.setattr(RES.requests, "get", lambda *a, **k: _R())

    rows = RES.active_rows(part_number=PN)

    assert [r["order_code"] for r in rows] == ["PO-BARU"]
    assert rows[0]["qty"] == 2


def test_status_map_kosong_bila_tak_ada_kode():
    assert O.status_map([]) == {}
