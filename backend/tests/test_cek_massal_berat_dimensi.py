"""cek_massal_part: berat selalu, dimensi opt-in, payload tak boleh membanjir.

Latar (log produksi 15 hari): 9 giliran meminta "cek berat dan dimensi <daftar
PN>" dan model memanggil detail_part 167× — satu giliran 50 kali, 70 detik.
Model tidak salah: cek_massal_part memang belum punya field itu, dan spec
detail_part malah menyuruh memakainya untuk berat/dimensi.

Semua sumber lambat di-mock: nol jaringan, nol panggilan model.
"""
from __future__ import annotations

import json

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture(autouse=True)
def _sumber(monkeypatch):
    """Katalog + stok + harga + berat + dimensi, semuanya deterministik & lokal."""
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: {p: {"part_name": f"Part {p}"} for p in pns})
    monkeypatch.setattr(ai.accurate, "snapshot", lambda: {})
    monkeypatch.setattr(ai, "_rincian_gudang_str", lambda pn: (7, "01.Jakarta: 7"))
    # 3,7 kg utk semua PN (harga.shipping_weight_for mengembalikan GRAM)
    monkeypatch.setattr(ai.harga, "shipping_weight_for",
                        lambda pn, allow_remote=False: 3700)
    monkeypatch.setattr(ai.sims, "get_part_spec_cached",
                        lambda pn: {"dimensi_cm": "46.5 x 30.5 x 30.2"})
    monkeypatch.setattr(ai.sims, "get_part_spec",
                        lambda pn: pytest.fail("get_part_spec (jaringan) tak boleh dipanggil "
                                               "bila cache sudah punya dimensinya"))


def _pn(n: int) -> list[str]:
    return [f"WG99187880{i:02d}" for i in range(n)]


# ── berat: selalu, tanpa argumen ────────────────────────────────────────────

def test_berat_selalu_ikut():
    out = ai._t_cek_massal_part({"daftar_pn": _pn(3)}, ADMIN)
    assert out["found"] is True
    assert all(p["berat_kg"] == 3.7 for p in out["part"])


def test_berat_nol_tidak_dilaporkan(monkeypatch):
    """Part tanpa data berat lebih baik TAK punya field daripada ditulis 0 —
    0 kg akan dibaca model sebagai fakta."""
    monkeypatch.setattr(ai.harga, "shipping_weight_for", lambda pn, allow_remote=False: 0)
    out = ai._t_cek_massal_part({"daftar_pn": _pn(2)}, ADMIN)
    assert all("berat_kg" not in p for p in out["part"])


def test_berat_tak_pernah_menyentuh_jaringan(monkeypatch):
    dipanggil = {}

    def _rekam(pn, allow_remote=False):
        dipanggil["allow_remote"] = allow_remote
        return 1200

    monkeypatch.setattr(ai.harga, "shipping_weight_for", _rekam)
    ai._t_cek_massal_part({"daftar_pn": _pn(2)}, ADMIN)
    assert dipanggil["allow_remote"] is False


# ── dimensi: opt-in + plafon ────────────────────────────────────────────────

def test_dimensi_tidak_ikut_tanpa_diminta():
    out = ai._t_cek_massal_part({"daftar_pn": _pn(3)}, ADMIN)
    assert all("dimensi_cm" not in p for p in out["part"])
    assert "dimensi=true" in out["catatan"]


def test_dimensi_ikut_saat_diminta():
    out = ai._t_cek_massal_part({"daftar_pn": _pn(3), "dimensi": True}, ADMIN)
    assert all(p["dimensi_cm"] == "46.5 x 30.5 x 30.2" for p in out["part"])


def test_dimensi_dibatasi_plafon():
    n = ai._MAX_DIM_MASSAL + 5
    out = ai._t_cek_massal_part({"daftar_pn": _pn(n), "dimensi": True}, ADMIN)
    ada = [p for p in out["part"] if p.get("dimensi_cm")]
    assert len(ada) == ai._MAX_DIM_MASSAL
    assert f"{ai._MAX_DIM_MASSAL} PN pertama" in out["catatan"]


