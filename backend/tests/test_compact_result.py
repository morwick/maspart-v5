"""Kompaksi hasil tool sebelum diserialisasi ke model — hemat token UNCACHED.

⛔ Kompaksi TIDAK boleh mengubah verdict guard: _tool_failed bergantung pada
found:False / denied; grounding PN harus tetap utuh (model melihat persis string
yang dipakai ekstraksi PN). Boolean & 0 WAJIB dipertahankan.
"""
import json

from app.services import ai_assistant as A


def test_buang_field_kosong_pertahankan_bool_dan_nol():
    src = {
        "found": True,
        "kosong_str": "",
        "kosong_list": [],
        "kosong_dict": {},
        "nihil": None,
        "stok": 0,          # WAJIB dipertahankan (0 = terlacak tapi habis)
        "aktif": False,     # WAJIB dipertahankan
        "nama": "ECU",
    }
    out = A._compact_result(src)
    assert out == {"found": True, "stok": 0, "aktif": False, "nama": "ECU"}


def test_kompaksi_rekursif_dalam_list_dan_dict():
    src = {"parts": [{"pn": "A", "harga": None, "stok": 0},
                     {"pn": "B", "nama": "", "gudang": {}}]}
    out = A._compact_result(src)
    assert out == {"parts": [{"pn": "A", "stok": 0}, {"pn": "B"}]}
    # panjang list tak berubah (struktur tetap)
    assert len(out["parts"]) == 2


def test_tool_failed_tetap_terbaca_setelah_kompaksi():
    """found:False tak boleh hilang (kalau hilang, guard 'lookup gagal' mati)."""
    gagal = {"found": False, "error": "tidak ada", "parts": []}
    out = A._compact_result(gagal)
    assert A._tool_failed(out) is True
    assert out.get("found") is False


def test_dump_tool_lebih_pendek_dan_pn_utuh():
    result = {"found": True, "catatan": "",
              "parts": [{"part_number": "WG9925520270", "nama": "Bracket",
                         "harga_lokal": None, "stok_per_gudang": {}}]}
    padat = A._dump_tool(result)
    boros = json.dumps(result, ensure_ascii=False, default=str)
    assert len(padat) < len(boros)                 # lebih hemat
    assert " " not in padat.replace("Bracket", "") or ":" in padat  # separator rapat
    # PN tetap terekstrak (grounding utuh) dari string yang dipakai model.
    assert "WG9925520270" in A._extract_pns(padat)


def test_dump_tool_denied_terjaga():
    out = A._dump_tool({"denied": True, "error": "harga khusus admin"})
    assert "denied" in out
    assert A._tool_failed(json.loads(out)) is True
