"""BALON → PART NUMBER: "no 4 dan 5 itu PN-nya apa?" harus TERJAWAB.

Kelas bug yang dikunci di sini, dari audit ai_chat_log 2026-08-17 (VIN
LZZ5DMSD0RT108874, PN EZ160043000028 'Front suspension of the cab'): user
bertanya PN balon 4 & 5, asisten MENYERAH 3× lalu menjawab SALAH 2× (balon 5
disebut WG1642430071, lalu EZ160043000035 — keduanya bukan). Padahal jawabannya
(balon 4 = WG1642430071, balon 5 = WG1642430072) ADA di respons EPC yang sama
yang dipakai menggambar figure-nya.

DUA cacat, dua-duanya diam (tak ada error, tak ada tool_failed):
  1. `figure_for_instance` hanya menyimpan baris PN yang dicari dan MEMBUANG 17
     item lainnya — sementara pemanggilnya membaca `items_ringkas`, kunci yang
     tak pernah ia hasilkan → daftar balon SELALU kosong di jalur reverse
     (jalur DEFAULT per-VIN).
  2. `part_di_balon` dicocokkan dengan `==` polos, padahal tipe `ballNum` dari
     EPC TIDAK STABIL (diprobe 2026-08-17: endpoint yang sama membalas '5'
     string di dua percobaan, 5 int di percobaan lain) sementara nomor dari
     user selalu di-int()-kan → cocok/tidaknya jadi ACAK, di jalur reverse
     MAUPUN jalur kategori lama.

⚠️ Test lama (test_gambar_exploded_reverse.py) LOLOS sepanjang bug ini hidup
karena stub-nya menyediakan `items_ringkas` yang fungsi aslinya tak pernah
kembalikan. Karena itu di sini `figure_for_instance` yang ASLI dipakai —
yang di-stub cuma lapisan HTTP (`_get_auto`), persis bentuk respons produksi.
Tanpa jaringan.
"""
from __future__ import annotations

import pytest

from app.services import ai_assistant as ai
from app.services import epc_bom

ADMIN = {"username": "admin", "role": "admin"}
VIN = "LZZ5DMSD0RT108874"
FRAME = "0RT108874"[-8:]          # _frame() → 8 char terakhir
PN = "EZ160043000028"
FIG_PN = "EC1600430000006"

# Bentuk respons NYATA part/tree/item (diprobe 2026-08-17): ballNum & amount
# dikirim sebagai STRING, bukan int. Dipangkas ke 6 dari 18 baris.
_ITEM_RESP = {"data": {
    "partListName": "Front suspension of the cab",
    "d2s": ["I00420333_C.2.svg"],
    "items": [
        {"code": "EZ160043000037", "name": "Cab front overhang bracket", "ballNum": "1", "amount": "1"},
        {"code": "EZ160043000038", "name": "Cab front overhang bracket", "ballNum": "2", "amount": "1"},
        {"code": PN, "name": "Front suspension assembly of the driver's cab", "ballNum": "3", "amount": "1"},
        {"code": "WG1642430071", "name": "Reversible shaft rear pin", "ballNum": "4", "amount": "2"},
        {"code": "WG1642430072", "name": "Limit washer", "ballNum": "5", "amount": "2"},
        {"code": "Q700B06", "name": "Straight-through Grease nipple", "ballNum": "7", "amount": "2"},
    ],
}}
_REVERSE_RESP = {"data": [{"partCode": FIG_PN, "partName": "Front suspension of the cab",
                           "partId": 1196124, "partListId": 319200, "rootId": 1132006}]}

_INST = {"parent_pn": FIG_PN, "parent_nama": "Front suspension of the cab",
         "part_id": 1196124, "part_list_id": 319200, "root_id": 1132006}


