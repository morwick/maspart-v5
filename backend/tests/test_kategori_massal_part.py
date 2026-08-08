"""kategori_massal_part — klasifikasi kategori BANYAK PN dari sumber katalog.

Latar: log produksi (ai_chat_log) menunjukkan kelas pertanyaan "daftar PN ini
masuk kategori apa / mana yang barang MESIN / mana yang produk WEICHAI" dijawab
model dengan MENEBAK dari nama part — dan tebakannya terbukti salah (composite
bushing dijawab 'sasis', sheet katalognya 01 Kabin). Tool ini menjawab dari
SHEET katalog (01..12) + FILE katalognya. Tes di sini mengunci: klasifikasi
benar, PN lintas-kategori tidak diakui sebagai 'part mesin' polos, dan
gagal-cek/tak-ketemu dilaporkan JUJUR (bukan jadi 'bukan mesin').
"""
import pytest

from app.services import ai_assistant as ai
from app.services import catalog_bom

BIASA = {"username": "budi", "role": "user"}


def _mock(monkeypatch, cats: dict, rows: dict | None = None, tersedia: bool = True):
    """cats: {pn: [kode,...]} → pn_categories_map; rows: {PN: baris part_index}."""
    peta = {catalog_bom._norm(pn): {"nama": f"Nama {pn}", "kategori": list(k)}
            for pn, k in cats.items()}
    monkeypatch.setattr(ai.catalog_bom, "available", lambda: tersedia)
    monkeypatch.setattr(ai.catalog_bom, "pn_categories_map", lambda: peta)
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: dict(rows or {}))


def _ring(res):
    return res["ringkasan"]


# ── klasifikasi dasar ────────────────────────────────────────────────────────
def test_pisah_mesin_dan_bukan_mesin(monkeypatch):
    _mock(monkeypatch, {"P-MESIN": ["02"], "P-TRANSMISI": ["05"], "P-KABIN": ["01"]})
    r = ai._t_kategori_massal_part({"daftar_pn": "P-MESIN P-TRANSMISI P-KABIN"}, BIASA)
    assert r["found"] is True and r["jumlah"] == 3
    assert _ring(r)["mesin"] == ["P-MESIN"]
    assert sorted(_ring(r)["bukan_mesin"]) == ["P-KABIN", "P-TRANSMISI"]
    kat = {it["pn"]: it["kategori"] for it in r["part"]}
    assert kat["P-KABIN"] == [catalog_bom.kategori_nama("01")]


def test_aksesori_powertrain_tidak_digabung_ke_mesin(monkeypatch):
    """Kategori 03 dilaporkan TERPISAH — user memang membedakan keduanya."""
    _mock(monkeypatch, {"P-AKS": ["03"], "P-MESIN": ["02"]})
    r = ai._t_kategori_massal_part({"daftar_pn": ["P-AKS", "P-MESIN"]}, BIASA)
    assert _ring(r)["aksesori_mesin"] == ["P-AKS"]
    assert _ring(r)["mesin"] == ["P-MESIN"]
    assert "P-AKS" not in _ring(r)["mesin"]


def test_pn_lintas_kategori_tidak_jadi_part_mesin_polos(monkeypatch):
    """Baut yang dipakai di kabin DAN di dudukan mesin bukan 'barang mesin'
    tanpa syarat — ia masuk ember terpisah & SEMUA kategorinya disebut."""
    _mock(monkeypatch, {"ZQ-BAUT": ["01", "03", "09"]})
    r = ai._t_kategori_massal_part({"daftar_pn": "ZQ-BAUT"}, BIASA)
    it = r["part"][0]
    assert it["mesin"] == "sebagian"
    assert it["lintas_kategori"] is True
    assert len(it["kategori"]) == 3
    assert _ring(r)["sebagian_mesin_lintas_kategori"] == ["ZQ-BAUT"]
    assert _ring(r)["mesin"] == [] and _ring(r)["bukan_mesin"] == []


def test_lintas_kategori_tanpa_mesin_tetap_bukan_mesin(monkeypatch):
    _mock(monkeypatch, {"P-UMUM": ["01", "10"]})
    r = ai._t_kategori_massal_part({"daftar_pn": "P-UMUM"}, BIASA)
    assert r["part"][0]["mesin"] == "bukan"
    assert _ring(r)["bukan_mesin"] == ["P-UMUM"]


