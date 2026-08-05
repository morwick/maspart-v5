"""Mode TELITI cari_part_di_unit — sisir SEMUA baris part list pohon unit.

Kasus nyata NJ248278: indeks pencarian EPC (home/match/part, t=car & t=global)
TIDAK meliput figure mesin MC — 'ECU' hanya mengembalikan 'ECU bracket', padahal
baris ECU 202V25803-7915 nyata ada di figure 'MC07H High-pressure common rail'
(terbukti reverse by-PN & sisiran pohon). Mode teliti menutup kelas luput ini.
"""
import pytest

from app.services import ai_assistant as A
from app.services import epc_bom as E

ADMIN = {"username": "mas", "role": "admin"}
FRAME = "NJ248278"

# Referensi ASLI (diambil saat import, sebelum fixture conftest mem-patch-nya) —
# untuk menguji perilaku items_index_ready sungguhan terhadap cache disk.
_REAL_READY = E.items_index_ready

ECU_ROW = {"pn": "202V25803-7915", "nama": "ECU", "nama_cn": "ECU", "qty": 1,
           "kata_kunci": "ecu",
           "dari_assembly": {"pn": "080-#0211-0477",
                             "nama": "MC07H High-pressure common rail system"}}


def _patch_handler_deps(monkeypatch, ready=False):
    monkeypatch.setattr(A.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(A.epc_bom, "items_index_ready", lambda r: ready)
    monkeypatch.setattr(A.epc_bom, "warm_items_index", lambda r: None)
    monkeypatch.setattr(A.epc_bom, "reverse_find_in_unit",
                        lambda r, p: pytest.fail("reverse tak boleh dipanggil — "
                                                 "hasil teliti sudah bawa assembly induk"))


# ── service: search_items_in_unit ────────────────────────────────────────────
def test_filter_dan_dedup_baris(monkeypatch):
    rows = [
        {"pn": "202V25803-7915", "nama": "ECU", "nama_cn": "", "qty": 1,
         "dari_assembly": {"pn": "080-X", "nama": "Common rail"}},
        {"pn": "202V25803-7915", "nama": "ECU", "nama_cn": "", "qty": 1,
         "dari_assembly": {"pn": "080-X", "nama": "Common rail"}},   # duplikat persis
        {"pn": "MQ6-03216-8311", "nama": "Hexagon bolt", "nama_cn": "", "qty": 2,
         "dari_assembly": {"pn": "080-X", "nama": "Common rail"}},   # tak cocok
    ]
    monkeypatch.setattr(E, "_all_items",
                        lambda r: {"found": True, "frame_number": FRAME,
                                   "rows": rows, "incomplete": False})

    out = E.search_items_in_unit(FRAME, ["ecu"])

    assert out["found"] is True
    assert [h["pn"] for h in out["hasil"]] == ["202V25803-7915"]
    assert out["hasil"][0]["dari_assembly"]["pn"] == "080-X"


def test_cari_di_nama_cn_dan_pn_juga(monkeypatch):
    rows = [{"pn": "080V10311-6063", "nama": "", "nama_cn": "高压油管", "qty": 1,
             "dari_assembly": {}}]
    monkeypatch.setattr(E, "_all_items",
                        lambda r: {"found": True, "frame_number": FRAME,
                                   "rows": rows, "incomplete": False})

    assert E.search_items_in_unit(FRAME, ["高压油管"])["found"] is True
    assert E.search_items_in_unit(FRAME, ["080V10311"])["found"] is True


def test_ranking_kata_utuh_di_atas_substring_nyerempet(monkeypatch):
    """Match KATA-UTUH ('ecu') harus di atas baris yang cuma nyerempet substring
    ('secure') supaya pemangkasan [:40] tak membuang jawaban tepat."""
    rows = [
        {"pn": "P-SEC", "nama": "Secure bracket", "nama_cn": "", "qty": 1,
         "dari_assembly": {}},                                   # 'ecu' ⊂ 'secure'
        {"pn": "P-ECU", "nama": "ECU", "nama_cn": "", "qty": 1,
         "dari_assembly": {}},                                   # kata utuh
    ]
    monkeypatch.setattr(E, "_all_items",
                        lambda r: {"found": True, "frame_number": FRAME,
                                   "rows": rows, "incomplete": False})

    out = E.search_items_in_unit(FRAME, ["ecu"])

    assert [h["pn"] for h in out["hasil"]] == ["P-ECU", "P-SEC"]


def test_ranking_keyword_spesifik_dan_cakupan(monkeypatch):
    """Baris yang kena keyword SPESIFIK (frasa) + BANYAK keyword naik ke atas."""
    rows = [
        {"pn": "P-PIPE", "nama": "Oil pipe", "nama_cn": "", "qty": 1,
         "dari_assembly": {}},                                   # kena 'pipe' saja
        {"pn": "P-AIR", "nama": "Air pipe assembly", "nama_cn": "", "qty": 1,
         "dari_assembly": {}},                                   # kena 'air pipe' + 'pipe'
    ]
    monkeypatch.setattr(E, "_all_items",
                        lambda r: {"found": True, "frame_number": FRAME,
                                   "rows": rows, "incomplete": False})

    out = E.search_items_in_unit(FRAME, ["air pipe", "pipe"])

    assert out["hasil"][0]["pn"] == "P-AIR"


def test_skor_item_kata_utuh_vs_substring():
    utuh, _ = E._skor_item("ecu module", ["ecu"])
    serempet, _ = E._skor_item("secure bracket", ["ecu"])
    assert utuh > serempet


def test_tekan_generik_buang_bolt_bila_ada_spesifik():
    assert A._tekan_generik(["wheel bolt", "bolt"]) == ["wheel bolt"]
    # tak ada keyword spesifik → generik dipertahankan (jangan kosongkan)
    assert A._tekan_generik(["bolt"]) == ["bolt"]
    # keyword China dianggap spesifik
    assert "bolt" not in A._tekan_generik(["车轮螺栓", "bolt"])


def test_all_items_pakai_cache_per_frame(monkeypatch, tmp_path):
    """Walk & buka node hanya SEKALI per frame per TTL — pencarian berikutnya instan."""
    frame = "TT999901"
    E._items_all_cache.pop(frame, None)
    _fake_disk(monkeypatch, tmp_path)   # jangan menulis/membaca data/ asli
    opened = {"n": 0}
    monkeypatch.setattr(E, "_walk_all_nodes",
                        lambda r: {"found": True, "frame_number": frame, "root_id": 1,
                                   "nodes": [{"id": 7, "part_list_id": 70, "code": "X",
                                              "nama": "Node", "nama_cn": "", "leaf": True}],
                                   "incomplete": False})

    def _items(fr, rid, plid, nid, code, errbox):
        opened["n"] += 1
        return [{"code": "202V25803-7915", "name": "ECU", "originalName": "", "amount": 1}]

    monkeypatch.setattr(E, "_atlas_items", _items)

    a = E._all_items(frame)
    b = E._all_items(frame)

    assert opened["n"] == 1                       # kedua kalinya dari cache
    assert a["rows"][0]["pn"] == b["rows"][0]["pn"] == "202V25803-7915"
    E._items_all_cache.pop(frame, None)


# ── handler: eskalasi otomatis & mode eksplisit ──────────────────────────────
def test_match_nihil_otomatis_eskalasi_ke_teliti(monkeypatch):
    _patch_handler_deps(monkeypatch)
    monkeypatch.setattr(A.epc_bom, "search_in_unit",
                        lambda r, k: {"found": False, "frame_number": FRAME, "hasil": []})
    monkeypatch.setattr(A.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": True, "frame_number": FRAME,
                                      "hasil": [ECU_ROW], "incomplete": False})

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "ecu"}, ADMIN)

    assert out["found"] is True
    assert "teliti" in out["mode"] and "otomatis" in out["mode"]
    assert out["parts"][0]["part_number"] == "202V25803-7915"
    assert out["parts"][0]["di_dalam_assembly"] == "MC07H High-pressure common rail system"


