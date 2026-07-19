"""Gating tool INTERNAL dari akun pembeli (_GATED_PEMBELI, p2_tool_specs).

Perluasan 2026-07-20: daftar_transmisi_assy / banding_assy / banding_kategori
ikut disembunyikan dari pembeli — sensus & banding internal untuk staf; pembeli
tetap punya jalur per-VIN (EPC-first). Allow-list eksekusi diturunkan dari spec
(_allowed_tool_names) sehingga gating spec = gating eksekusi.

Pola: tests/test_ai_ability_gate.py (monkeypatch permissions.effective).
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "staf", "role": "user"}
PEMBELI = {"username": "toko", "role": "pembeli"}

GATED = {"excel_stok_gudang", "banding_rangka_massal", "banding_rangka",
         "daftar_transmisi_assy", "banding_assy", "banding_kategori"}

# Tool per-VIN inti kebijakan EPC-first — pembeli WAJIB tetap ditawari.
EPC_FIRST = {"cek_kendaraan", "bom_dari_rangka", "part_aus_dari_rangka",
             "cari_part_di_unit", "gambar_exploded", "katalog_kategori"}


@pytest.fixture(autouse=True)
def kolom_on(monkeypatch):
    """col_stok/col_harga ON untuk semua agar _GATED_STOK tak ikut campur."""
    monkeypatch.setattr("app.services.permissions.effective",
                        lambda kind, u, r: ["col_stok", "col_harga"])


def _nama(user):
    return {f["function"]["name"] for f in ai._tool_specs(user, "")}


def test_spec_pembeli_tanpa_tool_internal():
    nama = _nama(PEMBELI)
    for t in GATED:
        assert t not in nama, t


def test_staf_dan_admin_tetap_dapat_tool_banding():
    for user in (STAF, ADMIN):
        nama = _nama(user)
        for t in ("daftar_transmisi_assy", "banding_assy", "banding_kategori",
                  "banding_rangka"):
            assert t in nama, (user["role"], t)


def test_pembeli_tetap_dapat_jalur_epc_first():
    nama = _nama(PEMBELI)
    for t in EPC_FIRST:
        assert t in nama, t


def test_eksekusi_ditolak_untuk_pembeli():
    """Defense-in-depth: _run_tool menolak nama di luar allow-list pembeli."""
    allowed = ai._allowed_tool_names(PEMBELI)
    for t in ("daftar_transmisi_assy", "banding_assy", "banding_kategori"):
        assert t not in allowed, t
    hasil = ai._run_tool("banding_assy", {"assy_a": "A", "assy_b": "B"}, PEMBELI)
    assert hasil.get("denied") is True


def test_eksekusi_tetap_boleh_untuk_staf():
    allowed = ai._allowed_tool_names(STAF)
    for t in ("daftar_transmisi_assy", "banding_assy", "banding_kategori"):
        assert t in allowed, t
