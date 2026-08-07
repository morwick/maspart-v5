"""REM DARURAT LATENSI garansi (audit 2026-08-06) — dua jalur SIMS yang, saat
server lambat/diam, menggiling HTTP jauh melewati batas masuk akal dan menahan
giliran asisten:

1. `sheet_garansi_massal` — 200 frame × _info_unit_teliti tanpa pemutus arus.
   SIMS diam = giliran itu menggiling berjam-jam untuk hasil yang sudah pasti
   "GAGAL DICEK" semua. Sekarang berhenti setelah 3 gagal BERTURUT-TURUT.

2. Probe unit di `_info_unit_teliti` — GET mentah TANPA cache di sisi service,
   jadi frame yang memang tak terdaftar diprobe ulang tiap giliran. Sekarang
   vonisnya (terjawab/gagal) diingat ber-TTL.

Rem ketiga (cooldown sims.status_jual) diuji di test_kejujuran_klaim_epc.py —
hanya modul itu yang dikecualikan fixture autouse `_jangan_status_jual_sims_nyata`
di conftest, jadi status_jual ASLI cuma bisa diuji di sana.

Semua jaringan di-MOCK; nol panggilan model.
"""
from __future__ import annotations

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


class _JamPalsu:
    """Pengganti modul `time` — hanya monotonic, dimajukan manual oleh test."""

    def __init__(self, mulai: float = 1000.0):
        self.t = mulai

    def monotonic(self) -> float:
        return self.t

    def maju(self, detik: float) -> None:
        self.t += detik


# ═══════════════════════════════════════════════════════════════════════
#  1. sheet_garansi_massal — pemutus arus 3 gagal beruntun
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture
def garansi(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: True)
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    monkeypatch.setattr(ai.sims_warranty, "frame_dari_rangka", lambda x: x[-8:])
    ai._reset_unit_probe_cache()
    yield monkeypatch
    ai._reset_unit_probe_cache()


def _pasang_sheet(mp, frames):
    mp.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: {
        "headers": ["No Rangka"], "_body": [[f] for f in frames]})
    ditulis = {}
    mp.setattr(ai.ai_export, "stash_export",
               lambda judul, kolom, baris: (ditulis.update(baris=baris)
                                            or ("EXP", "garansi.xlsx")))
    return ditulis


def test_massal_berhenti_setelah_3_gagal_beruntun(garansi):
    frames = [f"SJ88000{i}" for i in range(1, 6)]     # 5 unit, SIMS diam total
    ditulis = _pasang_sheet(garansi, frames)
    http = []
    garansi.setattr(ai.sims_warranty, "info_unit",
                    lambda rk: http.append(rk) or None)
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None:
                    http.append((params or {}).get("chassisNo")) or None)

    r = ai._t_sheet_garansi_massal({"_sheet_id": "s1"}, ADMIN)

    # HTTP berhenti di 3 frame pertama (info_unit + probe per frame).
    assert set(http) == set(frames[:3])
    assert len(http) == 6
    # Semua baris tetap berlabel gagal & hitungan ringkasan tetap benar.
    assert len(ditulis["baris"]) == 5
    assert all("GAGAL DICEK" in b[2] for b in ditulis["baris"])
    assert ditulis["baris"][4][2] == ai._GARANSI_BARIS_PUTUS
    assert r["gagal_dicek"] == 5 and r["tak_ada"] == 0 and r["ketemu"] == 0
    assert r["dihentikan_dini"] is True and r["_cek_tak_lengkap"] is True
    assert "DIHENTIKAN" in r["catatan"]
    assert list(r.keys())[-1] == "catatan"            # invarian _cap_tool_content


