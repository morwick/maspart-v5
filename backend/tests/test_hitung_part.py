"""Tool hitung_part — hitung DETERMINISTIK (total/urut/filter harga) di Python,
bukan oleh model. Sumber angka = indeks Accurate (mentah). Grounded-gate (anti PN
karangan) + harga-gate. ⛔ Tidak menyentuh evals."""
import re

import pytest

from app.services import accurate, ai_assistant as ai, part_index

ADMIN = {"username": "mas", "role": "admin"}


def _setup(monkeypatch, snap: dict, names: dict | None = None):
    """Snapshot Accurate tiruan (key = PN dasar uppercase). index_key/norm_pn di-mock
    ke bentuk PN dasar (potong suffix '/…','+…') agar deterministik tanpa indeks nyata."""
    monkeypatch.setattr(accurate, "snapshot", lambda: snap)
    monkeypatch.setattr(accurate, "index_key", lambda pn: re.split(r"[/+]", (pn or "").upper())[0])
    monkeypatch.setattr(accurate, "norm_pn", lambda pn: (pn or "").upper())
    monkeypatch.setattr(part_index, "rows_for_pns",
                        lambda pns: {p.upper(): {"part_name": (names or {}).get(p.upper(), "")}
                                     for p in pns})


def test_total_pasti(monkeypatch):
    _setup(monkeypatch, {"AZ450045000024": {"harga": 153000, "stok": 10},
                         "AZ450045000042": {"harga": 112000, "stok": 5}})
    out = ai._t_hitung_part(
        {"items": [{"pn": "AZ450045000024", "qty": 3}, {"pn": "AZ450045000042", "qty": 2}],
         "_grounded": {"AZ450045000024", "AZ450045000042"}}, ADMIN)
    assert out["total_num"] == 683000            # 3×153.000 + 2×112.000
    assert out["total"] == "Rp 683.000"
    assert out["items"][0]["subtotal_num"] in (459000, 224000)


def test_urut_termurah_dan_item_tanpa_harga(monkeypatch):
    _setup(monkeypatch, {"AZ450045000024": {"harga": 153000, "stok": 10},
                         "AZ450045000042": {"harga": 112000, "stok": 5}})
    out = ai._t_hitung_part(
        {"items": [{"pn": "AZ450045000024"}, {"pn": "AZ450045000042"}, {"pn": "WG9761450185"}],
         "urutkan": "termurah",
         "_grounded": {"AZ450045000024", "AZ450045000042", "WG9761450185"}}, ADMIN)
    assert [i["part_number"] for i in out["items"]] == ["AZ450045000042", "AZ450045000024"]
    assert out["items_tanpa_harga"] == ["WG9761450185"]     # tak ada harga → tak ikut total
    assert out["total_num"] == 265000                        # 112k + 153k (WG dikecualikan)


def test_grounded_gate_tolak_pn_karangan(monkeypatch):
    _setup(monkeypatch, {"AZ450045000042": {"harga": 112000, "stok": 5}})
    out = ai._t_hitung_part(
        {"items": [{"pn": "AZ999888777666"}], "_grounded": {"AZ450045000042"}}, ADMIN)
    assert "error" in out and "karangan" in out["error"].lower()
    assert "total_num" not in out                            # tak menghitung apa pun


def test_harga_gate_denied(monkeypatch):
    from app.services import permissions
    monkeypatch.setattr(permissions, "effective", lambda *a, **k: set())   # staf tanpa col_harga
    _setup(monkeypatch, {"AZ450045000042": {"harga": 112000, "stok": 5}})
    out = ai._t_hitung_part(
        {"items": [{"pn": "AZ450045000042"}], "_grounded": {"AZ450045000042"}},
        {"username": "budi", "role": "user"})
    assert out.get("denied") is True


def test_suffix_varian_resolve_ke_pn_dasar(monkeypatch):
    _setup(monkeypatch, {"WG9525160004": {"harga": 500000, "stok": 11}})
    out = ai._t_hitung_part(
        {"items": [{"pn": "WG9525160004/2", "qty": 1}], "_grounded": {"WG9525160004/2"}}, ADMIN)
    assert out["total_num"] == 500000            # PN '…/2' cocok ke harga PN dasar


def test_filter_harga_maks_dan_ready(monkeypatch):
    _setup(monkeypatch, {"AA111111": {"harga": 112000, "stok": 5},
                         "BB222222": {"harga": 900000, "stok": 0}})
    out = ai._t_hitung_part(
        {"items": [{"pn": "AA111111"}, {"pn": "BB222222"}],
         "harga_maks": 500000, "_grounded": {"AA111111", "BB222222"}}, ADMIN)
    assert [i["part_number"] for i in out["items"]] == ["AA111111"]   # 900k disaring


def test_terdaftar_dan_di_gate_harga(monkeypatch):
    assert ai._DISPATCH["hitung_part"] is ai._t_hitung_part
    assert "hitung_part" in {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    from app.services import permissions
    monkeypatch.setattr(permissions, "effective", lambda *a, **k: set())
    names = {s["function"]["name"] for s in ai._tool_specs({"username": "budi", "role": "user"})}
    assert "hitung_part" not in names            # tak berhak harga → tak ditawarkan
