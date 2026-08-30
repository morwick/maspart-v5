"""cari_part_di_unit — jaring ke-2 (cocok per TOKEN) & jaring ke-3 (BOM mesin
Weichai) saat frasa di Atlas Sinotruk nihil.

Kenapa ada (audit ai_chat_log 2026-08-30, giliran EPC per-VIN 8 ronde /
100-220 dtk / 450-600k token):
 • "valve ac", "housing AC", "kompressor ac", "handle luar pintu" → frasa utuh
   tak pernah cocok nama EPC ("Expansion valve (air conditioner)", "Outer door
   handle") → model meraba bom_dari_rangka/uraikan_assembly berulang.
 • "Tensioner RJ371375" 3 giliran (akhirnya ketemu di EPC Weichai), "metering
   unit" 6 giliran / 3 user semuanya nihil: Atlas berhenti di engine assembly,
   part mesin hidup di BOM Weichai yang tool ini dulu tak sentuh.

Sifat yang dijaga:
 1. Token hanya jalan saat frasa NIHIL — peringkat hasil frasa tak bergeser.
 2. SEMUA token wajib harus terwakili (alias id/en/cn); kata posisi = bonus.
 3. Fallback Weichai memakai bentuk hasil yang sama ('parts') + sumber_dipakai.
 4. GAGAL-CEK Weichai ≠ TIDAK-ADA: status dibedakan, miss tak dicatat.
"""
import pytest

from app.services import ai_assistant as ai, epc_bom as E

ADMIN = {"username": "mas", "role": "admin"}
VIN = "LZZ5DLSF2PN036136"

ROWS = [
    {"pn": "WG1642330061", "nama": "Right door glass assembly", "nama_cn": "右车门玻璃总成",
     "qty": 1, "dari_assembly": {"pn": "WG16R", "nama": "Right door"}},
    {"pn": "WG1642330060", "nama": "Left door glass assembly", "nama_cn": "左车门玻璃总成",
     "qty": 1, "dari_assembly": {"pn": "WG16L", "nama": "Left door"}},
    {"pn": "YZ167182300169", "nama": "Expansion valve", "nama_cn": "空调膨胀阀",
     "qty": 1, "dari_assembly": {"pn": "AC1", "nama": "Air conditioner assembly"}},
    {"pn": "WG1642340027", "nama": "Outer handle", "nama_cn": "外把手",
     "qty": 2, "dari_assembly": {"pn": "WG16L", "nama": "Left door"}},
    {"pn": "MQ6-03216", "nama": "Hexagon bolt", "nama_cn": "六角螺栓",
     "qty": 8, "dari_assembly": {"pn": "AC1", "nama": "Air conditioner assembly"}},
]


@pytest.fixture
def pohon(monkeypatch):
    monkeypatch.setattr(E, "_all_items", lambda r, _paksa=False: {
        "found": True, "frame_number": "PN036136", "rows": ROWS, "incomplete": False})


# ── service: search_items_in_unit ────────────────────────────────────────────

def test_frasa_nihil_jatuh_ke_token(pohon):
    r = E.search_items_in_unit(VIN, ["valve ac"])
    assert r["found"] and r["mode"] == "token"
    assert [h["pn"] for h in r["hasil"]] == ["YZ167182300169"]
    assert r["hasil"][0]["kata_kunci"] == "valve ac"


def test_token_kata_posisi_jadi_bonus(pohon):
    r = E.search_items_in_unit(VIN, ["kaca pintu kiri"])
    assert r["mode"] == "token"
    assert [h["pn"] for h in r["hasil"]] == ["WG1642330060", "WG1642330061"]   # kiri dulu


def test_token_alias_posisi_luar(pohon):
    r = E.search_items_in_unit(VIN, ["handle luar pintu"])
    # 'pintu' (door) ada di nama assembly, bukan nama part → token 'pintu' wajib
    # tak terwakili di baris ini; 'handle luar' saja yang cocok.
    assert r["found"] is False
    r = E.search_items_in_unit(VIN, ["handle luar"])
    assert r["mode"] == "token" and r["hasil"][0]["pn"] == "WG1642340027"


def test_frasa_ada_token_tak_dijalankan(pohon):
    r = E.search_items_in_unit(VIN, ["door glass"])
    assert r["mode"] == "frasa" and len(r["hasil"]) == 2


def test_semua_token_wajib_harus_kena(pohon):
    assert E.search_items_in_unit(VIN, ["valve rem"])["found"] is False
    # satu kata = jalur frasa saja (alias token TIDAK dipakai): 'kompresor' polos
    # tak ketemu walau aliasnya 'compressor' — token hanya utk keyword multi-kata
    assert E.search_items_in_unit(VIN, ["kompresor"])["found"] is False


