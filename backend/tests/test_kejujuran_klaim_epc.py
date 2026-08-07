"""KEJUJURAN KLAIM tool EPC (2026-08-06).

Satu tema: **gagal mengecek ≠ tidak ada**. Sebelum perbaikan ini, setiap
kegagalan menghubungi EPC/SIMS/Accurate berakhir jadi kalimat positif yang
terdengar pasti — "tidak ada part itu di unit ini", "rangka tak dikenal EPC",
"belum ada di katalog/stok lokal" — sehingga user mencari barang ke tempat lain
padahal barangnya ada, atau disuruh mengeja ulang nomor rangkanya padahal
servernya yang mati.

Ditambah dua klaim cakupan yang dilebihkan (posisi poros 'PASTI', daftar
transmisi 'LENGKAP & PASTI') dan status jual yang akhirnya punya sumber sah
(SIMS isSale) menggantikan marketability EPC yang tafsirnya terbalik.

SEMUA jaringan di-MOCK; tak satu pun test di sini menyentuh EPC/SIMS/model.
"""
import json

import pytest

from app.services import epc, sims, ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


# ═══════════════════════════════════════════════════════════════════════
#  1. cek_massal_part_rangka — jaringan/token EPC ≠ "part tak ada di unit"
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture
def _tanpa_katalog(monkeypatch):
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "injector"], []))


def _pasang_massal_rangka(monkeypatch, per):
    monkeypatch.setattr(ai.epc_bom, "find_part_massal_rangka",
                        lambda rgs, kws: {"per_rangka": per, "order_unik": 1,
                                          "pn_unik": []})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)


def test_rangka_gagal_jaringan_bukan_tidak_ada(monkeypatch, _tanpa_katalog):
    """_err network/token → BUKAN 'tidak ada injector di katalog unit ini'."""
    _pasang_massal_rangka(monkeypatch, {
        "LZZ1BG3H0SJ111111": {"found": False, "_err": "network"},
        "LZZ1BG3H0SJ222222": {"found": False, "_err": "token_expired"},
        "LZZ1BG3H0SJ333333": {"found": False, "_err": "not_found"},
        "LZZ1BG3H0SJ444444": {"found": False, "hits": []},      # dijawab EPC: nihil
    })
    r = ai._t_cek_massal_part_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ111111", "LZZ1BG3H0SJ222222",
                           "LZZ1BG3H0SJ333333", "LZZ1BG3H0SJ444444"],
         "part": "injector"}, ADMIN)

    by = {h["rangka"]: h for h in r["hasil"]}
    # yang GAGAL DICEK tak boleh mengaku tahu apa-apa
    for v in ("LZZ1BG3H0SJ111111", "LZZ1BG3H0SJ222222"):
        assert by[v]["gagal_dicek"] is True
        assert by[v]["catatan"].startswith("gagal dicek")
        assert "BUKAN berarti" in by[v]["catatan"]
    # jawaban SAH dari EPC tetap apa adanya
    assert "tak ditemukan di EPC" in by["LZZ1BG3H0SJ333333"]["catatan"]
    assert "tidak ada 'injector'" in by["LZZ1BG3H0SJ444444"]["catatan"]
    # hitungan: 2 gagal keluar dari 'tak_ada'
    assert r["tak_ada"] == 2 and r["gagal_dicek"] == 2 and r["ketemu"] == 0
    assert set(r["rangka_gagal_dicek"]) == {"LZZ1BG3H0SJ111111", "LZZ1BG3H0SJ222222"}
    assert r["sumber_dicek"]["epc_atlas"] == "sebagian gagal"
    assert "GAGAL DICEK" in r["catatan"]


def test_rangka_semua_sehat_sumber_dicek_ok(monkeypatch, _tanpa_katalog):
    _pasang_massal_rangka(monkeypatch, {
        "LZZ1BG3H0SJ555555": {"found": False, "hits": []},
    })
    r = ai._t_cek_massal_part_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ555555"], "part": "injector"}, ADMIN)
    assert r["gagal_dicek"] == 0 and r["tak_ada"] == 1
    assert r["sumber_dicek"]["epc_atlas"] == "ok"
    assert "rangka_gagal_dicek" not in r


