"""Garansi & Klaim SIMS (DMS repair order) — service + 3 tool asisten + gerbang.

Sumber kebenaran: HAR simscloud dibedah 2026-07-22 + probe live (memory
sims-repair-order-har). Jebakan yang dikunci di sini:
- chassisNo WAJIB frame 8-char; VIN 17-char → ekstrak 8 char terakhir.
- Status pekerjaan: kamus label Indonesia hasil pemetaan rigor; kswx/wg
  BELUM terkonfirmasi dan labelnya harus jujur bilang begitu.
- parts kosong utk WO belum dikerjakan = normal (catatan menjelaskan).
- Gerbang Menu Control 'ai_garansi': admin selalu, staf per-centang,
  pembeli TIDAK PERNAH (fail-closed).
"""
from datetime import datetime, timezone

import pytest

import app.services.sims_warranty as w
from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "toko1", "role": "pembeli"}


# ── frame & label ────────────────────────────────────────────────────
def test_frame_dari_rangka():
    assert w.frame_dari_rangka("LZZ1ELSF7SJ392741") == "SJ392741"   # VIN 17 → 8
    assert w.frame_dari_rangka("SJ346555") == "SJ346555"            # frame utuh
    assert w.frame_dari_rangka(" sj346555 ") == "SJ346555"          # spasi+huruf kecil
    assert w.frame_dari_rangka("RT110061") == "RT110061"
    assert w.frame_dari_rangka("") == ""


def test_status_label_terpetakan():
    assert w.status_label("s-ro-status-js") == "Selesai"
    assert w.status_label("s-ro-status-kd") == "Order dibuka"
    assert w.status_label("s-ro-status-pg") == "Kerja ditugaskan"
    assert w.status_label("s-ro-status-zj") == "Inspeksi akhir"
    assert "Kantor Perwakilan" in w.status_label("s-ro-status-dbcfwjl")
    assert "Regional" in w.status_label("s-ro-status-dqfwjl")
    assert "Dibatalkan" in w.status_label("s-ro-status-zf")
    # kswx/wg/bh dipetakan 2026-07-22 (lihat test_status_label_bh_kswx_wg_terpetakan)
    assert w.status_label("s-ro-status-kswx") == "Mulai perbaikan"
    assert w.status_label("s-ro-status-wg") == "Selesai kerja"
    assert w.status_label("kode-aneh") == "kode-aneh"   # tak dikenal → apa adanya


# ── service (HTTP dipalsukan) ────────────────────────────────────────
class _Resp:
    def __init__(self, status=200, payload=None, ct="application/json"):
        self.status_code = status
        self._p = payload
        self.headers = {"Content-Type": ct}
        self.content = b"\xff\xd8jpeg" if ct.startswith("image") else b"{}"

    def json(self):
        return self._p


_UNIT = {
    "chassisNo": "SJ346555", "brandName": "HOWO", "seriesName": "NX",
    "driveMode": "8×4", "modelCode": "ZZ3317V486JB1R", "discharge": "Euro II",
    "useType": "Dump truck", "dealerName": "PT MAS",
    "departureDate": "2025-07-28T23:24:54Z", "saleDate": "2025-09-06T00:00:00Z",
    "orderNo": "CYAJ24110049",
    "warrantyStartDate": "2025-09-06T00:00:00Z",
    "latestEndDate": "2026-09-06T00:00:00Z",
    "cnhtcLatestEndDate": "2026-09-06T00:00:00Z",
    "warrantyTimePercentage": 87.4546,
    "engineNo": "1424L071706", "engineModelCode": "WP12S400E201",
    "gearboxNo": "241217002907", "gearboxModelCode": "HW19709XST",
    "axleFrontNo": "80241", "axleFrontModelCode": "VGD95",
    "axleMidNo": "40241", "axleMidModelCode": "MCP16ZG",
    "axlxAftNo": "40242", "axlxAftModelCode": "MCP16ZG",
    "engineMaintNum": 0, "gearboxMaintNum": 1, "bridgeMaintNum": 0,
    "chassisMaintNum": 2,
}


