"""Tool `cek_massal_part_mesin` (2026-07-23): cek SATU part (starter) di BANYAK
nomor mesin Weichai sekaligus — versi massal part_dari_mesin. Deteksi supersession
(pn_order_terkini), silang stok, Excel opsional. Service di-MOCK (tanpa jaringan)."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}

# 3 mesin: 2 pakai PN lama 13031962, 1 pakai PN baru 1019578238; 1 tak resolve.
_PER = {
    "4P24B000713": {"found": True, "model": "WP4G130E22", "dhhNumber": "D1",
                    "hits": [{"pn": "13031962", "nama": "Starter Motor", "group": "Starter Motor Group"}]},
    "4P25G002767": {"found": True, "model": "WP4G130E22", "dhhNumber": "D1",
                    "hits": [{"pn": "13031962", "nama": "Starter Motor", "group": "Starter Motor Group"}]},
    "4P25H003744": {"found": True, "model": "WP4G130E22", "dhhNumber": "D2",
                    "hits": [{"pn": "1019578238", "nama": "Starter Motor", "group": "Starter Motor Group"}]},
    "4PZZZ999999": {"found": False, "reason": "no_order"},
}


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "find_part_massal",
                        lambda nos, terms: {"per_engine": _PER, "orders_unik": 2,
                                            "pn_unik": ["1019578238", "13031962"]})
    # supersession: 13031962 → 1019578238 (2025-07-01); 1019578238 = terkini
    def _replace(pn, rangka=""):
        if pn == "13031962":
            return {"found": True, "digantikan_oleh": [
                {"pn": "13066058", "tanggal": "2018-04-01"},
                {"pn": "1019578238", "tanggal": "2025-07-01"}]}
        return {"found": True, "digantikan_oleh": []}
    monkeypatch.setattr(ai.epc_weichai, "replace_part", _replace)
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "1019578238": {"part_number": "1019578238", "part_name": "Starter Motor",
                       "stok": "2", "harga": "Rp 4.500.000"}})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "starter"], []))
    return {}


def test_cek_massal_starter_supersession(dunia):
    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000713", "4P25G002767", "4P25H003744", "4PZZZ999999"],
         "part": "starter"}, ADMIN)
    assert r["found"] and r["jumlah_mesin"] == 4
    assert r["ketemu"] == 3 and r["tak_ada"] == 1
    assert r["konfigurasi_unik"] == 2
    by_no = {h["no_mesin"]: h for h in r["hasil"]}
    # PN lama → order terkini 1019578238
    assert by_no["4P24B000713"]["pn_epc"] == "13031962"
    assert by_no["4P24B000713"]["pn_order_terkini"] == "1019578238"
    assert "digantikan" in by_no["4P24B000713"]["catatan_pn"]
    # PN sudah terkini → tanpa pn_order_terkini
    assert "pn_order_terkini" not in by_no["4P25H003744"]
    # stok dari PN order terkini
    assert by_no["4P24B000713"]["stok_lokal"] == "2"
    # mesin tak resolve
    assert by_no["4PZZZ999999"]["found"] is False


def test_string_pemisah(dunia):
    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": "4P24B000713, 4P25H003744", "part": "starter"}, ADMIN)
    assert r["jumlah_mesin"] == 2


def test_excel_dibuat(dunia, monkeypatch):
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda judul, kolom, baris: ("EXP1", "cek_starter.xlsx"))
    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000713", "4P25H003744"], "part": "starter",
         "excel": True}, ADMIN)
    assert r["export_id"] == "EXP1" and r["jumlah_baris"] == 2
    assert "Excel siap" in r["catatan"]


def test_harga_disembunyikan_dari_staf(dunia):
    r = ai._run_tool("cek_massal_part_mesin",
                     {"daftar_no_mesin": ["4P24B000713"], "part": "starter"}, STAF)
    assert all("harga_lokal" not in h for h in r["hasil"])


def test_butuh_daftar_dan_part(dunia):
    assert "error" in ai._t_cek_massal_part_mesin({"part": "starter"}, ADMIN)
    assert "PART" in ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000713"]}, ADMIN)["error"]


def test_sesi_kosong_jujur(monkeypatch):
    monkeypatch.setattr(ai.epc_weichai, "find_part_massal",
                        lambda nos, terms: {"_err": "no_session"})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    r = ai._t_cek_massal_part_mesin(
        {"daftar_no_mesin": ["4P24B000713"], "part": "starter"}, ADMIN)
    assert r["found"] is False and "Weichai" in r["error"]


def test_terdaftar_dispatch_spec():
    assert ai._DISPATCH["cek_massal_part_mesin"] is ai._t_cek_massal_part_mesin
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert "cek_massal_part_mesin" in names
