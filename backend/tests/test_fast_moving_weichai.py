# -*- coding: utf-8 -*-
"""Pelengkap MESIN WEICHAI di dataset fast moving — EPC Weichai DI-MOCK.

Masalahnya: katalog EPC Sinotruk berhenti di batas mesin, jadi untuk unit
bermesin Weichai element filter oli & solar mesin TIDAK ADA di pohon Atlas.
Terukur di produksi 2026-08-07: 17 dari 42 model tanpa slot 'filter oli mesin'
sama sekali — bolong tepat di part yang paling sering diganti.

Yang dikunci di sini: lubang itu ditambal dari EPC Weichai SAAT BUILD (bukan
tiap kali user bertanya), asal barisnya jujur disebut, kegagalan bridge tak
menjatuhkan build, dan model yang pohon Sinotruk-nya sudah lengkap tidak
membuang panggilan jaringan sama sekali.
"""
from __future__ import annotations

import json

import pytest

from app.services import fast_moving as fm

FRAME_A = "RT108966"
FRAME_B = "SJ346500"

# Baris EPC Weichai apa adanya (nama Inggris + China bercampur, seperti aslinya).
WC_HASIL = [
    {"pn": "1000442956", "nama": "Oil filter element component", "group": "Engine Block Group"},
    {"pn": "1000424916", "nama": "Fuel filter element With O-ring", "group": "Fuel System Group"},
    {"pn": "612630060087", "nama": "Bolt M8", "group": "Engine Block Group"},
    # Aksesori listrik mesin — juga tak ada di pohon Atlas unit Weichai.
    {"pn": "612600090206", "nama": "Alternator", "group": "Generator Group"},
    {"pn": "612630030029", "nama": "Starter motor", "group": "Starter Group"},
]


@pytest.fixture(autouse=True)
def _isolasi(tmp_path, monkeypatch):
    """Cache Weichai & keluaran dataset diarahkan ke tmp — jangan sentuh data/."""
    monkeypatch.setattr(fm, "_dir_out", lambda: tmp_path)
    monkeypatch.setattr(fm, "_WC_AKTIF", True)
    yield


def _per_model(*, punya_filter_mesin: bool) -> dict:
    """Bentuk `per_model` sebagaimana dibangun build() dari cache EPC Sinotruk."""
    rows = [{
        "kat": "rem", "slot": "brake lining", "id": "kampas rem",
        "pn": "WG9100443050", "pn_asli": "WG9100443050", "nama": "Brake lining",
        "nama_cn": "", "qty": 8, "assembly": "brake", "pengganti": [],
    }]
    if punya_filter_mesin:
        rows.append({
            "kat": "filter", "slot": "engine oil filter", "id": "filter oli mesin",
            "pn": "VG1540080311", "pn_asli": "VG1540080311",
            "nama": "Engine oil filter", "nama_cn": "", "qty": 1,
            "assembly": "engine", "pengganti": [],
        })
    return {"ZZ1TEST": {"jenis": "HOWO 6X4", "unit": {FRAME_A: rows},
                        "tahun": {FRAME_A: "2024"}}}


def _mock_weichai(monkeypatch, hasil=None, err=None, found=True, catat=None):
    """Tambal `find_parts` pada MODUL ASLINYA.

    ⚠️ Jangan memakai `monkeypatch.setitem(sys.modules, …)`: `_wc_ambil` memanggil
    `from . import epc_weichai`, dan bentuk itu mengambil ATRIBUT paket
    `app.services` lebih dulu. Begitu modul aslinya sudah pernah diimpor test
    lain di sesi yang sama, atribut itu sudah terikat ke modul asli → tambalan
    sys.modules DIABAIKAN dan test menembak jaringan sungguhan. Itulah sebabnya
    berkas ini lolos sendirian tapi gagal di suite penuh (2026-08-07)."""
    from app.services import epc_weichai

    def find_parts(rangka, terms):
        if catat is not None:
            catat.append(rangka)
        if err:
            return {"_err": err}
        if not found:
            return {"found": False}
        return {"found": True, "hasil": list(hasil if hasil is not None else WC_HASIL)}

    monkeypatch.setattr(epc_weichai, "find_parts", find_parts)


