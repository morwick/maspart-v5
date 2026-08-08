"""part_aus_katalog — daftar part AUS satu model dari KOLOM KETERANGAN katalog
resmi pabrik (26.181 baris terisi, tak pernah dipakai sebelumnya).

Sebelum ini satu-satunya jalur "part aus" adalah part_aus_dari_rangka yang
MENUNTUT user menyebut nama partnya lalu menjaring pohon EPC dengan kata kunci —
tak bisa menjawab "part apa saja yang aus di unit ini".

Yang paling mudah salah & karena itu dikunci:
  • '售后常用件' = SERING DIPAKAI after-sales (fast moving), BUKAN part aus;
    '普通件' = part biasa dan harus DIBUANG. Meratakan semuanya jadi "aus" akan
    menyebut ratusan part biasa sebagai komponen aus.
  • satu model punya BEBERAPA berkas katalog varian → PN ganda; tanpa dedup
    jumlahnya menggelembung berkali lipat.
"""
import pytest

from app.services import part_index
from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}

BARIS = [
    {"pn": "AUS-1", "nama": "Clutch disc", "remark": "Wearing parts",
     "unit": "HOWO-380 6X4 (A)", "path": "p"},
    {"pn": "AUS-1", "nama": "", "remark": "Wearing parts",
     "unit": "HOWO-380 6X4 (B)", "path": "p"},          # PN sama, berkas lain
    {"pn": "AUS-2", "nama": "Brake lining", "remark": "易损件",
     "unit": "HOWO-380 6X4 (A)", "path": "p"},          # hanya di varian A
    {"pn": "HAB-1", "nama": "Grease", "remark": "Consumable parts",
     "unit": "HOWO-380 6X4 (A)", "path": "p"},
    {"pn": "HAB-2", "nama": "Oli", "remark": "油液类",
     "unit": "HOWO-380 6X4 (B)", "path": "p"},
    {"pn": "RWT-1", "nama": "Filter", "remark": "保养件",
     "unit": "HOWO-380 6X4 (A)", "path": "p"},
    {"pn": "FAST-1", "nama": "Brake chamber", "remark": "售后常用件",
     "unit": "HOWO-380 6X4 (A)", "path": "p"},
    {"pn": "BIASA-1", "nama": "Bolt", "remark": "普通件",
     "unit": "HOWO-380 6X4 (A)", "path": "p"},
    {"pn": "ANGKA-1", "nama": "X", "remark": "12",
     "unit": "HOWO-380 6X4 (A)", "path": "p"},
]


def _mock(monkeypatch, baris=BARIS):
    monkeypatch.setattr(ai.part_index, "rows_with_remark", lambda u, **k: list(baris))
    monkeypatch.setattr(ai.part_index, "unit_models",
                        lambda: [{"unit": "HOWO-380 6X4 (A)", "kategori": "Sinotruk"}])
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})


def _lbl(r, kunci):
    return next((v for k, v in (r.get("part") or {}).items() if kunci in k), None)


# ── klasifikasi ─────────────────────────────────────────────────────────────
def test_kelompok_dipisah_tegas(monkeypatch):
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    assert r["found"] is True
    # daftar diurut NAMA (Brake lining < Clutch disc), bukan urutan katalog
    assert [x["pn"] for x in _lbl(r, "RAWAN RUSAK")] == ["AUS-2", "AUS-1"]
    assert sorted(x["pn"] for x in _lbl(r, "Habis pakai")) == ["HAB-1", "HAB-2"]
    assert [x["pn"] for x in _lbl(r, "perawatan berkala")] == ["RWT-1"]


def test_sering_dipakai_tidak_dicampur_ke_aus(monkeypatch):
    """售后常用件 = fast moving, BUKAN aus — wajib berlabel terpisah & eksplisit."""
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    fast = _lbl(r, "Sering dipakai")
    assert [x["pn"] for x in fast] == ["FAST-1"]
    label = next(k for k in r["part"] if "Sering dipakai" in k)
    assert "BUKAN penanda aus" in label
    assert "FAST-1" not in [x["pn"] for x in _lbl(r, "RAWAN RUSAK")]


def test_part_biasa_dan_angka_dibuang(monkeypatch):
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    semua = [x["pn"] for v in r["part"].values() for x in v]
    assert "BIASA-1" not in semua and "ANGKA-1" not in semua