def test_teliti_eksplisit_langsung_sisir_tanpa_match(monkeypatch):
    _patch_handler_deps(monkeypatch)
    monkeypatch.setattr(A.epc_bom, "search_in_unit",
                        lambda r, k: pytest.fail("match tak boleh dipanggil saat teliti=true"))
    monkeypatch.setattr(A.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": True, "frame_number": FRAME,
                                      "hasil": [ECU_ROW], "incomplete": False})

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "ecu", "teliti": True}, ADMIN)

    assert out["found"] is True and "teliti" in out["mode"]


def test_mode_cepat_membawa_catatan_cakupan(monkeypatch):
    """Hasil cepat ADA tapi bisa saja bukan part yang diminta (kasus bracket) —
    model diberi tahu cara eskalasi, dan sisiran mahal TIDAK jalan otomatis
    (hanya DIPANASKAN di latar)."""
    monkeypatch.setattr(A.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(A.epc_bom, "items_index_ready", lambda r: False)
    warmed = []
    monkeypatch.setattr(A.epc_bom, "warm_items_index", lambda r: warmed.append(r))
    monkeypatch.setattr(A.epc_bom, "reverse_find_in_unit", lambda r, p: {"instances": []})
    monkeypatch.setattr(A.epc_bom, "search_in_unit",
                        lambda r, k: {"found": True, "frame_number": FRAME,
                                      "hasil": [{"pn": "082V11640-0287/1",
                                                 "nama": "ECU bracket (E050)",
                                                 "kata_kunci": "ecu"}]})
    monkeypatch.setattr(A.epc_bom, "search_items_in_unit",
                        lambda r, k: pytest.fail("sisiran teliti tak boleh auto saat hasil ada"))

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "ecu"}, ADMIN)

    assert out["found"] is True
    assert "teliti=true" in out["catatan_cakupan"]
    assert warmed == [FRAME]                # indeks lengkap mulai dibangun di latar