def test_rangka_supersession_indeks_dingin_tidak_diklaim_nihil(
        monkeypatch, _tanpa_katalog):
    """Indeks persamaan SIMS DINGIN → lookup selalu kosong; itu bukan bukti
    'tak ada pengganti'."""
    monkeypatch.setattr(ai.epc_bom, "find_part_massal_rangka", lambda rgs, kws: {
        "per_rangka": {"LZZ1BG3H0SJ666666": {
            "found": True, "order_no": "O1",
            "hits": [{"pn": "AZ9998887776", "nama": "Fuel injector"}]}},
        "order_unik": 1, "pn_unik": ["AZ9998887776"]})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 0)   # DINGIN

    r = ai._t_cek_massal_part_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ666666"], "part": "injector"}, ADMIN)

    assert r["pengganti_belum_tuntas"] == ["AZ9998887776"]
    assert "belum tuntas" in r["catatan"]


def test_rangka_supersession_indeks_hangat_nihil_itu_sah(
        monkeypatch, _tanpa_katalog):
    monkeypatch.setattr(ai.epc_bom, "find_part_massal_rangka", lambda rgs, kws: {
        "per_rangka": {"LZZ1BG3H0SJ777777": {
            "found": True, "order_no": "O1",
            "hits": [{"pn": "AZ9998887776", "nama": "Fuel injector"}]}},
        "order_unik": 1, "pn_unik": ["AZ9998887776"]})
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)

    r = ai._t_cek_massal_part_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ777777"], "part": "injector"}, ADMIN)

    assert "pengganti_belum_tuntas" not in r


# ═══════════════════════════════════════════════════════════════════════
#  2. cek_massal_part_mesin — sesi/jaringan Weichai ≠ "part tak ada di BOM"
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture
def _mesin_dasar(monkeypatch):
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "starter"], []))
    monkeypatch.setattr(ai.epc_weichai, "replace_part",
                        lambda pn, rangka="": {"found": True, "digantikan_oleh": []})


def test_mesin_reason_gagal_bukan_tidak_ada(monkeypatch, _mesin_dasar):
    monkeypatch.setattr(ai.epc_weichai, "find_part_massal", lambda nos, terms: {
        "per_engine": {
            "4P24B000001": {"found": False, "reason": "network"},
            "4P24B000002": {"found": False, "reason": "no_session"},
            "4P24B000003": {"found": False, "reason": "no_order"},
            "4P24B000004": {"found": False, "hits": []},     # BOM dibuka, nihil
        }, "orders_unik": 1, "pn_unik": []})

    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000001", "4P24B000002",
                             "4P24B000003", "4P24B000004"],
         "part": "starter"}, ADMIN)

    by = {h["no_mesin"]: h for h in r["hasil"]}
    for no in ("4P24B000001", "4P24B000002"):
        assert by[no]["gagal_dicek"] is True
        assert by[no]["catatan"].startswith("gagal dicek")
        assert "BUKAN berarti" in by[no]["catatan"]
    assert "tak ada di EPC Weichai" in by["4P24B000003"]["catatan"]
    assert "tidak ada 'starter'" in by["4P24B000004"]["catatan"]
    assert r["tak_ada"] == 2 and r["gagal_dicek"] == 2 and r["ketemu"] == 0
    assert set(r["mesin_gagal_dicek"]) == {"4P24B000001", "4P24B000002"}
    assert "GAGAL DICEK" in r["catatan"]


def test_walk_bom_gagal_dibawa_keluar_bukan_hits_kosong(monkeypatch):
    """Lapis SERVICE. find_part_massal dulu menelan kegagalan walk BOM: order
    ter-resolve tapi findBomTree gagal → hits kosong, tak terbedakan dari BOM
    yang memang tak punya part itu."""
    monkeypatch.setattr(ai.epc_weichai, "_ensure_token", lambda rangka="": "TOK")
    monkeypatch.setattr(ai.epc_weichai, "resolve_engine_order", lambda no: {
        "found": True, "dhhNumber": "D-" + no[-1], "dhhDate": "",
        "model": "WP4G130E22"})

    def _walk(tok, dhh, dd, meta):
        if dhh == "D-1":
            return {"found": False, "reason": "network"}
        if dhh == "D-2":
            return {"found": False, "reason": "empty"}
        if dhh == "D-3":
            raise RuntimeError("koneksi putus")   # menembus dari _get
        return {"found": True, "groups": [{"nama": "Fuel", "parts": [
            {"pn": "AZ9998887776", "nama": "Fuel injector"}]}]}

    monkeypatch.setattr(ai.epc_weichai, "_walk_bom", _walk)
    res = ai.epc_weichai.find_part_massal(
        ["4P24B00000" + s for s in "1234"], ["injector"])

    per = res["per_engine"]
    assert per["4P24B000001"] == {"found": False, "reason": "network",
                                  "model": "WP4G130E22", "dhhNumber": "D-1"}
    assert per["4P24B000002"]["reason"] == "empty"
    assert per["4P24B000003"]["reason"] == "api"      # exception → tetap 'gagal'
    assert per["4P24B000004"]["found"] is True
    assert res["pn_unik"] == ["AZ9998887776"]


