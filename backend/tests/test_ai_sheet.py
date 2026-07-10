"""Excel unggahan user di chat asisten: deteksi kolom, isi kolom, scoping harga SIMS."""
import io

import pytest
from openpyxl import Workbook

from app.services import ai_assistant as ai
from app.services import ai_sheet

ADMIN = {"username": "admin", "role": "admin"}
USER = {"username": "budi", "role": "user"}
PEMBELI = {"username": "toko", "role": "pembeli"}


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def katalog(monkeypatch):
    """Katalog palsu: 2 PN dikenal, lengkap dgn stok/harga/nama."""
    rows = [
        {"part_number": "WG9925520270", "part_name": "Spring bracket", "stok": "12", "harga": "Rp 1.500.000"},
        {"part_number": "AZ9925520271", "part_name": "Leaf spring", "stok": "0", "harga": "—"},
    ]
    monkeypatch.setattr(ai_sheet.part_index, "search_exact_pns",
                        lambda pns: [r for r in rows if (r["part_number"] or "").upper() in {p.upper() for p in pns}])
    return rows


# ── Parsing & deteksi peran kolom ────────────────────────────────────────────

def test_deteksi_kolom_pn_nama_qty(katalog):
    data = _xlsx([
        ["DAFTAR PERMINTAAN PART"],                 # baris judul, harus dilewati
        ["No", "Kode", "Deskripsi", "Jumlah"],      # header sebenarnya
        [1, "WG9925520270", "Spring bracket", 4],
        [2, "AZ9925520271", "Leaf spring", 2],
    ])
    p = ai_sheet.parse_upload(data, "permintaan.xlsx")
    assert p["ok"]
    assert p["jumlah_baris"] == 2
    # 'Kode' bukan kata kunci PN, tapi ISI-nya cocok katalog → tetap terdeteksi.
    assert p["kolom_pn"] == "Kode"
    assert p["pn_dikenal"] == 2
    roles = dict(zip(p["headers"], p["roles"]))
    assert roles["Deskripsi"] == "part_name"
    assert roles["Jumlah"] == "qty"


def test_tolak_format_dan_file_rusak():
    assert ai_sheet.parse_upload(b"", "a.xlsx")["error"] == "File kosong."
    assert "xlsx" in ai_sheet.parse_upload(b"x", "a.csv")["error"]
    assert "valid" in ai_sheet.parse_upload(b"bukan-excel", "a.xlsx")["error"]


def test_tolak_file_kelewat_besar():
    r = ai_sheet.parse_upload(b"x" * (ai_sheet.MAX_BYTES + 1), "a.xlsx")
    assert not r["ok"] and "besar" in r["error"]


# ── Stash discoped per-user ──────────────────────────────────────────────────

def test_sheet_id_user_lain_tidak_terbaca(katalog):
    p = ai_sheet.parse_upload(_xlsx([["PN"], ["WG9925520270"]]), "a.xlsx")
    sid = ai_sheet.put_sheet("budi", p)
    assert ai_sheet.get_sheet(sid, "budi") is not None
    assert ai_sheet.get_sheet(sid, "orang-lain") is None       # milik user lain
    assert ai_sheet.get_sheet("ngawur", "budi") is None


# ── Isi kolom ────────────────────────────────────────────────────────────────

def _sheet_untuk(user, katalog):
    p = ai_sheet.parse_upload(_xlsx([
        ["Part Number", "Qty"],
        ["WG9925520270", 4],
        ["ZZZ0000000", 1],        # PN tak dikenal → dibiarkan kosong
    ]), "order.xlsx")
    return ai_sheet.put_sheet(user["username"], p)


def test_isi_kolom_stok_tambah_kolom_baru(katalog):
    sid = _sheet_untuk(USER, katalog)
    r = ai_sheet.fill_column(sid, USER, isi="stok")
    assert r["found"]
    assert r["kolom_diisi"] == "Stok" and r["kolom_part_number"] == "Part Number"
    # PN dikenal terisi, PN asing dibiarkan kosong (tidak dikarang).
    assert r["baris_terisi"] == 1 and r["baris_kosong"] == 1
    assert r["export_id"]


def test_isi_kolom_menimpa_kolom_yang_disebut_huruf(katalog):
    sid = _sheet_untuk(USER, katalog)
    r = ai_sheet.fill_column(sid, USER, isi="nama_part", kolom_tujuan="B")  # kolom Qty
    assert r["found"] and r["kolom_diisi"] == "Qty"


def test_isi_kolom_sheet_kedaluwarsa():
    r = ai_sheet.fill_column("tidak-ada", USER, isi="stok")
    assert r["found"] is False and "unggah" in r["error"].lower()


# ── HARGA SIMS: hanya admin ──────────────────────────────────────────────────

def test_harga_sims_ditolak_untuk_user_biasa(katalog):
    sid = _sheet_untuk(USER, katalog)
    r = ai_sheet.fill_column(sid, USER, isi="harga_sims", can_sims=False)
    assert r["denied"] is True and "admin" in r["error"]


def test_harga_sims_ditolak_untuk_pembeli(katalog):
    sid = _sheet_untuk(PEMBELI, katalog)
    r = ai_sheet.fill_column(sid, PEMBELI, isi="harga_sims", can_sims=False)
    assert r["denied"] is True


