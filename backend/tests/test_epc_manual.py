"""Service manual EPC per nomor rangka (epc_manual) + dua tool asisten.

Yang dikunci di sini adalah jebakan-jebakan yang DIUKUR saat integrasi
(2026-08-14), bukan sekadar jalur bahagia:

 1. Endpoint berkas EPC membalas ``Content-Type: application/json`` untuk PDF
    yang SAH — tipe yang diklaim ikut berubah mengikuti header Accept kita.
    Keputusan 'ini PDF atau bukan' WAJIB dari isi ('%PDF'), bukan dari header.
 2. 'gagal cek' ≠ 'tidak ada manual'. EPC mati / token kedaluwarsa harus
    dilaporkan sebagai BELUM PASTI, bukan sebagai unit tanpa manual.
 3. maintenance/select dengan brand KOSONG membalas KATALOG GLOBAL lintas model
    (QDQ 24 dokumen) — filter brand wajib terisi supaya hasilnya per-unit.
 4. Satu berkas fisik menggantung di beberapa id manual → harus dedup.
 5. Nama field 'axlxAftModelCode' salah eja DI SISI EPC; kalau "dirapikan" jadi
    axleAft, gardan belakang hilang diam-diam tanpa error.
"""
import pytest

from app.services import epc, epc_bom, epc_manual


# ── perkakas tiruan ────────────────────────────────────────────────────────
_CFG = {
    "chassisNo": "PB087964", "vin": "LZZ8CUWD8PB087964",
    "modelCode": "ZZ4356V395ME1R", "brandId": "27", "brandName": "汕德卡",
    "subSeriesId": "68", "subSeriesName": "C7H", "driveMode": "6×6",
    "engineModelCode": "MC13.48-50", "gearboxModelCode": "ZF16S2531",
    "axleFrontModelCode": "MVP09", "axleMidModelCode": "MCP16",
    "axleMidSecModelCode": None, "axlxAftModelCode": "MCP16-belakang",
}


class _Res:
    def __init__(self, payload, status=200, ctype="application/json", body=b""):
        self._payload = payload
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self._body = body

    def json(self):
        return self._payload

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def close(self):
        pass


def _ok(data):
    return _Res({"success": True, "code": "200003", "data": data})


@pytest.fixture(autouse=True)
def _bersih(monkeypatch):
    """Token & pemutus arus selalu 'sehat'; cache daftar selalu kosong."""
    epc_bom.circuit_reset()
    epc_manual._cache_daftar.clear()
    monkeypatch.setattr(epc_bom, "_token", lambda: "Bearer tokentes")
    monkeypatch.setattr(epc, "get_config", lambda r: dict(_CFG))
    yield
    epc_bom.circuit_reset()
    epc_manual._cache_daftar.clear()


def _pasang(monkeypatch, handler):
    """handler(method, path, params, json_body) → _Res."""
    def _req(method, url, headers=None, params=None, json=None, **kw):
        return handler(method, url.split("/api/rest", 1)[-1], params or {}, json or {})
    monkeypatch.setattr(epc_manual.requests, "request", _req)
    return _req


def _handler_standar(method, path, params, body):
    """EPC 'normal': 1 dok kelistrikan + 1 transmisi + 2 gardan, berkas per id."""
    if path == "/maintenance/select":
        if body.get("position") == "DQXT":
            return _ok([{"id": 624, "title": None, "modelClass": None}])
        return _ok([])
    if path == "/modellink/getFdjBsxView":
        if body.get("position") == "BSX":
            return _ok([{"id": 1206, "title": None, "modelClass": "ZF 16S"}])
        return _ok([])
    if path == "/modellink/getQiaoView":
        if body.get("position") == "QDQ":
            return _ok([{"id": 1938, "title": "MCP16 Rear drive axle"},
                        {"id": 1939, "title": "MCP16 Rear drive axle (salinan)"}])
        return _ok([])
    if path == "/maintenance/queryMaintenanceManualFile":
        mid = int(params["maintenanceManualId"])
        berkas = {
            624: {"fileCode": "kode-listrik", "fileName": "skema.pdf",
                  "fileTitle": "SITRAK C7H Electrical Diagram", "fileDescription": "英文版"},
            1206: {"fileCode": "kode-zf", "fileName": "zf.pdf",
                   "fileTitle": "ZF_Ecosplit4_16speed", "fileDescription": None},
            # id 1938 & 1939 menunjuk BERKAS FISIK yang SAMA → wajib dedup.
            1938: {"fileCode": "kode-gardan", "fileName": "mcp16.pdf",
                   "fileTitle": "MCP16 Rear drive axle", "fileDescription": None},
            1939: {"fileCode": "kode-gardan", "fileName": "mcp16.pdf",
                   "fileTitle": "MCP16 Rear drive axle", "fileDescription": None},
        }[mid]
        return _ok([berkas])
    raise AssertionError(f"path tak terduga: {path}")