def test_dimensi_gagal_per_pn_tak_menjatuhkan_daftar(monkeypatch):
    monkeypatch.setattr(ai.sims, "get_part_spec_cached", lambda pn: {})

    def _kadang_meledak(pn):
        if pn.endswith("01"):
            raise RuntimeError("SIMS timeout")
        return {"dimensi_cm": "10 x 10 x 10"}

    monkeypatch.setattr(ai.sims, "get_part_spec", _kadang_meledak)
    out = ai._t_cek_massal_part({"daftar_pn": _pn(3), "dimensi": True}, ADMIN)
    assert out["found"] is True
    assert sum(1 for p in out["part"] if p.get("dimensi_cm")) == 2


# ── payload: jangan sampai dipotong tengah oleh _cap_tool_content ───────────

def test_payload_worst_case_muat(monkeypatch):
    """100 PN + stok per-gudang panjang + harga + berat + dimensi harus tetap di
    bawah _MAX_TOOL_CONTENT. Kalau melewati, _cap_tool_content memotong TENGAH
    dan PN di tengah daftar hilang SENYAP dari konteks model."""
    monkeypatch.setattr(ai, "_rincian_gudang_str",
                        lambda pn: (42, "01.Jakarta: 12 · 02.Surabaya: 8 · 03.Medan: 7 · "
                                        "04.Makasar: 6 · 05.Balikpapan: 5 · 06.Pekanbaru: 4"))
    monkeypatch.setattr(ai.accurate, "snapshot", lambda: {})
    out = ai._t_cek_massal_part(
        {"daftar_pn": _pn(ai._MAX_MASSAL_PN), "dimensi": True}, ADMIN)
    ukuran = len(json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str))
    assert ukuran < ai._MAX_TOOL_CONTENT, f"payload {ukuran} char ≥ cap"


def test_perampingan_membuang_rincian_gudang_bukan_baris(monkeypatch):
    """Saat terpaksa merampingkan: yang hilang RINCIAN per-gudang, bukan PN-nya.
    stok_total wajib tetap utuh untuk SEMUA baris."""
    monkeypatch.setattr(ai, "_rincian_gudang_str", lambda pn: (42, "X" * 400))
    out = ai._t_cek_massal_part({"daftar_pn": _pn(ai._MAX_MASSAL_PN)}, ADMIN)
    assert len(out["part"]) == ai._MAX_MASSAL_PN          # tak ada baris hilang
    assert all("stok_total" in p for p in out["part"])    # angka utama utuh
    assert any("stok_per_gudang" not in p for p in out["part"])
    assert "PER-GUDANG sebagian dihilangkan" in out["catatan"]


# ── kontrak yang dijaga suite lama ──────────────────────────────────────────

def test_catatan_tetap_key_terakhir():
    """_cap_tool_content memotong TENGAH — instruksi di ekor harus selamat."""
    for args in ({"daftar_pn": _pn(3)},
                 {"daftar_pn": _pn(3), "dimensi": True},
                 {"daftar_pn": _pn(3), "excel": True}):
        out = ai._t_cek_massal_part(args, ADMIN)
        assert list(out)[-1] == "catatan"


def test_kolom_excel_ikut_berat_dan_dimensi(monkeypatch):
    tangkap = {}

    def _stash(judul, kolom, baris):
        tangkap["kolom"] = kolom
        tangkap["baris"] = baris
        return "id1", "f.xlsx"

    monkeypatch.setattr(ai.ai_export, "stash_export", _stash)
    ai._t_cek_massal_part({"daftar_pn": _pn(2), "excel": True, "dimensi": True}, ADMIN)
    assert "Berat (kg)" in tangkap["kolom"]
    assert "Dimensi P×L×T (cm)" in tangkap["kolom"]
    assert all(len(r) == len(tangkap["kolom"]) for r in tangkap["baris"])


def test_excel_tanpa_dimensi_tak_punya_kolomnya(monkeypatch):
    tangkap = {}
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda j, k, b: (tangkap.setdefault("kolom", k), "id", "f.xlsx")[1:])
    ai._t_cek_massal_part({"daftar_pn": _pn(2), "excel": True}, ADMIN)
    assert "Berat (kg)" in tangkap["kolom"]
    assert "Dimensi P×L×T (cm)" not in tangkap["kolom"]
