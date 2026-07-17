"""Fase 3a rombakan (2026-07-17): domain_block pindah ke file ai_domain.md
BYTE-IDENTIK dgn literal lama, dan kamus sinonim penuh keluar dari prompt
statik → subset relevan per giliran disuntik di pesan system ekor.
"""
from __future__ import annotations

import hashlib

from app.services import ai_assistant as A

ADMIN = {"username": "admin", "role": "admin"}

# sha256 literal domain_block lama (28.510 chars, diekstrak via AST saat 3a) —
# menjamin externalize TIDAK mengubah satu byte pun. Boleh di-update saat fase
# 3b (pangkas isi) dgn sengaja.
_SNAPSHOT_SHA = "f4b631cd1d2f24ec722227717052285a0bde6d5d3036c5582963e6fae963fbf6"


def test_domain_block_byte_identik_dgn_literal_lama():
    blok = A._domain_block()
    assert len(blok) == 28510
    assert hashlib.sha256(blok.encode("utf-8")).hexdigest() == _SNAPSHOT_SHA


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
    monkeypatch.setattr(A.ai_chat_log, "log_turn", lambda **kw: True)
    A.chat(ADMIN, [{"role": "user", "content": "ada kampas kopling nx360?"}])
    msgs = sent["messages"]
    # prompt statik hanya MERUJUK nama bloknya; isi kamus (header lengkap +
    # baris '- trigger → keyword') tidak boleh ada di sana.
    header = "[KAMUS ISTILAH GILIRAN INI] (Indonesia → kata kunci katalog"
    assert header not in msgs[0]["content"]
    dyn = [m for m in msgs[1:] if m["role"] == "system"
           and header in (m.get("content") or "")]
    assert dyn and "clutch disc" in dyn[0]["content"]
