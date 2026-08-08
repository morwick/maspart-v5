"""Tabel penggantian (supersession) part MESIN Weichai OFFLINE — hasil panen
seluruh endpoint `replace/page` (58.100 record) ke data/weichai_replace.json.gz.

Dua hal yang paling mudah salah dan karena itu dikunci di sini:
 1. satu record bisa memuat BANYAK PN dipisah koma (relasi grup many-to-many) —
    memperlakukannya sbg PN tunggal membuat kita menyarankan pasangan yang salah;
 2. tabel tak ada / belum lengkap ≠ "tidak ada pengganti" — pemanggil WAJIB tetap
    bertanya ke sumber live.
"""
import pytest

from app.services import weichai_replace as wr
from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}

REC = [
    {"oldPartNumber": "90011400002", "newPartNumber": "90011420022",
     "replaceGroup": "", "replacementDate": "2025-07-01",
     "replaceType": "Bidirectional substitution"},
    # relasi GRUP: 2 PN lama ↔ 2 PN baru
    {"oldPartNumber": "1000986091,1001007728", "newPartNumber": "1008645621,1008645622",
     "replaceGroup": "00033257", "replacementDate": "2022-08-01",
     "replaceType": "Bidirectional Replacement | Combination"},
]


@pytest.fixture(autouse=True)
def _pakai_tabel(monkeypatch):
    """Modul ini memang menguji tabelnya → isi cache langsung (tanpa file)."""
    data = {"versi": 1, "total_api": 58100, "lengkap": True,
            "diambil": "2026-08-08T13:00:00Z", "record": REC}
    monkeypatch.setattr(wr, "_load", lambda: data)
    monkeypatch.setitem(wr._CACHE, "data", data)
    monkeypatch.setitem(wr._CACHE, "idx", wr._bangun_indeks(data))


# ── indeks & lookup ─────────────────────────────────────────────────────────
def test_tersedia_dan_status():
    st = wr.status()
    assert st["tersedia"] and st["lengkap"] is True
    assert st["total_api"] == 58100
    assert st["jumlah_record"] == 2


def test_pn_tunggal_arah_maju():
    r = wr.cari("90011400002")
    assert r["found"]
    assert [x["pn"] for x in r["digantikan_oleh"]] == ["90011420022"]
    assert r["menggantikan"] == []
    assert r["digantikan_oleh"][0]["tanggal"] == "2025-07-01"


def test_pn_tunggal_arah_balik():
    r = wr.cari("90011420022")
    assert [x["pn"] for x in r["menggantikan"]] == ["90011400002"]
    assert r["digantikan_oleh"] == []


def test_record_multi_pn_terindeks_untuk_tiap_pn():
    """PN kedua di sel 'oldPartNumber' harus ikut ketemu — bukan hanya yang pertama."""
    r = wr.cari("1001007728")
    assert r["found"]
    assert sorted(x["pn"] for x in r["digantikan_oleh"]) == ["1008645621", "1008645622"]


def test_relasi_grup_dibawa_utuh():
    """Memilih satu PN dari grup 2-ke-2 tanpa menyebut grupnya menyesatkan pembeli."""
    r = wr.cari("1000986091")
    grp = r["digantikan_oleh"][0]["grup"]
    assert grp["lama"] == ["1000986091", "1001007728"]
    assert grp["baru"] == ["1008645621", "1008645622"]


def test_pn_sendiri_tak_disarankan_sbg_pengganti():
    r = wr.cari("1000986091")
    assert "1000986091" not in [x["pn"] for x in r["digantikan_oleh"]]


def test_pn_pemaaf_beda_penulisan():
    assert wr.cari("900114-00002")["found"] is True
    assert wr.cari(" 90011400002 ")["found"] is True


def test_pn_tak_dikenal_bukan_error():
    r = wr.cari("PN-ASING-999")
    assert r["found"] is False and "reason" not in r


def test_input_kosong():
    assert wr.cari("")["reason"] == "input"


def test_tanpa_data_bukan_vonis_nihil(monkeypatch):
    monkeypatch.setitem(wr._CACHE, "idx", {})
    r = wr.cari("90011400002")
    assert r["found"] is False and r["reason"] == "no_data"


# ── integrasi ke pengganti_part ─────────────────────────────────────────────
def _diamkan_sumber_lain(monkeypatch):
    monkeypatch.setattr(ai.sims, "equivalents_count", lambda: 1)
    monkeypatch.setattr(ai.sims, "equivalents_for", lambda pn: {})
    monkeypatch.setattr(ai.sims, "status_jual", lambda pn: None)
    monkeypatch.setattr(ai.part_index, "search_exact_pns", lambda pns: [])