def test_kelompok_remark_pemetaan_langsung():
    assert ai._kelompok_remark("Wearing \n parts") == "aus"
    assert ai._kelompok_remark("维修更换件") == "aus"
    assert ai._kelompok_remark("Consumable parts") == "habis_pakai"
    assert ai._kelompok_remark("保养件") == "perawatan"
    assert ai._kelompok_remark("售后常用件") == "sering_dipakai"
    assert ai._kelompok_remark("普通件") is None
    assert ai._kelompok_remark("") is None


# ── dedup lintas berkas varian ──────────────────────────────────────────────
def test_pn_ganda_lintas_varian_di_dedup(monkeypatch):
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    pns = [x["pn"] for x in _lbl(r, "RAWAN RUSAK")]
    assert len(pns) == len(set(pns))
    assert r["jumlah_per_kelompok"][next(k for k in r["part"] if "RAWAN RUSAK" in k)] == 2


def test_nama_kosong_diisi_dari_varian_lain(monkeypatch):
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    aus1 = next(x for x in _lbl(r, "RAWAN RUSAK") if x["pn"] == "AUS-1")
    assert aus1["nama"] == "Clutch disc"


def test_pn_sebagian_varian_ditandai(monkeypatch):
    """PN yang hanya ada di sebagian varian model = fakta penting bagi pembeli."""
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    aus2 = next(x for x in _lbl(r, "RAWAN RUSAK") if x["pn"] == "AUS-2")
    assert aus2["hanya_di_varian"] == ["HOWO-380 6X4 (A)"]
    aus1 = next(x for x in _lbl(r, "RAWAN RUSAK") if x["pn"] == "AUS-1")
    assert "hanya_di_varian" not in aus1        # ada di SEMUA varian


# ── argumen ─────────────────────────────────────────────────────────────────
def test_saring_satu_kelompok(monkeypatch):
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380", "kelompok": "aus"}, ADMIN)
    assert len(r["part"]) == 1 and _lbl(r, "RAWAN RUSAK")


def test_rangka_dipetakan_ke_model_lewat_populasi(monkeypatch):
    """EPC tak bisa dihubungi → turun ke daftar per-MODEL lewat populasi."""
    _mock(monkeypatch)
    monkeypatch.setattr(ai.epc_bom, "loading_list", lambda r: {"found": False, "_err": "api"})
    from app.services import fast_moving
    monkeypatch.setattr(fast_moving, "peta_populasi",
                        lambda: {"SJ346500": {"model": "HOWO-380 6X4 (A)"}})
    r = ai._t_part_aus_katalog({"rangka": "LZZ5DLSD2RSJ346500"}, ADMIN)
    assert r["found"] is True and r["unit_diminta"] == "HOWO-380 6X4 (A)"


# ── jalur PER-NOMOR RANGKA (BOM unit × penanda katalog) ─────────────────────
BOM = {"found": True, "frame_number": "SJ346500", "jumlah_part": 5, "parts": [
    {"pn": "AUS-1", "nama_cn": "离合器片", "qty": 2},
    {"pn": "FAST-1", "nama_cn": "制动气室", "qty": 4},
    {"pn": "TANPA-CAP", "nama_cn": "螺栓", "qty": 9},     # tak bercap → dilewati
    {"pn": "KONFLIK-1", "nama_cn": "X", "qty": 1},
    {"pn": "LAIN-1", "nama_cn": "Y", "qty": 1},
]}


def _mock_vin(monkeypatch, peta_global=None, peta_model=None, bom=None):
    monkeypatch.setattr(ai.epc_bom, "loading_list", lambda r: dict(bom or BOM))
    monkeypatch.setattr(ai, "_unit_katalog_dari_vin", lambda r: "LZZ5EXSF")
    g = peta_global if peta_global is not None else {
        "AUS-1": ["Wearing parts"], "FAST-1": ["售后常用件"],
        "KONFLIK-1": ["Wearing parts", "售后常用件"], "LAIN-1": ["Wearing parts"]}
    m = peta_model if peta_model is not None else {
        "AUS-1": ["Wearing parts"], "FAST-1": ["售后常用件"],
        "KONFLIK-1": ["Wearing parts", "售后常用件"]}
    monkeypatch.setattr(ai.part_index, "remark_map",
                        lambda unit_query="": dict(m) if unit_query else dict(g))
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})