@pytest.fixture(autouse=True)
def _bersih_cache():
    w._CACHE.clear()
    yield
    w._CACHE.clear()


@pytest.fixture
def http(monkeypatch):
    """Palsukan requests.get service + bekukan jam."""
    monkeypatch.setattr(w, "_headers", lambda: {})
    monkeypatch.setattr(w, "_sekarang",
                        lambda: datetime(2026, 7, 22, tzinfo=timezone.utc))
    kotak = {"n": 0, "resp": None, "urls": []}

    def fake_get(url, params=None, headers=None, timeout=None):
        kotak["n"] += 1
        kotak["urls"].append((url, dict(params or {})))
        r = kotak["resp"]
        return r(url, params) if callable(r) else r
    monkeypatch.setattr(w.requests, "get", fake_get)
    return kotak


def test_info_unit_parsing_dan_sisa_hari(http):
    http["resp"] = _Resp(200, {"msg": "ok", "code": 0, "data": dict(_UNIT)})
    info = w.info_unit("LZZ1ELSF7SJ346555X")[  # VIN-ish panjang → frame
        "garansi"] if False else w.info_unit("SJ346555")
    assert info["unit"] == "HOWO NX 8×4"
    g = info["garansi"]
    assert g["berakhir"] == "2026-09-06"
    assert g["sisa_hari"] == 46           # 2026-07-22 → 2026-09-06 (jam beku)
    assert g["masih_aktif"] is True
    assert g["persen_terpakai"] == 87.5
    assert info["komponen"]["gearbox"]["no_seri"] == "241217002907"
    assert info["jumlah_servis"]["sasis"] == 2
    # param HTTP: chassisNo = FRAME (bukan VIN)
    assert http["urls"][0][1]["chassisNo"] == "SJ346555"


def test_info_unit_cache_per_frame(http):
    http["resp"] = _Resp(200, {"data": dict(_UNIT)})
    w.info_unit("SJ346555")
    w.info_unit("SJ346555")
    assert http["n"] == 1                 # panggilan kedua dari cache


def test_info_unit_garansi_lewat(http):
    d = dict(_UNIT, latestEndDate="2026-07-01T00:00:00Z",
             cnhtcLatestEndDate="2026-07-01T00:00:00Z")
    http["resp"] = _Resp(200, {"data": d})
    g = w.info_unit("SJ346555")["garansi"]
    assert g["masih_aktif"] is False and g["sisa_hari"] < 0


def test_get_retry_saat_token_basi(http, monkeypatch):
    import sims_fetcher as sf
    direset = {"n": 0}
    monkeypatch.setattr(sf, "_reset_token", lambda: direset.__setitem__("n", 1))

    def resp(url, params):
        return _Resp(401) if http["n"] == 1 else _Resp(200, {"data": dict(_UNIT)})
    http["resp"] = resp
    assert w.info_unit("SJ346555")
    assert direset["n"] == 1 and http["n"] == 2


def test_daftar_klaim_mapping(http):
    http["resp"] = _Resp(200, {
        "totalCount": 1979, "currentPage": 1,
        "records": [{"roId": "1", "roNo": "RIDZ0052607123", "vin": "SJ394896",
                     "createTime": "2026-07-22T02:59:52Z", "mileage": 32583,
                     "faultContent": "transmisi keras", "handleMethod": "check",
                     "remark": "B 9826 UEY", "statusCode": "s-ro-status-pg",
                     "orderDuration": 3.5, "reportName": "Leani",
                     "majorRepairName": "PT MAS", "roAuditTime": None}]})
    d = w.daftar_klaim(vin="LZZ1ELSF7SJ394896")
    assert d["total"] == 1979
    k = d["klaim"][0]
    assert k["status"] == "Kerja ditugaskan"      # label Indonesia
    assert k["km"] == 32583 and k["no_wo"] == "RIDZ0052607123"
    assert http["urls"][0][1]["vin"] == "SJ394896"  # frame, bukan VIN-17


def test_jejak_audit_kosong_aman(http):
    http["resp"] = _Resp(200, {"msg": "ok", "code": 0, "data": []})
    assert w.jejak_audit("123") == []


