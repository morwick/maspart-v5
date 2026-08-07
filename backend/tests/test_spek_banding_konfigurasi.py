"""Tool spek_massal_rangka + banding_konfigurasi_rangka (2026-07-23): spesifikasi
& banding KONFIGURASI banyak unit dari EPC getVehicleConfig (bukan part). Juga
flag `marketability` dari HAR — nilainya tetap DISIMPAN di baris item (kunci
netral `marketability_epc`, kode mentah), tapi sejak 2026-08-04 TIDAK BOLEH
disajikan sebagai klaim "discontinued" dan sejak 2026-08-06 tak lagi
diterjemahkan sama sekali (tafsirnya terbukti terbalik; lihat _pasok_of di
epc_bom.py). Semua jaringan di-MOCK."""
import json

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "roni", "role": "pembeli"}

_CFG = {
    "SJ399160": {"modelCode": "ZZ1257N60H", "seriesName": "NX", "driveMode": "6×2",
                 "useType": "载货车", "discharge": "欧V", "breakMode": "ABS",
                 "engineModelCode": "MC07.28-50发动机",
                 "gearboxModelCode": "HW13709XST变速箱"},
    "SJ417438": {"modelCode": "ZZ4187N361", "seriesName": "NX", "driveMode": "4×2",
                 "useType": "牵引车", "discharge": "欧V", "breakMode": "normal",
                 "engineModelCode": "MC07.28-50发动机",
                 "gearboxModelCode": "HW13709XST变速箱"},
    "PJ312521": {"modelCode": "ZZ1257V584", "seriesName": "NX", "driveMode": "6×4",
                 "useType": "载货车", "discharge": "欧V", "breakMode": "ABS",
                 "engineModelCode": "MC07.28-50发动机",
                 "gearboxModelCode": "HW13709XSTC2变速箱"},
}


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai.epc, "get_config", lambda f: dict(_CFG.get(f, {})))
    monkeypatch.setattr(ai.epc_bom, "_frame", lambda r: r[-8:] if len(r) >= 8 else r)
    return {}


def test_spek_massal(dunia):
    r = ai._t_spek_massal_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ399160", "LZZ1CCSD6SJ417438", "XXBADVIN99"]},
        ADMIN)
    assert r["found"] and r["ketemu"] == 2 and r["tak_ada"] == 1
    by = {h["rangka"]: h for h in r["hasil"]}
    h1 = by["LZZ1BG3H0SJ399160"]
    assert h1["gerak"] == "6×2" and h1["jenis"] == "Cargo"
    assert h1["emisi"] == "Euro V" and h1["mesin"] == "MC07.28-50"   # CN suffix dibuang
    h2 = by["LZZ1CCSD6SJ417438"]
    assert h2["jenis"] == "Tractor (kepala)" and h2["rem"] == "tanpa ABS"
    assert by["XXBADVIN99"]["found"] is False


def test_spek_excel(dunia, monkeypatch):
    tangkap = {}
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda j, k, b: tangkap.update(judul=j, kolom=k, baris=b)
                        or ("EX1", "spek.xlsx"))
    r = ai._t_spek_massal_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ399160", "LZZ1CCSD6SJ417438"],
         "excel": True}, ADMIN)
    assert r["export_id"] == "EX1"
    assert "Model" in tangkap["kolom"] and "Gerak" in tangkap["kolom"]


def test_banding_konfigurasi(dunia):
    r = ai._t_banding_konfigurasi_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ399160", "LZZ1CCSD6SJ417438",
                           "LZZ1BLMJ9PJ312521"]}, ADMIN)
    assert r["found"] and r["dikenal_epc"] == 3
    # sama: seri NX, emisi Euro V, mesin MC07.28-50
    assert r["spek_sama_semua_unit"]["seri"] == "NX"
    assert r["spek_sama_semua_unit"]["mesin"] == "MC07.28-50"
    # beda: model/gerak/jenis/rem/gearbox
    for f in ("model", "gerak", "jenis", "rem", "gearbox"):
        assert f in r["field_berbeda"]
    # 3 unit beda semua → 3 kelompok, label A/B/C
    assert r["jumlah_kelompok"] == 3
    assert [k["kelompok"] for k in r["kelompok"]] == ["A", "B", "C"]
    assert "banding_rangka_massal" in r["catatan"]   # arahan part vs spek


