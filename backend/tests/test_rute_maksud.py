"""RUTE MAKSUD — frasa khas pemilik → TOOL yang dipakai asisten.

Celah yang ditutup fitur ini: sebelum 2026-08-01 tak ada satu pun kanal yang
bisa diisi pemilik DAN mempengaruhi pemilihan tool. Kamus Sinonim hanya
mengubah kata kunci pencarian; store pengetahuan hanya terbaca bila model
kebetulan memanggil cari_pengetahuan — jadi "ingat: gambar teknis = exploded
view" tersimpan rapi lalu tak pernah terpakai.

Yang dijaga ketat di sini:

  • Frasa GENERIK ('gambar', 'cek part') DITOLAK — rute semacam itu membajak
    hampir semua percakapan.
  • Nama tool KARANGAN ditolak: rute ke tool hantu = gagal senyap, model tak
    akan pernah bisa mematuhinya.
  • Rute hanya masuk prompt pada giliran yang MENYEBUT frasanya (blok dinamis),
    dan cocoknya frasa UTUH — 'gambarkan' bukan 'gambar teknis'.
  • Alur ajar-lewat-chat: draf → kartu 'Simpan ke Rute Maksud' → tersimpan;
    ⛔ draf tidak menulis apa pun sebelum user menekan kartu.

Nol panggilan model: hanya handler tool + store yang diuji.
"""
from __future__ import annotations

import json

import pytest

from app.services import ai_assistant as ai
from app.services import knowledge_util, maksud, pengetahuan, pengetahuan_index, sinonim

ADMIN = {"username": "agus", "role": "admin"}
PEMBELI = {"username": "toko", "role": "pembeli"}

JUDUL = "Istilah lapangan: gambar teknis"
ISI = ("Di bengkel kami 'gambar teknis' berarti gambar exploded view part, "
       "bukan foto part fisik.")
FRASA = ["gambar teknis"]
TOOL = "gambar_exploded"


@pytest.fixture(autouse=True)
def bersih(tmp_path, monkeypatch):
    """Store rute + store pengetahuan ke tmp_path; cache per-mtime dibersihkan
    (beberapa test menulis file yang sama dalam hitungan milidetik)."""
    f = tmp_path / "maksud.json"
    monkeypatch.setattr(maksud, "_file", lambda: f)
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sinonim, "entries", lambda: [])
    monkeypatch.setattr(pengetahuan_index, "antre", lambda dok_id: None)
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    ai._AJAR_DRAF.clear()
    yield f
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    ai._AJAR_DRAF.clear()


def _tambah(frasa=None, tool=TOOL, catatan="", tools_sah=None):
    return maksud.add(frasa or list(FRASA), tool, catatan, oleh="agus",
                      tools_sah=tools_sah if tools_sah is not None else {TOOL, "diagram_wiring"})


def _draf(user=ADMIN, cid="c1", **extra):
    return ai._t_ajarkan_pengetahuan(
        {"aksi": "draf", "judul": JUDUL, "isi": ISI,
         "kata_kunci": ["gambar teknis", "exploded"], "_cid": cid, **extra}, user)


# ── Store: validasi ──────────────────────────────────────────────────
def test_rute_tersimpan_dan_terbaca_kembali(bersih):
    e = _tambah(catatan="maksudnya exploded view, bukan foto")
    assert e["frasa"] == ["gambar teknis"] and e["tool"] == TOOL
    rows = json.loads(bersih.read_text(encoding="utf-8"))
    assert rows[0]["oleh"] == "agus"
    assert rows[0]["catatan"] == "maksudnya exploded view, bukan foto"


@pytest.mark.parametrize("frasa", [["gambar"], ["cek part"], ["foto"], ["ada"]])
def test_frasa_generik_ditolak(frasa):
    """Kata yang muncul di hampir semua pertanyaan tak boleh jadi rute —
    kalau lolos, satu entri membajak seluruh percakapan bengkel."""
    with pytest.raises(ValueError, match="umum|pendek"):
        _tambah(frasa=frasa)


def test_frasa_kepanjangan_ditolak():
    with pytest.raises(ValueError, match="terlalu panjang"):
        _tambah(frasa=["kalau saya minta gambar teknis dari part ini"])


def test_tool_karangan_ditolak():
    """Rute ke tool yang tidak ada = gagal SENYAP (tersimpan, tak pernah dipatuhi)."""
    with pytest.raises(ValueError, match="tidak dikenal"):
        _tambah(tool="tampilkan_gambar")


