"""
Kamus MAKSUD — frasa user → TOOL yang dimaksud (data/maksud/maksud.json).

Kenapa store ini ada (celah yang ditutup 2026-08-01):
Sebelum ini pemilik punya dua kanal untuk "mengajari" asisten, dan tak satu pun
bisa mempengaruhi PEMILIHAN TOOL —
  • Kamus Sinonim (sinonim.py) → hanya kata kunci PENCARIAN part (expand_query);
  • store pengetahuan (pengetahuan.py) → hanya terbaca bila model kebetulan
    memanggil cari_pengetahuan, jadi "ingat: gambar teknis = exploded view"
    mati di rak karena permintaan gambar tak pernah memicu tool itu.
Satu-satunya tempat yang mengendalikan routing tool adalah ai_domain.md — file
kode, artinya butuh deploy. Store ini memberi pemilik jalur yang sama TANPA
deploy: entri diinjeksi sebagai blok prompt DINAMIS per giliran (lihat
_maksud_subset_block di ai_parts/p1_dasar.py), hanya bila frasanya benar-benar
muncul di pesan user — jadi murah token & tak menggeser prompt-cache statik.

Format entri: {"frasa": [istilah user...], "tool": "<nama tool>",
"catatan": str, "oleh": str}.

⚠️ Ini SARAN KUAT ke model, bukan paksaan deterministik seperti
_paksa_istilah_kamus (yang menimpa ARGUMEN, bukan nama tool). Memaksa nama tool
dari frasa berbahaya: "gambar teknis kabel EGR" harus ke diagram_wiring, bukan
gambar_exploded. Karena itu blok promptnya berbunyi "ikuti KECUALI kalimat user
jelas berkata lain" — model tetap boleh menolak berdasarkan konteks.

Tulis ATOMIK (tmp + os.replace) & serialisasi lewat lock proses — pola sama
persis dengan sinonim.py.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..core.config import get_settings
from .knowledge_util import load_json
from .sinonim import hit  # cocok FRASA/KATA UTUH, bukan substring di tengah kata

_lock = threading.Lock()

# Plafon entri: blok prompt ini ikut di SETIAP giliran yang cocok. 60 entri
# (±3rb chars bila semuanya cocok sekaligus, praktis tak mungkin) cukup untuk
# istilah lapangan yang benar-benar berulang; lebih dari itu tandanya pemilik
# sedang memakai store ini sebagai catatan, bukan rute.
MAKS_ENTRI = 60
MAKS_FRASA = 4          # frasa per entri
MAKS_KATA = 4           # kata per frasa (sejalan pagar trigger Kamus Sinonim)
MAKS_CATATAN = 160

# Kata GENERIK yang tak boleh berdiri sendiri sebagai rute. Pelajaran kamus
# 2026-07-29 dalam bentuk yang lebih keras: di Kamus Sinonim trigger generik
# cuma mengotori hasil pencarian, di sini ia MEMBAJAK pilihan tool untuk hampir
# semua kalimat ("gambar" muncul di ratusan pertanyaan yang tak ada
# hubungannya dengan exploded view).
_GENERIK = {
    "gambar", "foto", "part", "spare", "sparepart", "stok", "harga", "unit",
    "mesin", "truk", "alat", "barang", "data", "info", "nomor", "kode",
    "cari", "cek", "lihat", "minta", "tolong", "kirim", "tampilkan", "buka",
    "berapa", "apa", "mana", "yang", "untuk", "dari", "ada", "punya", "ini",
    "itu", "saya", "aku", "mau", "bisa", "dong", "ya",
}


def _file() -> Path:
    return get_settings().data_path / "maksud" / "maksud.json"


def _norm_frasa(vals) -> list[str]:
    """Rapikan daftar frasa: lowercase, spasi tunggal, buang kosong & duplikat."""
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(vals, str):
        vals = [vals]
    for v in vals or []:
        s = " ".join(str(v).split()).lower()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _validasi_frasa(frasa: list[str]) -> None:
    """Tolak frasa yang akan MEMBAJAK routing. Pesan error ditulis untuk dibaca
    langsung oleh user (dipakai apa adanya oleh tool ajarkan_pengetahuan)."""
    for f in frasa:
        kata = f.split()
        if len(kata) > MAKS_KATA:
            raise ValueError(
                f"Frasa '{f}' terlalu panjang ({len(kata)} kata, maks {MAKS_KATA}) — "
                "rute dicocokkan sebagai frasa utuh, kalimat panjang hampir tak "
                "pernah cocok. Pakai inti istilahnya saja.")
        if all(k in _GENERIK for k in kata):
            raise ValueError(
                f"Frasa '{f}' terlalu umum — kata seperti itu muncul di hampir "
                "semua pertanyaan, jadi rute ini akan membajak percakapan lain. "
                "Tambahkan kata yang khas (mis. 'gambar teknis', bukan 'gambar').")
        if len(kata) == 1 and len(f) < 6:
            raise ValueError(
                f"Frasa satu kata '{f}' terlalu pendek (minimal 6 huruf) — "
                "berisiko cocok di tempat yang tidak dimaksud.")


def _normalize(frasa, tool: str, catatan: str = "", oleh: str = "",
               tools_sah=None) -> dict:
    fr = _norm_frasa(frasa)[:MAKS_FRASA]
    t = " ".join(str(tool or "").split())
    if not fr:
        raise ValueError("Minimal satu frasa.")
    if not t:
        raise ValueError("Tool tujuan wajib diisi.")
    # Nama tool divalidasi ke daftar tool NYATA. Tanpa ini, salah ketik nama
    # tool menghasilkan rute yang diam-diam tak pernah bisa dipatuhi model —
    # gagal senyap, jenis bug paling mahal di jalur prompt.
    if tools_sah is not None and t not in set(tools_sah):
        raise ValueError(
            f"Tool '{t}' tidak dikenal. Rute harus menunjuk nama tool yang "
            "benar-benar ada, kalau tidak asisten tak akan pernah bisa "
            "mematuhinya.")
    _validasi_frasa(fr)
    return {"frasa": fr, "tool": t,
            "catatan": " ".join(str(catatan or "").split())[:MAKS_CATATAN],
            "oleh": str(oleh or "")}


def load() -> list[dict]:
    """Seluruh entri (list kosong bila file belum ada/korup) — untuk CRUD admin."""
    p = _file()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    return [e for e in data if isinstance(e, dict)]


def entries() -> list[dict]:
    """Entri dgn cache per-mtime — murah dipanggil tiap giliran, dan editan
    admin/chat langsung terpakai tanpa restart (pola sinonim.entries)."""
    data = load_json(_file())
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def cocok(teks: str, entries_fn=None) -> list[dict]:
    """Entri yang salah satu frasanya muncul di `teks` (frasa utuh).

    Diurutkan by panjang frasa yang match (desc) — aturan yang sama dengan
    _kamus_subset_block: rute paling spesifik menang bila dua frasa sama-sama
    cocok ('gambar teknis kabel' harus mengalahkan 'gambar teknis')."""
    hasil: list[tuple[int, dict]] = []
    for e in (entries_fn or entries)():
        f = max((x for x in (e.get("frasa") or []) if x and hit(x, teks or "")),
                key=len, default=None)
        if f:
            hasil.append((len(f), e))
    hasil.sort(key=lambda x: -x[0])
    return [e for _, e in hasil]


def block(teks: str, entries_fn=None) -> str:
    """[RUTE MAKSUD GILIRAN INI] — blok prompt dinamis, '' bila tak ada yang cocok."""
    lines: list[str] = []
    for e in cocok(teks, entries_fn):
        fr = ", ".join(f'"{x}"' for x in (e.get("frasa") or []) if x)
        tool = e.get("tool") or ""
        if not fr or not tool:
            continue
        cat = e.get("catatan") or ""
        lines.append(f"- {fr} → {tool}" + (f" — {cat}" if cat else ""))
    if not lines:
        return ""
    return (
        "[RUTE MAKSUD GILIRAN INI] (istilah khas pemilik/lapangan → tool yang "
        "DIMAKSUD; ditetapkan admin, ikuti KECUALI kalimat user jelas berkata "
        "lain). Rute ini menentukan tool MANA yang dipakai — syarat argumen tool "
        "itu tetap berlaku (mis. bila tool butuh nomor rangka, minta dulu):\n"
        + "\n".join(lines))


def _save(entries_: list[dict]) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries_, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # atomik: pembaca melihat file lama ATAU baru, tak pernah separuh


def cari_frasa(frasa: str, entries_fn=None) -> dict | None:
    """Entri yang sudah memuat frasa persis ini (untuk cegah rute ganda)."""
    f = " ".join(str(frasa or "").split()).lower()
    for e in (entries_fn or entries)():
        if f in [str(x).lower() for x in (e.get("frasa") or [])]:
            return e
    return None


def add(frasa, tool: str, catatan: str = "", oleh: str = "",
        tools_sah=None) -> dict:
    """Tambah rute baru. Tolak bila salah satu frasa sudah dipakai rute lain —
    dua rute untuk frasa yang sama membuat blok prompt saling bertentangan."""
    e = _normalize(frasa, tool, catatan, oleh, tools_sah)
    with _lock:
        rows = load()
        if len(rows) >= MAKS_ENTRI:
            raise ValueError(
                f"Rute sudah mencapai batas {MAKS_ENTRI} entri. Hapus rute yang "
                "tak terpakai di menu Rute Maksud dulu.")
        dipakai = {str(x).lower(): r for r in rows for x in (r.get("frasa") or [])}
        for f in e["frasa"]:
            if f in dipakai:
                raise ValueError(
                    f"Frasa '{f}' sudah dipakai rute ke '{dipakai[f].get('tool')}' "
                    "— edit rute itu di menu Rute Maksud bila pemetaannya keliru.")
        rows.append(e)
        _save(rows)
    return e


def update(index: int, frasa, tool: str, catatan: str = "", oleh: str = "",
           tools_sah=None) -> dict:
    e = _normalize(frasa, tool, catatan, oleh, tools_sah)
    with _lock:
        rows = load()
        if not 0 <= index < len(rows):
            raise IndexError("Rute tidak ditemukan — data mungkin berubah, muat ulang halaman.")
        dipakai = {str(x).lower(): i for i, r in enumerate(rows)
                   for x in (r.get("frasa") or [])}
        for f in e["frasa"]:
            if dipakai.get(f, index) != index:
                raise ValueError(f"Frasa '{f}' sudah dipakai rute lain.")
        rows[index] = e
        _save(rows)
    return e


def delete(index: int) -> dict:
    with _lock:
        rows = load()
        if not 0 <= index < len(rows):
            raise IndexError("Rute tidak ditemukan — data mungkin berubah, muat ulang halaman.")
        gone = rows.pop(index)
        _save(rows)
    return gone