def test_walk_bom_sukses_tapi_kosong_tetap_tanpa_reason(monkeypatch):
    """Kebalikannya: BOM TERBUKA & memang tak punya part itu = jawaban SAH —
    tak boleh ikut ditandai gagal (kalau tidak, 'tak ada' jadi mustahil)."""
    monkeypatch.setattr(ai.epc_weichai, "_ensure_token", lambda rangka="": "TOK")
    monkeypatch.setattr(ai.epc_weichai, "resolve_engine_order", lambda no: {
        "found": True, "dhhNumber": "D1", "dhhDate": "", "model": "WP4G130E22"})
    monkeypatch.setattr(ai.epc_weichai, "_walk_bom", lambda tok, dhh, dd, meta: {
        "found": True, "groups": [{"nama": "Fuel", "parts": [
            {"pn": "AZ9998887770", "nama": "Fuel pipe"}]}]})

    per = ai.epc_weichai.find_part_massal(["4P24B000009"], ["injector"])["per_engine"]
    assert per["4P24B000009"]["found"] is False
    assert "reason" not in per["4P24B000009"]
    assert per["4P24B000009"]["hits"] == []


def test_walk_bom_gagal_tembus_sampai_tool_massal(monkeypatch, _mesin_dasar):
    """Lapis TOOL (jalur nyata, find_part_massal ASLI): mesin yang BOM-nya gagal
    dibuka masuk 'gagal_dicek', BUKAN 'tidak ada starter di mesin ini'."""
    monkeypatch.setattr(ai.epc_weichai, "_ensure_token", lambda rangka="": "TOK")
    monkeypatch.setattr(ai.epc_weichai, "resolve_engine_order", lambda no: {
        "found": True, "dhhNumber": "D-" + no[-1], "dhhDate": "",
        "model": "WP4G130E22"})
    monkeypatch.setattr(ai.epc_weichai, "_walk_bom", lambda tok, dhh, dd, meta: (
        {"found": False, "reason": "network"} if dhh == "D-1" else
        {"found": True, "groups": [{"nama": "Fuel", "parts": [
            {"pn": "AZ9998887770", "nama": "Fuel pipe"}]}]}))

    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000001", "4P24B000002"], "part": "starter"}, ADMIN)

    by = {h["no_mesin"]: h for h in r["hasil"]}
    assert by["4P24B000001"]["gagal_dicek"] is True
    assert "BUKAN berarti" in by["4P24B000001"]["catatan"]
    assert "tidak ada 'starter'" in by["4P24B000002"]["catatan"]   # dijawab EPC
    assert r["gagal_dicek"] == 1 and r["tak_ada"] == 1
    assert r["mesin_gagal_dicek"] == ["4P24B000001"]
    assert "GAGAL DICEK" in r["catatan"]


def test_mesin_reason_empty_ikut_gagal_dicek(monkeypatch, _mesin_dasar):
    """'empty' = findBomTree menjawab tanpa data terpakai (badan 401/500 pun JSON)
    — jalur satu-mesin sudah memperlakukannya sebagai gagal; massal ikut."""
    monkeypatch.setattr(ai.epc_weichai, "find_part_massal", lambda nos, terms: {
        "per_engine": {"4P24B000007": {"found": False, "reason": "empty"}},
        "orders_unik": 1, "pn_unik": []})
    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000007"], "part": "starter"}, ADMIN)
    assert r["gagal_dicek"] == 1 and r["tak_ada"] == 0
    assert r["hasil"][0]["gagal_dicek"] is True


def test_mesin_supersession_gagal_dicatat(monkeypatch, _mesin_dasar):
    monkeypatch.setattr(ai.epc_weichai, "find_part_massal", lambda nos, terms: {
        "per_engine": {"4P24B000005": {
            "found": True, "model": "WP4G130E22", "dhhNumber": "D1",
            "hits": [{"pn": "1019578238", "nama": "Starter Motor",
                      "group": "Starter Motor Group"}]}},
        "orders_unik": 1, "pn_unik": ["1019578238"]})
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka="": {
        "found": False, "reason": "no_session"})

    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000005"], "part": "starter"}, ADMIN)

    assert r["pengganti_belum_tuntas"] == ["1019578238"]
    assert "belum tuntas" in r["catatan"]


