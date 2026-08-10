"""EPC Shantui (alat berat) — pohon model/varian, part per subsistem, cari PN,
exploded figure, dan flow 4 tool asisten. Jaringan di-mock (tanpa epc.shantui.com).

⚠️ Batas kelas '_norm': deteksi SE75 vs SE750 (varian vs model beda) diuji khusus.
"""
import pytest

from app.services import ai_assistant as ai
from app.services import epc_shantui as sh

U = {"username": "mas", "role": "admin"}


# ── Data katalog tiruan (bentuk = respons Shantui asli) ──────────────────────
_MODEL_TREE = [
    {"id": 1, "code": "2", "name": "挖掘机", "leaf": True, "children": [
        {"id": 964047, "code": "60070-00-00001", "name": "SE75-9", "leaf": False},
        {"id": 1144295, "code": "60070-00-00004", "name": "SE75-9W1", "leaf": False},
        {"id": 979411, "code": "60075-00-00002", "name": "SE75-9W4", "leaf": False},
        {"id": 514745, "code": "60750-00-00002", "name": "SE750LC-9", "leaf": False},
    ]},
    {"id": 3, "code": "1", "name": "推土机", "leaf": True, "children": [
        {"id": 700001, "code": "16Y-00-00001", "name": "SD22", "leaf": False},
    ]},
]

# top assembly SE75-9W1 (rootId 1144295): satu engine (03) + satu track (45)
_TOP_9W1 = [
    {"id": 111, "code": "60070-03-00038", "name": "发动机安装", "leaf": True},
    {"id": 222, "code": "60070-45-00001", "name": "履带总成安装", "leaf": True},
]
# item figure engine (punya d2s+d3s, PN mesin balon 14)
_ITEM_ENGINE = {"items": [
    {"ballNum": 1, "code": "01011-G1610", "name": "螺栓", "amount": 4, "unit": "个"},
    {"ballNum": 14, "code": "60070-03-00084", "name": "发动机", "amount": 1, "unit": "个"},
], "d2s": ["I00050639_2_L-CREO 2D - 2D.EN.svg"], "d3s": ["I00050639_2.pvz"]}
_ITEM_TRACK = {"items": [
    {"ballNum": 1, "code": "60070-45-00010", "name": "履带板", "amount": 42, "unit": "个"},
], "d2s": ["I00099999_1.EN.svg"], "d3s": []}


def _fake_get(path, params=None, timeout=25.0, _retry=True):
    params = params or {}
    if path == "/product/all":
        return {"data": _MODEL_TREE}
    if path == "/part/tree/module":
        # top-level SE75-9W1
        if str(params.get("rootId")) == "1144295" and str(params.get("partId")) == "1144295":
            return {"data": _TOP_9W1}
        return {"data": []}
    if path == "/part/tree/item":
        pid = str(params.get("partId"))
        if pid == "111":
            return {"data": _ITEM_ENGINE}
        if pid == "222":
            return {"data": _ITEM_TRACK}
        return {"data": {}}
    return {"_err": "api", "message": "unexpected " + path}


def _fake_post(path, body, timeout=25.0, _retry=True):
    if path == "/home/match/part/codeitem":
        if body.get("k") == "60070-03-00084":
            return {"data": [{"code": "60070-03-00084", "name": "发动机",
                              "transName": None, "weight": "268.0", "lwh": ""}]}
        return {"data": []}
    return {"_err": "api"}


@pytest.fixture(autouse=True)
def _mock_net(monkeypatch):
    monkeypatch.setattr(sh, "_get", _fake_get)
    monkeypatch.setattr(sh, "_post", _fake_post)
    monkeypatch.setattr(sh, "_token", lambda: "Bearer x")
    sh._tree_cache.clear()
    sh._asm_cache.clear()
    yield
    sh._tree_cache.clear()
    sh._asm_cache.clear()


# ── varian: SE75 (8-ish) vs SE750 (model beda) ───────────────────────────────
def test_variants_batas_se75_vs_se750():
    v = sh.variants("SE75")
    tipe = [t["tipe"] for t in v["tipe"]]
    assert v["found"] and set(tipe) == {"SE75-9", "SE75-9W1", "SE75-9W4"}
    # SE750LC-9 TIDAK boleh masuk varian SE75 (angka lanjut → model beda)
    serupa = [t["tipe"] for t in v.get("tipe_serupa_beda_model", [])]
    assert "SE750LC-9" in serupa
    assert v.get("catatan")


def test_variants_rootcode_terisi():
    v = sh.variants("SE75-9W1")
    assert v["found"]
    assert v["tipe"][0]["rootCode"] == "60070-00-00004"
    assert v["tipe"][0]["rootId"] == 1144295


def test_variants_tak_ada():
    v = sh.variants("ZZZ999")
    assert v["found"] is False


def test_list_models_per_kategori():
    lm = sh.list_models("excavator")
    assert "SE75-9W1" in lm["tipe"] and "SD22" not in lm["tipe"]


# ── subsistem + part ─────────────────────────────────────────────────────────
def test_subsys_kode():
    assert sh._subsys("60070-03-00038") == "03"
    assert sh._subsys("60070-45-00001") == "45"


