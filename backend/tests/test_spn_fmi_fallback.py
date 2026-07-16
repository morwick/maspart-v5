"""Fallback SPN/FMI di cari_kode_kesalahan & diagnosa — anti "jawaban pertama
meleset lalu harus cari ulang".

Akar yang ditutup (audit 2026-07-16): 324/764 SPN ber-multi-FMI → pasangan
SPN+FMI yang tak persis terdaftar dulu menghasilkan 0 hit (model menebak);
171 pasangan ambigu (>1 kode); SPN/FMI bisa tertukar posisi (FMI valid 0–31).
Kini: tangga fallback + flag kejujuran, panggilan PERTAMA sudah benar.
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}


def test_fmi_tak_terdaftar_fallback_spn():
    # SPN 1241 tak punya FMI 5 → dulu 0 hasil; kini semua FMI utk SPN itu + flag.
    r = ai._t_cari_kode_kesalahan({"spn": 1241, "fmi": 5}, USER)
    assert r["jumlah_cocok"] > 0
    assert r["fmi_diminta_tak_terdaftar"] == 5
    assert 15 in r["fmi_tersedia"] and 21 in r["fmi_tersedia"]
    assert "TIDAK ADA" in r["catatan"]


def test_spn_fmi_tertukar_dikoreksi():
    # FMI 1551 mustahil (valid 0–31) & SPN 5 kecil → auto-tukar jadi 1551/5.
    r = ai._t_cari_kode_kesalahan({"spn": 5, "fmi": 1551}, USER)
    assert r["spn_fmi_ditukar"] is True
    assert r["kriteria"]["spn"] == 1551 and r["kriteria"]["fmi"] == 5
    assert any(h["kode"] == "P0645" for h in r["hasil"])


def test_fmi_luar_rentang_diabaikan():
    # SPN besar + FMI 99 (tak bisa ditukar) → fmi diabaikan, hasil semua FMI.
    r = ai._t_cari_kode_kesalahan({"spn": 1241, "fmi": 99}, USER)
    assert r["fmi_di_luar_rentang"] == 99
    assert r["jumlah_cocok"] > 0


def test_spn_tak_terdaftar_jujur():
    r = ai._t_cari_kode_kesalahan({"spn": 424242, "fmi": 3}, USER)
    assert r["spn_tak_terdaftar"] is True
    assert r["jumlah_cocok"] == 0
    assert "TIDAK terdaftar" in r["catatan"]


def test_pasangan_ambigu_tampilkan_semua():
    # (91,19) punya 3 kode berbeda → semuanya dikembalikan + catatan tegas.
    r = ai._t_cari_kode_kesalahan({"spn": 91, "fmi": 19}, USER)
    assert r["jumlah_cocok"] == 3
    assert "3 KODE BERBEDA" in r["catatan"]


def test_perilaku_lama_tak_berubah():
    r = ai._t_cari_kode_kesalahan({"spn": 1551, "fmi": 5}, USER)
    assert r["jumlah_cocok"] == 1 and r["hasil"][0]["kode"] == "P0645"
    assert "fmi_diminta_tak_terdaftar" not in r
    assert "spn_fmi_ditukar" not in r


def test_diagnosa_jangkar_fallback(monkeypatch):
    # SIMS dimock; fault_codes NYATA — pasangan 1241/5 tak terdaftar → jangkar
    # tetap terisi FMI lain utk SPN sama + catatan jujur.
    monkeypatch.setattr(ai.sims_eol, "tanya",
                        lambda q, **kw: {"found": False, "kosong": True,
                                         "jawaban": "belum terindex"})
    r = ai._t_diagnosa({"spn": 1241, "fmi": 5}, USER)
    assert r["kode_kesalahan_lokal"], "jangkar DTC tidak boleh kosong"
    assert all(x["spn"] == 1241 for x in r["kode_kesalahan_lokal"])
    assert r["fmi_diminta_tak_terdaftar"] == 5
    assert "TIDAK ADA" in r["catatan_dtc"]