def test_mesin_supersession_exception_dicatat(monkeypatch, _mesin_dasar):
    monkeypatch.setattr(ai.epc_weichai, "find_part_massal", lambda nos, terms: {
        "per_engine": {"4P24B000006": {
            "found": True, "model": "WP4G130E22", "dhhNumber": "D1",
            "hits": [{"pn": "1019578238", "nama": "Starter Motor"}]}},
        "orders_unik": 1, "pn_unik": ["1019578238"]})

    def _boom(pn, rangka=""):
        raise RuntimeError("jaringan putus")

    monkeypatch.setattr(ai.epc_weichai, "replace_part", _boom)
    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000006"], "part": "starter"}, ADMIN)
    assert r["pengganti_belum_tuntas"] == ["1019578238"]


# ═══════════════════════════════════════════════════════════════════════
#  3. epc.get_config — sentinel 'EPC mati' terpisah dari 'VIN tak dikenal'
# ═══════════════════════════════════════════════════════════════════════
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_get_config_network_beri_sentinel(monkeypatch):
    monkeypatch.setattr(epc, "_cache", {})
    monkeypatch.setattr(epc.time, "sleep", lambda s: None)

    def _boom(*a, **kw):
        raise OSError("connection reset")

    monkeypatch.setattr(epc.requests, "get", _boom)
    assert epc.get_config("LZZ1BG3H0SJ398963") == {"_err": "network"}


def test_get_config_vin_tak_dikenal_tetap_kosong(monkeypatch):
    monkeypatch.setattr(epc, "_cache", {})
    monkeypatch.setattr(epc.requests, "get",
                        lambda *a, **kw: _Resp({"success": False, "data": None}))
    assert epc.get_config("LZZ1BG3H0SJ398963") == {}


def test_lookup_bedakan_epc_mati_vs_vin_salah(monkeypatch):
    monkeypatch.setattr(epc, "_cache", {})
    monkeypatch.setattr(epc, "get_config", lambda r: {"_err": "network"})
    mati = epc.lookup("LZZ1BG3H0SJ398963")
    assert mati["found"] is False and mati["_err"] == "network"
    assert "GAGAL DIHUBUNGI" in mati["catatan"]
    assert "cek ejaan" not in mati["catatan"]

    monkeypatch.setattr(epc, "get_config", lambda r: {})
    salah = epc.lookup("LZZ1BG3H0SJ398963")
    assert salah["found"] is False and "_err" not in salah
    assert "tidak ditemukan" in salah["catatan"]


def test_cek_kendaraan_tidak_menyalahkan_nomor_saat_epc_mati(monkeypatch):
    monkeypatch.setattr(ai.epc, "lookup", lambda r: {
        "found": False, "_err": "network", "frame_number": "SJ398963",
        "catatan": "EPC Sinotruk GAGAL DIHUBUNGI (jaringan)."})
    r = ai._t_cek_kendaraan({"rangka": "LZZ1BG3H0SJ398963"}, ADMIN)
    assert r["gagal_dicek"] is True
    assert "GAGAL DIHUBUNGI" in r["catatan"]
    assert "cek ejaan" not in r["catatan"]


def test_spek_massal_pisahkan_epc_mati_dari_tak_dikenal(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "_frame", lambda r: r[-8:])
    cfg = {"SJ111111": {"modelCode": "ZZ1257N60H", "driveMode": "6×4"},
           "SJ222222": {"_err": "network"}}
    monkeypatch.setattr(ai.epc, "get_config", lambda f: dict(cfg.get(f, {})))

    r = ai._t_spek_massal_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ111111", "LZZ1BG3H0SJ222222",
                           "LZZ1BG3H0SJ333333"]}, ADMIN)

    by = {h["rangka"]: h for h in r["hasil"]}
    assert by["LZZ1BG3H0SJ111111"]["found"] is True
    assert by["LZZ1BG3H0SJ222222"]["gagal_dicek"] is True
    assert "tak dikenal" not in by["LZZ1BG3H0SJ222222"]["catatan"]
    assert "tak dikenal EPC" in by["LZZ1BG3H0SJ333333"]["catatan"]
    assert r["ketemu"] == 1 and r["tak_ada"] == 1 and r["gagal_dicek"] == 1
    assert r["rangka_gagal_dicek"] == ["LZZ1BG3H0SJ222222"]


def test_banding_konfigurasi_epc_mati_bukan_tak_dikenal(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "_frame", lambda r: r[-8:])
    cfg = {"SJ111111": {"modelCode": "ZZ1257N60H", "driveMode": "6×4"},
           "SJ222222": {"modelCode": "ZZ1257N60H", "driveMode": "4×2"},
           "SJ333333": {"_err": "network"}}
    monkeypatch.setattr(ai.epc, "get_config", lambda f: dict(cfg.get(f, {})))

    r = ai._t_banding_konfigurasi_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ111111", "LZZ1BG3H0SJ222222",
                           "LZZ1BG3H0SJ333333"]}, ADMIN)

    assert r["found"] and r["dikenal_epc"] == 2
    assert r["gagal_dicek"] == ["LZZ1BG3H0SJ333333"]
    assert "LZZ1BG3H0SJ333333" not in r["tak_dikenal"]   # ⛔ bukan 'tak dikenal'
    assert "EPC gagal dihubungi" in r["catatan"]