# ── sumber katalog: Weichai vs Sinotruk vs Shantui ───────────────────────────
def test_pn_bom_weichai_dihitung_part_mesin(monkeypatch):
    """PN mesin Weichai tak ada di katalog unit Sinotruk — file katalognya yang
    membuktikan ia part mesin."""
    _mock(monkeypatch, {}, rows={
        "612650040011": {"part_name": "Cylinder Head Cover Gasket", "file": "HOWO380 6X4",
                         "sheet": "HOWO 380 6X4",
                         "path": "Wechai\\Sinotruk\\HOWO380 6X4 _WP10.380E22.xlsx"}})
    r = ai._t_kategori_massal_part({"daftar_pn": "612650040011"}, BIASA)
    it = r["part"][0]
    assert it["mesin"] == "ya"
    assert it["sumber_katalog"] == "Katalog MESIN Weichai"
    assert _ring(r)["mesin"] == ["612650040011"]


def test_sumber_sinotruk_dilabeli_dan_kode_sheet_dipakai(monkeypatch):
    """Tanpa entri catalog_bom, kode kategori masih terbaca dari nama sheet."""
    _mock(monkeypatch, {}, rows={
        "WG-X": {"part_name": "Front plate spring", "file": "NX280TH 6X2",
                 "sheet": "10底盘 Chassis", "path": "Sinotruk\\NX280HP\\a.xlsx"}})
    r = ai._t_kategori_massal_part({"daftar_pn": "WG-X"}, BIASA)
    it = r["part"][0]
    assert it["kategori_kode"] == ["10"]
    assert it["sumber_katalog"] == "Katalog unit Sinotruk/HOWO"
    assert it["mesin"] == "bukan"


def test_shantui_ketemu_tapi_tanpa_kategori_dilaporkan_jujur(monkeypatch):
    """Katalog Shantui tak punya sheet kategori. Part-nya KETEMU — catatannya
    tak boleh bilang 'tidak ada di indeks'."""
    _mock(monkeypatch, {}, rows={
        "01010-50612": {"part_name": "Bolt", "file": "SD16E", "sheet": "SD16E",
                        "path": "Shantui\\SD16E _ CHSD16AEJR1054542.xlsx"}})
    r = ai._t_kategori_massal_part({"daftar_pn": "01010-50612"}, BIASA)
    it = r["part"][0]
    assert it["mesin"] == "tidak_diketahui"
    assert it["sumber_katalog"] == "Katalog alat berat Shantui"
    assert "TANPA sheet kategori" in it["catatan_pn"]
    assert "tidak ada di" not in it["catatan_pn"]


# ── kejujuran: tak ketemu ≠ bukan mesin, gagal cek ≠ nihil ───────────────────
def test_pn_tak_ketemu_masuk_tidak_diketahui_bukan_bukan_mesin(monkeypatch):
    _mock(monkeypatch, {"P-ADA": ["02"]})
    r = ai._t_kategori_massal_part({"daftar_pn": "P-ADA PN-KARANGAN-XYZ"}, BIASA)
    assert _ring(r)["tidak_diketahui"] == ["PN-KARANGAN-XYZ"]
    assert "PN-KARANGAN-XYZ" not in _ring(r)["bukan_mesin"]
    it = next(x for x in r["part"] if x["pn"] == "PN-KARANGAN-XYZ")
    assert it["mesin"] == "tidak_diketahui"
    assert "bukan berarti bukan part mesin" in it["catatan_pn"]


def test_kedua_sumber_mati_adalah_gagal_cek_bukan_nihil(monkeypatch):
    """catalog_bom belum ada DAN indeks part tumbang → jangan memvonis 120 PN
    'tidak diketahui' seolah katalog memang tak memuatnya."""
    _mock(monkeypatch, {}, rows={}, tersedia=False)

    def boom(pns):
        raise RuntimeError("indeks tumbang")
    monkeypatch.setattr(ai.part_index, "rows_for_pns", boom)
    r = ai._t_kategori_massal_part({"daftar_pn": "A-123 B-456"}, BIASA)
    assert "error" in r and r.get("_cek_tak_lengkap") is True
    assert "BUKAN bukti" in r["error"]


