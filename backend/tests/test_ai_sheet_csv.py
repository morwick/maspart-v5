"""Fase 4 — ketahanan input: parse CSV (delimiter/BOM/encoding) + header 2-baris."""
import pytest

from app.services import ai_sheet


@pytest.fixture
def katalog(monkeypatch):
    monkeypatch.setattr(ai_sheet.part_index, "search_exact_pns", lambda pns: [])
    monkeypatch.setattr(ai_sheet.part_index, "_pn_flat_map", lambda: {})
    return True


def test_csv_koma(katalog):
    data = b"Part Number,Qty\nWG9925520270,4\nAZ9925520271,2\n"
    p = ai_sheet.parse_upload(data, "order.csv")
    assert p["ok"]
    roles = dict(zip(p["headers"], p["roles"]))
    assert roles["Part Number"] == "part_number" and roles["Qty"] == "qty"
    assert p["jumlah_baris"] == 2


def test_csv_titik_koma_dan_bom(katalog):
    # BOM + delimiter ';' (umum Excel Indonesia)
    data = "﻿Part Number;Nama;Qty\nWG9925520270;Spring;4\n".encode("utf-8")
    p = ai_sheet.parse_upload(data, "order.csv")
    assert p["ok"] and p["jumlah_baris"] == 1
    assert "Part Number" in p["headers"] and "Qty" in p["headers"]


def test_csv_cp1252(katalog):
    data = "Part Number;Nama\nWG9925520270;Pegasâ\n".encode("cp1252")
    p = ai_sheet.parse_upload(data, "order.csv")
    assert p["ok"] and p["jumlah_baris"] == 1


def test_xls_tetap_ditolak():
    r = ai_sheet.parse_upload(b"x", "a.xls")
    assert not r["ok"] and "Format" in r["error"]


def test_csv_baris_data_terbaca(katalog):
    # Baris data qty per baris (delimiter tab) terbaca utuh.
    data = b"Part Number\tQty\nWG9925520270\t4\nAZ9925520271\t2\nBB0001\t1\n"
    p = ai_sheet.parse_upload(data, "order.csv")
    assert p["ok"] and p["jumlah_baris"] == 3


def test_csv_multisheet_absen(katalog):
    # CSV tak punya konsep sheet lain.
    p = ai_sheet.parse_upload(b"Part Number,Qty\nWG9925520270,1\n", "a.csv")
    assert p["ok"] and p["sheet_lain"] == []


# ── Fase 5: impor Google Sheets (anti-SSRF) ──────────────────────────────────

class _FakeResp:
    def __init__(self, code, ctype, body=b""):
        self.status_code = code
        self.headers = {"Content-Type": ctype}
        self._body = body

    def iter_content(self, n):
        yield self._body


def test_gsheet_url_tak_dikenal():
    data, err = ai_sheet.import_gsheet("https://contoh.com/bukan-sheet")
    assert data is None and "Google Sheets" in err


def test_gsheet_sukses(monkeypatch):
    captured = {}

    def _get(url, **kw):
        captured["url"] = url
        return _FakeResp(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"XLSXBYTES")

    monkeypatch.setattr("requests.get", _get)
    url = "https://docs.google.com/spreadsheets/d/ABCDEFGHIJ1234567890xyz/edit#gid=0"
    data, err = ai_sheet.import_gsheet(url)
    assert data == b"XLSXBYTES" and not err
    # anti-SSRF: HANYA fetch docs.google.com dgn id terekstrak (bukan URL mentah)
    assert captured["url"] == ("https://docs.google.com/spreadsheets/d/"
                               "ABCDEFGHIJ1234567890xyz/export?format=xlsx")


def test_gsheet_privat_ditolak(monkeypatch):
    monkeypatch.setattr("requests.get", lambda url, **kw: _FakeResp(200, "text/html", b"<html>login</html>"))
    data, err = ai_sheet.import_gsheet(
        "https://docs.google.com/spreadsheets/d/ABCDEFGHIJ1234567890xyz/edit")
    assert data is None and "berbagi" in err.lower()
