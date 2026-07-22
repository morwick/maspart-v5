"""cek_massal_part (2026-07-22): cek BANYAK PN dalam 1 panggilan — hemat token
(ganti detail_part ×N), Excel opsional, agentik (tawaran penawaran)."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}


def test_parse_daftar_pn():
    assert ai._parse_daftar_pn(["wg9925520270", "az123456"]) == ["WG9925520270", "AZ123456"]
    assert ai._parse_daftar_pn("WG9925520270, AZ123456\nVG160") == \
        ["WG9925520270", "AZ123456", "VG160"]
    # dedup + buang token pendek (<4 char)
    assert ai._parse_daftar_pn("AZ100 AZ100 xy") == ["AZ100"]


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_harga", lambda u: u["role"] == "admin")
    monkeypatch.setattr(ai, "_boleh_stok", lambda u: u["role"] == "admin")
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "WG9925520270": {"part_name": "Kampas kopling"},
        "AZ100": {"part_name": "Baut"}})
    monkeypatch.setattr(ai.accurate, "snapshot", lambda: {
        "WG9925520270": {"harga": 1500000}})
    monkeypatch.setattr(ai.accurate, "index_key", lambda pn: pn)
    monkeypatch.setattr(ai, "_rincian_gudang_str",
                        lambda pn: (11, "Jakarta: 11") if pn == "WG9925520270" else (0, ""))
    return monkeypatch


def test_cek_massal_dasar(dunia):
    r = ai._t_cek_massal_part(
        {"daftar_pn": "WG9925520270, AZ100, XXNOTEXIST"}, ADMIN)
    assert r["jumlah"] == 3 and r["ketemu"] == 2 and r["tak_ada"] == 1
    p0 = next(x for x in r["part"] if x["pn"] == "WG9925520270")
    assert p0["nama"] == "Kampas kopling" and p0["harga"] == 1500000
    assert p0["stok_total"] == 11 and "Jakarta" in p0["stok_per_gudang"]
    hilang = next(x for x in r["part"] if x["pn"] == "XXNOTEXIST")
    assert "tidak ditemukan" in hilang["catatan_pn"]
    assert list(r.keys())[-1] == "catatan"
    assert "buat_penawaran" in r["catatan"] and "JANGAN detail_part" in r["catatan"]


def test_cek_massal_excel(dunia):
    monkeypatch = dunia
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda judul, kolom, baris: ("EXP", "cek.xlsx"))
    r = ai._t_cek_massal_part(
        {"daftar_pn": ["WG9925520270", "AZ100"], "excel": True}, ADMIN)
    assert r["export_id"] == "EXP" and r["jumlah_baris"] == 2
    assert "Excel" in r["catatan"]


def test_cek_massal_staf_tanpa_harga_stok(dunia):
    """Staf tanpa izin harga/stok: field itu tak muncul (gate)."""
    r = ai._t_cek_massal_part({"daftar_pn": ["WG9925520270"]}, STAF)
    p0 = r["part"][0]
    assert "harga" not in p0 and "stok_total" not in p0
    # tanpa stok/harga, PN yg ada di indeks tetap dianggap ketemu (via nama)
    assert p0["nama"] == "Kampas kopling"


def test_cek_massal_kosong():
    assert "error" in ai._t_cek_massal_part({"daftar_pn": ""}, ADMIN)


def test_cek_massal_cap_100(dunia):
    banyak = [f"WG{i:07d}" for i in range(150)]
    r = ai._t_cek_massal_part({"daftar_pn": banyak}, ADMIN)
    assert r["jumlah"] == 100 and "dipotong" in r["catatan"].lower()


def test_spec_terdaftar():
    na = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "")}
    assert "cek_massal_part" in na
