"""Tool stok_gudang: daftar part 1 kategori yang READY (stok>0) di SATU gudang.
Sumber per-gudang = part_index.gudang_breakdown (indeks stok multi-gudang in-memory).
Semua akses data di-mock (tanpa Excel/Accurate).
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

_GUDANG = ["01.Jakarta", "02.Pekanbaru", "04.Palembang", "05.Makasar", "23.Medan"]

# Breakdown stok {PN: {gudang: qty}}
_BREAK = {
    "CLUTCH1": {"04.Palembang": 5, "01.Jakarta": 2},
    "CLUTCH2": {"01.Jakarta": 3},                    # tak ada di Palembang
    "CLUTCH3": {"04.Palembang": 1},
    "REMX": {"04.Palembang": 9},                     # bukan hasil pencarian kopling
}

# Katalog: apa pun term-nya, kembalikan 3 part kopling (dedup by PN menangani ulangan).
_ROWS = [
    {"part_number": "CLUTCH1", "part_name": "Clutch Driven Disc", "file": "NX360.xlsx", "stok": "7", "harga": "Rp 100"},
    {"part_number": "CLUTCH2", "part_name": "Clutch Pressure Plate", "file": "NX360.xlsx", "stok": "3", "harga": "Rp 200"},
    {"part_number": "CLUTCH3", "part_name": "Clutch Release Bearing", "file": "HOWO.xlsx", "stok": "1", "harga": ""},
]


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _SIN)
    monkeypatch.setattr(ai.part_index, "gudang_names", lambda: list(_GUDANG))
    monkeypatch.setattr(ai.part_index, "gudang_breakdown", lambda pn: dict(_BREAK.get((pn or "").upper(), {})))
    monkeypatch.setattr(ai.part_index, "search_part_name", lambda t: list(_ROWS))
    monkeypatch.setattr(ai.part_index, "search_part_number", lambda t: [])
    monkeypatch.setattr(ai.part_index, "name_for", lambda pn: "")
    monkeypatch.setattr(ai.accurate, "search_index", lambda terms, limit=8: [])


# ── resolusi gudang ──────────────────────────────────────────────────────────
def test_resolve_gudang(monkeypatch):
    monkeypatch.setattr(ai.part_index, "gudang_names", lambda: list(_GUDANG))
    assert ai._resolve_gudang("palembang") == "04.Palembang"
    assert ai._resolve_gudang("PALEMBANG") == "04.Palembang"
    assert ai._resolve_gudang("jakarta") == "01.Jakarta"
    assert ai._resolve_gudang("makasar") == "05.Makasar"
    assert ai._resolve_gudang("gudang palembang") == "04.Palembang"
    assert ai._resolve_gudang("kota antah") is None
    assert ai._resolve_gudang("") is None


# ── umbrella kategori ────────────────────────────────────────────────────────
def test_umbrella_kopling(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _SIN)
    kws = ai._umbrella_keywords("kopling")
    # Ketiga grup kopling ikut (trigger/keyword memuat 'kopling'/'clutch')…
    assert "driven disc" in kws and "pressure plate" in kws and "release bearing" in kws
    # …tapi grup NON-kopling (saringan solar) TIDAK ikut.
    assert "fuel filter" not in kws


def test_umbrella_bukan_kategori(monkeypatch):
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: _SIN)
    # 'baut roda' bukan kata payung → tak ada tambahan dari umbrella.
    assert ai._umbrella_keywords("baut roda") == []


# ── handler: filter part berstok di gudang ───────────────────────────────────
def test_stok_gudang_palembang(patched):
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "palembang"}, ADMIN)
    assert r["found"] and r["gudang"] == "04.Palembang"
    pns = [x["part_number"] for x in r["ditampilkan"]]
    # Hanya part kopling yg berstok di Palembang (CLUTCH1, CLUTCH3); CLUTCH2 (cuma
    # Jakarta) dibuang; REMX (bukan hasil pencarian kopling) tak muncul.
    assert pns == ["CLUTCH1", "CLUTCH3"]              # urut stok menurun (5, 1)
    assert r["ditampilkan"][0]["stok_di_gudang"] == 5
    assert r["jumlah_part_ready"] == 2


def test_stok_gudang_kosong(patched):
    # Gudang tanpa stok kopling → daftar kosong + catatan jujur.
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "medan"}, ADMIN)
    assert r["found"] and r["gudang"] == "23.Medan"
    assert r["jumlah_part_ready"] == 0 and r["ditampilkan"] == []
    assert "gudang lain" in r["catatan"].lower()


def test_gudang_tak_dikenal(patched):
    r = ai._t_stok_gudang({"kata_kunci": "kopling", "gudang": "singapura"}, ADMIN)
    assert not r["found"] and "gudang_tersedia" in r
    assert "Palembang" in r["gudang_tersedia"]


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