def test_top_assemblies_label():
    t = sh.top_assemblies("SE75-9W1")
    assert t["found"] and t["jumlah"] == 2
    labels = {a["subsistem"]: a["subsistem_label"] for a in t["assembly"]}
    assert labels["03"] == "engine mounting" and labels["45"] == "track"


def test_part_list_filter_engine():
    pl = sh.part_list("SE75-9W1", "engine")
    assert pl["found"] and pl["jumlah_figure"] == 1
    f = pl["figures"][0]
    assert f["kode"] == "60070-03-00038" and f["jumlah_part"] == 2
    assert any(it["pn"] == "60070-03-00084" for it in f["items"])


def test_part_list_filter_kode_yy():
    pl = sh.part_list("SE75-9W1", "45")
    assert pl["found"] and pl["figures"][0]["kode"] == "60070-45-00001"


def test_part_list_subsistem_tak_ada():
    pl = sh.part_list("SE75-9W1", "gearbox")
    assert pl["found"] is False


# ── cari PN ──────────────────────────────────────────────────────────────────
def test_find_part_global():
    r = sh.find_part("60070-03-00084")
    assert r["found"] and r["hasil"][0]["nama"] == "发动机"
    assert r["hasil"][0]["berat"] == "268.0"


def test_find_part_kosong():
    assert sh.find_part("TIDAKADA")["found"] is False


# ── exploded ─────────────────────────────────────────────────────────────────
def test_exploded_by_pn_balon():
    d = sh.exploded_figures("SE75-9W1", pn="60070-03-00084")
    assert d["found"]
    f = d["figures"][0]
    assert f["svg"].endswith(".EN.svg") and f["balon"] == 14 and f["pvz_3d"]


def test_exploded_by_subsistem():
    d = sh.exploded_figures("SE75-9W1", subsistem="track")
    assert d["found"] and d["figures"][0]["kode"] == "60070-45-00001"


def test_exploded_pn_tak_ada():
    d = sh.exploded_figures("SE75-9W1", pn="99999-99-99999")
    assert d["found"] is False


# ── token kedaluwarsa → jujur, bukan 'tidak ada' ─────────────────────────────
def test_token_expired_jujur(monkeypatch):
    monkeypatch.setattr(sh, "_get", lambda *a, **k: {"_err": "token_expired"})
    sh._tree_cache.clear()
    v = sh.variants("SE75")
    assert v["found"] is False and v["reason"] == "token_expired" and v["gagal_dicek"]


# ── flow 4 tool asisten ──────────────────────────────────────────────────────
def test_tool_tipe_unit():
    r = ai._DISPATCH["tipe_unit_shantui"]({"model": "SE75"}, U)
    assert r["found"] and r["jumlah_tipe"] == 3
    assert r.get("tipe_serupa_beda_model")


def test_tool_part_shantui_assembly():
    r = ai._DISPATCH["part_shantui"]({"tipe": "SE75-9W1"}, U)
    assert r["found"] and r["jumlah_assembly"] == 2


def test_tool_part_shantui_subsistem():
    r = ai._DISPATCH["part_shantui"]({"tipe": "SE75-9W1", "subsistem": "engine"}, U)
    assert r["found"] and r["jumlah_figure"] == 1


def test_tool_cari_part():
    r = ai._DISPATCH["cari_part_shantui"]({"pn": "60070-03-00084"}, U)
    assert r["found"] and r["hasil"][0]["nama"] == "发动机"


def test_tool_exploded_stash(monkeypatch):
    # stash_builder tak boleh menembak jaringan; cukup pastikan gambar+image_id keluar
    r = ai._DISPATCH["gambar_exploded_shantui"]({"tipe": "SE75-9W1", "subsistem": "engine"}, U)
    assert r["found"] and r["gambar"] and r["gambar"][0]["image_id"]
    assert r["gambar"][0]["nama_figure"] == "发动机安装"
    # projeksi ke model harus buang image_id
    proj = ai._PROJECTIONS["gambar_exploded_shantui"](r)
    assert not any("image_id" in g for g in proj["gambar"])


def test_tool_token_expired_diteruskan(monkeypatch):
    monkeypatch.setattr(sh, "_get", lambda *a, **k: {"_err": "token_expired"})
    sh._tree_cache.clear()
    r = ai._DISPATCH["tipe_unit_shantui"]({"model": "SE75"}, U)
    assert r.get("reason") == "token_expired"


def test_exploded_shantui_di_whitelist_inline():
    """⚠️ regresi: gambar exploded Shantui HARUS masuk _TOOLS_GAMBAR_INLINE, kalau
    tidak PNG di-render tapi TAK PERNAH tampil di chat (bug 'mana gambarnya')."""
    assert "gambar_exploded_shantui" in ai._TOOLS_GAMBAR_INLINE


def test_exploded_shantui_gambar_punya_image_id_dan_kategori():
    r = ai._DISPATCH["gambar_exploded_shantui"]({"tipe": "SE75-9W1", "subsistem": "engine"}, U)
    g = r["gambar"][0]
    # _capture_meta memakai image_id + kategori → keduanya wajib ada
    assert g.get("image_id") and g.get("kategori")