def test_eskalasi_teliti_saat_indeks_siap_di_tengah_call(monkeypatch):
    """P8: indeks lengkap SELESAI dibangun selama pencarian cepat → langsung sisir
    lengkap (instan + penuh), tanpa 'catatan_cakupan' & tanpa ronde tool kedua —
    walau hasil cepat sebenarnya ADA (bracket)."""
    monkeypatch.setattr(A.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(A.epc_bom, "warm_items_index", lambda r: None)
    monkeypatch.setattr(A.epc_bom, "reverse_find_in_unit", lambda r, p: {"instances": []})
    ready = {"calls": 0}

    def _ready(r):
        # False saat cek awal, True saat re-cek setelah pencarian cepat (indeks selesai).
        ready["calls"] += 1
        return ready["calls"] > 1

    monkeypatch.setattr(A.epc_bom, "items_index_ready", _ready)
    monkeypatch.setattr(A.epc_bom, "search_in_unit",
                        lambda r, k: {"found": True, "frame_number": FRAME,
                                      "hasil": [{"pn": "082V11640-0287/1",
                                                 "nama": "ECU bracket", "kata_kunci": "ecu"}]})
    called = {"teliti": False}

    def _items(r, k):
        called["teliti"] = True
        return {"found": True, "frame_number": FRAME, "hasil": [ECU_ROW], "incomplete": False}

    monkeypatch.setattr(A.epc_bom, "search_items_in_unit", _items)

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "ecu"}, ADMIN)

    assert out["found"] is True
    assert called["teliti"] is True                    # sisir lengkap dijalankan
    assert "indeks unit sudah siap" in out["mode"]     # label 'instan'
    assert "catatan_cakupan" not in out                # jalur lengkap → tak perlu saran
    assert out["parts"][0]["part_number"] == "202V25803-7915"


def test_indeks_siap_langsung_jalur_lengkap_tanpa_match(monkeypatch):
    """Indeks unit sudah hangat (RAM/disk) → SATU ronde: langsung sisiran lengkap,
    indeks cepat yang bolong dilewati sama sekali."""
    _patch_handler_deps(monkeypatch, ready=True)
    monkeypatch.setattr(A.epc_bom, "search_in_unit",
                        lambda r, k: pytest.fail("match tak boleh dipanggil saat indeks siap"))
    monkeypatch.setattr(A.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": True, "frame_number": FRAME,
                                      "hasil": [ECU_ROW], "incomplete": False})

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "ecu"}, ADMIN)

    assert out["found"] is True
    assert "indeks unit sudah siap" in out["mode"]
    assert "catatan_cakupan" not in out     # jalur lengkap → tak perlu saran eskalasi


# ── lapisan DISK: sekali sisir per unit, tahan restart/redeploy ──────────────
def _fake_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(E, "_items_disk_path", lambda fr: tmp_path / f"{fr}.json")


def test_build_lengkap_dipersist_dan_dibaca_tanpa_walk(monkeypatch, tmp_path):
    frame = "TT999902"
    E._items_all_cache.pop(frame, None)
    _fake_disk(monkeypatch, tmp_path)
    monkeypatch.setattr(E, "_walk_all_nodes",
                        lambda r: {"found": True, "frame_number": frame, "root_id": 1,
                                   "nodes": [{"id": 7, "part_list_id": 70, "code": "X",
                                              "nama": "Node", "nama_cn": "", "leaf": True}],
                                   "incomplete": False})
    monkeypatch.setattr(E, "_atlas_items", lambda *a: [
        {"code": "202V25803-7915", "name": "ECU", "originalName": "", "amount": 1}])

    E._all_items(frame)
    assert (tmp_path / f"{frame}.json").exists()

    # Proses "baru": RAM kosong; walk DILARANG — harus terbaca dari disk.
    E._items_all_cache.pop(frame, None)
    monkeypatch.setattr(E, "_walk_all_nodes",
                        lambda r: pytest.fail("walk tak boleh jalan — indeks ada di disk"))
    monkeypatch.setattr(E, "_frame", lambda r: frame)

    r = E._all_items(frame)
    assert r["found"] is True and r["rows"][0]["pn"] == "202V25803-7915"
    assert _REAL_READY(frame) is True
    E._items_all_cache.pop(frame, None)