def test_tool_pakai_tabel_offline_dan_lewati_live(monkeypatch):
    """Tabel LENGKAP + PN ketemu → panggilan live Weichai tak perlu dilakukan."""
    _diamkan_sumber_lain(monkeypatch)
    live = []
    monkeypatch.setattr(ai.epc_weichai, "replace_part",
                        lambda pn, rangka: live.append(pn) or {})
    monkeypatch.setattr(ai.weichai_replace, "available", lambda: True)
    r = ai._t_pengganti_part({"part_number": "90011400002"}, ADMIN)
    assert live == []                                  # live TIDAK dipanggil
    assert r["sumber_dicek"]["weichai"] == "dilewati_pakai_tabel_offline"
    assert r["sumber_dicek"]["weichai_offline"] == "ok"
    assert "90011420022" in [x["part_number"] for x in r.get("digantikan_oleh", [])]


def test_tabel_belum_lengkap_tetap_tanya_live(monkeypatch):
    """Panen separuh tak boleh membungkam sumber live — 'tak ada di tabelku'
    bukan 'tak ada'."""
    _diamkan_sumber_lain(monkeypatch)
    data = {"total_api": 58100, "lengkap": False, "record": REC}
    monkeypatch.setattr(wr, "_load", lambda: data)
    monkeypatch.setitem(wr._CACHE, "data", data)
    monkeypatch.setitem(wr._CACHE, "idx", wr._bangun_indeks(data))
    live = []
    monkeypatch.setattr(ai.epc_weichai, "replace_part",
                        lambda pn, rangka: live.append(pn) or {"found": False})
    monkeypatch.setattr(ai.weichai_replace, "available", lambda: True)
    r = ai._t_pengganti_part({"part_number": "90011400002"}, ADMIN)
    assert live == ["90011400002"]                     # live TETAP dipanggil
    assert r["sumber_dicek"]["weichai_offline"] == "ok"


def test_pn_tak_ada_di_tabel_tetap_tanya_live(monkeypatch):
    _diamkan_sumber_lain(monkeypatch)
    live = []
    monkeypatch.setattr(ai.epc_weichai, "replace_part",
                        lambda pn, rangka: live.append(pn) or {"found": False})
    monkeypatch.setattr(ai.weichai_replace, "available", lambda: True)
    ai._t_pengganti_part({"part_number": "PN-ASING-999"}, ADMIN)
    assert live == ["PN-ASING-999"]


def test_tabel_rusak_tak_menjatuhkan_tool(monkeypatch):
    _diamkan_sumber_lain(monkeypatch)
    monkeypatch.setattr(ai.weichai_replace, "available", lambda: True)

    def boom(pn):
        raise RuntimeError("gz rusak")
    monkeypatch.setattr(ai.weichai_replace, "cari", boom)
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {"found": False})
    r = ai._t_pengganti_part({"part_number": "90011400002"}, ADMIN)
    assert "sumber_dicek" in r                          # tetap menjawab, tak melempar


def test_sumber_ditandai_di_hasil(monkeypatch):
    _diamkan_sumber_lain(monkeypatch)
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {})
    monkeypatch.setattr(ai.weichai_replace, "available", lambda: True)
    r = ai._t_pengganti_part({"part_number": "90011400002"}, ADMIN)
    sumber = {x.get("sumber") for x in r.get("digantikan_oleh", [])}
    assert sumber == {"Weichai (tabel offline)"}


def test_relasi_grup_sampai_ke_hasil_tool(monkeypatch):
    """Grup 2-ke-2 harus terbawa sampai payload tool — kalau hilang, satu PN
    tampak seperti pasangan tunggal padahal penggantinya satu SET."""
    _diamkan_sumber_lain(monkeypatch)
    monkeypatch.setattr(ai.epc_weichai, "replace_part", lambda pn, rangka: {})
    monkeypatch.setattr(ai.weichai_replace, "available", lambda: True)
    r = ai._t_pengganti_part({"part_number": "1000986091"}, ADMIN)
    grup = [x.get("grup") for x in r.get("digantikan_oleh", []) if x.get("grup")]
    assert grup and grup[0]["lama"] == ["1000986091", "1001007728"]


# ── pemecah PN & normalisasi ────────────────────────────────────────────────
def test_pecah_sel_multi_pn():
    assert wr._pns("A,B ; C") == ["A", "B", "C"]
    assert wr._pns("") == []
    assert wr._pns(None) == []


def test_norm_pn():
    assert wr._norm("wg-9725/520 789") == "WG9725520789"
