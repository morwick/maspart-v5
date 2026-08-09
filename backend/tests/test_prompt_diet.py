"""Fase 3a rombakan (2026-07-17): domain_block pindah ke file ai_domain.md
BYTE-IDENTIK dgn literal lama, dan kamus sinonim penuh keluar dari prompt
statik → subset relevan per giliran disuntik di pesan system ekor.
"""
from __future__ import annotations

import hashlib

from app.services import ai_assistant as A

ADMIN = {"username": "admin", "role": "admin"}

# Fase 3b: isi ai_domain.md DIPANGKAS dgn sengaja (28.510 → ±11rb chars) —
# snapshot byte-identik 3a (sha f4b631cd…) pensiun; kini yang dijaga: aturan
# keras inti tetap ada & ukuran terkendali.
# 2026-08-09: sebagian aturan pindah ke ai_domain_pervin.md (blok DINAMIS,
# hanya saat percakapan menyinggung rangka — lihat test_prompt_diet_pervin.py).
# Invariannya karena itu digeser: yang dijaga bukan "ada di SATU berkas" tapi
# "ada di prompt yang BENAR-BENAR dikirim" saat aturan itu relevan.
_PENANDA_STATIK = (
    "PENGETAHUAN DOMAIN — TRANSMISI / GEARBOX",
    "LARANGAN MUTLAK MENGARANG PART NUMBER",
    "AKURASI PER-UNIT",
)
_PENANDA_PERVIN = (
    "ATURAN KERAS",
    "DEPAN ≠ BELAKANG",
    "part_aus_dari_rangka",
    "sumber='mesin'",  # Fase 4: uraikan_mesin dilebur → uraikan_assembly(sumber='mesin')
)
_ADA_RANGKA = [{"role": "user", "content": "kampas rem SJ346500"}]


def test_domain_block_terpangkas_tapi_aturan_inti_utuh():
    blok = A._domain_block()
    assert 5_000 < len(blok) < 16_000  # terpangkas 28.510 → 9.456, tak menciut ekstrem
    for p in _PENANDA_STATIK:
        assert p in blok, p


def test_aturan_pervin_utuh_di_blok_dinamis():
    """Dipindah ≠ hilang — saat rangka disebut, aturannya WAJIB ikut terkirim."""
    blok = A._pervin_block(_ADA_RANGKA)
    for p in _PENANDA_PERVIN:
        assert p in blok, p


def test_semua_aturan_inti_sampai_ke_model_saat_rangka_disebut():
    """Uji gabungan: prompt yang benar-benar dikirim (statik + dinamis) tetap
    memuat SELURUH aturan keras yang dulu dijamin satu berkas."""
    terkirim = A._system_prompt(ADMIN) + "\n" + A._pervin_block(_ADA_RANGKA)
    for p in _PENANDA_STATIK + _PENANDA_PERVIN:
        assert p in terkirim, p


def test_domain_block_stabil_antar_panggilan():
    assert A._domain_block() is A._domain_block()  # cache mtime → objek sama


def test_domain_block_file_hilang_fallback_kosong(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "_DOMAIN_FILE", tmp_path / "tidak_ada.md")
    monkeypatch.setattr(A, "_DOMAIN_CACHE", {"mtime": None, "text": ""})
    assert A._domain_block() == ""  # produksi jangan crash


def test_prompt_statik_tanpa_kamus_penuh_tapi_masih_ber_domain():
    sp = A._system_prompt(ADMIN)
    assert "KAMUS ISTILAH LAPANGAN (Indonesia → kata kunci Inggris):" not in sp
    assert "[KAMUS ISTILAH GILIRAN INI]" in sp  # rujukan cara kerjanya tetap dijelaskan
    assert "PENGETAHUAN DOMAIN — TRANSMISI / GEARBOX" in sp  # dari ai_domain.md
    # diet: prompt admin turun dari 86,7rb → jauh di bawah 75rb chars
    assert len(sp) < 75_000


def test_aturan_teruskan_mentah_berlaku_semua_tool_pencarian():
    """Kasus produksi 'cucuk per' (2026-07-22): aturan lama hanya menyebut
    cari_part → model merasa bebas menerjemahkan sendiri utk cari_part_di_unit
    ('cucuk per' ditebak 'cross joint', padahal kamus tahu itu 'spring pin').
    Kunci: larangan pra-terjemah + penyebutan tool per-unit."""
    sp = A._system_prompt(ADMIN)
    assert "cari_part_di_unit, part_aus_dari_rangka" in sp
    assert "JANGAN" in sp and "SEBELUM mencoba istilah mentahnya" in sp


def test_spec_kata_kunci_melarang_pra_terjemah():
    specs = A._tool_specs(ADMIN, sheet_id="")
    spec = next(s for s in specs
                if s.get("function", {}).get("name") == "cari_part_di_unit")
    desc = spec["function"]["parameters"]["properties"]["kata_kunci"]["description"]
    assert "APA ADANYA" in desc and "JANGAN terjemahkan" in desc


_ENTRIES = [
    {"grup": "kopling", "triggers": ["kampas kopling"], "keywords": ["clutch disc"]},
    {"grup": "rem", "triggers": ["kampas rem"], "keywords": ["brake lining", "friction plate"]},
]


def test_kamus_subset_hanya_trigger_yang_disebut(monkeypatch):
    monkeypatch.setattr(A, "_load_sinonim_entries", lambda: _ENTRIES)
    blok = A._kamus_subset_block([
        {"role": "user", "content": "berapa harga kampas rem howo?"}])
    assert "kampas rem" in blok and "brake lining" in blok
    assert "clutch disc" not in blok            # grup tak disebut → tak ikut
    assert blok.startswith("[KAMUS ISTILAH GILIRAN INI]")


def test_kamus_subset_kosong_bila_tak_ada_trigger(monkeypatch):
    monkeypatch.setattr(A, "_load_sinonim_entries", lambda: _ENTRIES)
    assert A._kamus_subset_block([{"role": "user", "content": "cek stok WG9925520270"}]) == ""
    assert A._kamus_subset_block([]) == ""


def test_kamus_subset_ikut_pesan_ekor(monkeypatch):
    # end-to-end chat(): subset muncul sbg pesan system dinamis (bukan di prompt
    # statik) saat user menyebut istilah lapangan.
    monkeypatch.setattr(A, "_load_sinonim_entries", lambda: _ENTRIES)
    sent: dict = {}

    def _fake_post(messages, tools, max_tokens=6000):
        sent["messages"] = [dict(m) for m in messages]
        return {"choices": [{"message": {"content": "Siap."},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(A, "_post_chat", _fake_post)
    monkeypatch.setattr(A, "_prefetch_epc_rangka", lambda h: None)
    monkeypatch.setattr(A.ai_chat_log, "log_turn_async", lambda **kw: True)
    A.chat(ADMIN, [{"role": "user", "content": "ada kampas kopling nx360?"}])
    msgs = sent["messages"]
    # prompt statik hanya MERUJUK nama bloknya; isi kamus (header lengkap +
    # baris '- trigger → keyword') tidak boleh ada di sana.
    header = "[KAMUS ISTILAH GILIRAN INI] (Indonesia → kata kunci katalog"
    assert header not in msgs[0]["content"]
    dyn = [m for m in msgs[1:] if m["role"] == "system"
           and header in (m.get("content") or "")]
    assert dyn and "clutch disc" in dyn[0]["content"]