def test_build_parsial_dipersist_tapi_belum_siap(monkeypatch, tmp_path):
    """Node ada yang gagal dibuka → indeks PARSIAL: TETAP dipersist (agar restart/
    redeploy tak memicu rebuild 56-84 dtk lagi) TAPI ditandai incomplete, jadi
    items_index_ready = False → jalur teliti-instan tak salah menyimpulkan lengkap."""
    frame = "TT999903"
    E._items_all_cache.pop(frame, None)
    _fake_disk(monkeypatch, tmp_path)
    monkeypatch.setattr(E, "_walk_all_nodes",
                        lambda r: {"found": True, "frame_number": frame, "root_id": 1,
                                   "nodes": [{"id": 7, "part_list_id": 70, "code": "X",
                                              "nama": "Node", "nama_cn": "", "leaf": True}],
                                   "incomplete": True})   # walk tak tuntas
    monkeypatch.setattr(E, "_atlas_items", lambda *a: [
        {"code": "202V25803-7915", "name": "ECU", "originalName": "", "amount": 1}])

    r = E._all_items(frame)

    assert r["incomplete"] is True
    assert (tmp_path / f"{frame}.json").exists()            # parsial kini dipersist
    monkeypatch.setattr(E, "_frame", lambda x: frame)
    assert _REAL_READY(frame) is False                     # parsial ≠ siap
    # Proses "baru": RAM kosong, walk DILARANG rebuild — disk parsial terbaca ulang
    # (menahan rebuild) tapi tetap incomplete.
    E._items_all_cache.pop(frame, None)
    monkeypatch.setattr(E, "_walk_all_nodes",
                        lambda r: pytest.fail("rebuild tak perlu — parsial ada di disk & masih segar"))
    r2 = E._all_items(frame)
    assert r2["found"] is True and r2["incomplete"] is True
    E._items_all_cache.pop(frame, None)


def test_nihil_di_kedua_mode_jujur_dan_tandai_sudah_teliti(monkeypatch):
    _patch_handler_deps(monkeypatch)
    monkeypatch.setattr(A.epc_bom, "search_in_unit",
                        lambda r, k: {"found": False, "frame_number": FRAME, "hasil": []})
    monkeypatch.setattr(A.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": False, "frame_number": FRAME,
                                      "hasil": [], "incomplete": False})

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "flux capacitor"}, ADMIN)

    assert out["found"] is False
    assert out["sudah_mode_teliti"] is True
    assert "JANGAN mengarang" in out["jawaban_wajib"]


def test_error_token_di_mode_teliti_tersampaikan(monkeypatch):
    _patch_handler_deps(monkeypatch)
    monkeypatch.setattr(A.epc_bom, "search_in_unit",
                        lambda r, k: {"found": False, "frame_number": FRAME, "hasil": []})
    monkeypatch.setattr(A.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": False, "_err": "token_expired"})

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "ecu"}, ADMIN)

    assert out.get("_token_issue") is True


# ═══════════════════════════════════════════════════════════════════════
#  TEMA FILTER — element vs assembly (kasus RJ326978, 2026-08-05)
#  Dua kelas kegagalan berbeda yang menghasilkan jawaban salah yang SAMA:
#  (1) di sisiran teliti, ELEMENT kalah skor melawan frasa kanonik
#      ('air filter assembly') sampai terpangkas [:40];
#  (2) pertanyaan filter tak pernah SAMPAI ke sisiran teliti karena indeks
#      cepat match/part mengembalikan hasil (yang isinya housing semua).
# ═══════════════════════════════════════════════════════════════════════
ELEMEN_ROW = {"pn": "WG9525195201/1", "nama": "Main filter element", "nama_cn": "",
              "qty": 1, "kata_kunci": "filter",
              "dari_assembly": {"pn": "WG9525195010/1", "nama": "Air filter assembly"}}


