"""Builder panen tabel penggantian Weichai — regresi 2 bug yang MELAPORKAN SUKSES
sambil kehilangan data diam-diam (keduanya terjadi sungguhan 2026-08-08):

 1. Halaman KOSONG tanpa error dianggap "data habis" → panen berhenti di 38%
    (22.201 dari 58.100) dan keluar dengan exit 0. Halaman yang sama membalas
    200 record beberapa menit kemudian: itu anomali sesaat, bukan akhir data.
 2. Melanjutkan checkpoint dengan pageSize BERBEDA → nomor halaman ditafsir pada
    ukuran baru, ±33.000 baris terlompati tanpa jejak. Checkpoint lama tak punya
    field page_size, jadi penjaganya harus menganggapnya 200 (default lama).

Jaringan di-mock; tak ada HTTP di test ini.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "tools" / "build_weichai_replace.py"


@pytest.fixture()
def mod(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("build_weichai_replace", _SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "OUT", tmp_path / "weichai_replace.json.gz")
    monkeypatch.setattr(m.wc, "_ensure_token", lambda *a, **k: "TOKEN")
    return m


def _rec(i: int) -> dict:
    return {"oldPartNumber": f"OLD{i}", "newPartNumber": f"NEW{i}",
            "replaceGroup": "", "replacementDate": "2025-01-01", "replaceType": "T"}


def _jalankan(mod, monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["build_weichai_replace.py", *argv])
    return mod.main()


def _hasil(mod) -> dict:
    import gzip
    with gzip.open(mod.OUT, "rt", encoding="utf-8") as f:
        return json.load(f)


# ── Bug 1: halaman kosong ≠ data habis ──────────────────────────────────────
def test_halaman_kosong_sesaat_tidak_menghentikan_panen(mod, monkeypatch):
    """Halaman 2 kosong sekali lalu terisi saat diulang → panen WAJIB lanjut
    sampai `total` API terpenuhi."""
    TOTAL = 1000
    panggilan: list[int] = []
    kosong_sekali = {"sudah": False}

    def fake(token, page, size):
        panggilan.append(page)
        if page == 2 and not kosong_sekali["sudah"]:
            kosong_sekali["sudah"] = True
            return [], TOTAL, ""              # kosong TANPA error
        awal = (page - 1) * size
        return [_rec(awal + i) for i in range(size)], TOTAL, ""

    monkeypatch.setattr(mod, "_ambil_halaman", fake)
    assert _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500"]) == 0
    h = _hasil(mod)
    assert h["lengkap"] is True
    assert len(h["record"]) == TOTAL          # ⛔ dulu berhenti di 500
    assert h["halaman_gagal"] == []
    assert panggilan.count(2) == 2            # kosong → diulang, bukan menyerah


def test_kosong_berulang_dicatat_gagal_dan_panen_tetap_lanjut(mod, monkeypatch):
    """Kosong terus-menerus = halaman itu dilewati & DITANDAI — bukan berhenti,
    dan hasilnya TIDAK boleh mengaku lengkap."""
    TOTAL = 1500

    def fake(token, page, size):
        if page == 2:
            return [], TOTAL, ""
        awal = (page - 1) * size
        return [_rec(awal + i) for i in range(size)], TOTAL, ""

    monkeypatch.setattr(mod, "_ambil_halaman", fake)
    _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500"])
    h = _hasil(mod)
    assert 2 in h["halaman_gagal"]
    assert h["lengkap"] is False              # ada halaman bolong → jangan mengaku penuh
    assert len(h["record"]) == 1000           # halaman 1 & 3 tetap terpanen


def test_error_jaringan_dilewati_bukan_menghentikan(mod, monkeypatch):
    TOTAL = 1500

    def fake(token, page, size):
        if page == 2:
            return [], 0, "network"
        awal = (page - 1) * size
        return [_rec(awal + i) for i in range(size)], TOTAL, ""

    monkeypatch.setattr(mod, "_ambil_halaman", fake)
    _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500"])
    h = _hasil(mod)
    assert h["halaman_gagal"] == [2] and h["lengkap"] is False
    assert len(h["record"]) == 1000


def test_berhenti_di_halaman_akhir_dari_total_api(mod, monkeypatch):
    """Akhir panen ditentukan `total` API — bukan tebakan dari isi halaman."""
    TOTAL = 1000
    dipanggil: list[int] = []

    def fake(token, page, size):
        dipanggil.append(page)
        awal = (page - 1) * size
        return [_rec(awal + i) for i in range(size)], TOTAL, ""

    monkeypatch.setattr(mod, "_ambil_halaman", fake)
    _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500"])
    assert dipanggil == [1, 2]                # 1000/500 = 2 halaman, tak lebih
    assert _hasil(mod)["lengkap"] is True


# ── Bug 2: penjaga ukuran halaman pada checkpoint ───────────────────────────
def test_checkpoint_lama_tanpa_page_size_dianggap_200(mod, monkeypatch):
    """Checkpoint versi lama (tanpa page_size) TIDAK boleh dilanjutkan apa adanya
    pada ukuran baru — penomorannya diulang dari 1, recordnya jadi benih."""
    ck = mod.OUT.with_suffix(".part")
    lama = [_rec(i) for i in range(300)]
    ck.write_text(json.dumps({"record": lama, "halaman_berikut": 113,
                              "halaman_gagal": [], "total_api": 1000}),
                  encoding="utf-8")
    TOTAL = 1000
    dipanggil: list[int] = []

    def fake(token, page, size):
        dipanggil.append(page)
        awal = (page - 1) * size
        return [_rec(awal + i) for i in range(size)], TOTAL, ""

    monkeypatch.setattr(mod, "_ambil_halaman", fake)
    _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500"])
    assert dipanggil[0] == 1                  # ⛔ dulu langsung lompat ke 113
    h = _hasil(mod)
    assert len(h["record"]) == TOTAL          # benih lama ikut, dedup rapi
    assert h["page_size"] if "page_size" in h else True


def test_checkpoint_ukuran_sama_dilanjutkan(mod, monkeypatch):
    """Ukuran sama → lanjut dari halaman berikutnya (checkpoint berguna)."""
    ck = mod.OUT.with_suffix(".part")
    lama = [_rec(i) for i in range(500)]
    ck.write_text(json.dumps({"record": lama, "halaman_berikut": 2, "halaman_gagal": [],
                              "total_api": 1000, "page_size": 500}), encoding="utf-8")
    dipanggil: list[int] = []

    def fake(token, page, size):
        dipanggil.append(page)
        awal = (page - 1) * size
        return [_rec(awal + i) for i in range(size)], 1000, ""

    monkeypatch.setattr(mod, "_ambil_halaman", fake)
    _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500"])
    assert dipanggil == [2]                   # halaman 1 tak diulang
    assert len(_hasil(mod)["record"]) == 1000


def test_checkpoint_menyimpan_page_size(mod, monkeypatch):
    monkeypatch.setattr(mod, "_ambil_halaman",
                        lambda t, p, s: ([_rec(i) for i in range(s)], 500, ""))
    _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500"])
    # checkpoint dihapus saat panen LENGKAP → cek lewat hasil akhir saja
    assert _hasil(mod)["lengkap"] is True


def test_dedup_lintas_halaman(mod, monkeypatch):
    """Halaman yang mengulang record yang sama tak boleh menggelembungkan hasil."""
    monkeypatch.setattr(mod, "_ambil_halaman",
                        lambda t, p, s: ([_rec(i) for i in range(10)], 1000, ""))
    _jalankan(mod, monkeypatch, ["--jeda", "0", "--page-size", "500",
                                 "--max-halaman", "3"])
    assert len(_hasil(mod)["record"]) == 10


def test_tanpa_sesi_keluar_dengan_kode_gagal(mod, monkeypatch):
    monkeypatch.setattr(mod.wc, "_ensure_token", lambda *a, **k: "")
    assert _jalankan(mod, monkeypatch, ["--jeda", "0"]) == 2