def test_massal_sukses_di_tengah_mereset_hitungan(garansi):
    """SIMS cuma tersendat: 2 gagal → 1 terjawab → 2 gagal = TIDAK boleh putus."""
    frames = [f"SJ88010{i}" for i in range(1, 6)]
    ditulis = _pasang_sheet(garansi, frames)
    unit = {"frame": "SJ880103", "unit": "HOWO NX", "brand": "HOWO",
            "emisi": "Euro II", "garansi": {"mulai": "2025-09-06",
                                            "berakhir": "2026-09-06",
                                            "masih_aktif": True, "sisa_hari": 45,
                                            "persen_terpakai": 87.5},
            "komponen": {}}
    dicek = []
    garansi.setattr(ai.sims_warranty, "info_unit",
                    lambda rk: dicek.append(rk) or (dict(unit)
                                                    if rk == "SJ880103" else None))
    garansi.setattr(ai.sims_warranty, "_get", lambda path, params=None: None)

    r = ai._t_sheet_garansi_massal({"_sheet_id": "s1"}, ADMIN)

    assert dicek == frames                      # kelimanya tetap dicek
    assert r["ketemu"] == 1 and r["gagal_dicek"] == 4 and r["tak_ada"] == 0
    assert "dihentikan_dini" not in r
    assert "DIHENTIKAN" not in r["catatan"]
    assert len(ditulis["baris"]) == 5


def test_massal_semua_terjawab_tanpa_pemutus(garansi):
    """Regresi: SIMS sehat & unit memang tak terdaftar → jalur normal utuh."""
    frames = [f"SJ88020{i}" for i in range(1, 6)]
    _pasang_sheet(garansi, frames)
    garansi.setattr(ai.sims_warranty, "info_unit", lambda rk: None)
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None: {"code": 0, "data": None})

    r = ai._t_sheet_garansi_massal({"_sheet_id": "s1"}, ADMIN)
    assert r["tak_ada"] == 5 and r["gagal_dicek"] == 0
    assert "dihentikan_dini" not in r and "_cek_tak_lengkap" not in r


# ═══════════════════════════════════════════════════════════════════════
#  2. Probe unit — vonis diingat ber-TTL
# ═══════════════════════════════════════════════════════════════════════
def test_probe_terjawab_hanya_sekali_per_ttl(garansi):
    n = []
    garansi.setattr(ai.sims_warranty, "info_unit", lambda rk: None)
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None: n.append(1) or {"code": 0, "data": None})

    assert ai._info_unit_teliti("SJ880301") == (None, "")
    assert ai._info_unit_teliti("SJ880301") == (None, "")
    assert len(n) == 1                      # panggilan kedua dari ingatan


def test_probe_gagal_juga_diingat(garansi):
    """Vonis 'gagal' ikut disimpan — justru itu kasus paling mahal (GET yang
    menggantung), dan artinya tetap "belum terjawab", bukan "tak terdaftar"."""
    n = []
    garansi.setattr(ai.sims_warranty, "info_unit", lambda rk: None)
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None: n.append(1) or None)

    assert ai._info_unit_teliti("SJ880302")[1] == ai._UNIT_GAGAL
    assert ai._info_unit_teliti("SJ880302")[1] == ai._UNIT_GAGAL
    assert len(n) == 1


def test_probe_diulang_setelah_ttl_lewat(garansi):
    jam = _JamPalsu()
    garansi.setattr(ai, "time", jam)
    n = []
    garansi.setattr(ai.sims_warranty, "info_unit", lambda rk: None)
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None: n.append(1) or {"code": 0, "data": None})

    ai._info_unit_teliti("SJ880303")
    jam.maju(ai._UNIT_PROBE_TTL - 1.0)
    ai._info_unit_teliti("SJ880303")
    assert len(n) == 1                      # masih segar
    jam.maju(2.0)
    ai._info_unit_teliti("SJ880303")
    assert len(n) == 2                      # kedaluwarsa → diprobe lagi


def test_ingatan_probe_dibatasi(garansi):
    """Cap entri: frame unik tak boleh menumpuk tanpa batas di proses panjang."""
    garansi.setattr(ai.sims_warranty, "info_unit", lambda rk: None)
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None: {"code": 0, "data": None})
    for i in range(ai._UNIT_PROBE_MAKS + 20):
        ai._info_unit_teliti(f"SJ{i:06d}")
    assert len(ai._unit_probe_cache) <= ai._UNIT_PROBE_MAKS