def test_banding_minimal_2(dunia):
    assert "error" in ai._t_banding_konfigurasi_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ399160"]}, ADMIN)


def test_banding_excel_dengan_baris_sama(dunia, monkeypatch):
    tangkap = {}
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda j, k, b: tangkap.update(baris=b) or ("EX2", "b.xlsx"))
    r = ai._t_banding_konfigurasi_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ399160", "LZZ1CCSD6SJ417438"],
         "excel": True}, ADMIN)
    assert r["export_id"] == "EX2"
    assert any("SAMA di semua unit:" in str(row[1]) for row in tangkap["baris"])


def test_dua_tool_di_gate_dari_pembeli():
    names = {s["function"]["name"] for s in ai._tool_specs(PEMBELI)}
    assert "spek_massal_rangka" not in names
    assert "banding_konfigurasi_rangka" not in names
    names_admin = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert {"spek_massal_rangka", "banding_konfigurasi_rangka"} <= names_admin


# ── Flag marketability dari HAR tree/item: MENTAH, tanpa tafsir ──────────────

def test_atlas_item_row_marketability_mentah():
    """Nilai `marketability` disimpan APA ADANYA ('g'/'b') di kunci netral.

    Sampai 2026-08-06 ia diterjemahkan jadi 'stop'/'dipasok' di kunci `pasok`.
    Terjemahan itu TERBALIK (uji 50 PN ke SIMS isSale) dan, walau lapisan
    penyajian sudah membuangnya, kata 'stop' tetap ikut tercetak setiap kali
    baris item di-dump ke model → cukup untuk melahirkan kembali klaim
    'discontinued'. Kode mentah tak punya arti bawaan, jadi tak bisa bocor."""
    row = ai.epc_bom._atlas_item_row(
        {"code": "AZ9998887776", "name": "Backing plate", "originalName": "垫板",
         "amount": 1, "marketability": "b"}, "")
    assert row["marketability_epc"] == "b"
    row2 = ai.epc_bom._atlas_item_row(
        {"code": "AZ9998887777", "name": "Bolt", "amount": 1, "marketability": "g"}, "")
    assert row2["marketability_epc"] == "g"
    # ⛔ tak ada satu pun kata bertafsir di baris item
    for r in (row, row2):
        assert "pasok" not in r
        teks = json.dumps(r, ensure_ascii=False).lower()
        assert "stop" not in teks and "dipasok" not in teks
    # tanpa marketability (cache lama) → field tak ada
    row3 = ai.epc_bom._atlas_item_row({"code": "AZ9998887778", "name": "Nut"}, "")
    assert "marketability_epc" not in row3


def test_cari_part_di_unit_TIDAK_klaim_discontinued(monkeypatch):
    """DULU tool ini menandai part ber-marketability 'b' sebagai
    "STOP — discontinued pabrik". Uji silang 50 PN ke status jual RESMI SIMS
    (`isSale`) pada 2026-08-04 membuktikan tafsirnya TERBALIK: 25/25 part
    ber-'b' justru DIJUAL, sementara 16/25 ber-'g' TIDAK dijual. Klaim itu
    menjauhkan user dari part yang paling bisa dipesan → DIBUANG, dan model
    dilarang menyimpulkannya sendiri dari hasil EPC."""
    monkeypatch.setattr(ai.epc_bom, "items_index_ready", lambda r: False)
    monkeypatch.setattr(ai.epc_bom, "warm_items_index", lambda r: None)
    monkeypatch.setattr(ai.epc_bom, "search_in_unit", lambda r, k: {
        "found": True, "frame_number": "SJ346500",
        "hasil": [{"pn": "AZ9998887776", "nama": "Old part", "kata_kunci": "part",
                   "marketability_epc": "b"}]})
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai.epc_bom, "reverse_find_in_unit",
                        lambda r, pn: {"found": False, "instances": []})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    r = ai._t_cari_part_di_unit({"rangka": "SJ346500", "kata_kunci": "part"}, ADMIN)
    assert "status_pasok" not in r["parts"][0]
    assert "discontinued" not in json.dumps(r["parts"], ensure_ascii=False).lower()
    # pagar aktif: model dilarang menyimpulkan status pasok dari hasil EPC
    assert "JANGAN menyatakan part discontinued" in r["catatan"]