def test_hasil_kosong_dari_indeks_sehat_bukan_error(monkeypatch):
    """`rows` kosong itu wajar bila PN-nya memang tak ada — jangan disamakan
    dengan indeks tumbang."""
    _mock(monkeypatch, {}, rows={}, tersedia=False)
    r = ai._t_kategori_massal_part({"daftar_pn": "A-123"}, BIASA)
    assert "error" not in r
    assert _ring(r)["tidak_diketahui"] == ["A-123"]
    # peta kategori tak termuat → 'tidak diketahui' ditandai belum lengkap
    assert r["_cek_tak_lengkap"] is True and "peringatan" in r


def test_indeks_part_melempar_tidak_menjatuhkan_giliran(monkeypatch):
    _mock(monkeypatch, {"P-MESIN": ["02"]})

    def boom(pns):
        raise RuntimeError("indeks tumbang")
    monkeypatch.setattr(ai.part_index, "rows_for_pns", boom)
    r = ai._t_kategori_massal_part({"daftar_pn": "P-MESIN"}, BIASA)
    assert _ring(r)["mesin"] == ["P-MESIN"]     # catalog_bom masih hidup


# ── input & plafon ───────────────────────────────────────────────────────────
def test_daftar_kosong_ditolak(monkeypatch):
    _mock(monkeypatch, {})
    assert "error" in ai._t_kategori_massal_part({"daftar_pn": ""}, BIASA)


def test_suffix_varian_epc_tetap_dikenali(monkeypatch):
    """EPC memberi 'WG…+002' sedangkan katalog menyimpan PN dasarnya."""
    _mock(monkeypatch, {"WG9725520789": ["10"]})
    r = ai._t_kategori_massal_part({"daftar_pn": "WG9725520789+002"}, BIASA)
    assert r["part"][0]["kategori_kode"] == ["10"]


def test_plafon_dilaporkan_terbuka(monkeypatch):
    _mock(monkeypatch, {})
    banyak = [f"PN-{i:04d}" for i in range(ai._MAX_KATEGORI_MASSAL + 5)]
    r = ai._t_kategori_massal_part({"daftar_pn": banyak}, BIASA)
    assert r["jumlah"] == ai._MAX_KATEGORI_MASSAL
    assert len(r["pn_belum_diproses"]) == 5


def test_excel_opsional(monkeypatch):
    _mock(monkeypatch, {"P-MESIN": ["02"]})
    r = ai._t_kategori_massal_part({"daftar_pn": "P-MESIN"}, BIASA)
    assert "export_id" not in r
    r = ai._t_kategori_massal_part({"daftar_pn": "P-MESIN", "excel": True}, BIASA)
    assert r["export_id"] and r["filename"].endswith(".xlsx") and r["jumlah_baris"] == 1


# ── pendaftaran tool ─────────────────────────────────────────────────────────
def test_tool_terdaftar_di_spec_dan_dispatch():
    assert ai._DISPATCH["kategori_massal_part"] is ai._t_kategori_massal_part
    nama = [s["function"]["name"] for s in ai._tool_specs(BIASA)]
    assert "kategori_massal_part" in nama


def test_catatan_selalu_key_terakhir(monkeypatch):
    _mock(monkeypatch, {"P-MESIN": ["02"]})
    r = ai._t_kategori_massal_part({"daftar_pn": "P-MESIN"}, BIASA)
    assert list(r)[-1] == "catatan"


# ── peta kategori: SEMUA kategori, bukan hanya yang pertama ──────────────────
def test_pn_categories_map_kumpulkan_semua_kategori(monkeypatch):
    data = {"units": {
        "U1": {"kategori": {"01": {"parts": [{"pn": "ZQ-1", "nama": "Bolt"}]},
                            "09": {"parts": [{"pn": "ZQ-1", "nama": "Bolt"}]}}},
        "U2": {"kategori": {"05": {"parts": [{"pn": "ZQ-1", "nama": ""},
                                             {"pn": "X-2", "nama": "Gear"}]}}},
    }}
    monkeypatch.setattr(catalog_bom, "_load", lambda: data)
    monkeypatch.setitem(catalog_bom._CACHE, "mtime", "t-baru")
    monkeypatch.setitem(catalog_bom._CACHE, "data", data)
    m = catalog_bom.pn_categories_map()
    assert m[catalog_bom._norm("ZQ-1")]["kategori"] == ["01", "05", "09"]
    assert m[catalog_bom._norm("ZQ-1")]["nama"] == "Bolt"
    assert m[catalog_bom._norm("X-2")]["kategori"] == ["05"]
