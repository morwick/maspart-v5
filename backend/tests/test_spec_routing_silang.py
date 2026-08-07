"""Spek tool & rute maksud: yang menyetir PEMILIHAN tool sebelum satu byte data
pun diambil.

Deskripsi tool bukan dokumentasi — ia satu-satunya bahan model saat memutuskan
tool mana yang dipanggil. Empat cacat yang diperbaiki di sini semuanya berupa
kalimat yang menyesatkan, bukan kode yang salah:

  (6) stok_accurate berbunyi seolah sumber stok KEDUA ("membandingkan stok
      Accurate vs stok katalog lokal") — padahal sumbernya sama persis dengan
      detail_part, jadi "perbandingan" itu mustahil menghasilkan apa pun.
  (7) 'massal' dipakai dua arti: cek_massal_part = banyak PN, sedangkan
      cek_massal_part_rangka = SATU part × banyak unit. Tanpa silang eksplisit,
      "cek injector 5 VIN ini" wajar sekali nyasar ke yang salah.
 (10) parameter `mode` cari_part = tuas MATI (handler tak pernah membacanya)
      yang deskripsinya menjanjikan penyempitan hasil.
 (13) 'penyebab kode X' cocok untuk cari_kode_kesalahan (instan) DAN diagnosa
      (20-90 dtk) — urutannya harus disebut, di kedua sisi.

Plus (9): Rute Maksud hanya boleh membaca pesan user TERAKHIR — ia menyetir
pilihan tool, jadi frasa basi dari topik lama tak boleh ikut memerintah.
"""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture(scope="module")
def spec():
    return {s["function"]["name"]: s["function"] for s in ai._tool_specs(ADMIN)}


# ── (10) parameter mati ──────────────────────────────────────────────
def test_cari_part_tanpa_parameter_mode(spec):
    props = spec["cari_part"]["parameters"]["properties"]
    assert "mode" not in props, "handler _t_cari_part tak pernah membaca 'mode'"
    assert set(spec["cari_part"]["parameters"]["required"]) == {"query"}


def test_semua_parameter_spec_setidaknya_dikenal_handler():
    """Pagar tipis anti-tuas-mati: 'mode' hilang dari spec, dan tak ada handler
    yang kehilangan argumen wajibnya gara-gara pembersihan ini."""
    props = {s["function"]["name"]: set(
        (s["function"].get("parameters") or {}).get("properties") or {})
        for s in ai._tool_specs(ADMIN)}
    assert props["cari_part"] == {"query", "unit"}


# ── (6) satu sumber stok, bukan dua ──────────────────────────────────
def test_stok_accurate_tidak_mengaku_sumber_pembanding(spec):
    d = spec["stok_accurate"]["description"]
    assert "membandingkan stok Accurate vs stok katalog lokal" not in d
    assert "SUMBER STOKNYA SAMA PERSIS" in d
    assert "detail_part" in d and "cek_massal_part" in d


# ── (7) 'massal' dua arti ────────────────────────────────────────────
def test_cek_massal_part_menyilang_ke_versi_rangka(spec):
    d = spec["cek_massal_part"]["description"]
    assert "cek_massal_part_rangka" in d and "BANYAK RANGKA" in d


def test_cek_massal_rangka_menyilang_balik_dan_jujur_soal_pn(spec):
    f = spec["cek_massal_part_rangka"]
    assert "cek_massal_part" in f["description"]
    # `part` dicari lewat indeks NAMA EPC (epc_bom.search_in_unit) — spec tak
    # boleh menjanjikan PN sebagai input yang setara.
    assert "NAMA komponen" in f["parameters"]["properties"]["part"]["description"]


# ── (13) kode diketahui → kamus lokal dulu ───────────────────────────
@pytest.mark.parametrize("nama", ["cari_kode_kesalahan", "diagnosa"])
def test_urutan_kode_disebut_di_kedua_tool(spec, nama):
    d = spec[nama]["description"]
    assert "URUTAN BAKU" in d
    assert "cari_kode_kesalahan DULU" in d


def test_diagnosa_tak_lagi_mengklaim_penyebab_kode_sebagai_pintu_utama(spec):
    d = spec["diagnosa"]["description"]
    # dulu: "pakai untuk … 'apa penyebab kode X'" — bertabrakan langsung dgn
    # cari_kode_kesalahan yang instan.
    assert "pakai untuk 'kenapa …', 'apa penyebab kode X'" not in d
    assert "KELUHAN/GEJALA" in d


# ── (9) rute maksud = maksud kalimat SEKARANG ────────────────────────
_RUTE = [{"frasa": ["gambar teknis"], "tool": "gambar_exploded", "catatan": ""}]


def test_rute_dari_pesan_terakhir_saja(monkeypatch):
    monkeypatch.setattr(ai, "_load_maksud_entries", lambda: _RUTE)
    basi = [{"role": "user", "content": "minta gambar teknis rem"},
            {"role": "assistant", "content": "ini gambarnya"},
            {"role": "user", "content": "berapa harga filter olinya?"}]
    assert ai._maksud_subset_block(basi) == ""      # topik sudah pindah
    kini = basi[:-1] + [{"role": "user", "content": "gambar teknisnya mana?"}]
    assert "[RUTE MAKSUD GILIRAN INI]" in ai._maksud_subset_block(kini)


def test_kamus_istilah_TETAP_membaca_6_pesan(monkeypatch):
    """Jendela kamus sinonim SENGAJA berbeda & tidak ikut dipersempit: istilah
    basi cuma menambah kata kunci, sedangkan rute basi merebut tool."""
    ent = [{"grup": "rem", "triggers": ["kampas rem"], "keywords": ["brake lining"]}]
    monkeypatch.setattr(ai, "_load_sinonim_entries", lambda: ent)
    hist = [{"role": "user", "content": "cek kampas rem RT110061"},
            {"role": "assistant", "content": "ada 2 varian"},
            {"role": "user", "content": "yang depan saja"}]
    assert "brake lining" in ai._kamus_subset_block(hist)


def test_rute_abaikan_pesan_assistant(monkeypatch):
    monkeypatch.setattr(ai, "_load_maksud_entries", lambda: _RUTE)
    assert ai._maksud_subset_block(
        [{"role": "assistant", "content": "ini gambar teknis part itu"}]) == ""
    assert ai._maksud_subset_block([]) == ""
    assert ai._maksud_subset_block([{"role": "user", "content": "   "}]) == ""


def test_rute_store_rusak_tak_mematikan_chat(monkeypatch):
    def _boom():
        raise RuntimeError("store korup")

    monkeypatch.setattr(ai, "_load_maksud_entries", _boom)
    assert ai._maksud_subset_block([{"role": "user", "content": "gambar teknis"}]) == ""
