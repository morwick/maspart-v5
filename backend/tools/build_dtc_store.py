# -*- coding: utf-8 -*-
"""Bangun store DTC KANONIK — union 3 kamus kode kesalahan bertanda `sumber`.

Input (tabel per-sumber, hasil builder lama yang tetap dipertahankan):
  backend/app/services/fault_codes.json      (bosch: mesin Bosch MC, SPN/FMI, desc CHINA)
  backend/app/services/eol_dtc.json.gz       (eol  : EOL CNHTC 52 unit, Indonesia)
  backend/app/services/abs_scr_codes.json.gz (abs/scr: ABS WABCO + SCR gas 国V, Indonesia)
  backend/tools/i18n/fault_codes_i18n.json   (kamus statik desc_cn → Indonesia)

Output:
  backend/app/services/dtc_codes.json.gz — list-of-dict FLAT skema kanonik
  (kolom Indonesia; kolom tak berlaku utk suatu sumber = ""/None, TIDAK dihapus).
  Byte-stable: urutan baris deterministik + knowledge_util.write_json_gz.

Skema baris:
  sumber   : "bosch" | "eol" | "abs" | "scr"
  unit     : bosch→"EMS"; eol→unit ECU; abs→"ABS"; scr→"SCR"
  kode     : kode P/U (uppercase); abs = "" (dicari via SPN/FMI)
  spn, fmi : int | None (hanya bosch & abs)
  label    : bosch = label internal Bosch (english); abs = nama komponen SPN (English)
  deskripsi: arti gangguan (Indonesia; bosch dari kamus i18n — "" bila belum ada)
  deskripsi_cn: HANYA bosch — teks asli China (fallback + audit terjemahan)
  penyebab, perbaikan, part, reaksi, lampu, mil, svs, blink, sid

Validasi: jumlah baris per sumber wajib = jumlah tabel asal; spot-check kode kunci.
Jalankan dari root repo:  python backend/tools/build_dtc_store.py
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.knowledge_util import write_json_gz  # noqa: E402

SVC = Path(__file__).resolve().parents[1] / "app" / "services"
I18N = Path(__file__).resolve().parent / "i18n" / "fault_codes_i18n.json"
OUT = SVC / "dtc_codes.json.gz"


def _read_json(p: Path):
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(p.read_text(encoding="utf-8"))


def _baris(**kw) -> dict:
    """Baris kanonik lengkap — kolom tak berlaku diisi ""/None (deterministik).
    `kartu` True = ada lembar diagnosa PDF utk (spn,fmi) ini (detail terstruktur
    di dtc_diagnosa.json.gz — build_fault_cards.py)."""
    b = {
        "sumber": "", "unit": "", "kode": "",
        "spn": None, "fmi": None,
        "label": "", "deskripsi": "", "deskripsi_cn": "",
        "penyebab": "", "perbaikan": "", "part": "",
        "reaksi": "", "lampu": "", "mil": "", "svs": "",
        "blink": "", "sid": None, "kartu": False,
    }
    b.update(kw)
    return b


def dari_bosch(rows: list[dict], tr: dict) -> tuple[list[dict], int]:
    out, miss = [], 0
    for r in rows:
        cn = (r.get("desc_cn") or "").strip()
        idn = tr.get(cn, "")
        if cn and not idn:
            miss += 1
        out.append(_baris(
            sumber="bosch", unit="EMS",
            kode=(r.get("code") or "").upper(),
            spn=r.get("spn"), fmi=r.get("fmi"),
            label=r.get("english") or "",
            deskripsi=idn, deskripsi_cn=cn,
            mil=r.get("mil") or "", svs=r.get("svs") or "",
        ))
    return out, miss


# Sebagian besar baris EOL menyimpan SPN/FMI DI DALAM string kode ("SPN444/FMI14")
# alih-alih di kolom tersendiri. Selama ini kolom spn/fmi-nya dibiarkan None,
# sehingga ratusan pasangan yang datanya SUDAH KITA MILIKI — lengkap dengan
# deskripsi Indonesia — tak pernah terjangkau pencarian berbasis SPN/FMI.
# Kasus nyata dari log produksi: user menanyakan "SPN 444 FMI 14", dijawab
# "tidak ada di semua database", padahal barisnya ada di store.
_EOL_SPN_FMI_RE = re.compile(r"^SPN\s*(\d{1,6})\s*/\s*FMI\s*(\d{1,3})$", re.I)
_SPN_MAKS = 524287      # J1939: SPN 19-bit
_FMI_MAKS = 31          # J1939: FMI 5-bit


def _spn_fmi_dari_kode(kode: str) -> tuple[int | None, int | None]:
    """('SPN444/FMI14') → (444, 14). (None, None) bila bukan pola itu / di luar
    rentang J1939 (angka mustahil = salah parse, jangan cemari store)."""
    m = _EOL_SPN_FMI_RE.match((kode or "").strip())
    if not m:
        return None, None
    spn, fmi = int(m.group(1)), int(m.group(2))
    if spn <= 0 or spn > _SPN_MAKS or fmi < 0 or fmi > _FMI_MAKS:
        return None, None
    return spn, fmi


def dari_eol(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        kode = (r.get("kode") or "").upper()
        spn, fmi = _spn_fmi_dari_kode(kode)
        out.append(_baris(
            sumber="eol", unit=r.get("unit") or "",
            kode=kode, spn=spn, fmi=fmi,
            deskripsi=r.get("deskripsi") or "",
            penyebab=r.get("penyebab") or "",
            perbaikan=r.get("perbaikan") or "",
            part=r.get("part") or "",
        ))
    return out


def dari_abs_scr(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        sistem = (r.get("sistem") or "").upper()
        if sistem == "ABS":
            out.append(_baris(
                sumber="abs", unit="ABS",
                spn=r.get("spn"), fmi=r.get("fmi"), sid=r.get("sid"),
                blink=r.get("blink") or "",
                label=r.get("komponen") or "",
                deskripsi=r.get("deskripsi") or "",
                penyebab=r.get("penyebab") or "",
                reaksi=r.get("reaksi") or "",
                perbaikan=r.get("perbaikan") or "",
                lampu=r.get("lampu") or "",
            ))
        else:  # SCR
            out.append(_baris(
                sumber="scr", unit="SCR",
                kode=(r.get("kode") or "").upper(),
                part=r.get("komponen") or "",
                deskripsi=r.get("deskripsi") or "",
                penyebab=r.get("penyebab") or "",
                reaksi=r.get("reaksi") or "",
            ))
    return out


def _sort_key(r: dict):
    return (r["sumber"], r["unit"], r["kode"],
            r["spn"] if r["spn"] is not None else -1,
            r["fmi"] if r["fmi"] is not None else -1,
            r["deskripsi"], r["deskripsi_cn"], r["blink"], r["perbaikan"])


def main() -> int:
    bosch_src = _read_json(SVC / "fault_codes.json")
    eol_src = _read_json(SVC / "eol_dtc.json.gz")
    abs_scr_src = _read_json(SVC / "abs_scr_codes.json.gz")
    tr = json.loads(I18N.read_text(encoding="utf-8")) if I18N.exists() else {}

    bosch, miss = dari_bosch(bosch_src, tr)
    eol = dari_eol(eol_src)
    abs_scr = dari_abs_scr(abs_scr_src)

    # ── Lembar diagnosa PDF (dtc_diagnosa.json.gz, build_fault_cards.py) ──
    # Tandai baris ber-(spn,fmi) yang punya kartu; pasangan PDF-ONLY (tak ada
    # di tabel Bosch — 32 pasangan) ditambahkan sbg baris sumber="kartu" agar
    # terjangkau pencarian SPN/FMI.
    kartu_rows: list[dict] = []
    dp = SVC / "dtc_diagnosa.json.gz"
    if dp.exists():
        cards = _read_json(dp)
        pdf_pairs = {(c["spn"], c["fmi"]) for c in cards}
        ada = set()
        for r in bosch + abs_scr:
            if (r["spn"], r["fmi"]) in pdf_pairs:
                r["kartu"] = True
                ada.add((r["spn"], r["fmi"]))
        seen_new: set = set()
        for c in sorted(cards, key=lambda c: (c["spn"], c["fmi"], c.get("varian") or "")):
            pair = (c["spn"], c["fmi"])
            if pair in ada or pair in seen_new:
                continue
            seen_new.add(pair)
            kartu_rows.append(_baris(
                sumber="kartu", unit="EMS",
                kode=(c.get("kode") or "").upper(),
                spn=c["spn"], fmi=c["fmi"],
                deskripsi=c.get("judul") or "",
                part=c.get("part_terkait") or "",
                reaksi=c.get("reaksi") or "",
                kartu=True,
            ))
        print(f"   kartu PDF: {len(pdf_pairs)} pasangan; flag pada tabel: {len(ada)}; "
              f"baris baru sumber='kartu': {len(kartu_rows)}")

    # ── Arsip CSV EOL mentah (build_eol_csv) — sumber KELIMA ──
    # Hanya pasangan (spn,fmi) yang BELUM terwakili sumber lain. Fail-soft: arsip
    # ada di mesin pemilik (luar repo), jadi build tetap sukses tanpanya.
    csv_rows: list[dict] = []
    try:
        from build_eol_csv import kumpulkan as _csv_kumpulkan
        tambahan = _csv_kumpulkan()
    except Exception as e:      # noqa: BLE001 — arsip opsional
        print(f"   arsip CSV dilewati: {e}")
        tambahan = []
    if tambahan:
        sudah = {(r["spn"], r["fmi"]) for r in bosch + eol + abs_scr + kartu_rows
                 if r["spn"] is not None and r["fmi"] is not None}
        for r in tambahan:
            pair = (r["spn"], r["fmi"])
            if pair in sudah:
                continue
            sudah.add(pair)
            csv_rows.append(_baris(
                sumber="eolcsv", unit=r.get("unit") or "EOL",
                kode=r.get("kode") or "", spn=r["spn"], fmi=r["fmi"],
                label=r.get("label") or "",
                deskripsi=r.get("deskripsi") or "",
                deskripsi_cn=r.get("deskripsi_cn") or "",
                penyebab=r.get("penyebab") or "",
                perbaikan=r.get("perbaikan") or "",
                part=r.get("part") or "",
            ))
        ber_id = sum(1 for r in csv_rows if r["deskripsi"])
        print(f"   arsip CSV: {len(tambahan)} pasangan dibaca; BARU (belum ada di "
              f"sumber lain): {len(csv_rows)} (ber-deskripsi Indonesia: {ber_id})")

    rows = sorted(bosch + eol + abs_scr + kartu_rows + csv_rows, key=_sort_key)

    # ── validasi ──
    if len(bosch) != len(bosch_src) or len(eol) != len(eol_src) \
            or len(abs_scr) != len(abs_scr_src):
        raise SystemExit("⛔ jumlah baris tak cocok dgn tabel asal")
    per = {}
    for r in rows:
        per[r["sumber"]] = per.get(r["sumber"], 0) + 1
        if not r["sumber"] or not r["unit"]:
            raise SystemExit(f"⛔ baris tanpa sumber/unit: {r}")
    spot = {
        "bosch P0645": any(r["kode"] == "P0645" for r in bosch),
        "abs SPN 789": any(r["spn"] == 789 for r in abs_scr if r["sumber"] == "abs"),
        "scr P0427": any(r["kode"] == "P0427" for r in abs_scr if r["sumber"] == "scr"),
        "eol P0100*": any(r["kode"].startswith("P0100") for r in eol),
        # SPN/FMI yang di-parse dari string kode EOL — dulu selalu None sehingga
        # baris ini tak terjangkau pencarian SPN.
        "eol SPN444/FMI14": any(r["spn"] == 444 and r["fmi"] == 14 for r in eol),
    }
    gagal = [k for k, ok in spot.items() if not ok]
    if gagal:
        raise SystemExit(f"⛔ spot-check gagal: {gagal}")
    n_eol_spn = sum(1 for r in eol if r["spn"] is not None)
    print(f"   EOL ber-SPN/FMI hasil parse kode: {n_eol_spn} baris")

    terisi = sum(1 for r in bosch if r["deskripsi"])
    write_json_gz(OUT, rows)
    print(f"✅ {OUT.name}: {len(rows)} baris {per}")
    print(f"   deskripsi ID bosch terisi: {terisi}/{len(bosch)} "
          f"({terisi / max(len(bosch), 1):.1%}); miss i18n: {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