def test_frasa_ganda_ditolak():
    _tambah()
    with pytest.raises(ValueError, match="sudah dipakai"):
        _tambah(tool="diagram_wiring")


def test_plafon_entri(monkeypatch):
    monkeypatch.setattr(maksud, "MAKS_ENTRI", 2)
    _tambah(frasa=["gambar teknis"])
    _tambah(frasa=["gambar susunan"])
    with pytest.raises(ValueError, match="batas 2 entri"):
        _tambah(frasa=["gambar rakitan"])


def test_update_dan_delete(bersih):
    _tambah()
    maksud.update(0, ["gambar teknis", "gambar urai"], "diagram_wiring",
                  tools_sah={TOOL, "diagram_wiring"})
    assert maksud.load()[0]["tool"] == "diagram_wiring"
    maksud.delete(0)
    assert maksud.load() == []


# ── Blok prompt dinamis ──────────────────────────────────────────────
def test_blok_hanya_muncul_saat_frasa_disebut():
    ent = [{"frasa": ["gambar teknis"], "tool": TOOL, "catatan": "bukan foto"}]
    assert maksud.block("tolong kirim gambar teknis part ini", lambda: ent)
    assert maksud.block("berapa stok filter oli?", lambda: ent) == ""


def test_blok_cocok_frasa_utuh_bukan_substring():
    """'gambarkan' bukan 'gambar teknis' — pagar yang sama dengan kamus sinonim."""
    ent = [{"frasa": ["gambar teknis"], "tool": TOOL, "catatan": ""}]
    assert maksud.block("gambarkan teknisnya dong", lambda: ent) == ""


def test_blok_menyebut_tool_dan_syarat_argumen():
    ent = [{"frasa": ["gambar teknis"], "tool": TOOL, "catatan": "bukan foto part"}]
    blok = maksud.block("minta gambar teknis", lambda: ent)
    assert "[RUTE MAKSUD GILIRAN INI]" in blok
    assert TOOL in blok and "bukan foto part" in blok
    # Rute menentukan TOOL, bukan menghapus syarat argumennya (mis. VIN wajib).
    assert "nomor rangka" in blok
    # Bukan paksaan buta: model tetap boleh menolak bila konteks jelas lain.
    assert "KECUALI" in blok


def test_blok_rute_paling_spesifik_di_atas():
    ent = [{"frasa": ["gambar teknis"], "tool": TOOL, "catatan": ""},
           {"frasa": ["gambar teknis kabel"], "tool": "diagram_wiring", "catatan": ""}]
    blok = maksud.block("minta gambar teknis kabel EGR", lambda: ent)
    assert blok.index("diagram_wiring") < blok.index(TOOL)


def test_subset_block_membaca_pesan_user_saja(monkeypatch):
    ent = [{"frasa": ["gambar teknis"], "tool": TOOL, "catatan": ""}]
    monkeypatch.setattr(ai, "_load_maksud_entries", lambda: ent)
    hist_asisten = [{"role": "assistant", "content": "ini gambar teknis part itu"}]
    assert ai._maksud_subset_block(hist_asisten) == ""
    hist_user = [{"role": "user", "content": "minta gambar teknis part ini"}]
    assert "[RUTE MAKSUD GILIRAN INI]" in ai._maksud_subset_block(hist_user)


def test_store_rusak_tidak_mematikan_chat(monkeypatch):
    def meledak():
        raise RuntimeError("store korup")
    monkeypatch.setattr(ai, "_load_maksud_entries", meledak)
    assert ai._maksud_subset_block([{"role": "user", "content": "gambar teknis"}]) == ""


# ── Alur ajar lewat chat ─────────────────────────────────────────────
def test_draf_menawarkan_kartu_rute_dan_belum_menyimpan(bersih):
    out = _draf(maksud_frasa=FRASA, maksud_tool=TOOL,
                maksud_catatan="maksudnya exploded view, bukan foto")
    assert out["_tanya"][0]["opsi"][0] == "Simpan ke Rute Maksud"
    assert "Rute Maksud" in out["_tanya_pengantar"]
    assert TOOL in out["_tanya_pengantar"]
    assert not bersih.exists()          # ⛔ belum ada yang ditulis


