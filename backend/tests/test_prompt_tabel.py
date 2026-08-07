# -*- coding: utf-8 -*-
"""Aturan TABEL di system prompt — kapan dipakai & bagaimana bentuknya.

Latar: dulu satu butir berbunyi "…BUKAN tabel yang menggabung beberapa PN di
bawah satu judul unit" dan terbaca sebagai LARANGAN TABEL secara umum, padahal
dua blok lain (berpikir_block & agentik_block) justru meminta "data rapi
(tabel/daftar ringkas)". Model menebak sendiri kapan boleh bertabel → hasilnya
tak konsisten. Test ini mengunci penyelesaiannya, DAN mengunci bahwa maksud asli
larangan lama (jangan melebur banyak PN di bawah satu judul unit) tidak ikut
hilang saat kalimatnya ditulis ulang.

Renderer kedua klien (web Markdown.tsx & Flutter md_table.dart) menyandarkan
kerapian pada dua hal yang diwajibkan di sini: penanda rata kanan '---:' untuk
kolom angka, dan kolom Stok berisi ANGKA saja (kata 'KOSONG (0 pcs)' membuat
seluruh kolom dianggap non-numerik → rata kiri).
"""
from __future__ import annotations

import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


def test_kalimat_larangan_tabel_lama_sudah_pensiun():
    """Kalimat yang bikin konflik tak boleh tersisa — kalau masih ada, model
    tetap punya alasan untuk menghindari tabel sama sekali."""
    sp = ai._system_prompt(ADMIN)
    assert "BUKAN tabel yang menggabung" not in sp


def test_aturan_kapan_dan_format_tabel_ada():
    sp = ai._system_prompt(ADMIN)
    assert "KAPAN MEMAKAI TABEL" in sp
    assert "FORMAT TABEL WAJIB" in sp
    assert "---:" in sp                      # penanda rata kanan diwajibkan
    assert "MAKSIMAL 5 kolom" in sp


def test_maksud_asli_larangan_lama_tetap_utuh():
    """Yang dilarang tetap: baris tabel berupa UNIT, dan 'Part Digunakan Pada'
    dilebur jadi satu daftar bersama."""
    sp = ai._system_prompt(ADMIN)
    assert "baris tabel TIDAK BOLEH berupa unit" in sp
    assert "MILIKNYA SENDIRI" in sp
    assert "TERPISAH di " in sp              # blok per-PN di bawah tabel
    assert "'Part Digunakan Pada' TIDAK BOLEH jadi kolom tabel" in sp


def test_stok_dalam_tabel_angka_saja():
    """Gerbang untuk heuristik kolom numerik di kedua klien."""
    sp = ai._system_prompt(ADMIN)
    assert "kolom Stok diisi ANGKA saja" in sp
    # Aturan lama utk DI LUAR tabel tidak dihapus.
    assert "Stok: KOSONG (0 pcs)" in sp


@pytest.mark.parametrize("role", ["admin", "user", "pembeli"])
def test_aturan_tabel_berlaku_semua_peran(role):
    sp = ai._system_prompt({"username": "x", "role": role})
    assert "KAPAN MEMAKAI TABEL" in sp
    assert "FORMAT TABEL WAJIB" in sp


def test_blok_domain_tak_ikut_membengkak():
    """Aturan tabel WAJIB tinggal di p8_prompt.py: jendela ai_domain.md hanya
    menyisakan ~1,2rb char (test_prompt_diet.py) — menaruhnya di sana akan
    memecahkan plafon itu."""
    dom = ai._domain_block()
    assert "KAPAN MEMAKAI TABEL" not in dom
    assert len(dom) < 16_000