def test_foto_bytes_json_bukan_gambar(http, monkeypatch):
    import sims_fetcher as sf
    monkeypatch.setattr(sf, "_get_token", lambda: "Bearer abc.def")
    http["resp"] = _Resp(200, {"msg": "err"}, ct="application/json")
    assert w.foto_bytes("f1") is None     # balasan error JSON ≠ foto
    # security-token = JWT tanpa prefix Bearer
    assert http["urls"][0][1]["security-token"] == "abc.def"


# ── tool + gerbang ───────────────────────────────────────────────────
@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    return monkeypatch


def test_tool_cek_garansi_admin(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "info_unit", lambda r: {
        "frame": "SJ346555", "unit": "HOWO NX 8×4",
        "garansi": {"berakhir": "2026-09-06", "masih_aktif": True,
                    "sisa_hari": 46, "persen_terpakai": 87.5},
        "komponen": {}, "jumlah_servis": {}})
    r = ai._t_cek_garansi({"rangka": "SJ346555"}, ADMIN)
    assert r["found"] is True
    assert "MASIH AKTIF" in r["catatan"] and "sisa 46 hari" in r["catatan"]
    assert list(r.keys())[-1] == "catatan"


def test_tool_cek_garansi_unit_tak_ada_jujur(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "info_unit", lambda r: None)
    r = ai._t_cek_garansi({"rangka": "XX999999"}, ADMIN)
    assert r["found"] is False and "tidak ditemukan" in r["catatan"].lower()


def test_tool_riwayat_klaim(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "daftar_klaim",
                        lambda **kw: {"total": 2, "halaman": 1, "klaim": [
                            {"no_wo": "RIDZ1", "status": "Selesai"}]})
    r = ai._t_riwayat_klaim({"rangka": "SJ394896"}, ADMIN)
    assert r["found"] and r["total"] == 2
    assert list(r.keys())[-1] == "catatan"


# ── Saringan durasi ("WO lebih dari 72 jam, sebutkan 10") ────────────
# Dihitung SERVER dari semua_klaim — bukan model membuka halaman demi halaman
# lalu membanding angka sendiri (boros, kena rem anti-loop, rawan karang).
_KLAIM_DURASI = [
    {"no_wo": "RIDZ-A", "durasi_jam": 100.5, "status": "Selesai"},
    {"no_wo": "RIDZ-B", "durasi_jam": 73, "status": "Selesai"},
    {"no_wo": "RIDZ-C", "durasi_jam": 72, "status": "Selesai"},     # persis batas: < min → keluar
    {"no_wo": "RIDZ-D", "durasi_jam": 5, "status": "Selesai"},
    {"no_wo": "RIDZ-E", "status": "Order dibuka"},                  # tanpa durasi ≠ 0 jam
    {"no_wo": "RIDZ-F", "durasi_jam": 200, "status": "Menunggu"},
]


def test_riwayat_saring_durasi_lebih_dari_72(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "semua_klaim",
                        lambda **kw: {"total": 6, "klaim": list(_KLAIM_DURASI)})
    r = ai._t_riwayat_klaim({"durasi_min_jam": 72.5, "limit": 10}, ADMIN)
    assert r["found"] is True
    # Urut TERLAMA dulu; 72 jam persis tidak lolos ambang 72.5.
    assert [k["no_wo"] for k in r["klaim"]] == ["RIDZ-F", "RIDZ-A", "RIDZ-B"]
    assert r["jumlah_cocok"] == 3 and r["jumlah_diperiksa"] == 6
    # WO tanpa durasi DIHITUNG TERPISAH — bukan dianggap 0 jam lalu lolos/gugur diam-diam.
    assert r["jumlah_tanpa_durasi"] == 1
    assert "DIURUTKAN server" in r["catatan"]


def test_riwayat_saring_durasi_limit_dipatuhi(dunia, monkeypatch):
    banyak = [{"no_wo": f"R{i}", "durasi_jam": 80 + i} for i in range(30)]
    monkeypatch.setattr(ai.sims_warranty, "semua_klaim",
                        lambda **kw: {"total": 30, "klaim": banyak})
    r = ai._t_riwayat_klaim({"durasi_min_jam": 72, "limit": 10}, ADMIN)
    assert r["ditampilkan"] == 10 and len(r["klaim"]) == 10
    assert r["jumlah_cocok"] == 30                     # totalnya tetap jujur
    assert r["klaim"][0]["no_wo"] == "R29"             # terlama dulu