def test_harga_sims_jalan_untuk_admin(monkeypatch, katalog):
    monkeypatch.setattr(ai_sheet.harga, "batch_harga", lambda pns, **k: {
        "rate": 2200.0, "count": len(pns), "found": 1,
        "results": [{"pn": "WG9925520270", "cny": 700, "idr": 1540000, "status": "ok"},
                    {"pn": "ZZZ0000000", "cny": None, "idr": None, "status": "not_found"}],
    })
    sid = _sheet_untuk(ADMIN, katalog)
    r = ai_sheet.fill_column(sid, ADMIN, isi="harga_sims", can_sims=True)
    assert r["found"] and r["baris_terisi"] == 1 and "SIMS" in r["sumber"]


def test_harga_sims_batasi_jumlah_pn(monkeypatch, katalog):
    baris = [["Part Number"]] + [[f"WG99255{i:05d}"] for i in range(ai_sheet._MAX_SIMS + 5)]
    p = ai_sheet.parse_upload(_xlsx(baris), "besar.xlsx")
    sid = ai_sheet.put_sheet("admin", p)
    r = ai_sheet.fill_column(sid, ADMIN, isi="harga_sims", can_sims=True)
    assert r["found"] is False and "Maksimum" in r["error"]


# ── Scoping di lapisan tool (allow-list) ─────────────────────────────────────

def test_tool_sheet_hanya_ada_bila_ada_lampiran():
    assert "sheet_ringkasan" not in ai._allowed_tool_names(USER, "")
    assert "sheet_isi_kolom" not in ai._allowed_tool_names(ADMIN, "")
    assert "sheet_ringkasan" in ai._allowed_tool_names(USER, "sid-apapun")


def test_pilihan_harga_sims_hanya_ditawarkan_ke_admin():
    def _enum(user):
        for f in ai._tool_specs(user, "sid"):
            if f["function"]["name"] == "sheet_isi_kolom":
                return f["function"]["parameters"]["properties"]["isi"]["enum"]
        return []

    assert "harga_sims" in _enum(ADMIN)
    assert "harga_sims" not in _enum(USER)
    assert "harga_sims" not in _enum(PEMBELI)


def test_harga_sims_tool_tak_pernah_untuk_non_admin():
    """Aturan pemilik: SEMUA akses harga SIMS di asisten hanya admin."""
    for u in (USER, PEMBELI):
        assert "harga_sims" not in ai._allowed_tool_names(u, "sid")
    assert "harga_sims" in ai._allowed_tool_names(ADMIN, "sid")


def test_run_tool_menolak_sheet_tool_tanpa_lampiran():
    r = ai._run_tool("sheet_isi_kolom", {"isi": "stok"}, ADMIN, sheet_id="")
    assert r["denied"] is True


def test_chat_lampiran_excel_sampai_kartu_unduh(monkeypatch, katalog):
    """Jalur PENUH: sheet_id → tool ditawarkan → model panggil sheet_isi_kolom →
    kartu unduh Excel muncul di metadata. DeepSeek di-mock (tanpa network)."""
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    sid = _sheet_untuk(USER, katalog)

    dilihat_tools: list[list[str]] = []
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "t1", "function": {"name": "sheet_isi_kolom",
                                      "arguments": '{"isi":"stok","kolom_tujuan":"Stok"}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Sudah saya isikan stoknya."}, "finish_reason": "stop"}]},
    ]
    n = {"i": 0}

    def fake_post(messages, tools):
        dilihat_tools.append([t["function"]["name"] for t in (tools or [])])
        c = seq[min(n["i"], len(seq) - 1)]
        n["i"] += 1
        return c

    monkeypatch.setattr(ai, "_post_chat", fake_post)
    out = ai.chat(USER, [{"role": "user", "content": "isikan stoknya di kolom Stok"}], sheet_id=sid)

    assert "sheet_isi_kolom" in dilihat_tools[0]      # tool ditawarkan krn ada lampiran
    assert "sheet_isi_kolom" in out["tools_used"]
    assert out["excel_exports"] and out["excel_exports"][0]["id"]   # kartu unduh muncul
    assert "stok" in out["reply"].lower()


def test_chat_sheet_id_milik_orang_lain_diabaikan(monkeypatch, katalog):
    """sheet_id curian → lampiran dianggap tidak ada, tool sheet_* tak ditawarkan."""
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    sid = _sheet_untuk(USER, katalog)          # milik 'budi'
    dilihat: list[list[str]] = []

    def fake_post(messages, tools):
        dilihat.append([t["function"]["name"] for t in (tools or [])])
        return {"choices": [{"message": {"content": "Tidak ada lampiran."}, "finish_reason": "stop"}]}

    monkeypatch.setattr(ai, "_post_chat", fake_post)
    penyusup = {"username": "penyusup", "role": "admin"}
    ai.chat(penyusup, [{"role": "user", "content": "isi stok"}], sheet_id=sid)
    assert not any(t.startswith("sheet_") for t in dilihat[0])


def test_model_tak_bisa_memilih_sheet_id_lewat_argumen(monkeypatch, katalog):
    """_sheet_id selalu ditimpa server; argumen dari model diabaikan."""
    sid = _sheet_untuk(USER, katalog)
    dilihat = {}

    def _spy(args, user):
        dilihat.update(args)
        return {"found": True}

    monkeypatch.setitem(ai._DISPATCH, "sheet_ringkasan", _spy)
    ai._run_tool("sheet_ringkasan", {"_sheet_id": "sheet-orang-lain"}, USER, sheet_id=sid)
    assert dilihat["_sheet_id"] == sid
