"""Tool export Excel SERVER-SIDE asisten: excel_bom_rangka & excel_stok_gudang.

Kenapa ada: buat_excel menuntut MODEL menyalin ulang semua baris dari hasil tool —
hasil tool dipangkas (top-N) dan BOM per-rangka bisa 1.500+ part, jadi 'Excel BOM
lengkap dengan stok & harga' mustahil benar lewat jalur itu. Tool ini menarik data
lengkap di server (EPC + indeks Accurate): utuh, nol risiko karangan model.
"""
import pytest

from app.services import ai_assistant as ai


ADMIN = {"username": "admin", "role": "admin"}
STAF = {"username": "budi", "role": "user"}
PEMBELI = {"username": "roni", "role": "pembeli"}

_PARTS = [{"pn": f"WG{i:010d}", "nama_cn": f"零件{i}", "qty": i % 3 + 1} for i in range(120)]


@pytest.fixture
def dunia(monkeypatch):
    """EPC + katalog + indeks Accurate tiruan; tangkap payload stash_export."""
    monkeypatch.setattr(ai.epc_bom, "loading_list",
                        lambda rangka: {"found": True, "frame_number": rangka.upper(),
                                        "jumlah_part": len(_PARTS), "parts": list(_PARTS)})
    monkeypatch.setattr(ai.part_index, "search_exact_pns",
                        lambda pns: [{"part_number": p, "part_name": f"Part {p[-3:]}"} for p in pns])
    monkeypatch.setattr(ai.part_index, "name_for", lambda pn: f"Part {pn[-3:]}")
    monkeypatch.setattr(ai.accurate, "gudang_breakdown",
                        lambda pn: {"01.Jakarta": 2, "02.Pekanbaru": 1})
    monkeypatch.setattr(ai.accurate, "snapshot",
                        lambda: {ai.accurate.norm_pn(p["pn"]): {"stok": 3, "harga": 50_000}
                                 for p in _PARTS})
    monkeypatch.setattr(ai.accurate, "gudang_enriched_count", lambda: 99)
    captured = {}

    def _stash(judul, kolom, baris):
        captured.update(judul=judul, kolom=kolom, baris=baris)
        return "EXPID", "file.xlsx"

    monkeypatch.setattr(ai.ai_export, "stash_export", _stash)
    return captured


# ── excel_bom_rangka ─────────────────────────────────────────────────────────
def test_bom_lengkap_admin_dengan_stok_dan_harga(dunia):
    r = ai._t_excel_bom_rangka({"rangka": "LZZTEST", "dengan_stok": True,
                                "dengan_harga": True}, ADMIN)
    assert r["found"] and r["export_id"] == "EXPID"
    assert r["jumlah_baris"] == 120                       # SEMUA part, tanpa pangkas
    assert dunia["kolom"] == ["No", "Part Number", "Nama Part", "Qty",
                              "Stok Total", "Stok per Gudang", "Harga"]
    row = dunia["baris"][0]
    # Sel Stok/Harga = ANGKA (bukan "3"/"Rp 50.000") supaya rumus Excel user
    # jalan — aturan pemilik 2026-07-20. 'Stok per Gudang' tetap teks rincian.
    assert row[4] == 3 and isinstance(row[4], int)
    assert "01.Jakarta: 2" in row[5]                       # stok dari indeks Accurate
    assert row[6] == 50_000 and isinstance(row[6], int)


def test_bom_staf_biasa_harga_disembunyikan(dunia):
    """Aturan harga asisten: HANYA admin & akun 'mas'. Staf minta harga → kolomnya
    tidak dibuat (bukan error), stok tetap boleh."""
    r = ai._t_excel_bom_rangka({"rangka": "LZZTEST", "dengan_stok": True,
                                "dengan_harga": True}, STAF)
    assert r["found"] and r["kolom_harga"] is False and r["kolom_stok"] is True
    assert "Harga" not in dunia["kolom"] and "Stok Total" in dunia["kolom"]


