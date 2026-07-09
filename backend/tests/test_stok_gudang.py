"""Tool stok_gudang: daftar part 1 kategori yang READY (stok>0) di SATU gudang.
Sumber per-gudang = INDEKS Accurate (accurate.gudang_breakdown; enrichment 5-jam).
Nama gudang dari gudang_config. Semua akses data di-mock (tanpa Excel/HTTP).
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}
CABANG = {"username": "jakarta", "role": "user"}
PEMBELI = {"username": "beli", "role": "pembeli"}

# Grup sinonim minimal (keluarga KOPLING) untuk umbrella deterministik.
_SIN = [
    {"grup": "kampas kopling", "triggers": ["kampas kopling", "plat kopling"],
     "keywords": ["driven disc", "clutch driven disc"]},
    {"grup": "matahari kopling", "triggers": ["matahari kopling", "dekrup"],
     "keywords": ["pressure plate", "clutch pressure plate"]},
    {"grup": "release bearing", "triggers": ["drek laher", "release bearing"],
     "keywords": ["release bearing", "clutch release bearing"]},
    {"grup": "saringan solar", "triggers": ["saringan solar"],
     "keywords": ["fuel filter"]},  # NON-kopling → tak boleh ikut umbrella 'kopling'
]

# Gudang KANONIK dari config (coords_map: nama -> koordinat).
_COORDS = {"01.Jakarta": (0, 0), "02.Pekanbaru": (0, 0), "04.Palembang": (0, 0),
           "05.Makasar": (0, 0), "23.Medan": (0, 0)}

# Rincian per-gudang dari INDEKS Accurate {PN: {gudang: qty}} (float → uji _acc_qty).
_BREAK = {
    "CLUTCH1": {"04.Palembang": 5.0, "01.Jakarta": 2.0},
    "CLUTCH2": {"01.Jakarta": 3.0},                   # tak ada di Palembang
    "CLUTCH3": {"04.Palembang": 1.0},
}

# Kandidat dari INDEKS Accurate (jalur cepat no-unit): item {pn, name, price}.
_ITEMS = [
    {"pn": "CLUTCH1", "name": "Clutch Driven Disc", "price": 100000.0},
    {"pn": "CLUTCH2", "name": "Clutch Pressure Plate", "price": 200000.0},
    {"pn": "CLUTCH3", "name": "Clutch Release Bearing", "price": 0.0},
]
# Katalog (jalur scope unit): baris dgn kolom 'file'.
_ROWS = [
    {"part_number": "CLUTCH1", "part_name": "Clutch Driven Disc", "file": "NX360.xlsx", "harga": "Rp 100"},
    {"part_number": "CLUTCH2", "part_name": "Clutch Pressure Plate", "file": "NX360.xlsx", "harga": "Rp 200"},
    {"part_number": "CLUTCH3", "part_name": "Clutch Release Bearing", "file": "HOWO.xlsx", "harga": ""},
]


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _SIN)
    monkeypatch.setattr(ai.gudang_config, "coords_map", lambda: dict(_COORDS))
    monkeypatch.setattr(ai.accurate, "items_matching", lambda terms, limit=400: list(_ITEMS))
    monkeypatch.setattr(ai.part_index, "search_part_name", lambda t: list(_ROWS))
    monkeypatch.setattr(ai.part_index, "name_for", lambda pn: "")
    monkeypatch.setattr(ai.accurate, "gudang_breakdown",
                        lambda pn: dict(_BREAK.get((pn or "").upper(), {})))
    monkeypatch.setattr(ai.accurate, "gudang_enriched_count", lambda: len(_BREAK))


# ── resolusi gudang (dari config) ────────────────────────────────────────────
def test_resolve_gudang(monkeypatch):
    monkeypatch.setattr(ai.gudang_config, "coords_map", lambda: dict(_COORDS))
    assert ai._resolve_gudang("palembang") == "04.Palembang"
    assert ai._resolve_gudang("PALEMBANG") == "04.Palembang"
    assert ai._resolve_gudang("jakarta") == "01.Jakarta"
    assert ai._resolve_gudang("makasar") == "05.Makasar"
    assert ai._resolve_gudang("gudang palembang") == "04.Palembang"
    assert ai._resolve_gudang("kota antah") is None
    assert ai._resolve_gudang("") is None


def test_acc_qty():
    assert ai._acc_qty(8.0) == 8            # float Accurate → 8 (BUKAN 80)
    assert ai._acc_qty("55") == 55
    assert ai._acc_qty(None) == 0
    assert ai._acc_qty("x") == 0


# ── umbrella kategori ────────────────────────────────────────────────────────
def test_umbrella_kopling(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _SIN)
    kws = ai._umbrella_keywords("kopling")
    assert "driven disc" in kws and "pressure plate" in kws and "release bearing" in kws
    assert "fuel filter" not in kws          # grup NON-kopling tak ikut


def test_umbrella_bukan_kategori(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _SIN)
    assert ai._umbrella_keywords("baut roda") == []


# ── handler: filter part berstok di gudang (via indeks Accurate) ─────────────
def test_stok_gudang_palembang(patched):
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "palembang"}, ADMIN)
    assert r["found"] and r["gudang"] == "04.Palembang"
    pns = [x["part_number"] for x in r["ditampilkan"]]
    # Hanya kopling berstok di Palembang (CLUTCH1=5, CLUTCH3=1); CLUTCH2 (Jakarta) dibuang.
    assert pns == ["CLUTCH1", "CLUTCH3"]
    assert r["ditampilkan"][0]["stok_di_gudang"] == 5      # float 5.0 → 5 (bukan 50)
    assert r["ditampilkan"][0]["stok_total"] == 7          # 5+2 dari breakdown
    assert r["jumlah_part_ready"] == 2


def test_stok_gudang_kosong(patched):
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert r["found"] and r["gudang"] == "23.Medan"
    assert r["jumlah_part_ready"] == 0 and r["ditampilkan"] == []
    assert "gudang lain" in r["catatan"].lower()


def test_indeks_belum_siap(patched, monkeypatch):
    # by_gudang belum terisi (enrichment belum jalan) → jangan salah lapor "tidak ada".
    monkeypatch.setattr(ai.accurate, "gudang_breakdown", lambda pn: {})
    monkeypatch.setattr(ai.accurate, "gudang_enriched_count", lambda: 0)
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert not r["found"] and "disiapkan" in r["error"].lower()


def test_gudang_tak_dikenal(patched):
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "singapura"}, ADMIN)
    assert not r["found"] and "gudang_tersedia" in r
    assert any("palembang" in g.lower() for g in r["gudang_tersedia"])


def test_argumen_kurang(patched):
    assert "error" in ai._t_stok_gudang({"gudang": "palembang"}, ADMIN)   # tanpa kata_kunci
    assert "error" in ai._t_stok_gudang({"kata_kunci": "kopling"}, ADMIN)  # tanpa gudang


def test_scope_unit(patched):
    # unit='HOWO' → hanya part dari file HOWO (CLUTCH3), meski berstok Palembang.
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "palembang", "unit": "HOWO"}, ADMIN)
    assert [x["part_number"] for x in r["ditampilkan"]] == ["CLUTCH3"]


# ── peran ────────────────────────────────────────────────────────────────────
def test_pembeli_ditolak(patched):
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "palembang"}, PEMBELI)
    assert "error" in r and "pembeli" in r["error"].lower()


def test_tool_terdaftar_untuk_non_pembeli():
    assert "stok_gudang" in ai._DISPATCH
    assert "stok_gudang" in ai._allowed_tool_names(ADMIN)
    assert "stok_gudang" in ai._allowed_tool_names(CABANG)
    assert "stok_gudang" not in ai._allowed_tool_names(PEMBELI)