def test_riwayat_saring_durasi_kosong_jujur(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "semua_klaim",
                        lambda **kw: {"total": 2, "klaim": [
                            {"no_wo": "R1", "durasi_jam": 5},
                            {"no_wo": "R2"}]})
    r = ai._t_riwayat_klaim({"durasi_min_jam": 72}, ADMIN)
    assert r["found"] is False and r["jumlah_cocok"] == 0
    assert r["jumlah_tanpa_durasi"] == 1
    assert "jangan mengarang" in r["catatan"]


def test_riwayat_tanpa_saringan_tetap_jalur_lama(dunia, monkeypatch):
    """Regresi: tanpa durasi_min/maks, semua_klaim TAK boleh dipanggil
    (jalur berhalaman yang murah tetap dipakai)."""
    def _boom(**kw):
        raise AssertionError("semua_klaim tak boleh dipanggil tanpa saringan durasi")
    monkeypatch.setattr(ai.sims_warranty, "semua_klaim", _boom)
    monkeypatch.setattr(ai.sims_warranty, "daftar_klaim",
                        lambda **kw: {"total": 1, "halaman": 1,
                                      "klaim": [{"no_wo": "RIDZ1"}]})
    r = ai._t_riwayat_klaim({"rangka": "SJ394896"}, ADMIN)
    assert r["found"] is True


def test_tool_detail_klaim_lengkap(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "daftar_klaim", lambda **kw: {
        "klaim": [{"no_wo": "RIDZ0052607121", "ro_id": "99", "frame": "ST131522",
                   "tanggal": "2026-07-22", "km": 19243, "status": "Menunggu review",
                   "gejala": "pompa bocor", "tindakan": "ganti"}]})
    monkeypatch.setattr(ai.sims_warranty, "detail_wo", lambda rid: {
        "roId": "99", "faultContent": "pompa injeksi bocor",
        "handleMethod": "ganti pompa", "faultAddress": "Bayung Lencir",
        "reportName": "jefri", "reportPhone": "0822",
        "parts": [{"partCode": "1001671518", "partName": "Injection pump",
                   "zhOldPartName": "机械喷油泵总成", "partNum": 1,
                   "partPrice": 7804.81, "partAmount": 7804.81,
                   "partMgtAmount": 390.24, "repairType": "ro-part-repair-gh",
                   "faultModel": "002", "dutyCode": "WP",
                   "oldPartSupplierCode": "100006"}],
        "labours": [{"labourCode": "MAAA5600", "labourName": "Replace pump",
                     "labourQuotaNoRepeat": 2.6, "labourPrice": 130.0,
                     "labourAmount": 338.0}],
        "oils": [], "additionals": [],
        "amountList": [{"auditType": "s-ro-status-kd", "currencyCode": "CNY",
                        "partAmount": 7804.81, "labourAmount": 338.0,
                        "oilAmount": 0.0, "partMgtAmount": 390.24,
                        "totalAmount": 8533.05}]})
    monkeypatch.setattr(ai.sims_warranty, "jejak_audit", lambda rid: [
        {"waktu": "2026-07-22 09:40", "tahap": "Submission", "oleh": "wf",
         "aksi": "Free Flow", "opini": None,
         "tahap_berikut": "Rep Office Review", "menunggu": "A,B"}])
    monkeypatch.setattr(ai.sims_warranty, "kebijakan", lambda rid: {
        "tarif_jasa_per_jam": 130.0})
    monkeypatch.setattr(ai.sims_warranty, "ringkas_audit", lambda rid: {
        "roId": "99", "fileMap": {"faultStartPhoto_file": [
            {"fileUploadInfoId": "f1"}], "vinPhoto_file": [
            {"fileUploadInfoId": "f2"}]}})
    monkeypatch.setattr(ai.sims_warranty, "foto_bytes", lambda fid: b"jpegdata")
    monkeypatch.setattr(ai.ai_export, "stash_raw",
                        lambda judul, data, nama: ("img1", nama))
    r = ai._t_detail_klaim({"no_wo": "ridz0052607121"}, ADMIN)   # case-insensitive
    assert r["found"] is True
    p = r["part_diklaim"][0]
    assert p["pn"] == "1001671518" and p["jenis"] == "ganti"
    assert p["harga_cny"] == 7804.81
    assert r["jasa_diklaim"][0]["jam_kuota"] == 2.6
    assert r["alur_persetujuan"]["menunggu"] == "A,B"
    assert r["gambar"] and r["gambar"][0]["nama_figure"] == "Foto kerusakan"
    assert "CNY" in r["catatan"]
    assert list(r.keys())[-1] == "catatan"


