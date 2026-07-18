# -*- coding: utf-8 -*-
"""Ekstrak TEKS & TABEL dari 2 manual teknik China → manual_teks.json.gz.

Sumber (di luar tabel pin yg sudah ke pin_ecu & gambar yg sudah ke manual_media):
  1. Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf (256 hal) — naratif troubleshooting
     per-gangguan (故障触发条件/故障保护策略/故障可能原因/故障排查建议/故障排除后
     测试/特殊说明) + tabel DTC. Bagian yg SELAMA INI DITUNDA.
  2. skema/manual_tft_nanobcu.pdf (72 hal) — judul seksi + tabel data sensor +
     tabel kasus gangguan panel instrumen TFT NanoBCU.

Chunk PER-HALAMAN (naratif gangguan ≈ 1 hal; kadang meluber 2 hal → 2 record yg
sama-sama cocok topik). Record: {sumber, halaman, judul, teks, tabel, tipe,
blok?, gambar_ref}. `teks` = teks halaman (utuh, PRIMER — model membaca &
menerjemahkan runtime, pola pin_ecu). `blok` = struktur gangguan best-effort.
`gambar_ref` = PNG halaman itu dari manual_media (tautan, tampil inline).

⛔ TANPA pra-terjemah (berat) — teks China disimpan apa adanya, model terjemahkan
saat menjawab (fallback aman). Kamus anchor CN→ID inline (istilah struktur baku).

Jalankan dari root repo:  python backend/tools/build_manual_teks.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.knowledge_util import write_json_gz  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC_BOSCH = ROOT / "data" / "manuals" / "Manual_Sinotruk_MC_BOSCHECU_DH_CHINESE.pdf"
SRC_TFT = ROOT / "data" / "manuals" / "skema" / "manual_tft_nanobcu.pdf"
MEDIA_IDX = ROOT / "data" / "manual_media" / "index.json"
OUT = Path(__file__).resolve().parents[1] / "app" / "services" / "manual_teks.json.gz"

# Anchor struktur kartu gangguan Bosch CN → field Indonesia (nilai tetap China,
# diterjemah runtime). Urutan = urutan kemunculan di manual.
_ANCHORS = [
    ("故障触发条件", "kondisi_trigger"),
    ("故障保护策略", "reaksi"),
    ("故障可能原因", "penyebab"),
    ("故障排查建议", "langkah"),
    ("故障排除后测试", "tes_setelah"),
    ("特殊说明", "catatan"),
]
_LIST_FIELDS = {"penyebab", "langkah"}  # simpan sebagai list bernomor

# Baris boilerplate / footer yang dibuang.
_DROP_LINE = re.compile(
    r"^(space for sender information|confidential|q/cv&am china|"
    r"www\.continental|commercial vehicles|大陆汽车电子|"
    r"\d{1,3}(\s+\d{1,3})?)$", re.I)
_PCODE = re.compile(r"\bP[0-9A-F]{4}\b")


def _clean_lines(text: str) -> list[str]:
    out = []
    for ln in (text or "").split("\n"):
        s = " ".join(ln.split())
        if not s or _DROP_LINE.match(s):
            continue
        out.append(s)
    return out


def _parse_blok(lines: list[str]) -> dict | None:
    """Best-effort: pisah baris ke field anchor. Teks sebelum anchor pertama →
    kondisi_trigger (trigger biasanya memimpin). blok dibuat hanya bila ≥2 anchor
    hadir (agar bukan kecocokan kebetulan)."""
    amap = {cn: f for cn, f in _ANCHORS}
    buf: dict[str, list[str]] = {}
    cur = "kondisi_trigger"
    seen: set[str] = set()
    for s in lines:
        hit = next((cn for cn in amap if s.startswith(cn)), None)
        if hit:
            seen.add(hit)
            cur = amap[hit]
            rest = s[len(hit):].strip("：:").strip()
            buf.setdefault(cur, [])
            if rest:
                buf[cur].append(rest)
        else:
            buf.setdefault(cur, []).append(s)
    if len(seen) < 2:
        return None
    blok: dict = {}
    for _, field in _ANCHORS:
        vals = buf.get(field)
        if not vals:
            continue
        if field in _LIST_FIELDS:
            blok[field] = vals
        else:
            blok[field] = " ".join(vals)
    # kondisi_trigger sering memuat lead sebelum anchor — gabung bila ada
    if "kondisi_trigger" in buf and "kondisi_trigger" not in blok:
        blok["kondisi_trigger"] = " ".join(buf["kondisi_trigger"])
    return blok or None


def _media_map() -> dict[tuple[str, int], list[str]]:
    m: dict[tuple[str, int], list[str]] = {}
    try:
        for r in json.loads(MEDIA_IDX.read_text(encoding="utf-8")) or []:
            hal = r.get("halaman")
            if hal:
                m.setdefault((r.get("sumber"), int(hal)), []).append(r.get("file"))
    except Exception:
        pass
    return m


def _extract(path: Path, media: dict) -> list[dict]:
    import pdfplumber

    rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for i, pg in enumerate(pdf.pages):
            hal = i + 1
            lines = _clean_lines(pg.extract_text() or "")
            tabs = [t for t in (pg.extract_tables() or []) if t and len(t) > 1]
            gref = media.get((path.name, hal), [])
            # buang halaman kosong (tanpa teks bermakna, tabel, & gambar)
            if len(" ".join(lines)) < 40 and not tabs and not gref:
                continue
            judul = lines[0][:90] if lines else f"{path.stem} hal {hal}"
            blok = _parse_blok(lines) if lines else None
            tipe = ("gangguan" if blok else ("tabel" if tabs else "umum"))
            # dicari = boleh muncul dari cari_manual. TFT semua (seksi/tabel
            # panel instrumen); Bosch hanya KARTU GANGGUAN (tabel DTC/pin/TOC
            # sudah dilayani cari_kode_kesalahan/pin_ecu + tetap findable via 'kode').
            dicari = (path.name == SRC_TFT.name) or tipe == "gangguan"
            rec = {
                "sumber": path.name, "halaman": hal, "judul": judul,
                # judul_id + kata_kunci = SEED kosong; diisi pass kurasi (China→
                # Indonesia) supaya bisa dicari pakai istilah Indonesia.
                "judul_id": "", "kata_kunci": [],
                "teks": " \n".join(lines),
                "tabel": [[[ (c or "").strip() for c in r] for r in t] for t in tabs],
                "tipe": tipe, "dicari": dicari, "gambar_ref": sorted(gref),
            }
            if blok:
                rec["blok"] = blok
            kode = sorted(set(_PCODE.findall(rec["teks"])))
            if kode:
                rec["kode"] = kode
            rows.append(rec)
    return rows


def main() -> int:
    media = _media_map()
    rows: list[dict] = []
    for src, nama in ((SRC_BOSCH, "Bosch CN"), (SRC_TFT, "TFT NanoBCU")):
        if not src.exists():
            print(f"⚠️ lewati (tak ada): {src.name}")
            continue
        got = _extract(src, media)
        ng = sum(1 for r in got if r.get("blok"))
        print(f"  {nama:12s} {len(got):3d} record ({ng} kartu gangguan)")
        rows.extend(got)

    if len(rows) < 50:
        raise SystemExit(f"⛔ hanya {len(rows)} record — cek layout/lib?")

    # pertahankan kurasi (judul_id/kata_kunci) dari store lama — cocok sumber+hal
    kur: dict[tuple, dict] = {}
    if OUT.exists():
        try:
            import gzip
            with gzip.open(OUT, "rt", encoding="utf-8") as f:
                for r in json.load(f):
                    kur[(r.get("sumber"), r.get("halaman"))] = r
        except Exception:
            kur = {}
    for r in rows:
        prev = kur.get((r["sumber"], r["halaman"]))
        if prev:
            if prev.get("judul_id"):
                r["judul_id"] = prev["judul_id"]
            if prev.get("kata_kunci"):
                r["kata_kunci"] = prev["kata_kunci"]

    rows.sort(key=lambda r: (r["sumber"], r["halaman"]))
    write_json_gz(OUT, rows)
    tot_gref = sum(1 for r in rows if r.get("gambar_ref"))
    kur_n = sum(1 for r in rows if r.get("kata_kunci"))
    print(f"✅ {OUT.name}: {len(rows)} record; "
          f"{sum(1 for r in rows if r.get('blok'))} kartu gangguan; "
          f"{tot_gref} bergambar; {sum(1 for r in rows if r.get('dicari'))} dicari; "
          f"{kur_n} terkurasi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