# ── daftar() ───────────────────────────────────────────────────────────────
def test_daftar_mengumpulkan_tiga_kanal_dan_dedup(monkeypatch):
    _pasang(monkeypatch, _handler_standar)
    d = epc_manual.daftar("LZZ8CUWD8PB087964")     # VIN penuh → frame otomatis
    assert d["found"] and d["rangka"] == "PB087964"
    judul = [x["judul"] for x in d["dokumen"]]
    assert "ZF_Ecosplit4_16speed" in judul
    assert "SITRAK C7H Electrical Diagram" in judul
    # dua id manual → satu berkas fisik: muncul SEKALI saja
    assert judul.count("MCP16 Rear drive axle") == 1
    assert d["jumlah"] == 3
    assert [x["nomor"] for x in d["dokumen"]] == [1, 2, 3]
    assert {x["bagian"] for x in d["dokumen"]} == {"Kelistrikan", "Transmisi",
                                                   "Gardan penggerak"}


def test_select_selalu_kirim_filter_brand(monkeypatch):
    """⛔ brand/subSeries KOSONG = katalog global lintas model (bukan unit ini)."""
    terlihat = []

    def _h(method, path, params, body):
        if path == "/maintenance/select":
            terlihat.append(body)
        return _handler_standar(method, path, params, body)

    _pasang(monkeypatch, _h)
    epc_manual.daftar("PB087964")
    assert terlihat, "maintenance/select tak pernah dipanggil"
    for b in terlihat:
        assert b["brandId"] == "27" and b["subSeriesId"] == "68", (
            "filter brand/subSeries kosong → EPC membalas katalog GLOBAL")


def test_qiao_pakai_ejaan_axlx_apa_adanya(monkeypatch):
    """Nama field salah eja di sisi EPC. Kalau 'diperbaiki', server mengabaikannya
    dan gardan belakang lenyap TANPA error."""
    kirim = []

    def _h(method, path, params, body):
        if path == "/modellink/getQiaoView":
            kirim.append(body)
        return _handler_standar(method, path, params, body)

    _pasang(monkeypatch, _h)
    epc_manual.daftar("PB087964")
    assert kirim
    for b in kirim:
        assert "axlxAftModelCode" in b, "ejaan API EPC tak boleh 'dirapikan'"
        assert b["axlxAftModelCode"] == "MCP16-belakang"


def test_epc_mati_bukan_berarti_tak_punya_manual(monkeypatch):
    def _mati(*a, **k):
        raise OSError("read timed out")
    monkeypatch.setattr(epc_manual.requests, "request", _mati)
    d = epc_manual.daftar("PB087964")
    assert d["found"] is False
    assert d["_err"] == "network"
    assert not d.get("kosong"), "EPC mati TIDAK boleh dilaporkan sebagai 'kosong'"


def test_epc_menjawab_kosong_memang_kosong(monkeypatch):
    _pasang(monkeypatch, lambda m, p, q, b: _ok([]))
    d = epc_manual.daftar("PB087964")
    assert d["found"] is False and d.get("kosong") is True
    assert d.get("_err") is None


def test_hasil_sebagian_tidak_di_cache(monkeypatch):
    """Sebagian kanal gagal → tandai 'sebagian' DAN jangan diawetkan, supaya
    percobaan berikutnya bisa melengkapi."""
    gagal = {"aktif": True}

    def _h(method, path, params, body):
        if path == "/modellink/getFdjBsxView" and gagal["aktif"]:
            raise OSError("blip")
        return _handler_standar(method, path, params, body)

    _pasang(monkeypatch, _h)
    d1 = epc_manual.daftar("PB087964")
    assert d1.get("sebagian") is True
    assert "PB087964" not in epc_manual._cache_daftar

    gagal["aktif"] = False
    d2 = epc_manual.daftar("PB087964")
    assert not d2.get("sebagian")
    assert d2["jumlah"] == 3
    assert "PB087964" in epc_manual._cache_daftar


def test_token_kedaluwarsa_memicu_refresh_lalu_berhasil(monkeypatch):
    keadaan = {"basi": True, "refresh": 0}

    def _h(method, path, params, body):
        if keadaan["basi"]:
            return _Res({"success": False, "code": "110003",
                         "message": "Login expired!"})
        return _handler_standar(method, path, params, body)

    def _refresh():
        keadaan["refresh"] += 1
        keadaan["basi"] = False
        return "tokenbaru"

    _pasang(monkeypatch, _h)
    monkeypatch.setattr(epc_bom, "refresh_token", _refresh)
    d = epc_manual.daftar("PB087964")
    assert keadaan["refresh"] >= 1, "token basi harus memicu refresh otomatis"
    assert d["found"] and d["jumlah"] == 3