def test_per_rangka_hanya_part_yang_terpasang(monkeypatch):
    _mock_vin(monkeypatch)
    r = ai._t_part_aus_katalog({"rangka": "SJ346500"}, ADMIN)
    assert r["sumber"].startswith("BOM pabrik unit ini")
    assert r["part_terpasang_di_unit"] == 5
    # TANPA-CAP tak dihitung; LAIN-1 hanya dicap model lain → tak ikut (lihat
    # test_warisan_model_lain_tidak_ikut_default)
    assert r["part_berpenanda"] == 3
    semua = [x["pn"] for v in r["part"].values() for x in v]
    assert "TANPA-CAP" not in semua


def test_per_rangka_bawa_qty_di_unit(monkeypatch):
    _mock_vin(monkeypatch)
    r = ai._t_part_aus_katalog({"rangka": "SJ346500"}, ADMIN)
    aus = next(x for x in _lbl(r, "RAWAN RUSAK") if x["pn"] == "AUS-1")
    assert aus["qty_di_unit"] == 2


def test_penanda_konflik_antar_model_ditampilkan(monkeypatch):
    """PN yang dicap beda di model berbeda: kelompok dipilih deterministik
    (aus menang), TAPI perbedaannya wajib terlihat."""
    _mock_vin(monkeypatch)
    r = ai._t_part_aus_katalog({"rangka": "SJ346500"}, ADMIN)
    k = next(x for x in _lbl(r, "RAWAN RUSAK") if x["pn"] == "KONFLIK-1")
    assert k["penanda_berbeda"] == ["Wearing parts", "售后常用件"]


def test_warisan_model_lain_tidak_ikut_default(monkeypatch):
    """PELAJARAN 2026-08-08: PN yang tak dicap katalog model unit ini, tapi dicap
    katalog model LAIN, dulu ikut masuk — 24 part aus SJ346500 membengkak jadi
    510 dan daftarnya memuat pemantik rokok, relay, resistor. Sekarang dihitung
    terpisah, TIDAK dicampur."""
    _mock_vin(monkeypatch)
    r = ai._t_part_aus_katalog({"rangka": "SJ346500"}, ADMIN)
    semua = [x["pn"] for v in r["part"].values() for x in v]
    assert "LAIN-1" not in semua
    assert r["part_bercap_di_model_lain"] == 1
    assert r["dasar_penanda"] == "katalog model unit ini"
    assert "catatan_warisan" in r


def test_warisan_ikut_hanya_bila_diminta(monkeypatch):
    _mock_vin(monkeypatch)
    r = ai._t_part_aus_katalog(
        {"rangka": "SJ346500", "sertakan_penanda_model_lain": True}, ADMIN)
    lain = next(x for x in _lbl(r, "RAWAN RUSAK") if x["pn"] == "LAIN-1")
    assert lain["penanda_dari_model_lain"] is True
    assert r["dari_katalog_model_lain"] == 1
    assert "warisan model lain" in r["dasar_penanda"]


def test_katalog_model_tak_dikenali_ditandai_petunjuk(monkeypatch):
    """Tanpa katalog model unit ini, SELURUH cap berasal dari model lain —
    hasilnya boleh disajikan tapi wajib berlabel petunjuk, bukan cap resmi."""
    _mock_vin(monkeypatch, peta_model={})
    monkeypatch.setattr(ai, "_unit_katalog_dari_vin", lambda r: "")
    r = ai._t_part_aus_katalog({"rangka": "SJ346500"}, ADMIN)
    assert r["_cek_tak_lengkap"] is True
    assert "PETUNJUK" in r["peringatan"]
    assert "tak dikenali" in r["dasar_penanda"]


def test_prioritas_kelompok_deterministik():
    assert ai._kelompok_dari_daftar(["售后常用件", "Wearing parts"])[0] == "aus"
    assert ai._kelompok_dari_daftar(["售后常用件"])[0] == "sering_dipakai"
    assert ai._kelompok_dari_daftar(["普通件"])[0] is None
    assert ai._kelompok_dari_daftar([])[0] is None


