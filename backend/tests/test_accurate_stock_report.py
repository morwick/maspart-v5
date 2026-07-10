"""Enrichment per-gudang CEPAT via Report (crosstab XLSX) — parser & orkestrasi."""
import io

import pytest
from openpyxl import Workbook

from app.services import accurate as a


def _report_xlsx(rows, warehouses=("01.Jakarta", "02.Pekanbaru"), with_total=True):
    """Bangun XLSX meniru export 'Kuantitas Barang per Gudang'."""
    wb = Workbook()
    ws = wb.active
    ws.append(["PT MAS"])                       # baris judul
    ws.append(["Kuantitas Barang per Gudang"])
    ws.append(["Per Tgl. ..."])
    ws.append(["Cabang : [Semua Cabang]"])
    header = ["Kode Barang", "Kategori Barang", "Nama Barang", "Catatan", "Status Part"]
    header += list(warehouses)
    if with_total:
        header.append("Total Nama Gudang")     # kolom total → HARUS diabaikan
    ws.append(header)
    for code, cat, name, qtys in rows:
        row = [code, cat, name, "", ""]
        row += list(qtys)
        if with_total:
            row.append(sum(qtys))
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_stock_report_crosstab():
    data = _report_xlsx([
        ("000001.WG916058050", "Cabin", "ABS", (0, 3)),        # hanya Pekanbaru
        ("000002.YZ16008", "Cabin", "AC Comp", (3, 1)),        # dua gudang
        ("000003.ZERO", "X", "Nol", (0, 0)),                   # tak ada stok → dilewati
    ])
    out = a._parse_stock_report(data)
    assert a._norm_pn("WG916058050") in out
    assert out[a._norm_pn("WG916058050")] == {"02.Pekanbaru": 3.0}
    assert out[a._norm_pn("YZ16008")] == {"01.Jakarta": 3.0, "02.Pekanbaru": 1.0}
    assert a._norm_pn("ZERO") not in out       # 0 di semua gudang → tak masuk


def test_parse_stock_report_abaikan_kolom_total():
    data = _report_xlsx([("000001.WG1", "C", "N", (5,))], warehouses=("01.Jakarta",))
    out = a._parse_stock_report(data)
    # 'Total Nama Gudang' TIDAK boleh jadi gudang
    assert out[a._norm_pn("WG1")] == {"01.Jakarta": 5.0}


def test_parse_stock_report_kunci_cocok_dgn_by_pn():
    """Kode 'NNNNNN.PN' → parse_pn SAMA dgn by_pn → kunci norm_pn cocok."""
    code = "003330.WG9725220536+305"
    data = _report_xlsx([(code, "C", "N", (2,))], warehouses=("01.Jakarta",))
    out = a._parse_stock_report(data)
    # kunci = norm_pn(parse_pn(code)) — sama dgn yang dipakai _build_by_pn
    assert a._norm_pn(a.parse_pn(code)) in out


def test_parse_stock_report_header_hilang():
    wb = Workbook(); wb.active.append(["bukan header"])
    buf = io.BytesIO(); wb.save(buf)
    with pytest.raises(a.AccurateError):
        a._parse_stock_report(buf.getvalue())


def test_enrich_via_report_isi_by_gudang(monkeypatch):
    data = _report_xlsx([("000001.WG1", "C", "N", (4, 0))])
    monkeypatch.setattr(a, "_ensure_session", lambda: {"jsessionid": "J", "dsi": "D"})
    monkeypatch.setattr(a, "_stock_report_xls", lambda s: data)
    monkeypatch.setitem(a._index_cache, "by_gudang", {})
    n = a.enrich_warehouses_via_report()
    assert n == 1
    assert a.gudang_breakdown("WG1") == {"01.Jakarta": 4.0}


def test_enrich_via_report_fallback_per_pn(monkeypatch):
    """Report gagal → fallback ke enrich_warehouses (per-PN lama)."""
    monkeypatch.setattr(a, "_ensure_session", lambda: {"jsessionid": "J", "dsi": "D"})

    def _boom(s):
        raise a.AccurateError("report down")
    monkeypatch.setattr(a, "_stock_report_xls", _boom)
    dipanggil = {"n": 0}
    monkeypatch.setattr(a, "enrich_warehouses", lambda: dipanggil.update(n=1) or 7)
    assert a.enrich_warehouses_via_report() == 7
    assert dipanggil["n"] == 1                  # fallback dipakai
