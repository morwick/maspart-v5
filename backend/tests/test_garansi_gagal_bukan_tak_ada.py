"""Kejujuran klaim tool diagnosa & garansi: GAGAL AMBIL ≠ TIDAK ADA.

Tiga kebohongan diam-diam yang ditutup di sini (audit 2026-08-06), semuanya
lahir dari sumber data yang MELEBUR "gagal" dengan "kosong":

1. `diagnosa` — flag "SPN+FMI TIDAK ADA di database" dulu dihitung HANYA dari
   tabel Bosch (fault_codes). Untuk SPN 520264 FMI 11 tool membalas found=True
   dengan lembar diagnosa resmi TERLAMPIR, sekaligus "FMI 11 tak terdaftar":
   dua pesan bertentangan di satu payload. Kontradiksi yang sama sudah ditutup
   di `cari_kode_kesalahan` (cocok_eksak_kanonik) tapi masih hidup di sini.

2. `semua_klaim` — saat halaman-1 SIMS gagal, service membalas {"klaim": []}
   (bukan None) DAN menyimpannya di cache 10 menit. Rekap/Excel/saring-durasi
   lalu menyimpulkan "tidak ada klaim" dari kegagalan API — dan mengulanginya
   selama 10 menit setelah SIMS pulih.

3. `info_unit` — None untuk "gagal fetch" MAUPUN "tak terdaftar". Excel garansi
   massal menulis "TIDAK ADA DI SIMS" untuk unit yang nyata ketika SIMS mati.

Nol jaringan, nol panggilan model: SIMS dipalsukan di level sims_warranty.
"""
from __future__ import annotations

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture
def garansi(monkeypatch):
    """Gerbang ai_garansi terbuka + SIMS 'siap'; cache service dibersihkan.

    Ingatan PROBE unit (_info_unit_teliti, TTL 10 menit) ikut dikosongkan: ia
    hidup selama proses, jadi vonis frame dari test lain bisa membuat test di
    sini melewati _get yang justru sedang diuji."""
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: True)
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    ai.sims_warranty._CACHE.clear()
    ai._reset_unit_probe_cache()
    yield monkeypatch
    ai.sims_warranty._CACHE.clear()
    ai._reset_unit_probe_cache()


# ══ 1. diagnosa: cocok-eksak lintas-sumber ═══════════════════════════════
def test_diagnosa_pasangan_ada_di_sumber_lain_tak_diklaim_tak_terdaftar(monkeypatch):
    """SPN 520264 FMI 11: tabel Bosch tak punya, TAPI store kanonik ('kartu'),
    lembar diagnosa terstruktur, dan PDF resminya punya pasangan PERSIS itu."""
    monkeypatch.setattr(ai.sims_eol, "tanya",
                        lambda q, **kw: {"found": False, "kosong": True,
                                         "jawaban": "belum terindex"})
    r = ai._t_diagnosa({"spn": 520264, "fmi": 11}, ADMIN)
    assert r["found"] is True
    # ⛔ Tidak boleh mengaku "tak terdaftar" saat sumber lain punya persis.
    assert "fmi_diminta_tak_terdaftar" not in r
    assert "TIDAK ADA" not in r.get("catatan_dtc", "")
    assert "JANGAN bilang kodenya tidak terdaftar" in r["catatan_dtc"]
    # Barisnya IKUT disajikan, bukan cuma diklaim ada.
    assert any(x["spn"] == 520264 and x["fmi"] == 11
               for x in r["kode_kesalahan_kanonik"])


def test_diagnosa_pasangan_benar_benar_tak_ada_tetap_tegas(monkeypatch):
    """Regresi kebalikannya: 1241/5 memang tak ada di sumber MANA PUN →
    flag tegas harus TETAP muncul (fix tak boleh melunakkan yang jujur)."""
    monkeypatch.setattr(ai.sims_eol, "tanya",
                        lambda q, **kw: {"found": False, "kosong": True,
                                         "jawaban": "belum terindex"})
    r = ai._t_diagnosa({"spn": 1241, "fmi": 5}, ADMIN)
    assert r["fmi_diminta_tak_terdaftar"] == 5
    assert "TIDAK ADA" in r["catatan_dtc"]
    assert "kode_kesalahan_kanonik" not in r
    assert r["kode_kesalahan_lokal"], "jangkar FMI-lain tetap wajib terisi"


