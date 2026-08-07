"""Indeks tautan entitas antar-store (knowledge_links) — Fase A program
"pengetahuan nyambung" 2026-07-23. Build dari store sintetis di tmp_path;
invarian: byte-stable, pembatas ≥2 store, dedup/exclude/cap/pembeli."""
import gzip
import json

import pytest

from app.services import knowledge_links as kl


@pytest.fixture
def dunia(tmp_path, monkeypatch):
    """Store sintetis: dtc_codes (via modul), jadwal, filter, media; data_path
    dialihkan ke tmp; store svc dialihkan ke tmp jg."""
    svc = tmp_path / "svc"
    svc.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(kl, "_SVC", svc)

    class _S:
        data_path = data

    monkeypatch.setattr(kl, "get_settings", lambda: _S())

    from app.services.knowledge_util import write_json_gz

    # jadwal: model SD16 + PN filter (entitas pn & model)
    write_json_gz(svc / "jadwal_perawatan.json.gz", [
        {"jenis": "dozer", "model": "SD16", "varian": "", "sistem": "mesin",
         "nama": "Filter oli mesin", "part_number": "175-49-11580",
         "ganti_jam": [250]},
    ])
    # filter: PN sama (cross-store!) + cross-ref
    write_json_gz(svc / "filter_shantui.json.gz", [
        {"alat": "bulldozer", "jenis": "oli", "model": "SD16",
         "part_name": "Oil filter", "part_number": "175-49-11580",
         "cross_reference": ["LF9009"]},
    ])
    # dtc_codes via monkeypatch modul (loader beda pola)
    import app.services.dtc_codes as dtc
    monkeypatch.setattr(dtc, "rows", lambda sumber="": [
        {"sumber": "bosch", "unit": "EMS", "kode": "P0087", "spn": 94, "fmi": 1,
         "label": "Tekanan rail rendah", "part": "common rail WG1034121181"},
    ])
    monkeypatch.setattr(dtc, "units", lambda: ["EMS"])
    # media wiring: menyebut model SD16 (entitas model lintas store)
    (data / "wiring").mkdir()
    (data / "wiring" / "index.json").write_text(json.dumps([
        {"file": "cts.jpg", "grp": "sensor", "label": "Sensor coolant SD16"},
    ]), encoding="utf-8")
    # manual_teks menyebut P0087 → dtc lintas-store
    write_json_gz(svc / "manual_teks.json.gz", [
        {"sumber": "bosch_cn", "halaman": 12, "judul": "P0087 rail pressure",
         "judul_id": "Tekanan rail rendah P0087", "kata_kunci": ["rail"],
         "dicari": True},
    ])
    return tmp_path


def _fresh_load():
    # loader cache per-mtime — file baru ditulis di test yang sama; paksa baca ulang
    from app.services import knowledge_util
    knowledge_util._LOAD_CACHE.clear()


def test_build_entitas_lintas_store(dunia):
    d = kl.build()
    ents = d["entitas"]
    # PN filter oli disebut jadwal + filter (2 store) → tersimpan
    assert "pn:1754911580" in ents
    stores = {p["store"] for p in ents["pn:1754911580"]}
    assert {"jadwal_perawatan", "filter_shantui"} <= stores
    # model SD16 disebut jadwal + filter + wiring → tersimpan
    assert "model:SD16" in ents
    # DTC P0087 disebut dtc_codes + manual_teks → tersimpan
    assert "dtc:P0087" in ents
    stores_dtc = {p["store"] for p in ents["dtc:P0087"]}
    assert {"dtc_codes", "manual_teks"} <= stores_dtc


def test_entitas_satu_store_dibuang(dunia):
    d = kl.build()
    # PN common-rail hanya disebut dtc_codes (1 store) → TIDAK tersimpan
    assert "pn:WG1034121181" not in d["entitas"]
    # cross-ref LF9009 hanya di filter (1 store) → tidak tersimpan... KECUALI
    # ikut PN utama? LF9009 pendek (6 char) tapi cuma 1 store → buang
    assert "pn:LF9009" not in d["entitas"]


def test_build_byte_stable(dunia):
    kl.build_and_save()
    b1 = kl._path().read_bytes()
    _fresh_load()
    kl.build_and_save()
    assert kl._path().read_bytes() == b1


def test_entitas_dari_teks_dan_kriteria(dunia):
    kl.build_and_save()
    _fresh_load()
    ents = kl.entitas(teks="unit SD16 kode P0087 dan SPN 94 FMI 1")
    assert "model:SD16" in ents and "dtc:P0087" in ents
    assert "spnfmi:94:1" in ents
    # word boundary: SD1600X bukan SD16
    assert "model:SD16" not in kl.entitas(teks="tipe SD1600X")
    # kriteria eksplisit
    assert kl.entitas(pn="175-49-11580") == ["pn:1754911580"]


def test_terkait_exclude_dedup_cap(dunia):
    kl.build_and_save()
    _fresh_load()
    rows = kl.terkait(["pn:1754911580", "model:SD16"],
                      exclude_store="jadwal_perawatan", limit=3)
    assert rows and all(r["store"] != "jadwal_perawatan" for r in rows)
    assert len(rows) <= 3
    # dedup (store, ref)
    refs = [(r["store"], r["ref"]) for r in rows]
    assert len(refs) == len(set(refs))


def test_epc_edges_menyebut_cakupan_parsial(dunia):
    """Edges EPC lahir dari cache yang isinya HANYA unit yang kebetulan pernah
    disebut user → daftar modelnya POTONGAN. Judul posting terbawa apa adanya ke
    jawaban model, jadi 'Dipakai di: A, B' polos terbaca sebagai klaim tuntas."""
    from app.services.knowledge_util import write_json_gz
    write_json_gz(dunia / "data" / "ai_belajar" / "part_unit_edges.json.gz",
                  {"1754911580": {"models": ["SD16", "SD22"], "frames_n": 4,
                                  "nama": "Oil filter"},
                   "1754911581": {"models": [], "frames_n": 2, "nama": "Seal"}})
    _fresh_load()
    ents = kl.build()["entitas"]
    p = next(x for x in ents["pn:1754911580"] if x["store"] == "epc_unit")
    assert p["judul"].startswith("Dipakai di: SD16, SD22")
    assert "unit terpantau" in p["judul"]          # cakupan ikut terbawa…
    assert "unit_dari_part" in p["judul"]          # …beserta jalan ke daftar penuh
    assert len(p["judul"]) <= kl._MAX_JUDUL_EPC    # kualifikasi tak terpotong
    # Posting sisi MODEL juga jujur soal asal datanya.
    pm = next(x for x in ents["model:SD16"] if x["store"] == "epc_unit")
    assert "terpantau" in pm["judul"]


def test_judul_store_lain_tetap_dipotong_70(dunia):
    """Plafon longgar khusus epc_unit — store lain tak ikut melar."""
    kl.build()
    assert kl._judul("x" * 200) == "x" * kl._MAX_JUDUL


def test_terkait_kosong_dan_file_absen(tmp_path, monkeypatch):
    class _S:
        data_path = tmp_path

    monkeypatch.setattr(kl, "get_settings", lambda: _S())
    _fresh_load()
    assert kl.terkait(["pn:APAJUGA"]) == []
    assert kl.available() is False and kl.count() == 0
