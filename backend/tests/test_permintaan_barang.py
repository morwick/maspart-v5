"""Tool `buat_permintaan_barang`: Permintaan Barang (Purchase Requisition) Accurate.

Dibedah dari HAR UI pemilik (iris.accurate.id11.har, 2026-08-06). Yang dijaga:
  • WAJIB 2 LANGKAH — panggilan pertama hanya PRATINJAU, dokumen belum dibuat;
  • tiga kolom WAJIB Accurate terisi: Sektor (charField1), No Unit (charField2),
    Kts Jkt (charField4) = stok Jakarta saat ini dari indeks (bukan karangan);
  • qty yang tak disebut user = 1 dan itu DILAPORKAN (bisa dikoreksi);
  • PN tak ada di Accurate → SELURUH permintaan batal (⛔ jangan ganti PN mirip);
  • ADMIN saja; PN wajib grounded (anti-karangan);
  • simpan lewat proses LATAR: 's:true' pertama BELUM berarti tersimpan —
    hasilnya baru sah setelah polling bg-proc-response FINISHED.
Tanpa jaringan: seluruh lapisan HTTP Accurate di-stub.
"""
from __future__ import annotations

import pytest

from app.services import accurate
from app.services import ai_assistant as ai

ADMIN = {"username": "mas", "role": "admin"}
STAF = {"username": "budi", "role": "staf"}

PN_A = "WG9925550182"
PN_B = "AZ9100440002"
_ITEM = {
    PN_A: {"id": 17451, "no": "0001.WG9925550182", "pn": PN_A,
           "name": "Fuel coarse filter element", "unit_id": 100, "unit": "PCS",
           "price": 250000, "available": 3},
    PN_B: {"id": 17452, "no": "0002.AZ9100440002", "pn": PN_B, "name": "Bracket",
           "unit_id": 100, "unit": "PCS", "price": 90000, "available": 0},
}


@pytest.fixture
def acc(monkeypatch):
    """Accurate tiruan: item ada, stok Jakarta diketahui, sesi selalu sehat."""
    dibuat: list[dict] = []
    monkeypatch.setattr(accurate, "available", lambda: True)
    monkeypatch.setattr(accurate, "ensure_session_force", lambda: {"dsi": "d", "jsessionid": "j"})
    monkeypatch.setattr(accurate, "item_for_quotation", lambda pn: _ITEM.get(pn.upper()))
    monkeypatch.setattr(accurate, "gudang_breakdown",
                        lambda pn: {"01.Jakarta": 28, "02.Pekanbaru": 5} if pn == PN_A else {})
    monkeypatch.setattr(accurate, "next_pr_number", lambda: "PERMINTAAN-05")

    def _create(**kw):
        dibuat.append(kw)
        return {"id": 15559, "number": kw["number"], "jumlah_baris": len(kw["lines"])}

    monkeypatch.setattr(accurate, "create_purchase_requisition", _create)
    return dibuat


# ── Gerbang & pagar ─────────────────────────────────────────────────────────

def test_hanya_admin(acc):
    r = ai._t_buat_permintaan_barang({"barang": [{"part_number": PN_A}]}, STAF)
    assert r["denied"] is True and not acc


def test_pn_karangan_ditolak(acc):
    r = ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A}], "_grounded": {"PN-LAIN"}}, ADMIN)
    assert "karangan" in r["error"] and not acc


def test_tool_hanya_ditawarkan_ke_admin():
    assert "buat_permintaan_barang" in {f["function"]["name"] for f in ai._tool_specs(ADMIN, "")}
    assert "buat_permintaan_barang" not in {f["function"]["name"] for f in ai._tool_specs(STAF, "")}
    assert ai._DISPATCH["buat_permintaan_barang"] is ai._t_buat_permintaan_barang


# ── Langkah 1: PRATINJAU (dokumen belum dibuat) ─────────────────────────────

def test_pratinjau_dulu_tak_menyimpan(acc):
    r = ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A, "qty": 4}, {"part_number": PN_B}]}, ADMIN)
    assert r["pratinjau"] is True and r["found"] is False
    assert not acc, "dokumen TIDAK boleh dibuat sebelum user setuju"
    assert r["nomor_diusulkan"] == "PERMINTAAN-05"
    assert r["sektor"] == "MASPART" and r["no_unit"] == "STOK"
    baris = {b["part_number"]: b for b in r["baris"]}
    assert baris[PN_A]["qty"] == 4 and baris[PN_A]["kts_jkt"] == 28   # stok Jakarta
    assert baris[PN_B]["qty"] == 1 and baris[PN_B]["kts_jkt"] == 0    # tak ada di Jkt
    assert r["qty_default_1"] == [PN_B]                              # jujur soal qty 1
    assert "MINTA PERSETUJUAN" in r["jawaban_wajib"]
    assert "konfirmasi=true" in r["jawaban_wajib"]