# ═══════════════════════════════════════════════════════════════════════
#  4. pengganti_part — stok gagal dicek ≠ "belum ada di katalog"
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture
def _pengganti_satu(monkeypatch):
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 17000)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {
        "digantikan_oleh": [{"pn": "AZ9998887779", "nama": "Baru"}],
        "menggantikan": []})
    monkeypatch.setattr(ai.epc_weichai, "replace_part",
                        lambda pn, rangka="": {"found": False})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai.sims, "status_jual", lambda pn: None)


def test_stok_accurate_gagal_bukan_belum_ada(monkeypatch, _pengganti_satu):
    monkeypatch.setattr(ai.accurate, "available", lambda: True)

    def _boom(pn):
        raise ai.accurate.AccurateError("token expired")

    monkeypatch.setattr(ai.accurate, "stock_full", _boom)
    r = ai._t_pengganti_part({"part_number": "AZ9998887778"}, ADMIN)
    row = r["digantikan_oleh"][0]
    assert row["stok_dicek"] is False
    assert "belum bisa dicek" in row["catatan"]
    assert "belum ada di katalog" not in row["catatan"]


def test_stok_accurate_mati_bukan_belum_ada(monkeypatch, _pengganti_satu):
    monkeypatch.setattr(ai.accurate, "available", lambda: False)
    r = ai._t_pengganti_part({"part_number": "AZ9998887778"}, ADMIN)
    row = r["digantikan_oleh"][0]
    assert row["stok_dicek"] is False
    assert "BUKAN berarti barangnya tidak ada" in row["catatan"]


def test_stok_accurate_sehat_dan_kosong_tetap_belum_ada(monkeypatch, _pengganti_satu):
    """Kasus SAH: Accurate hidup & benar-benar tak punya barangnya."""
    monkeypatch.setattr(ai.accurate, "available", lambda: True)
    monkeypatch.setattr(ai.accurate, "stock_full", lambda pn: None)
    r = ai._t_pengganti_part({"part_number": "AZ9998887778"}, ADMIN)
    row = r["digantikan_oleh"][0]
    assert row["catatan"] == "belum ada di katalog/stok lokal"
    assert "stok_dicek" not in row


# ═══════════════════════════════════════════════════════════════════════
#  5. sims.status_jual — status jual RESMI (isSale), pengganti marketability
# ═══════════════════════════════════════════════════════════════════════
def test_status_jual_dijual(monkeypatch):
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False: {
                            "partName": "X", "raw": {"isSale": "10021001"}})
    s = sims.status_jual("AZ9998887776")
    assert s["dijual"] is True and "dijual di SIMS" in s["label"]


def test_status_jual_tidak_dijual(monkeypatch):
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False: {
                            "raw": {"isSale": "10021002", "isPreOffShelves": 1}})
    s = sims.status_jual("AZ9998887776")
    assert s["dijual"] is False and s["pra_turun_rak"] is True


def test_status_jual_cache_lama_dipaksa_segar_sekali(monkeypatch):
    """Entri cache lama TANPA 'raw' tak pernah tersegarkan oleh pemeriksa yang
    ada (mereka hanya menengok roughWeightKg/lengthCm) → paksa sekali di sini."""
    panggil = []
    monkeypatch.setattr(sims, "_SIMS_OK", True)

    def _info(pn, force_refresh=False):
        panggil.append(force_refresh)
        return ({"raw": {"isSale": "10021001"}} if force_refresh
                else {"partName": "X", "roughWeightKg": 1.0})   # cache lama

    monkeypatch.setattr(sims, "get_part_info", _info)
    assert sims.status_jual("AZ9998887776")["dijual"] is True
    assert panggil == [False, True]


def test_status_jual_gagal_none_bukan_tidak_dijual(monkeypatch):
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    monkeypatch.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {})
    assert sims.status_jual("AZ9998887776") is None
    # kode isSale yang belum dikenal → 'tak diketahui', BUKAN ditebak
    monkeypatch.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False: {"raw": {"isSale": "99999999"}})
    assert sims.status_jual("AZ9998887776")["dijual"] is None