def test_bom_pembeli_tanpa_stok_dan_harga(dunia):
    """Pembeli tak boleh melihat rincian stok gudang (audit hardening)."""
    r = ai._t_excel_bom_rangka({"rangka": "LZZTEST", "dengan_stok": True,
                                "dengan_harga": True}, PEMBELI)
    assert r["found"]
    assert dunia["kolom"] == ["No", "Part Number", "Nama Part", "Qty"]


def test_bom_filter_kata_kunci(dunia, monkeypatch):
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "Part 001"], []))
    r = ai._t_excel_bom_rangka({"rangka": "LZZTEST", "kata_kunci": "part satu"}, ADMIN)
    assert r["found"] and r["jumlah_baris"] == 1
    assert dunia["baris"][0][1] == "WG0000000001"


def test_bom_epc_gagal_tidak_mengarang(dunia, monkeypatch):
    monkeypatch.setattr(ai.epc_bom, "loading_list",
                        lambda rangka: {"found": False, "_err": "network"})
    r = ai._t_excel_bom_rangka({"rangka": "LZZTEST"}, ADMIN)
    assert not r.get("found") and "jaringan" in r["error"].lower()


# ── excel_stok_gudang ────────────────────────────────────────────────────────
@pytest.fixture
def stok_dunia(dunia, monkeypatch):
    items = [{"pn": f"AZ{i:04d}", "name": f"Brake lining {i}", "price": 10_000 + i}
             for i in range(60)]
    monkeypatch.setattr(ai.accurate, "items_matching", lambda terms, limit=400: items)
    monkeypatch.setattr(ai, "_expand_query", lambda q: ([q, "brake lining"], []))
    monkeypatch.setattr(ai, "_umbrella_keywords", lambda q: [])
    monkeypatch.setattr(ai, "_resolve_gudang", lambda g: "01.Jakarta" if "jakarta" in g.lower() else None)
    monkeypatch.setattr(ai, "_gudang_list", lambda: ["01.Jakarta", "02.Pekanbaru"])
    return dunia


def test_stok_satu_gudang_tanpa_pangkas_40(stok_dunia):
    """Jawaban chat stok_gudang dipangkas 40 — file Excel TIDAK boleh ikut terpangkas."""
    r = ai._t_excel_stok_gudang({"kata_kunci": "kampas rem", "gudang": "jakarta",
                                 "dengan_harga": True}, ADMIN)
    assert r["found"] and r["jumlah_baris"] == 60
    assert stok_dunia["kolom"] == ["No", "Part Number", "Nama Part",
                                   "Stok Jakarta", "Stok Total", "Harga"]
    harga = stok_dunia["baris"][0][5]
    assert isinstance(harga, int) and harga > 0     # angka, bukan "Rp …"


def test_stok_semua_gudang_pakai_rincian(stok_dunia):
    r = ai._t_excel_stok_gudang({"kata_kunci": "kampas rem"}, ADMIN)
    assert r["found"]
    assert "Stok per Gudang" in stok_dunia["kolom"]
    assert "01.Jakarta: 2" in stok_dunia["baris"][0][4]


def test_stok_gudang_pembeli_ditolak(stok_dunia):
    r = ai._t_excel_stok_gudang({"kata_kunci": "kampas rem"}, PEMBELI)
    assert "pembeli" in r["error"]


def test_stok_staf_harga_disembunyikan(stok_dunia):
    r = ai._t_excel_stok_gudang({"kata_kunci": "kampas rem", "dengan_harga": True}, STAF)
    assert r["found"] and "Harga" not in stok_dunia["kolom"]


# ── Kabel: dispatch, spec, kartu unduh ───────────────────────────────────────
def test_terdaftar_di_dispatch_dan_spec():
    assert ai._DISPATCH["excel_bom_rangka"] is ai._t_excel_bom_rangka
    assert ai._DISPATCH["excel_stok_gudang"] is ai._t_excel_stok_gudang
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert {"excel_bom_rangka", "excel_stok_gudang", "buat_excel"} <= names


def test_masuk_daftar_kartu_unduh():
    """Hasil tool harus dikoleksi jadi kartu unduh (excel_exports) — kalau tidak,
    file terbuat tapi kartunya tak pernah muncul di UI."""
    import inspect
    src = inspect.getsource(ai.chat)
    assert '"excel_bom_rangka"' in src and '"excel_stok_gudang"' in src