def test_diagnosa_cocok_eksak_dari_abs_scr(monkeypatch):
    """Sumber ABS/SCR saja pun cukup untuk membatalkan klaim 'tak terdaftar'
    (abs_scr_codes.search ikut fallback SPN-saja → diuji baris per baris)."""
    monkeypatch.setattr(ai.sims_eol, "tanya",
                        lambda q, **kw: {"found": False, "kosong": True, "jawaban": "x"})
    monkeypatch.setattr(ai.fault_codes, "search",
                        lambda **kw: ([] if kw.get("fmi") is not None else
                                      [{"code": "P0100", "spn": 4242, "fmi": 3,
                                        "english": "x", "desc_cn": "x",
                                        "mil": "ON", "svs": "OFF"}]))
    monkeypatch.setattr(ai.abs_scr_codes, "search", lambda **kw: [
        {"sistem": "ABS", "kode": "ABS-X", "spn": 4242, "fmi": 9, "blink": "1-2",
         "komponen": "sensor", "deskripsi": "d", "penyebab": "p", "reaksi": "r",
         "perbaikan": "cek konektor", "lampu": "ABS"}])
    monkeypatch.setattr(ai.dtc_codes, "search_spn_fmi", lambda *a, **kw: [])
    monkeypatch.setattr(ai.dtc_diagnosa, "for_pair", lambda spn, fmi: [])
    monkeypatch.setattr(ai, "_fault_pdf_cards", lambda spn, fmi: [])
    r = ai._t_diagnosa({"spn": 4242, "fmi": 9}, ADMIN)
    assert "fmi_diminta_tak_terdaftar" not in r
    assert "JANGAN bilang kodenya tidak terdaftar" in r["catatan_dtc"]


def test_diagnosa_kanonik_fallback_spn_saja_bukan_bukti_cocok(monkeypatch):
    """search_spn_fmi mengembalikan FMI LAIN saat pasangan tak ada — itu BUKAN
    kecocokan eksak dan tak boleh membatalkan flag jujur."""
    monkeypatch.setattr(ai.sims_eol, "tanya",
                        lambda q, **kw: {"found": False, "kosong": True, "jawaban": "x"})
    monkeypatch.setattr(ai.fault_codes, "search",
                        lambda **kw: ([] if kw.get("fmi") is not None else
                                      [{"code": "P0100", "spn": 4242, "fmi": 3,
                                        "english": "x", "desc_cn": "x",
                                        "mil": "ON", "svs": "OFF"}]))
    monkeypatch.setattr(ai.abs_scr_codes, "search", lambda **kw: [])
    monkeypatch.setattr(ai.dtc_codes, "search_spn_fmi", lambda *a, **kw: [
        {"spn": 4242, "fmi": 3, "sumber": "eol", "kode": "SPN4242/FMI3",
         "deskripsi": "beda FMI"}])
    monkeypatch.setattr(ai.dtc_diagnosa, "for_pair", lambda spn, fmi: [])
    monkeypatch.setattr(ai, "_fault_pdf_cards", lambda spn, fmi: [])
    r = ai._t_diagnosa({"spn": 4242, "fmi": 9}, ADMIN)
    assert r["fmi_diminta_tak_terdaftar"] == 9
    assert "TIDAK ADA" in r["catatan_dtc"]
    assert "kode_kesalahan_kanonik" not in r