def test_token_query_stopword_dan_alias():
    tq = E._token_query(["metering unit", "valve", "untuk yang"])
    assert len(tq) == 1
    k, wajib, ops = tq[0]
    assert k == "metering unit" and wajib == [("metering", "metering", "measuring", "计量")] and ops == []


# ── tool: _t_cari_part_di_unit ───────────────────────────────────────────────

@pytest.fixture
def alat(monkeypatch, pohon):
    monkeypatch.setattr(ai.epc_bom, "items_index_ready", lambda r: True)
    monkeypatch.setattr(ai.epc_bom, "warm_items_index", lambda r: None)
    monkeypatch.setattr(ai.part_index, "rows_for_pns", lambda pns: {})
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q], []))
    miss = []
    monkeypatch.setattr(ai.search_log, "record_miss", lambda *a, **k: miss.append(a))
    return miss


def test_tool_cocok_per_token_diberi_catatan(alat):
    r = ai._t_cari_part_di_unit({"rangka": VIN, "kata_kunci": "valve ac"}, ADMIN)
    assert r["found"] and r["cocok_per_token"] is True
    assert r["parts"][0]["part_number"] == "YZ167182300169"
    assert "per KATA" in r["catatan_token"]


def _weichai(monkeypatch, res):
    monkeypatch.setattr(ai.epc_weichai, "find_parts", lambda rangka, terms: res)


def test_fallback_weichai_ketemu(alat, monkeypatch):
    _weichai(monkeypatch, {"found": True, "engine": {"nama": "WP10.380E22", "nomor_mesin": "1624G039996"},
                           "hasil": [{"pn": "1003081467", "nama": "Oil Pressure Sensor", "group": "Engine Block"},
                                     {"pn": "612600090",  "nama": "Sensor seat", "group": "Engine Block",
                                      "keterangan": "komponen di dalam part di atas"}]})
    r = ai._t_cari_part_di_unit({"rangka": VIN, "kata_kunci": "sensor tekanan oli"}, ADMIN)
    assert r["found"] and r["sumber_dipakai"] == "mesin_weichai"
    assert r["parts"][0]["part_number"] == "1003081467"
    assert r["parts"][0]["di_dalam_assembly"] == "Engine Block"
    assert r["parts"][1]["keterangan"].startswith("komponen")
    assert r["mesin"]["model_mesin"] == "WP10.380E22"
    assert "Weichai" in r["catatan"] and alat == []          # bukan miss kamus


def test_fallback_weichai_gagal_cek_bukan_tidak_ada(alat, monkeypatch):
    _weichai(monkeypatch, {"found": False, "reason": "network"})
    r = ai._t_cari_part_di_unit({"rangka": VIN, "kata_kunci": "metering unit"}, ADMIN)
    assert r["found"] is False and r["mesin_weichai"] == "gagal_cek"
    assert "BELUM BISA DICEK" in r["error"] and "GAGAL dicek" in r["jawaban_wajib"]
    assert alat == []                                        # gagal-cek ≠ celah kamus


def test_fallback_weichai_nihil_jujur(alat, monkeypatch):
    _weichai(monkeypatch, {"found": True, "engine": {"nama": "WP10"}, "hasil": []})
    r = ai._t_cari_part_di_unit({"rangka": VIN, "kata_kunci": "metering unit"}, ADMIN)
    assert r["found"] is False and r["mesin_weichai"] == "nihil"
    assert "DAN BOM mesin Weichai" in r["jawaban_wajib"]
    assert len(alat) == 1                                    # kedua sisi nihil → miss dicatat


def test_fallback_bukan_weichai(alat, monkeypatch):
    _weichai(monkeypatch, {"found": False, "reason": "no_link", "message": "bukan Weichai"})
    r = ai._t_cari_part_di_unit({"rangka": VIN, "kata_kunci": "metering unit"}, ADMIN)
    assert r["mesin_weichai"] == "bukan_weichai" and "bukan bermesin Weichai" in r["error"]


def test_fallback_weichai_meledak_dianggap_gagal_cek(alat, monkeypatch):
    def _boom(rangka, terms):
        raise RuntimeError("SSO putus")
    monkeypatch.setattr(ai.epc_weichai, "find_parts", _boom)
    r = ai._t_cari_part_di_unit({"rangka": VIN, "kata_kunci": "metering unit"}, ADMIN)
    assert r["mesin_weichai"] == "gagal_cek"