# ── Inti: lubang mesin ditambal ─────────────────────────────────────────────

def test_model_bolong_ditambal_dari_weichai(monkeypatch):
    _mock_weichai(monkeypatch)
    pm = _per_model(punya_filter_mesin=False)
    stat = fm._lengkapi_mesin(pm, fm._kamus())

    rows = pm["ZZ1TEST"]["unit"][FRAME_A]
    ids = {r.get("id") for r in rows}
    assert "filter oli mesin" in ids
    assert "filter solar halus (atas)" in ids
    assert stat["model_ditambal"] == 1
    # Baut ikut terbawa hasil pencarian 'filter' TAPI bukan part mesin yang
    # dicari — tak boleh ikut masuk.
    assert "612630060087" not in {r["pn"] for r in rows}


def test_alternator_dan_starter_ikut_ditambal(monkeypatch):
    """Permintaan pemilik 2026-08-07: aksesori listrik mesin ikut, karena untuk
    unit Weichai keduanya sama-sama tak ada di pohon Atlas."""
    _mock_weichai(monkeypatch)
    pm = _per_model(punya_filter_mesin=False)
    fm._lengkapi_mesin(pm, fm._kamus())
    rows = pm["ZZ1TEST"]["unit"][FRAME_A]
    ids = {r.get("id") for r in rows}
    assert "alternator (dinamo ampere)" in ids
    assert "motor starter (dinamo starter)" in ids
    pns = {r["pn"] for r in rows}
    assert {"612600090206", "612630030029"} <= pns


def test_nama_weichai_sadar_konteks_mesin():
    """Nama asli dari BOM Weichai (terukur di produksi 2026-08-07)."""
    k = fm._kamus()
    # 'Oil Filter' polos: di kamus umum jatuh ke transmisi/hidrolik, di BOM
    # MESIN artinya filter oli mesin.
    assert fm._istilah("Oil Filter", "", k) == "filter oli (transmisi/hidrolik)"
    assert fm._wc_istilah("Oil Filter", k) == "filter oli mesin"
    assert fm._wc_istilah("Fuel Filter", k) == "filter solar halus (atas)"
    assert fm._wc_istilah("Fuel Fine Filter Element", k) == "filter solar halus (atas)"
    assert fm._wc_istilah("Fuel Coarse Filter Element", k) == "filter solar kasar (bawah)"
    # Dudukan/braket/gasket BUKAN barang habis pakai — jangan mengotori daftar.
    for n in ("Fuel Filter Seat", "Oil Filter Seat", "Fuel Filter Bracket",
              "Oil Filter Base Gasket"):
        assert fm._wc_istilah(n, k) == "", n
    # Ambigu (tak jelas oli atau solar) → lebih baik dilewat daripada salah label
    assert fm._wc_istilah("Filter Element", k) == ""


def test_filter_oli_polos_dari_weichai_masuk(monkeypatch):
    _mock_weichai(monkeypatch, hasil=[
        {"pn": "1000442956", "nama": "Oil Filter", "group": "Engine Block Group"},
        {"pn": "1000424916", "nama": "Fuel Filter Seat", "group": "Fuel System Group"},
    ])
    pm = _per_model(punya_filter_mesin=False)
    fm._lengkapi_mesin(pm, fm._kamus())
    rows = pm["ZZ1TEST"]["unit"][FRAME_A]
    assert "filter oli mesin" in {r.get("id") for r in rows}
    assert "1000424916" not in {r["pn"] for r in rows}   # dudukan tak ikut


def test_klasifikasi_alternator_starter_dari_kamus():
    """Baris Sinotruk pun kini terklasifikasi — bukan hanya jalur Weichai."""
    k = fm._kamus()
    assert fm._klasifikasi("Alternator assembly", "", k) == "kelistrikan_mesin"
    assert fm._klasifikasi("Starter motor", "", k) == "kelistrikan_mesin"
    assert fm._istilah("Alternator assembly", "", k) == "alternator (dinamo ampere)"
    assert fm._istilah("Starter motor", "", k) == "motor starter (dinamo starter)"
    # 发电机 = alternator dalam nama EPC berbahasa China
    assert fm._klasifikasi("", "发电机总成", k) == "kelistrikan_mesin"