# ══ 2. semua_klaim: SIMS mati ≠ tidak ada klaim ══════════════════════════
def test_sims_gagal_bukan_tidak_ada_klaim(garansi):
    """Halaman-1 gagal → semua_klaim ASLI membalas {"klaim": []}; tool WAJIB
    melapor gagal, bukan 'tidak ada klaim'."""
    garansi.setattr(ai.sims_warranty, "daftar_klaim",
                    lambda vin="", ro_no="", halaman=1, page_size=15: None)
    for tool, args in ((ai._t_rekap_klaim, {}),
                       (ai._t_excel_riwayat_klaim, {}),
                       (ai._t_riwayat_klaim, {"durasi_min_jam": 72})):
        r = tool(args, ADMIN)
        assert "error" in r, tool.__name__
        assert "BUKAN bukti" in r["error"]
        assert r.get("found") is not False, "jangan berpura-pura lookup nihil"


def test_hasil_kosong_karena_gagal_tidak_dicache(garansi):
    """Inti temuan: kegagalan tak boleh mengendap 10 menit. Setelah SIMS pulih,
    panggilan berikutnya harus melihat data — bukan cache kosong beracun."""
    rows = [{"no_wo": "R1", "status": "Selesai", "gejala": "bocor",
             "durasi_jam": 2.0, "tanggal": "2026-08-01", "frame": "SJ100001"}]
    mati = {"n": True}

    def fake_daftar(vin="", ro_no="", halaman=1, page_size=15):
        if mati["n"]:
            return None
        return {"total": 1, "halaman": halaman,
                "klaim": list(rows) if halaman == 1 else []}
    garansi.setattr(ai.sims_warranty, "daftar_klaim", fake_daftar)

    r1 = ai._t_rekap_klaim({}, ADMIN)
    assert "error" in r1
    assert not [k for k in ai.sims_warranty._CACHE if str(k).startswith("semua:")], \
        "hasil kosong-karena-gagal TIDAK boleh tersimpan di cache"

    mati["n"] = False                      # SIMS pulih
    r2 = ai._t_rekap_klaim({}, ADMIN)
    assert r2["found"] is True and r2["total_klaim"] == 1


def test_cache_beracun_lama_dibuang_bukan_dipercaya(garansi):
    """Entri kosong yang terlanjur mengendap (mis. dari proses sebelum fix)
    tak boleh dipercaya saat halaman-1 nyatanya berisi."""
    import time
    ai.sims_warranty._CACHE["semua::40"] = (time.time(), {"total": 0, "klaim": []})
    garansi.setattr(ai.sims_warranty, "daftar_klaim",
                    lambda vin="", ro_no="", halaman=1, page_size=15:
                    {"total": 1, "halaman": halaman,
                     "klaim": ([{"no_wo": "R9", "status": "Selesai",
                                 "gejala": "x", "durasi_jam": 3.0,
                                 "tanggal": "2026-08-02", "frame": "SJ1"}]
                               if halaman == 1 else [])})
    r = ai._t_rekap_klaim({}, ADMIN)
    assert r["found"] is True and r["total_klaim"] == 1


def test_memang_kosong_tetap_dijawab_kosong(garansi):
    """Kebalikannya: SIMS sehat & unit memang tak punya klaim → jawaban kosong
    yang JUJUR (bukan error) dan boleh di-cache."""
    garansi.setattr(ai.sims_warranty, "daftar_klaim",
                    lambda vin="", ro_no="", halaman=1, page_size=15:
                    {"total": 0, "halaman": halaman, "klaim": []})
    r = ai._t_rekap_klaim({"rangka": "LZZ1ELSF7SJ346555"}, ADMIN)
    assert r["found"] is False and "error" not in r
    assert "Tidak ada klaim" in r["catatan"]
    assert not r.get("data_terpotong")
    assert [k for k in ai.sims_warranty._CACHE if str(k).startswith("semua:")], \
        "kosong yang JUJUR boleh di-cache (paginasi mahal)"