@pytest.fixture
def epc_stub(monkeypatch):
    """Stub HTTP EPC saja — reverse_find_in_unit & figure_for_instance ASLI."""
    # **kw wajib: jalur GLOBAL memanggil _get_auto dgn timeout/retries.
    def fake_get_auto(url, params, **kw):
        if url == epc_bom._REVERSE_URL:
            return _REVERSE_RESP
        if url == epc_bom._ATLAS_ITEM_URL:
            return _ITEM_RESP
        return {"_err": "api"}

    monkeypatch.setattr(epc_bom, "_get_auto", fake_get_auto)
    monkeypatch.setattr(epc_bom, "_atlas_root_cached", lambda f: {"orderNo": 1})
    monkeypatch.setattr(ai.ai_export, "stash_builder",
                        lambda judul, spec, ext="png": ("img1", "gambar.png"))


# ── Lapisan sumber: figure_for_instance ──────────────────────────────────────

def test_figure_for_instance_mengembalikan_semua_balon(epc_stub):
    """Kunci KONTRAK-nya, bukan stub: kunci `items_ringkas` harus benar-benar ADA
    dan memuat SELURUH balon figure — bukan cuma PN yang dicari."""
    fig = epc_bom.figure_for_instance(VIN, _INST, PN)
    assert fig["found"] is True
    assert fig["balon"] == "3"
    items = fig["items_ringkas"]
    assert len(items) == 6 and fig["jumlah_item"] == 6, "17 dari 18 baris pernah dibuang di sini"
    assert {it["pn"] for it in items} >= {"WG1642430071", "WG1642430072"}
    b5 = next(it for it in items if str(it["balon"]) == "5")
    assert (b5["pn"], b5["nama"], b5["qty"]) == ("WG1642430072", "Limit washer", "2")


def test_baris_tanpa_pn_dan_tanpa_balon_dibuang(monkeypatch):
    resp = {"data": {"partListName": "F", "d2s": ["a.svg"], "items": [
        {"code": PN, "name": "Assy", "ballNum": "3"},
        {"code": "", "name": "keterangan gambar", "ballNum": None},   # bukan part
        {"code": "", "name": "Bagian dalam", "ballNum": "9"},         # berbalon → SIMPAN
    ]}}
    monkeypatch.setattr(epc_bom, "_get_auto", lambda url, params, **kw: resp)
    fig = epc_bom.figure_for_instance(VIN, _INST, PN)
    assert [it["balon"] for it in fig["items_ringkas"]] == ["3", "9"]
    assert next(it for it in fig["items_ringkas"] if it["balon"] == "9")["pn"] is None


# ── Pencocokan nomor balon (string EPC vs int user) ──────────────────────────

@pytest.mark.parametrize("nilai,diminta,cocok", [
    ("5", 5, True),        # ⛔ kasus NYATA yang membuat part_di_balon selalu None
    (5, 5, True),
    (" 5 ", 5, True),
    ("12A", "12a", True),
    ("5", 15, False),
    ("5", 4, False),
    (None, 5, False),
    ("5", None, False),
])
def test_balon_cocok(nilai, diminta, cocok):
    assert ai._balon_cocok(nilai, diminta) is cocok


# ── Jalur reverse (DEFAULT per-VIN) ──────────────────────────────────────────

def test_daftar_balon_terisi_di_jalur_reverse(epc_stub):
    """Yang dulu kosong: konteks follow-up 'no N itu apa'."""
    out = ai._gambar_exploded_atlas_impl({"rangka": VIN, "pn": PN}, ADMIN)
    assert out["found"] is True and out["sumber"] == "reverse"
    daftar = out["daftar_balon_gambar"]
    assert len(daftar) == 6, "daftar balon SELALU kosong sebelum perbaikan ini"
    assert {"balon": "5", "pn": "WG1642430072", "nama": "Limit washer", "qty": "2"} in daftar
    assert out["daftar_balon_cakupan"]["total_item_figure"] == 6


def test_balon_diminta_dijawab_pn_nya(epc_stub):
    """Pertanyaan pemilik yang gagal: 'No 5 berapa pn nya'."""
    out = ai._gambar_exploded_atlas_impl({"rangka": VIN, "pn": PN, "balon": 5}, ADMIN)
    p = out["part_di_balon"]
    assert p is not None, "dulu di-hardcode None di jalur reverse"
    assert p["part_number"] == "WG1642430072"
    assert p["nama"] == "Limit washer"
    assert p["figure"] == "Front suspension of the cab"
    assert "WG1642430072" in out["catatan"], "PN wajib ikut di catatan utk model"