def test_status_jual_sims_muncul_di_pengganti_part(monkeypatch, _pengganti_satu):
    monkeypatch.setattr(ai.accurate, "available", lambda: False)
    monkeypatch.setattr(ai.sims, "status_jual", lambda pn: {
        "dijual": True, "label": "dijual di SIMS", "pra_turun_rak": None,
        "sumber": "SIMS partInfo (isSale)"})
    r = ai._t_pengganti_part({"part_number": "AZ9998887778"}, ADMIN)
    assert r["status_jual_sims"]["dijual"] is True
    assert r["digantikan_oleh"][0]["status_jual_sims"]["dijual"] is True
    assert "status_jual_sims" in r["catatan"]


def test_status_jual_gagal_field_absen_bukan_error(monkeypatch, _pengganti_satu):
    monkeypatch.setattr(ai.accurate, "available", lambda: False)

    def _boom(pn):
        raise RuntimeError("SIMS down")

    monkeypatch.setattr(ai.sims, "status_jual", _boom)
    r = ai._t_pengganti_part({"part_number": "AZ9998887778"}, ADMIN)
    assert r["found"] is True                      # best-effort: tak menggagalkan tool
    assert "status_jual_sims" not in r


def test_status_jual_dibatasi_agar_tak_membakar_latensi(monkeypatch):
    """⛔ SIMS = 1 HTTP live per PN → jumlah panggilan per giliran DIPLAFON."""
    dipanggil = []
    monkeypatch.setattr(ai.sims, "status_jual",
                        lambda pn: dipanggil.append(pn) or None)
    ai._status_jual_map([f"AZ99988877{i:02d}" for i in range(30)])
    assert len(dipanggil) == ai._SIMS_STATUS_MAX_PN


# ── 5b. REM DARURAT LATENSI status_jual (cooldown per-proses) ────────────
# 1 HTTP live per PN lewat sims_fetcher (attempts=2 × timeout 15 dtk): satu PN
# dingin saat SIMS lambat menggantung ~31 dtk, dan pengganti_part (beberapa PN)
# menumpuknya jadi menit-menitan — detail_part ikut memikul. Sesudah sekali
# terbukti LAMBAT-DAN-GAGAL, jalurnya dibungkam sebentar; jawabannya TETAP None
# (arti sama persis: "tak bisa dipastikan"), hanya seketika.
class _JamPalsu:
    """Pengganti modul `time` — hanya monotonic, dimajukan manual oleh test."""

    def __init__(self, mulai: float = 1000.0):
        self.t = mulai

    def monotonic(self) -> float:
        return self.t

    def maju(self, detik: float) -> None:
        self.t += detik


@pytest.fixture
def sims_bersih(monkeypatch):
    sims._reset_status_jual_cooldown()
    monkeypatch.setattr(sims, "_SIMS_OK", True)
    yield monkeypatch
    sims._reset_status_jual_cooldown()


def test_status_jual_cooldown_aktif_tak_menyentuh_sims(sims_bersih):
    """Selama cooldown: None SEKETIKA, get_part_info TIDAK dipanggil."""
    panggil = []
    sims_bersih.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False:
                        panggil.append(pn) or {"raw": {"isSale": "10021001"}})
    sims_bersih.setattr(sims, "_status_jual_diam_sampai",
                        sims.time.monotonic() + 60.0)

    assert sims.status_jual("AZ9998887776") is None
    assert panggil == []


def test_status_jual_miss_cepat_tak_menyetel_cooldown(sims_bersih):
    """PN memang tak ada di master & server MENJAWAB cepat = jawaban sah, bukan
    gangguan → PN berikutnya tetap dicek."""
    panggil = []
    sims_bersih.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False: panggil.append(pn) or {})

    assert sims.status_jual("AZ9998887776") is None
    assert sims._status_jual_diam_sampai == 0.0
    assert sims.status_jual("AZ9998887777") is None
    assert panggil == ["AZ9998887776", "AZ9998887777"]


def test_status_jual_gagal_lambat_menyetel_cooldown_lalu_pulih(sims_bersih):
    jam = _JamPalsu()
    sims_bersih.setattr(sims, "time", jam)
    panggil = []

    def _lambat(pn, force_refresh=False):
        panggil.append(pn)
        jam.maju(31.0)          # attempts=2 × timeout 15 dtk → menggantung
        return {}
    sims_bersih.setattr(sims, "get_part_info", _lambat)

    assert sims.status_jual("AZ9998887776") is None
    assert sims._status_jual_diam_sampai == jam.t + sims._STATUS_JUAL_COOLDOWN
    # PN berikutnya di giliran yang sama: None TANPA menggantung 31 dtk lagi.
    assert sims.status_jual("AZ9998887777") is None
    assert panggil == ["AZ9998887776"]
    # Cooldown lewat → jalur hidup lagi (bukan pemadaman permanen).
    jam.maju(sims._STATUS_JUAL_COOLDOWN + 1.0)
    sims_bersih.setattr(sims, "get_part_info",
                        lambda pn, force_refresh=False: {"raw": {"isSale": "10021001"}})
    assert sims.status_jual("AZ9998887778")["dijual"] is True