def test_baris_weichai_ditandai_sumbernya(monkeypatch):
    _mock_weichai(monkeypatch)
    pm = _per_model(punya_filter_mesin=False)
    fm._lengkapi_mesin(pm, fm._kamus())
    wc = [r for r in pm["ZZ1TEST"]["unit"][FRAME_A] if r.get("wc")]
    assert wc, "baris Weichai wajib bertanda"
    assert all(r["assembly"] for r in wc)   # group mesin ikut terbawa


def test_model_yang_sudah_lengkap_tak_memanggil_weichai(monkeypatch):
    """Hemat jaringan: pohon Sinotruk lengkap → bridge TIDAK disentuh."""
    catat: list = []
    _mock_weichai(monkeypatch, catat=catat)
    pm = _per_model(punya_filter_mesin=True)
    stat = fm._lengkapi_mesin(pm, fm._kamus())
    assert catat == []
    assert stat["model_bolong"] == 0


# ── Kegagalan tidak boleh menjatuhkan build ─────────────────────────────────

def test_bridge_gagal_build_tetap_jalan(monkeypatch):
    _mock_weichai(monkeypatch, err="no_session")
    pm = _per_model(punya_filter_mesin=False)
    stat = fm._lengkapi_mesin(pm, fm._kamus())
    assert stat["gagal"] >= 1
    assert stat["model_ditambal"] == 0
    assert pm["ZZ1TEST"]["unit"][FRAME_A]        # baris lama utuh


def test_unit_bukan_weichai_bukan_kegagalan(monkeypatch):
    _mock_weichai(monkeypatch, found=False)
    pm = _per_model(punya_filter_mesin=False)
    stat = fm._lengkapi_mesin(pm, fm._kamus())
    assert stat["bukan_weichai"] >= 1
    assert stat["gagal"] == 0


def test_exception_ditelan(monkeypatch):
    from app.services import epc_weichai

    def _boom(rangka, terms):
        raise RuntimeError("bridge meledak")

    monkeypatch.setattr(epc_weichai, "find_parts", _boom)
    pm = _per_model(punya_filter_mesin=False)
    stat = fm._lengkapi_mesin(pm, fm._kamus())   # tak melempar
    assert stat["gagal"] >= 1


# ── Cache: build besok tak menembak jaringan lagi ───────────────────────────

def test_hasil_dicache_di_disk(monkeypatch, tmp_path):
    catat: list = []
    _mock_weichai(monkeypatch, catat=catat)
    fm._lengkapi_mesin(_per_model(punya_filter_mesin=False), fm._kamus())
    assert len(catat) == 1
    assert fm._wc_cache_path().exists()

    # build kedua: cache masih segar → nol panggilan jaringan
    catat.clear()
    stat = fm._lengkapi_mesin(_per_model(punya_filter_mesin=False), fm._kamus())
    assert catat == []
    assert stat["cache"] >= 1
    assert stat["model_ditambal"] == 1           # tetap tertambal dari cache


def test_bukan_weichai_dicache_lama(monkeypatch):
    """Mesin Sinotruk (MC/MT) menjawab 'tak ada di EPC Weichai' — itu sestatis
    BOM-nya. Kalau ikut TTL gagal, tiap build harian menghantam bridge lagi."""
    catat: list = []
    _mock_weichai(monkeypatch, found=False, catat=catat)
    fm._lengkapi_mesin(_per_model(punya_filter_mesin=False), fm._kamus())
    assert len(catat) == 1
    catat.clear()
    stat = fm._lengkapi_mesin(_per_model(punya_filter_mesin=False), fm._kamus())
    assert catat == [], "vonis 'bukan Weichai' harus dipakai ulang dari cache"
    assert stat["cache"] >= 1