def test_simpan_maksud_menulis_rute(bersih):
    _draf(maksud_frasa=FRASA, maksud_tool=TOOL)
    out = ai._t_ajarkan_pengetahuan({"aksi": "simpan_maksud", "_cid": "c1"}, ADMIN)
    assert out["tersimpan_maksud"] is True
    assert out["frasa"] == ["gambar teknis"] and out["tool"] == TOOL
    assert json.loads(bersih.read_text(encoding="utf-8"))[0]["tool"] == TOOL


def test_alias_aksi_dari_model():
    """Model menyebut aksinya bermacam-macam — jangan gagalkan alur karena kata."""
    _draf(maksud_frasa=FRASA, maksud_tool=TOOL)
    out = ai._t_ajarkan_pengetahuan({"aksi": "simpan_rute", "_cid": "c1"}, ADMIN)
    assert out.get("tersimpan_maksud") is True


def test_tool_karangan_tidak_jadi_rute_tapi_alur_lanjut(bersih):
    """Draf tetap tersaji sebagai catatan biasa + catatan jujur kenapa rute batal."""
    out = _draf(maksud_frasa=FRASA, maksud_tool="tampilkan_gambar")
    assert out["_tanya"][0]["opsi"][0] != "Simpan ke Rute Maksud"
    assert "tampilkan_gambar" in out["_tanya_pengantar"]
    assert not bersih.exists()


def test_frasa_generik_tidak_jadi_rute(bersih):
    out = _draf(maksud_frasa=["gambar"], maksud_tool=TOOL)
    assert out["_tanya"][0]["opsi"][0] != "Simpan ke Rute Maksud"
    assert "terlalu umum" in out["_tanya_pengantar"]


def test_frasa_sudah_punya_rute_tidak_ditawarkan_ulang():
    _tambah()
    knowledge_util._LOAD_CACHE.clear()
    out = _draf(maksud_frasa=FRASA, maksud_tool="diagram_wiring")
    assert out["_tanya"][0]["opsi"][0] != "Simpan ke Rute Maksud"
    assert "SUDAH punya rute" in out["_tanya_pengantar"]


def test_simpan_maksud_tanpa_draf_tidak_mengaku_tersimpan():
    out = ai._t_ajarkan_pengetahuan({"aksi": "simpan_maksud", "_cid": "kosong"}, ADMIN)
    assert out["found"] is False
    assert "JANGAN mengaku" in out["catatan"]


def test_simpan_maksud_atas_draf_biasa_ditolak():
    _draf()                                   # draf tanpa maksud_frasa/tool
    out = ai._t_ajarkan_pengetahuan({"aksi": "simpan_maksud", "_cid": "c1"}, ADMIN)
    assert out["found"] is False
    assert "BUKAN rute maksud" in out["catatan"]


def test_pembeli_tak_bisa_membuat_rute(bersih):
    out = _draf(user=PEMBELI, maksud_frasa=FRASA, maksud_tool=TOOL)
    assert out.get("denied") is True
    assert not bersih.exists()


# ── Endpoint admin ───────────────────────────────────────────────────
# Handler dipanggil LANGSUNG (pola test_pengetahuan_admin) — otorisasi sudah
# dijaga Depends(require_admin) di definisi route.
def test_endpoint_list_menyertakan_daftar_tool_nyata():
    from app.routers import admin as R
    out = R.maksud_list(ADMIN)
    assert out["entries"] == [] and out["maks"] == maksud.MAKS_ENTRI
    # Daftar tool = sumber kebenaran yang sama dgn yang ditawarkan ke model,
    # jadi dropdown admin tak pernah menyodorkan tool hantu.
    assert "gambar_exploded" in out["tools"]


def test_endpoint_add_tolak_tool_karangan():
    from fastapi import HTTPException
    from app.routers import admin as R
    body = R.MaksudEntryRequest(frasa=["gambar teknis"], tool="tampilkan_gambar")
    with pytest.raises(HTTPException) as err:
        R.maksud_add(body, ADMIN)
    assert err.value.status_code == 400
    assert "tidak dikenal" in str(err.value.detail)


def test_endpoint_add_lalu_hapus(bersih):
    from app.routers import admin as R
    body = R.MaksudEntryRequest(frasa=["gambar teknis"], tool=TOOL,
                                catatan="bukan foto")
    assert R.maksud_add(body, ADMIN)["ok"] is True
    assert R.maksud_list(ADMIN)["jumlah"] == 1
    R.maksud_delete(0, ADMIN)
    assert R.maksud_list(ADMIN)["jumlah"] == 0