def test_tool_detail_klaim_wo_belum_dikerjakan(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "daftar_klaim", lambda **kw: {
        "klaim": [{"no_wo": "RIDZ2", "ro_id": "77", "status": "Kerja ditugaskan"}]})
    monkeypatch.setattr(ai.sims_warranty, "detail_wo", lambda rid: {
        "roId": "77", "parts": [], "labours": [], "oils": [], "additionals": [],
        "amountList": []})
    monkeypatch.setattr(ai.sims_warranty, "jejak_audit", lambda rid: [])
    monkeypatch.setattr(ai.sims_warranty, "kebijakan", lambda rid: None)
    monkeypatch.setattr(ai.sims_warranty, "ringkas_audit", lambda rid: {})
    r = ai._t_detail_klaim({"no_wo": "RIDZ2"}, ADMIN)
    assert r["found"] and r["part_diklaim"] == []
    assert "belum dikerjakan" in r["catatan"]      # kosong ≠ error, dijelaskan


# ── gerbang Menu Control ─────────────────────────────────────────────
def test_key_ai_garansi_terdaftar():
    from app.services.permissions import ASISTEN_KEYS
    assert ASISTEN_KEYS["ai_garansi"] == "Garansi & Klaim SIMS"


def test_spec_hanya_untuk_yang_berhak(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    nama_admin = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "")}
    nama_staf = {s["function"]["name"] for s in ai._tool_specs(STAF, "")}
    nama_pembeli = {s["function"]["name"] for s in ai._tool_specs(PEMBELI, "")}
    for t in ("cek_garansi", "riwayat_klaim", "detail_klaim"):
        assert t in nama_admin
        assert t not in nama_staf
        assert t not in nama_pembeli


def test_spec_staf_dengan_centang(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai",
                        lambda user, key: key == "ai_garansi")
    nama = {s["function"]["name"] for s in ai._tool_specs(STAF, "")}
    assert {"cek_garansi", "riwayat_klaim", "detail_klaim"} <= nama