def test_pn_tak_ada_membatalkan_semua(acc):
    r = ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A}, {"part_number": "PN-TIDAK-ADA"}]}, ADMIN)
    assert r["found"] is False and r["pn_tidak_ada"] == ["PN-TIDAK-ADA"]
    assert "DIBATALKAN" in r["error"] and not acc


def test_qty_pn_kembar_dijumlah(acc):
    r = ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A, "qty": 2}, {"part_number": PN_A, "qty": 3}]}, ADMIN)
    assert r["jumlah_baris"] == 1 and r["baris"][0]["qty"] == 5


# ── Langkah 2: KONFIRMASI (baru menulis ke Accurate) ────────────────────────

def test_konfirmasi_membuat_dokumen(acc):
    r = ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A, "qty": 2}], "konfirmasi": True,
         "nomor": "PERMINTAAN-05", "tanggal": "06/08/2026"}, ADMIN)
    assert r["found"] is True and r["nomor"] == "PERMINTAAN-05" and r["id"] == 15559
    assert len(acc) == 1
    kw = acc[0]
    assert kw["transdate"] == "06/08/2026"
    ln = kw["lines"][0]
    # tiga kolom WAJIB Accurate ikut terkirim
    assert ln["sektor"] == "MASPART" and ln["no_unit"] == "STOK" and ln["kts_jkt"] == 28
    assert ln["item_id"] == 17451 and ln["unit_id"] == 100 and ln["qty"] == 2
    assert "JANGAN mengklaim" in r["catatan"]


def test_sektor_no_unit_bisa_ditimpa_user(acc):
    ai._t_buat_permintaan_barang(
        {"barang": [{"part_number": PN_A}], "konfirmasi": True,
         "sektor": "WORKSHOP", "no_unit": "SJ346500"}, ADMIN)
    ln = acc[0]["lines"][0]
    assert ln["sektor"] == "WORKSHOP" and ln["no_unit"] == "SJ346500"


# ── Lapisan klien Accurate (bentuk body & alur simpan latar) ────────────────

def test_body_detail_memuat_kolom_wajib():
    body = accurate._pr_detail_body(
        [{"item_id": 17451, "name": "Filter", "qty": 2, "unit_id": 100,
          "sektor": "MASPART", "no_unit": "STOK", "kts_jkt": 28}], "06/08/2026")
    assert body["param.detailItem[0].itemId"] == 17451
    assert body["param.detailItem[0].charField1"] == "MASPART"
    assert body["param.detailItem[0].charField2"] == "STOK"
    assert body["param.detailItem[0].charField4"] == 28
    assert body["param.detailItem[0].requiredDate"] == "06/08/2026"
    assert body["param.detailItem[0].unitPrice"] == 0        # harga urusan pembelian
    assert body["param.detailItem[0]._status"] == "insert"


def test_simpan_menunggu_proses_latar(monkeypatch):
    """bg-save balas {b: bgPid}; dokumen baru sah setelah polling FINISHED."""
    urls: list[str] = []

    class _R:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        text = "{}"

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    seq = [
        {"s": True, "b": "BGPID-1"},                                     # bg-save
        {"s": True, "d": {"status": "RUNNING", "response": {}}},         # poll 1
        {"s": True, "d": {"status": "FINISHED", "response": {
            "s": True, "r": {"id": 15559, "number": "PERMINTAAN-05"},
            "d": ['Permintaan Barang "PERMINTAAN-05" berhasil disimpan']}}},
    ]

    def _post(url, data=None, headers=None, timeout=None, allow_redirects=None):
        urls.append(url)
        return _R(seq[min(len(urls) - 1, len(seq) - 1)])

    monkeypatch.setattr(accurate.requests, "post", _post)
    monkeypatch.setattr(accurate, "_looks_expired", lambda r: False)
    monkeypatch.setattr(accurate.time, "sleep", lambda s: None)
    r = accurate._save_pr({"dsi": "d", "jsessionid": "j"}, "usi",
                          {"branchId": 50, "currencyId": 50, "tax1Id": 50,
                           "countAutoNumber": 4},
                          number="PERMINTAAN-05",
                          lines=[{"item_id": 1, "name": "x", "qty": 1, "unit_id": 100,
                                  "kts_jkt": 0}],
                          transdate="06/08/2026", description="", totals={})
    assert r == {"id": 15559, "number": "PERMINTAAN-05"}
    assert urls[0].endswith("vendor/bg-save-purchase-requisition.do")
    assert urls[1].endswith("company/bg-proc-response.do") and len(urls) == 3