def test_status_jual_exception_lambat_juga_menyetel_cooldown(sims_bersih):
    jam = _JamPalsu()
    sims_bersih.setattr(sims, "time", jam)

    def _meledak(pn, force_refresh=False):
        jam.maju(31.0)
        raise OSError("connection reset")
    sims_bersih.setattr(sims, "get_part_info", _meledak)

    assert sims.status_jual("AZ9998887776") is None
    assert sims._status_jual_diam_sampai > jam.t


def test_status_jual_jawaban_sah_tak_terpengaruh_rem(sims_bersih):
    """Regresi: isi & arti status_jual tidak berubah oleh rem ini."""
    sims_bersih.setattr(sims, "get_part_info", lambda pn, force_refresh=False: {
        "raw": {"isSale": "10021002", "isPreOffShelves": 1}})
    s = sims.status_jual("AZ9998887776")
    assert s["dijual"] is False and s["pra_turun_rak"] is True
    assert s["sumber"] == "SIMS partInfo (isSale)"
    assert sims._status_jual_diam_sampai == 0.0


# ═══════════════════════════════════════════════════════════════════════
#  6. Klaim cakupan yang dilebihkan
# ═══════════════════════════════════════════════════════════════════════
def test_posisi_poros_tidak_klaim_pasti(monkeypatch):
    """QDQ = poros PENGGERAK; pada 6×4 gardan tengah & belakang melebur di modul
    yang sama, jadi 'PASTI ... BELAKANG' menjanjikan ketelitian yang tak ada."""
    monkeypatch.setattr(ai.epc_bom, "atlas_find", lambda rangka, kws, modules: {
        "found": True, "frame_number": "SJ398963", "order_no": "O1",
        "parts": [{"pn": "AZ9998887776", "nama": "Brake drum", "nama_cn": "制动鼓",
                   "qty": 2, "posisi": "belakang", "modul": "QDQ", "pengganti": []}],
        "incomplete": False, "terpotong": False})
    monkeypatch.setattr(ai.epc_bom, "atlas_find_in_tree",
                        lambda r, kws: {"found": False})
    monkeypatch.setattr(ai.epc_bom, "loading_list",
                        lambda r: {"found": False, "parts": []})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "brake drum"], []))
    monkeypatch.setattr(ai, "_auto_exploded_gambar",
                        lambda *a, **kw: ([], [], ""))

    r = ai._t_part_aus_dari_rangka({"rangka": "SJ398963", "query": "brake drum"}, ADMIN)

    assert "PASTI" not in r["catatan"]
    assert "tengah" in r["catatan"]                       # cakupan jujur disebut
    baris = r["parts_belakang"][0]
    assert "tidak dipisah di Atlas" in baris["posisi_poros"]
    assert "tengah" in r["peringatan_posisi"].lower()


def test_daftar_transmisi_assy_tidak_klaim_lengkap_pasti(monkeypatch):
    monkeypatch.setattr(ai.part_index, "ensure_index", lambda: None)
    monkeypatch.setattr(ai.part_index, "all_parts_min",
                        lambda: [("AZ9998887776", "Transmission assembly")])
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [
        {"part_number": "AZ9998887776", "part_name": "Transmission assembly",
         "stok": "1", "harga": "Rp 1", "file": "NX"}])
    monkeypatch.setattr(ai.repairkit, "_load", lambda: {})
    monkeypatch.setattr(ai.repairkit, "assy_pns_raw", lambda: [])
    monkeypatch.setattr(ai, "_is_gearbox_assy", lambda pn, nm: True)

    r = ai._t_daftar_transmisi_assy({}, ADMIN)

    assert "LENGKAP & PASTI" not in r["catatan"]
    assert "TERLEWAT" in r["catatan"]                     # keterbatasan disebut
    assert r["total_transmisi_assy"] == 1