def test_paginasi_terpotong_ditandai_di_semua_pemakai(garansi):
    """Halaman-2 gagal di tengah → 2 dari 5 klaim. Ringkasan tak boleh
    disajikan seolah lengkap."""
    def fake_daftar(vin="", ro_no="", halaman=1, page_size=15):
        if halaman == 1:
            return {"total": 5, "halaman": 1, "klaim": [
                {"no_wo": "R1", "status": "Selesai", "gejala": "bocor",
                 "durasi_jam": 100.0, "tanggal": "2026-08-01", "frame": "SJ1"},
                {"no_wo": "R2", "status": "Selesai", "gejala": "bocor",
                 "durasi_jam": 90.0, "tanggal": "2026-08-02", "frame": "SJ1"}]}
        return None                        # paginasi putus di tengah
    garansi.setattr(ai.sims_warranty, "daftar_klaim", fake_daftar)
    garansi.setattr(ai.ai_export, "stash_export",
                    lambda judul, kolom, baris: ("EXP", "riwayat.xlsx"))
    garansi.setattr(ai.sims_warranty, "nilai_klaim",
                    lambda ro_ids, maks=60: {"total_cny": 0.0, "jumlah_wo": 0,
                                             "terpotong": False})

    rekap = ai._t_rekap_klaim({}, ADMIN)
    assert rekap["data_terpotong"] is True
    assert rekap["total_klaim"] == 5 and rekap["jumlah_dianalisis"] == 2
    assert "TERPOTONG" in rekap["catatan"]
    assert rekap["_cek_tak_lengkap"] is True      # telemetri 'err', bukan 'nf'

    xls = ai._t_excel_riwayat_klaim({}, ADMIN)
    assert xls["data_terpotong"] is True and "TERPOTONG" in xls["catatan"]

    dur = ai._t_riwayat_klaim({"durasi_min_jam": 72}, ADMIN)
    assert dur["data_terpotong"] is True and "TERPOTONG" in dur["catatan"]
    assert dur["jumlah_cocok"] == 2
    assert list(dur.keys())[-1] == "catatan"      # invarian _cap_tool_content


def test_lengkap_tidak_ditandai_terpotong(garansi):
    """Regresi: data utuh TIDAK boleh dituduh terpotong."""
    garansi.setattr(ai.sims_warranty, "semua_klaim", lambda vin="": {
        "total": 2, "klaim": [
            {"no_wo": "R1", "status": "Selesai", "gejala": "a", "durasi_jam": 80.0,
             "tanggal": "2026-08-01", "frame": "SJ1"},
            {"no_wo": "R2", "status": "Selesai", "gejala": "b", "durasi_jam": 90.0,
             "tanggal": "2026-08-02", "frame": "SJ1"}]})
    garansi.setattr(ai.sims_warranty, "nilai_klaim",
                    lambda ro_ids, maks=60: {"total_cny": 0.0, "jumlah_wo": 0,
                                             "terpotong": False})
    r = ai._t_rekap_klaim({}, ADMIN)
    assert "data_terpotong" not in r and "TERPOTONG" not in r["catatan"]
    assert r["jumlah_dianalisis"] == 2


def test_exception_jaringan_bukan_klaim_kosong(garansi):
    """requests.RequestException tak ditangkap service → menembus ke tool.
    Harus jadi 'gagal', bukan crash & bukan 'tidak ada klaim'."""
    import requests

    def _meledak(**kw):
        raise requests.ConnectionError("jaringan putus")
    garansi.setattr(ai.sims_warranty, "semua_klaim", _meledak)
    r = ai._t_excel_riwayat_klaim({}, ADMIN)
    assert "error" in r and "BUKAN bukti" in r["error"]


# ══ 3. info_unit: gagal fetch ≠ unit tak terdaftar ═══════════════════════
def test_cek_garansi_gagal_bukan_tak_terdaftar(garansi):
    """SIMS mati → jangan bilang unitnya tidak ada."""
    garansi.setattr(ai.sims_warranty, "info_unit", lambda r: None)
    garansi.setattr(ai.sims_warranty, "_get", lambda path, params=None: None)
    r = ai._t_cek_garansi({"rangka": "LZZ1ELSF7SJ346555"}, ADMIN)
    assert r["found"] is False and r["gagal_dicek"] is True
    assert r["_cek_tak_lengkap"] is True
    assert "GAGAL DICEK" in r["catatan"]
    assert "tidak ditemukan" not in r["catatan"].lower()
    assert list(r.keys())[-1] == "catatan"