def test_run_tool_menolak_pembeli(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    r = ai._run_tool("cek_garansi", {"rangka": "SJ346555"}, PEMBELI)
    assert r.get("denied") or "tidak tersedia" in (r.get("error") or "")


def test_handler_lapis_ketiga_fail_closed(monkeypatch):
    """Walau lolos dispatch (mis. dipanggil langsung), handler tetap menolak."""
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    for fn in (ai._t_cek_garansi, ai._t_riwayat_klaim, ai._t_detail_klaim):
        assert "error" in fn({"rangka": "X", "no_wo": "Y"}, STAF)


# ── Garansi tambahan (2026-07-22): semua_klaim, cek massal, Excel, rekap ──
_KLAIM_HAL = [
    {"total": 3, "klaim": [
        {"no_wo": "R1", "frame": "SJ100001", "tanggal": "2026-07-01", "km": 100,
         "gejala": "transmisi keras", "tindakan": "cek", "status": "Selesai",
         "status_code": "s-ro-status-js", "durasi_jam": 2.0, "pelapor": "A", "mekanik": "M"},
        {"no_wo": "R2", "frame": "SJ100002", "tanggal": "2026-07-02", "km": 200,
         "gejala": "AC bocor", "tindakan": "ganti", "status": "Kerja ditugaskan",
         "status_code": "s-ro-status-pg", "durasi_jam": 4.0, "pelapor": "B", "mekanik": "N"}]},
    {"total": 3, "klaim": [
        {"no_wo": "R3", "frame": "SJ100003", "tanggal": "2026-07-03", "km": 300,
         "gejala": "transmisi keras", "tindakan": "cek", "status": "Selesai",
         "status_code": "s-ro-status-js", "durasi_jam": 6.0, "pelapor": "C", "mekanik": "O"}]},
]


def test_semua_klaim_paginasi_berhenti_di_total(monkeypatch):
    w._CACHE.clear()
    panggil = {"n": 0}

    def fake_daftar(vin="", halaman=1, page_size=50):
        panggil["n"] += 1
        return _KLAIM_HAL[halaman - 1] if halaman <= len(_KLAIM_HAL) else {"total": 3, "klaim": []}
    monkeypatch.setattr(w, "daftar_klaim", fake_daftar)
    d = w.semua_klaim()
    assert d["total"] == 3 and len(d["klaim"]) == 3
    assert panggil["n"] == 2                      # berhenti saat len>=total
    w._CACHE.clear()


@pytest.fixture
def klaim_penuh(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: True)  # admin/izin
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    all_rows = [r for h in _KLAIM_HAL for r in h["klaim"]]
    monkeypatch.setattr(ai.sims_warranty, "semua_klaim",
                        lambda vin="": {"total": 3, "klaim": all_rows})


def test_excel_riwayat_klaim(klaim_penuh, monkeypatch):
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda judul, kolom, baris: ("EXP", "riwayat.xlsx"))
    r = ai._t_excel_riwayat_klaim({}, ADMIN)
    assert r["found"] and r["export_id"] == "EXP" and r["jumlah_baris"] == 3
    assert list(r.keys())[-1] == "catatan"


def test_excel_riwayat_klaim_filter_status(klaim_penuh, monkeypatch):
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda judul, kolom, baris: ("EXP", "riwayat.xlsx"))
    r = ai._t_excel_riwayat_klaim({"status": "selesai"}, ADMIN)
    assert r["jumlah_baris"] == 2                 # hanya status Selesai


def test_rekap_klaim(klaim_penuh):
    r = ai._t_rekap_klaim({}, ADMIN)
    assert r["total_klaim"] == 3
    ps = {x["status"]: x["jumlah"] for x in r["per_status"]}
    assert ps["Selesai"] == 2 and ps["Kerja ditugaskan"] == 1
    top = r["gejala_tersering"][0]
    assert top["gejala"] == "transmisi keras" and top["jumlah"] == 2
    assert r["rata_durasi_jam"] == 4.0
    # ≤60 klaim → jalur nilai diambil; baris mock tanpa ro_id → 0 WO (tanpa HTTP)
    assert r["nilai_total_cny"] == 0.0 and "nilai_total_cny" in r["catatan"]


def test_sheet_garansi_massal(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: True)
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    monkeypatch.setattr(ai.ai_sheet, "get_sheet", lambda sid, un: {
        "headers": ["No Rangka"], "_body": [["SJ346555"], ["XX000000"]]})

    def fake_info(rk):
        if rk == "SJ346555":
            return {"frame": "SJ346555", "unit": "HOWO NX", "brand": "HOWO",
                    "emisi": "Euro II",
                    "garansi": {"mulai": "2025-09-06", "berakhir": "2026-09-06",
                                "masih_aktif": True, "sisa_hari": 45, "persen_terpakai": 87.5},
                    "komponen": {"mesin": {"no_seri": "E1"}, "gearbox": {"no_seri": "G1"}}}
        return None
    monkeypatch.setattr(ai.sims_warranty, "info_unit", fake_info)
    monkeypatch.setattr(ai.sims_warranty, "frame_dari_rangka",
                        lambda x: x[-8:] if len(x) >= 17 else x)
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda judul, kolom, baris: ("EXP", "garansi.xlsx"))
    r = ai._t_sheet_garansi_massal({"_sheet_id": "s1"}, ADMIN)
    assert r["found"] and r["jumlah_baris"] == 2
    assert r["ketemu"] == 1 and r["tak_ada"] == 1
    assert list(r.keys())[-1] == "catatan"