# ═══════════════════════════════════════════════════════════════════════
#  7. Lookup stok PN Atlas ber-suffix varian (pemaaf, bukan exact)
# ═══════════════════════════════════════════════════════════════════════
def test_uraikan_assembly_pakai_lookup_pemaaf_suffix(monkeypatch):
    """PN Atlas 'AZ9998887776/2' vs indeks 'AZ9998887776' — dgn search_exact_pns
    komponen yang DISTOK tampil seolah tak ada di inventori."""
    dipakai = {}
    monkeypatch.setattr(ai.epc_bom, "assembly_components", lambda r, t, pn="": {
        "found": True, "frame_number": "SJ398963",
        "assembly": {"pn": "AZ9998887770", "nama": "V stay"},
        "components": [{"pn": "AZ9998887776/2", "nama": "Bush", "qty": 2}]})
    monkeypatch.setattr(ai.part_index, "rows_for_pns",
                        lambda pns: dipakai.update(pns=list(pns)) or {
                            "AZ9998887776/2": {"part_name": "Bush", "stok": "7",
                                               "harga": "Rp 1", "gudang": {}}})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))

    r = ai._uraikan_assembly_impl({"rangka": "SJ398963", "assembly": "v stay"}, ADMIN)

    assert dipakai["pns"] == ["AZ9998887776/2"]           # lewat jalur pemaaf
    assert r["komponen"][0]["stok_total"] == "7"


def test_kategori_unit_pakai_lookup_pemaaf_suffix(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "category_top", lambda r: {
        "found": True, "frame_number": "SJ398963",
        "kategori": [{"id": 1, "nama": "Rear axle", "nama_cn": "后桥",
                      "kode_kategori": "07", "part_list_id": 9, "code": "07"}]})
    monkeypatch.setattr(ai.epc_bom, "resolve_category", lambda r, t: [
        {"id": 1, "nama": "Rear axle", "nama_cn": "后桥", "kode_kategori": "07",
         "part_list_id": 9, "code": "07"}])
    monkeypatch.setattr(ai.epc_bom, "category_open", lambda r, i, pl, c: {
        "jumlah_sub": 0, "sub_kategori": [],
        "parts": [{"pn": "AZ9998887776/2", "nama": "Bush", "nama_cn": "衬套", "qty": 2}]})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "AZ9998887776/2": {"part_name": "Bush", "stok": "7", "harga": "Rp 1",
                           "gudang": {}}})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))

    r = ai._t_kategori_unit({"rangka": "SJ398963", "kategori": "gardan belakang"}, ADMIN)

    assert r["dibuka"][0]["parts"][0]["stok_total"] == "7"


# ═══════════════════════════════════════════════════════════════════════
#  8. 'daftar_balon_gambar' bukan "SEMUA balon"
# ═══════════════════════════════════════════════════════════════════════
def test_daftar_balon_cakupan_jujur():
    """_auto_exploded_gambar hanya mengambil figure PERTAMA & memotongnya di 40 —
    klaim 'SEMUA balon di gambar' membuat asisten memvonis 'balon N tak ada'
    untuk balon yang sebenarnya cuma di luar potongan."""
    db = [{"balon": i, "pn": f"AZ99988877{i:02d}", "nama": "x"} for i in range(40)]
    gambar = [{"nama_figure": "Rear axle", "jumlah_item": 96},
              {"nama_figure": "Hub", "jumlah_item": 12}]
    txt, cakupan = ai._balon_cakupan(db, gambar, "Rear axle")

    assert "SEMUA" not in txt
    assert "figure 'Rear axle' saja" in txt
    assert "40 dari 96" in txt
    assert "figure LAIN" in txt                    # kartu punya >1 figure
    assert "balon=N" in txt                        # jalan keluar disebut
    assert cakupan == {"ditampilkan": 40, "total_item_figure": 96,
                       "figure": "Rear axle"}


def test_daftar_balon_utuh_tak_mengaku_terpotong():
    db = [{"balon": 1, "pn": "AZ9998887776", "nama": "x"}]
    txt, cakupan = ai._balon_cakupan(db, [{"nama_figure": "Hub", "jumlah_item": 1}], "Hub")
    assert "(1 item)" in txt and "dipotong" not in txt
    assert "figure LAIN" not in txt                # cuma satu figure di kartu
    assert cakupan["ditampilkan"] == 1


# ═══════════════════════════════════════════════════════════════════════
#  9. Marketability EPC tak boleh muncul lagi sebagai kata bertafsir
# ═══════════════════════════════════════════════════════════════════════
def test_marketability_tak_pernah_jadi_kata_bertafsir():
    row = ai.epc_bom._atlas_item_row(
        {"code": "AZ9998887776", "name": "Plate", "amount": 1,
         "marketability": "b"}, "QDQ")
    teks = json.dumps(row, ensure_ascii=False).lower()
    for kata in ("stop", "discontinued", "dipasok", "dihentikan"):
        assert kata not in teks