def test_pemicu_hanya_butuh_filter_oli_hilang(monkeypatch):
    """Filter solar kerap ADA di pohon Sinotruk (terpasang di sasis) sementara
    filter olinya di sisi mesin — syarat 'kedua-duanya hilang' membuat 11 model
    tetap bolong (ukuran produksi 2026-08-07)."""
    catat: list = []
    _mock_weichai(monkeypatch, catat=catat)
    pm = _per_model(punya_filter_mesin=False)
    pm["ZZ1TEST"]["unit"][FRAME_A].append({
        "kat": "filter", "slot": "fuel filter", "id": "filter solar halus (atas)",
        "pn": "VG1092080030", "pn_asli": "VG1092080030", "nama": "Fuel filter",
        "nama_cn": "", "qty": 1, "assembly": "fuel", "pengganti": [],
    })
    stat = fm._lengkapi_mesin(pm, fm._kamus())
    assert catat, "punya filter solar tapi TANPA filter oli mesin → tetap ditambal"
    assert stat["model_ditambal"] == 1
    assert "filter oli mesin" in {r.get("id") for r in pm["ZZ1TEST"]["unit"][FRAME_A]}


def test_cache_gagal_ttl_pendek(monkeypatch):
    """Kegagalan di-cache SEBENTAR saja — jangan menghantam bridge tiap build,
    tapi juga jangan mengunci lubangnya selama 30 hari."""
    assert fm._WC_TTL_GAGAL < fm._WC_TTL
    catat: list = []
    _mock_weichai(monkeypatch, err="network", catat=catat)
    fm._lengkapi_mesin(_per_model(punya_filter_mesin=False), fm._kamus())
    assert len(catat) == 1

    # tuakan entri melewati TTL gagal → dicoba lagi
    import gzip
    p = fm._wc_cache_path()
    cache = json.load(gzip.open(p, "rt", encoding="utf-8"))
    for e in cache.values():
        e["at"] -= fm._WC_TTL_GAGAL + 60
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(cache, f)
    catat.clear()
    _mock_weichai(monkeypatch, catat=catat)
    fm._lengkapi_mesin(_per_model(punya_filter_mesin=False), fm._kamus())
    assert len(catat) == 1


def test_kill_switch(monkeypatch):
    catat: list = []
    _mock_weichai(monkeypatch, catat=catat)
    monkeypatch.setattr(fm, "_WC_AKTIF", False)
    stat = fm._lengkapi_mesin(_per_model(punya_filter_mesin=False), fm._kamus())
    assert catat == [] and stat == {}


# ── Slot hasil agregasi membawa sumbernya ───────────────────────────────────

def test_slot_dari_weichai_menyebut_sumber(monkeypatch, tmp_path):
    """Setelah agregasi build(), slot yang seluruh variannya dari Weichai wajib
    menyebutkan asalnya — part mesin memang TIDAK ada di pohon Atlas unit itu."""
    _mock_weichai(monkeypatch)
    monkeypatch.setattr(fm, "_peta_populasi",
                        lambda: {FRAME_A: {"model": "ZZ1TEST", "jenis": "HOWO 6X4",
                                           "tahun": "2024"}})

    d_items = tmp_path / "epc_unit_items"
    d_items.mkdir()
    (d_items / f"{FRAME_A}.json").write_text(json.dumps({"rows": [
        {"pn": "WG9100443050", "nama": "Brake lining", "qty": 8},
    ]}), encoding="utf-8")

    class _S:
        data_path = tmp_path
    monkeypatch.setattr(fm, "get_settings", lambda: _S())

    ringkas = fm.build()
    assert ringkas["mesin_weichai"]["model_ditambal"] == 1

    import gzip
    data = json.load(gzip.open(tmp_path / "fast_moving.json.gz", "rt", encoding="utf-8"))
    slots = data["model"]["ZZ1TEST"]["slot"]
    wc = [s for s in slots if s.get("sumber") == "EPC Weichai"]
    assert wc, "slot mesin dari Weichai wajib menyebut sumber"
    assert {s.get("nama_id") for s in wc} & fm._ID_MESIN_AMBIL
    for s in wc:
        assert all(v.get("sumber") == "EPC Weichai" for v in s["varian"])