def test_skor_item_boost_elemen_kalahkan_frasa_kanonik():
    """Tanpa boost, ELEMENT (skor 3) kalah telak dari nama ASSEMBLY yang menyerap
    seluruh keyword bertema filter — persis kenapa jawaban RJ326978 isinya housing.
    Panggilan 2-arg lama tetap sah (perilaku default tak berubah)."""
    kws = ["filter", "air filter", "air filter assembly"]
    elemen = "main filter element - flame retardant (mann- hummel)"
    assembly = "air filter assembly"

    assert E._skor_item(elemen, kws)[0] < E._skor_item(assembly, kws)[0]
    assert E._skor_item(elemen, kws, boost_elemen=True)[0] > \
        E._skor_item(assembly, kws, boost_elemen=True)[0]
    # baris lain TIDAK dipenalti — bonusnya hanya menaikkan element
    assert E._skor_item(assembly, kws)[0] == E._skor_item(assembly, kws, boost_elemen=True)[0]


def test_search_items_autoboost_saat_keyword_bertema_filter(monkeypatch):
    rows = [
        {"pn": "WG9525195010/1", "nama": "Air filter assembly", "nama_cn": "", "qty": 1,
         "dari_assembly": {"pn": "WG9525190000", "nama": "Air intake system"}},
        {"pn": "WG9525195201/1", "nama": "Main filter element", "nama_cn": "", "qty": 1,
         "dari_assembly": {"pn": "WG9525195010/1", "nama": "Air filter assembly"}},
    ]
    monkeypatch.setattr(E, "_all_items",
                        lambda r: {"found": True, "frame_number": FRAME,
                                   "rows": rows, "incomplete": False})

    out = E.search_items_in_unit(FRAME, ["filter", "air filter", "air filter assembly"])

    assert [h["pn"] for h in out["hasil"]] == ["WG9525195201/1", "WG9525195010/1"]


def test_tanpa_boost_untuk_tema_selain_filter(monkeypatch):
    """Boost menyala HANYA untuk keyword bertema filter — query lain (mis. 'ecu')
    tak boleh tiba-tiba menjunjung 'heating element'."""
    rows = [
        {"pn": "P-ECU", "nama": "ECU", "nama_cn": "", "qty": 1, "dari_assembly": {}},
        {"pn": "P-HEAT", "nama": "ECU heating element", "nama_cn": "", "qty": 1,
         "dari_assembly": {}},
    ]
    monkeypatch.setattr(E, "_all_items",
                        lambda r: {"found": True, "frame_number": FRAME,
                                   "rows": rows, "incomplete": False})

    out = E.search_items_in_unit(FRAME, ["ecu"])

    assert [h["pn"] for h in out["hasil"]] == ["P-ECU", "P-HEAT"]


def _patch_tema_deps(monkeypatch, cepat):
    monkeypatch.setattr(A.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(A.epc_bom, "reverse_find_in_unit", lambda r, p: {"instances": []})
    monkeypatch.setattr(A, "_expand_query", lambda q: ([q], []))
    monkeypatch.setattr(A, "_umbrella_keywords", lambda q: [])
    monkeypatch.setattr(A.search_log, "record_miss", lambda *a, **k: None)
    monkeypatch.setattr(A.epc_bom, "search_in_unit", cepat)
    monkeypatch.setattr(A.epc_bom, "search_items_in_unit",
                        lambda r, k: {"found": True, "frame_number": FRAME,
                                      "hasil": [ELEMEN_ROW], "incomplete": False})


@pytest.mark.parametrize("kata", ["filter", "saringan solar"])
def test_tema_filter_dipaksa_mode_teliti(monkeypatch, kata):
    """Indeks cepat match/part TIDAK memuat element figure mesin — kalau ia sempat
    menjawab, jawabannya housing semua. Tema filter karena itu MELEWATI jalur cepat."""
    def _terlarang(r, k):
        raise AssertionError("jalur cepat dilarang utk tema filter")

    _patch_tema_deps(monkeypatch, _terlarang)

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": kata}, ADMIN)

    assert out["found"] is True
    assert "teliti" in out["mode"] and "FILTER" in out["mode"]
    assert out["parts"][0]["part_number"] == "WG9525195201/1"


def test_tema_lain_tetap_lewat_jalur_cepat(monkeypatch):
    dipakai = {"cepat": False}

    def _cepat(r, k):
        dipakai["cepat"] = True
        return {"found": True, "frame_number": FRAME,
                "hasil": [{"pn": "082V11640-0287/1", "nama": "ECU bracket",
                           "kata_kunci": "ecu"}]}

    _patch_tema_deps(monkeypatch, _cepat)

    out = A._t_cari_part_di_unit({"rangka": FRAME, "kata_kunci": "ecu"}, ADMIN)

    assert dipakai["cepat"] is True
    assert out["mode"].startswith("cepat")