# ── unduh() ────────────────────────────────────────────────────────────────
_PDF = b"%PDF-1.6\r\n" + b"x" * 5000


def test_unduh_percaya_isi_bukan_content_type(monkeypatch):
    """⛔ INTI: EPC menyebut PDF-nya 'application/json'. Menilai dari header =
    menolak berkas yang sah (bug nyata saat integrasi)."""
    monkeypatch.setattr(epc_manual.requests, "get",
                        lambda *a, **k: _Res(None, ctype="application/json", body=_PDF))
    data, alasan = epc_manual.unduh("kode-zf")
    assert data == _PDF and alasan == ""


def test_unduh_menolak_amplop_error_json(monkeypatch):
    monkeypatch.setattr(epc_manual.requests, "get",
                        lambda *a, **k: _Res(None, ctype="application/json",
                                             body=b'{"success":false,"code":"110003"}'))
    monkeypatch.setattr(epc_bom, "refresh_token", lambda: "")
    data, alasan = epc_manual.unduh("kode-zf")
    assert data is None and alasan


def test_unduh_hormati_plafon_ukuran(monkeypatch):
    monkeypatch.setattr(epc_manual, "_MAKS_UNDUH", 1024)
    besar = b"%PDF-1.6" + b"y" * 20_000
    monkeypatch.setattr(epc_manual.requests, "get",
                        lambda *a, **k: _Res(None, ctype="application/pdf", body=besar))
    data, alasan = epc_manual.unduh("kode-besar")
    assert data is None and "MB" in alasan


# ── dua tool asisten (kontrak 'daftar dulu, berkas menyusul') ──────────────
_USER = {"username": "tes", "role": "admin", "is_admin": True}


def test_tool_terdaftar():
    from app.services import ai_assistant as ai
    assert ai._DISPATCH["manual_unit"] is ai._t_manual_unit
    assert ai._DISPATCH["manual_unit_file"] is ai._t_manual_unit_file
    nama = [s["function"]["name"] for s in ai._tool_specs(_USER)]
    assert "manual_unit" in nama and "manual_unit_file" in nama


def test_tool_daftar_tak_bocorkan_file_code(monkeypatch):
    """file_code cuma dipakai server saat mengirim berkas — mengirimnya ke model
    hanya membakar token dan mengundang model mengarang link."""
    from app.services import ai_assistant as ai
    _pasang(monkeypatch, _handler_standar)
    out = ai._t_manual_unit({"rangka": "PB087964"}, _USER)
    assert out["found"] and out["jumlah"] == 3
    assert all("file_code" not in d for d in out["dokumen"])
    # catatan WAJIB mengarahkan ke langkah kedua, jika tidak model akan berhenti
    # di daftar dan user tak pernah ditawari berkasnya.
    assert "manual_unit_file" in out["catatan"]


def test_tool_kirim_berkas_pilih_nomor_dan_judul(monkeypatch):
    from app.services import ai_assistant as ai
    _pasang(monkeypatch, _handler_standar)
    monkeypatch.setattr(epc_manual.requests, "get",
                        lambda *a, **k: _Res(None, ctype="application/json", body=_PDF))
    lewat_nomor = ai._t_manual_unit_file({"rangka": "PB087964", "nomor": 2}, _USER)
    assert lewat_nomor["found"] and lewat_nomor["pdf_skema"][0]["export_id"]

    lewat_judul = ai._t_manual_unit_file({"rangka": "PB087964", "judul": "gardan"}, _USER)
    assert lewat_judul["found"]
    assert lewat_judul["bagian"] == "Gardan penggerak"


def test_tool_pilihan_salah_menawarkan_daftar(monkeypatch):
    from app.services import ai_assistant as ai
    _pasang(monkeypatch, _handler_standar)
    out = ai._t_manual_unit_file({"rangka": "PB087964", "judul": "karburator"}, _USER)
    assert out["found"] is False
    assert len(out["pilihan"]) == 3, "user harus ditawari daftar, bukan ditolak buntu"


def test_tool_gagal_unduh_bukan_dokumen_tak_ada(monkeypatch):
    """Dokumen TERDAFTAR tapi unduhan gagal ≠ 'unit tak punya manual itu'."""
    from app.services import ai_assistant as ai
    _pasang(monkeypatch, _handler_standar)
    monkeypatch.setattr(epc_manual, "unduh", lambda kode: (None, "EPC menolak."))
    out = ai._t_manual_unit_file({"rangka": "PB087964", "nomor": 1}, _USER)
    assert out["found"] is False
    assert "TERDAFTAR" in out["catatan"]
