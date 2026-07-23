"""Tool `cek_massal_part_rangka` (2026-07-23): cek SATU part (injector) di BANYAK
nomor rangka (VIN) — jalur EPC Sinotruk Atlas, pasangan cek_massal_part_mesin.
Deteksi supersession (SIMS), silang stok, Excel. Service di-MOCK (tanpa jaringan)."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}

# 3 VIN: 2 order O1 (injector A), 1 order O2 (injector B); 1 tak resolve.
_PER = {
    "LZZ1BG3H0SJ398963": {"found": True, "order_no": "O1", "frame": "SJ398963",
                          "hits": [{"pn": "080V10100-6088", "nama": "Fuel injector (Ks1.5)"}]},
    "LZZ1BG3H1SJ398969": {"found": True, "order_no": "O1", "frame": "SJ398969",
                          "hits": [{"pn": "080V10100-6088", "nama": "Fuel injector (Ks1.5)"}]},
    "LZZ1BG3H2SJ390489": {"found": True, "order_no": "O2", "frame": "SJ390489",
                          "hits": [{"pn": "080V11100-7777", "nama": "Fuel injector"}]},
    "LZZBADVIN0000000": {"found": False, "_err": "not_found"},
}


@pytest.fixture
def dunia(monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "find_part_massal_rangka",
                        lambda rgs, kws: {"per_rangka": _PER, "order_unik": 2,
                                          "pn_unik": ["080V10100-6088", "080V11100-7777"]})
    # supersession: 080V10100-6088 punya pengganti; 080V11100-7777 tidak.
    def _eq(pn):
        if pn == "080V10100-6088":
            return {"digantikan_oleh": [{"pn": "080V10100-9999", "nama": "Injector baru"}]}
        return {"digantikan_oleh": []}
    monkeypatch.setattr(ai.sims, "equivalents_for", _eq)
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {
        "080V10100-9999": {"part_number": "080V10100-9999", "part_name": "Injector",
                           "stok": "4", "harga": "Rp 2.100.000", "gudang": {}}})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "injector", "nozzle"], []))
    return {}


def test_cek_massal_injector(dunia):
    r = ai._t_cek_massal_part_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ398963", "LZZ1BG3H1SJ398969",
                           "LZZ1BG3H2SJ390489", "LZZBADVIN0000000"],
         "part": "injector"}, ADMIN)
    assert r["found"] and r["jumlah_rangka"] == 4
    assert r["ketemu"] == 3 and r["tak_ada"] == 1 and r["konfigurasi_unik"] == 2
    by = {h["rangka"]: h for h in r["hasil"]}
    assert by["LZZ1BG3H0SJ398963"]["pn_epc"] == "080V10100-6088"
    assert by["LZZ1BG3H0SJ398963"]["pn_order_terkini"] == "080V10100-9999"
    assert by["LZZ1BG3H0SJ398963"]["stok_lokal"] == "4"     # dari PN pengganti
    assert "pn_order_terkini" not in by["LZZ1BG3H2SJ390489"]  # tanpa pengganti
    assert by["LZZBADVIN0000000"]["found"] is False


def test_terjemahan_istilah_id(dunia, monkeypatch):
    dipakai = {}
    monkeypatch.setattr(ai.epc_bom, "find_part_massal_rangka",
                        lambda rgs, kws: dipakai.update(kws=kws) or
                        {"per_rangka": {}, "order_unik": 0, "pn_unik": []})
    monkeypatch.setattr(ai, "_expand_query",
                        lambda q: ([q, "brake friction plate"], ["kampas rem"]))
    ai._t_cek_massal_part_rangka({"daftar_rangka": ["LZZ1BG3H0SJ398963"],
                                  "part": "kampas rem"}, ADMIN)
    assert "brake friction plate" in dipakai["kws"]          # ID→EN diteruskan ke EPC


def test_excel(dunia, monkeypatch):
    monkeypatch.setattr(ai.ai_export, "stash_export",
                        lambda j, k, b: ("EXP2", "cek_injector.xlsx"))
    r = ai._t_cek_massal_part_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ398963", "LZZ1BG3H2SJ390489"],
         "part": "injector", "excel": True}, ADMIN)
    assert r["export_id"] == "EXP2" and r["jumlah_baris"] == 2


def test_harga_disembunyikan_dari_staf(dunia):
    r = ai._run_tool("cek_massal_part_rangka",
                     {"daftar_rangka": ["LZZ1BG3H0SJ398963"], "part": "injector"}, STAF)
    assert all("harga_lokal" not in h for h in r["hasil"])


def test_butuh_daftar_dan_part(dunia):
    assert "error" in ai._t_cek_massal_part_rangka({"part": "injector"}, ADMIN)
    assert "PART" in ai._t_cek_massal_part_rangka(
        {"daftar_rangka": ["LZZ1BG3H0SJ398963"]}, ADMIN)["error"]


def test_terdaftar_dispatch_spec():
    assert ai._DISPATCH["cek_massal_part_rangka"] is ai._t_cek_massal_part_rangka
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert "cek_massal_part_rangka" in names