def test_bom_separuh_ditandai_cek_tak_lengkap(monkeypatch):
    _mock_vin(monkeypatch, bom={**BOM, "partial": True})
    r = ai._t_part_aus_katalog({"rangka": "SJ346500"}, ADMIN)
    assert r["_cek_tak_lengkap"] is True and "TIDAK LENGKAP" in r["peringatan"]


def test_epc_gagal_turun_ke_model_tapi_diberi_tahu(monkeypatch):
    """Menyajikan daftar MODEL untuk pertanyaan ber-nomor-rangka tanpa berkata
    apa-apa akan dibaca user sebagai 'ini isi unit saya'."""
    _mock(monkeypatch)
    monkeypatch.setattr(ai.epc_bom, "loading_list", lambda r: {"found": False, "_err": "api"})
    from app.services import fast_moving
    monkeypatch.setattr(fast_moving, "peta_populasi",
                        lambda: {"SJ346500": {"model": "HOWO-380 6X4 (A)"}})
    r = ai._t_part_aus_katalog({"rangka": "SJ346500"}, ADMIN)
    assert r["_cek_tak_lengkap"] is True
    assert "per MODEL" in r["peringatan"] and "BELUM tentu terpasang" in r["peringatan"]
    assert r["tingkat"].startswith("per MODEL")


def test_tanpa_unit_dan_rangka_ditolak_dengan_saran(monkeypatch):
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({}, ADMIN)
    assert "error" in r and r["unit_tersedia"]


def test_rangka_tak_terdaftar_minta_model(monkeypatch):
    _mock(monkeypatch)
    from app.services import fast_moving
    monkeypatch.setattr(fast_moving, "peta_populasi", lambda: {})
    r = ai._t_part_aus_katalog({"rangka": "XX99999999"}, ADMIN)
    assert "error" in r and "model" in r["error"].lower()


# ── kejujuran ───────────────────────────────────────────────────────────────
def test_unit_tak_ketemu_bukan_vonis_tak_punya_part_aus(monkeypatch):
    _mock(monkeypatch, baris=[])
    r = ai._t_part_aus_katalog({"unit": "MODEL-KARANGAN"}, ADMIN)
    assert r["found"] is False
    assert r["unit_tersedia"]
    assert "jangan simpulkan" in r["catatan"].lower()


def test_gagal_baca_katalog_ditandai_cek_tak_lengkap(monkeypatch):
    _mock(monkeypatch)

    def boom(u, **k):
        raise RuntimeError("katalog rusak")
    monkeypatch.setattr(ai.part_index, "rows_with_remark", boom)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    assert r["_cek_tak_lengkap"] is True and "BUKAN bukti" in r["error"]


def test_plafon_dilaporkan_terbuka(monkeypatch):
    banyak = [{"pn": f"P{i}", "nama": f"N{i}", "remark": "Wearing parts",
               "unit": "HOWO-380 6X4 (A)", "path": "p"}
              for i in range(ai._MAX_AUS_KATALOG + 25)]
    _mock(monkeypatch, baris=banyak)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    assert len(_lbl(r, "RAWAN RUSAK")) == ai._MAX_AUS_KATALOG
    assert r["terpotong"] == 25


def test_catatan_terakhir_dan_larang_gabung(monkeypatch):
    _mock(monkeypatch)
    r = ai._t_part_aus_katalog({"unit": "HOWO-380"}, ADMIN)
    assert list(r)[-1] == "catatan"
    assert "BUKAN penanda aus" in r["catatan"]
    assert "part_aus_dari_rangka" in r["catatan"]


def test_tool_terdaftar():
    assert ai._DISPATCH["part_aus_katalog"] is ai._t_part_aus_katalog
    assert "part_aus_katalog" in [s["function"]["name"] for s in ai._tool_specs(ADMIN)]


# ── lapisan data: sel NA tak boleh melempar ─────────────────────────────────
def test_sel_teks_tahan_na():
    """Regresi: `str(v or '')` pada sel pandas.NA melempar
    'boolean value of NA is ambiguous' dan menjatuhkan seluruh tool."""
    import pandas as pd
    assert part_index._sel_teks(pd.NA) == ""
    assert part_index._sel_teks(float("nan")) == ""
    assert part_index._sel_teks(None) == ""
    assert part_index._sel_teks("nan") == ""
    assert part_index._sel_teks("  Wearing \n parts ") == "Wearing parts"
    assert part_index._sel_teks(123) == "123"