def test_cek_garansi_exception_jaringan_juga_gagal_dicek(garansi):
    import requests

    def _meledak(rangka):
        raise requests.ConnectTimeout("timeout")
    garansi.setattr(ai.sims_warranty, "info_unit", _meledak)
    r = ai._t_cek_garansi({"rangka": "SJ346555"}, ADMIN)
    assert r["gagal_dicek"] is True and "GAGAL DICEK" in r["catatan"]


def test_excel_massal_pisahkan_gagal_dicek_dari_tak_ada(garansi):
    """Kolom & ringkasan: unit yang gagal dicek TIDAK boleh tertulis
    'TIDAK ADA DI SIMS' dan tak boleh menumpuk di hitungan tak_ada."""
    unit = {"frame": "SJ346555", "unit": "HOWO NX", "emisi": "Euro II",
            "garansi": {"mulai": "2025-09-06", "berakhir": "2026-09-06",
                        "masih_aktif": True, "sisa_hari": 45,
                        "persen_terpakai": 87.5},
            "komponen": {"mesin": {"no_seri": "E1"}, "gearbox": {"no_seri": "G1"}}}
    garansi.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: {
        "headers": ["No Rangka"],
        "_body": [["SJ346555"], ["XX000000"], ["SJ999999"]]})
    garansi.setattr(ai.sims_warranty, "info_unit",
                    lambda rk: dict(unit) if rk == "SJ346555" else None)
    # XX000000 → SIMS menjawab (memang tak ada); SJ999999 → SIMS gagal.
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None:
                    None if (params or {}).get("chassisNo") == "SJ999999"
                    else {"code": 0, "data": None})
    ditulis = {}
    garansi.setattr(ai.ai_export, "stash_export",
                    lambda judul, kolom, baris: (ditulis.update(baris=baris)
                                                 or ("EXP", "garansi.xlsx")))
    r = ai._t_sheet_garansi_massal({"_sheet_id": "s1"}, ADMIN)
    assert r["ketemu"] == 1 and r["tak_ada"] == 1 and r["gagal_dicek"] == 1
    assert r["_cek_tak_lengkap"] is True
    kolom_model = {b[1]: b[2] for b in ditulis["baris"]}
    assert kolom_model["XX000000"] == "TIDAK ADA DI SIMS"
    assert kolom_model["SJ999999"] == "GAGAL DICEK — ULANGI"
    assert "GAGAL DICEK" in r["catatan"]
    assert list(r.keys())[-1] == "catatan"


def test_detail_klaim_sims_gagal_bukan_nomor_wo_salah(garansi):
    """Cacat sekelas: daftar_klaim None dulu dijawab 'WO tidak ditemukan, cek
    ulang nomornya' — menyalahkan nomor user padahal SIMS yang diam."""
    garansi.setattr(ai.sims_warranty, "daftar_klaim",
                    lambda **kw: None)
    r = ai._t_detail_klaim({"no_wo": "RIDZ0052607121"}, ADMIN)
    assert "error" in r and "BUKAN bukti" in r["error"]
    assert r.get("found") is not False
    assert "cek ulang nomornya" not in r["error"].lower()


def test_frame_salah_format_bukan_gagal_server(garansi):
    """Frame bukan 8 karakter = kesalahan input (SIMS memang balas HTTP 500) —
    jangan diprobe & jangan dilaporkan sebagai gangguan server."""
    diprobe = {"n": 0}
    garansi.setattr(ai.sims_warranty, "info_unit", lambda r: None)
    garansi.setattr(ai.sims_warranty, "_get",
                    lambda path, params=None: diprobe.__setitem__("n", 1))
    r = ai._t_cek_garansi({"rangka": "SJ12"}, ADMIN)
    assert diprobe["n"] == 0
    assert r["found"] is False and not r.get("gagal_dicek")
    assert "tidak ditemukan" in r["catatan"].lower()