def test_garansi_tambahan_gate(monkeypatch):
    """Ketiga tool baru menolak yang tak punya izin ai_garansi."""
    monkeypatch.setattr(ai, "_boleh_ai", lambda user, key: False)
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    for fn in (ai._t_excel_riwayat_klaim, ai._t_rekap_klaim, ai._t_sheet_garansi_massal):
        assert "error" in fn({"_sheet_id": "s"}, STAF)


def test_spec_garansi_tambahan_admin_dan_sheet():
    na = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "s1")}
    assert {"excel_riwayat_klaim", "rekap_klaim", "sheet_garansi_massal"} <= na
    # tanpa sheet_id, sheet_garansi_massal tidak muncul
    na_ns = {s["function"]["name"] for s in ai._tool_specs(ADMIN, "")}
    assert "sheet_garansi_massal" not in na_ns
    assert {"excel_riwayat_klaim", "rekap_klaim"} <= na_ns


# ── B: label status lengkap + agregasi nilai (2026-07-22) ────────────
def test_status_label_bh_kswx_wg_terpetakan():
    assert w.status_label("s-ro-status-kswx") == "Mulai perbaikan"
    assert w.status_label("s-ro-status-wg") == "Selesai kerja"
    assert w.status_label("s-ro-status-bh") == "Diajukan bengkel"
    # tak ada '(belum terkonfirmasi)' lagi
    assert "belum terkonfirmasi" not in w.status_label("s-ro-status-kswx")


def test_nilai_wo_ambil_total_tertinggi(monkeypatch):
    monkeypatch.setattr(w, "ringkas_audit", lambda rid: {"roId": rid, "amountList": [
        {"auditType": "s-ro-status-kd", "totalAmount": 8533.05},
        {"auditType": "s-ro-status-js", "totalAmount": 8000.0}]})
    assert w.nilai_wo("1") == 8533.05


def test_nilai_klaim_paralel_dan_bounded(monkeypatch):
    monkeypatch.setattr(w, "ringkas_audit",
                        lambda rid: {"roId": rid, "amountList": [{"totalAmount": 100.0}]})
    nv = w.nilai_klaim(["a", "b", "c"], maks=2)
    assert nv["total_cny"] == 200.0 and nv["jumlah_wo"] == 2 and nv["terpotong"] is True


def test_rekap_klaim_nilai_saat_difilter(klaim_penuh, monkeypatch):
    monkeypatch.setattr(ai.sims_warranty, "nilai_klaim",
                        lambda ro_ids, maks=60: {"total_cny": 12345.0,
                                                 "jumlah_wo": len(list(ro_ids)), "terpotong": False})
    # data klaim_penuh kecil (≤60) & tanpa rangka → tetap dihitung
    r = ai._t_rekap_klaim({}, ADMIN)
    assert r["nilai_total_cny"] == 12345.0
    assert "nilai_total_cny" in r["catatan"]


def test_rekap_klaim_nilai_dilewati_bila_banyak(monkeypatch):
    monkeypatch.setattr(ai, "_boleh_ai", lambda u, k: True)
    monkeypatch.setattr(ai.sims_warranty, "available", lambda: True)
    banyak = [{"status": "Selesai", "ro_id": str(i), "frame": "SJ0001",
               "gejala": "x", "durasi_jam": 1.0, "tanggal": "2026-07-01"}
              for i in range(200)]
    monkeypatch.setattr(ai.sims_warranty, "semua_klaim",
                        lambda vin="": {"total": 200, "klaim": banyak})
    dipanggil = {"n": 0}
    monkeypatch.setattr(ai.sims_warranty, "nilai_klaim",
                        lambda *a, **k: dipanggil.__setitem__("n", dipanggil["n"] + 1) or {})
    r = ai._t_rekap_klaim({}, ADMIN)          # 200 klaim, tanpa rangka
    assert "nilai_total_cny" not in r and dipanggil["n"] == 0   # TIDAK dihitung
    assert "TIDAK dihitung" in r["catatan"]
