"""Fase 3 — sheet_jadi_penawaran: admin-only berlapis, PN+Qty dari file → Penawaran
Accurate; qty bermasalah batal/lewati; PN duplikat dijumlah; pelanggan ambigu; PN
tak ada di Accurate = batal."""
import io

import pytest
from openpyxl import Workbook

from app.services import ai_assistant as ai
from app.services import ai_sheet

ADMIN = {"username": "admin", "role": "admin"}
USER = {"username": "budi", "role": "user"}
PEMBELI = {"username": "toko", "role": "pembeli"}


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def acc(monkeypatch):
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "search_customers",
                        lambda q, limit=20: [{"id": 1, "no": "001", "name": "CV ANUGERAH"}]
                        if "anugerah" in q.lower() else
                        ([{"id": 2, "no": "002", "name": "JAYA ABADI"},
                          {"id": 3, "no": "003", "name": "JAYA MOTOR"}] if "jaya" in q.lower() else []))
    kat = {
        "WG9925520270": {"id": 30, "no": "x", "pn": "WG9925520270", "name": "Spring",
                         "unit_id": 100, "unit": "Pc", "price": 1500000, "available": 5},
        "AZ9925520271": {"id": 31, "no": "y", "pn": "AZ9925520271", "name": "Leaf",
                         "unit_id": 100, "unit": "Pc", "price": 90000, "available": 3},
    }
    monkeypatch.setattr(ai.accurate, "item_for_quotation", lambda pn: kat.get(pn.strip().upper()))
    box = {}

    def _create(*, number, customer_id, lines, transdate, taxable=True, inclusive=True, description=""):
        box["lines"] = lines
        return {"id": 99, "number": number, "total": sum(l["qty"] * l["unit_price"] for l in lines)}

    monkeypatch.setattr(ai.accurate, "ensure_session_force", lambda: {"j": "J"})
    monkeypatch.setattr(ai.accurate, "create_sales_quotation", _create)
    monkeypatch.setattr(ai.accurate, "next_quotation_number", lambda: "MASPART-01")
    monkeypatch.setattr(ai.accurate, "sales_quotation_pdf", lambda qid, layout_id=50: b"%PDF fake")
    monkeypatch.setattr(ai.accurate, "AccurateError", Exception, raising=False)
    monkeypatch.setattr(ai.accurate, "logout", lambda: True)
    monkeypatch.setattr(ai.accurate, "suppress_autologin", lambda *a, **k: None)
    # deteksi PN katalog
    monkeypatch.setattr(ai_sheet.part_index, "search_exact_pns",
                        lambda pns: [{"part_number": p} for p in pns if p.upper() in kat])
    monkeypatch.setattr(ai_sheet.part_index, "_pn_flat_map", lambda: {})
    return box


def _sid(rows, user=ADMIN):
    p = ai_sheet.parse_upload(_xlsx(rows), "order.xlsx")
    return ai_sheet.put_sheet(user["username"], p)


def test_admin_only_berlapis(acc):
    assert "sheet_jadi_penawaran" in ai._allowed_tool_names(ADMIN, "SID")
    assert "sheet_jadi_penawaran" not in ai._allowed_tool_names(USER, "SID")
    assert "sheet_jadi_penawaran" not in ai._allowed_tool_names(PEMBELI, "SID")
    # handler + terpusat menolak non-admin
    assert ai._t_sheet_jadi_penawaran({"_sheet_id": "x", "pelanggan": "CV"}, USER)["denied"]
    assert ai._run_tool("sheet_jadi_penawaran", {"pelanggan": "x"}, USER)["denied"]


def test_happy_path(acc):
    sid = _sid([["Part Number", "Qty"], ["WG9925520270", 2], ["AZ9925520271", 3]])
    r = ai._t_sheet_jadi_penawaran({"_sheet_id": sid, "pelanggan": "anugerah"}, ADMIN)
    assert r["found"] and r["nomor"] == "MASPART-01" and r["jumlah_barang"] == 2
    assert r["export_id"] and r["total"] == 2 * 1500000 + 3 * 90000


def test_qty_kosong_batal_default(acc):
    sid = _sid([["Part Number", "Qty"], ["WG9925520270", 2], ["AZ9925520271", ""]])
    r = ai._t_sheet_jadi_penawaran({"_sheet_id": sid, "pelanggan": "anugerah"}, ADMIN)
    assert not r["found"] and r["baris_bermasalah"]


def test_qty_kosong_lewati(acc):
    sid = _sid([["Part Number", "Qty"], ["WG9925520270", 2], ["AZ9925520271", ""]])
    r = ai._t_sheet_jadi_penawaran({"_sheet_id": sid, "pelanggan": "anugerah",
                                    "baris_bermasalah": "lewati"}, ADMIN)
    assert r["found"] and r["jumlah_barang"] == 1 and r["baris_dilewati"] == 1


def test_pn_duplikat_dijumlah(acc):
    sid = _sid([["Part Number", "Qty"], ["WG9925520270", 2], ["WG9925520270", 5]])
    r = ai._t_sheet_jadi_penawaran({"_sheet_id": sid, "pelanggan": "anugerah"}, ADMIN)
    assert r["found"] and r["jumlah_barang"] == 1
    assert acc["lines"][0]["qty"] == 7


def test_pelanggan_ambigu(acc):
    sid = _sid([["Part Number", "Qty"], ["WG9925520270", 1]])
    r = ai._t_sheet_jadi_penawaran({"_sheet_id": sid, "pelanggan": "jaya"}, ADMIN)
    assert r.get("perlu_klarifikasi") and len(r["kandidat"]) == 2


def test_pn_tak_ada_di_accurate_batal(acc):
    sid = _sid([["Part Number", "Qty"], ["WG9925520270", 1], ["ZZZ999", 1]])
    r = ai._t_sheet_jadi_penawaran({"_sheet_id": sid, "pelanggan": "anugerah"}, ADMIN)
    assert not r["found"] and "ZZZ999" in r.get("part_tidak_ditemukan", [])