def test_balon_4_dan_5_part_berbeda(epc_stub):
    """Jawaban salah di produksi menyebut balon 4 & 5 part yang SAMA."""
    b4 = ai._gambar_exploded_atlas_impl({"rangka": VIN, "pn": PN, "balon": 4}, ADMIN)
    b5 = ai._gambar_exploded_atlas_impl({"rangka": VIN, "pn": PN, "balon": 5}, ADMIN)
    assert b4["part_di_balon"]["part_number"] == "WG1642430071"
    assert b5["part_di_balon"]["part_number"] == "WG1642430072"


def test_ballnum_int_juga_kena(monkeypatch):
    """Tipe ballNum EPC tak stabil — varian INT harus sama-sama terjawab."""
    resp = {"data": {"partListName": "Front suspension of the cab",
                     "d2s": ["I00420333_C.2.svg"],
                     "items": [{"code": PN, "name": "Assy", "ballNum": 3, "amount": 1},
                               {"code": "WG1642430072", "name": "Limit washer",
                                "ballNum": 5, "amount": 2}]}}

    def fake_get_auto(url, params, **kw):
        return _REVERSE_RESP if url == epc_bom._REVERSE_URL else resp

    monkeypatch.setattr(epc_bom, "_get_auto", fake_get_auto)
    monkeypatch.setattr(epc_bom, "_atlas_root_cached", lambda f: {"orderNo": 1})
    monkeypatch.setattr(ai.ai_export, "stash_builder",
                        lambda judul, spec, ext="png": ("img1", "gambar.png"))
    out = ai._gambar_exploded_atlas_impl({"rangka": VIN, "pn": PN, "balon": 5}, ADMIN)
    assert out["part_di_balon"]["part_number"] == "WG1642430072"


def test_balon_tak_ada_dikatakan_terus_terang(epc_stub):
    """Balon yang memang tak di figure ini: JANGAN diam (diam = model mengisi
    sendiri, terbukti di log), tapi juga jangan menyodorkan PN mana pun."""
    out = ai._gambar_exploded_atlas_impl({"rangka": VIN, "pn": PN, "balon": 99}, ADMIN)
    assert out["part_di_balon"] is None
    catatan = out["catatan"]
    assert "TIDAK ADA" in catatan and "JANGAN menyebut PN" in catatan
    assert "WG1642430071" not in catatan and "WG1642430072" not in catatan


def test_gambar_tetap_tampil_saat_balon_tak_ada(epc_stub):
    """Kejujuran soal balon tak boleh mematikan gambarnya."""
    out = ai._gambar_exploded_atlas_impl({"rangka": VIN, "pn": PN, "balon": 99}, ADMIN)
    assert out["found"] is True and out["gambar"][0]["image_id"] == "img1"


# ── Jalur kategori lama (flag reverse mati) — bug string/int yang sama ────────

def test_jalur_kategori_lama_juga_cocokkan_balon(monkeypatch):
    mati = ai.get_settings().model_copy(update={"epc_exploded_reverse": False})
    monkeypatch.setattr(ai, "get_settings", lambda: mati)
    monkeypatch.setattr(ai.ai_export, "stash_builder",
                        lambda judul, spec, ext="png": ("img1", "gambar.png"))
    monkeypatch.setattr(ai.epc_bom, "exploded_figures", lambda r, p, k: {
        "found": True, "frame_number": FRAME, "figures": [{
            "svg": "I00420333_C.2.svg", "balon": "3", "nama": "Front suspension of the cab",
            "kategori": "kabin", "jumlah_item": 2,
            "items_ringkas": [{"balon": "3", "pn": PN, "nama": "Assy", "qty": "1"},
                              {"balon": "5", "pn": "WG1642430072", "nama": "Limit washer",
                               "qty": "2"}]}]})
    out = ai._gambar_exploded_atlas_impl(
        {"rangka": VIN, "pn": PN, "kategori": "kabin", "balon": 5}, ADMIN)
    assert out["part_di_balon"]["part_number"] == "WG1642430072"
