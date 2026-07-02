"""
Export Excel untuk hasil Asisten AI — dibangun RAPI & profesional (bukan dump polos).

Saat ini: perbandingan PART dua unit (banding_rangka) → workbook berwarna dengan
sheet Ringkasan + sheet part-beda per sisi + sheet part-sama. Sumber sama dengan
tool banding_rangka (EPC Loading List per-VIN), tapi TANPA cap 30 baris — Excel
memuat SELURUH part yang berbeda.
"""
from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import catalog_bom, epc_bom, part_index

# ── Palet warna (samakan dengan UI brand) ──
_BRAND = "028912"
_BRAND_DK = "026A0E"
_HEAD_FILL = PatternFill("solid", fgColor=_BRAND)
_SUB1_FILL = PatternFill("solid", fgColor="EAF6EC")   # hijau muda
_SUB2_FILL = PatternFill("solid", fgColor="FFF3E2")   # oranye muda (sisi 2)
_ZEBRA = PatternFill("solid", fgColor="F8F9F7")
_WHITE = Font(color="FFFFFF", bold=True, size=11)
_TITLE_FONT = Font(color="FFFFFF", bold=True, size=15)
_BOLD = Font(bold=True, color="1B211D")
_INK = Font(color="1B211D")
_MONO = Font(name="Consolas", color="0F1411")
_THIN = Side(style="thin", color="E1E4E1")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_CENTER = Alignment(horizontal="center", vertical="center")
_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)


# ── Perbandingan PENUH dua VIN (tanpa cap) ──
def compare_rangka(rangka_1: str, rangka_2: str, kategori: str = "") -> dict:
    """Bandingkan SET PART dua unit dari EPC Loading List. Return:
      {ok:True, frame_1, frame_2, kat_nama, total_1, total_2,
       same:[{pn,nama,qty}], only1:[...], only2:[...]}  atau  {ok:False, error}."""
    r1 = (rangka_1 or "").strip()
    r2 = (rangka_2 or "").strip()
    if not r1 or not r2:
        return {"ok": False, "error": "Butuh dua nomor rangka."}

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(epc_bom.loading_list, r1)
        f2 = ex.submit(epc_bom.loading_list, r2)
        ll1, ll2 = f1.result(), f2.result()
    for ll in (ll1, ll2):
        if not ll.get("found"):
            return {"ok": False, "error": "Salah satu unit tak ditemukan / token EPC bermasalah."}
        if ll.get("partial"):
            return {"ok": False, "error": "Data EPC salah satu unit terbaca tidak lengkap — coba lagi."}

    code = None
    kat_nama = "SEMUA part"
    if kategori:
        code = catalog_bom.resolve_kategori(kategori) if catalog_bom.available() else None
        if not code:
            return {"ok": False, "error": f"Kategori '{kategori}' tak dikenal."}
        kat_nama = catalog_bom.KATEGORI_NAMA.get(code, kategori)

    pncat = catalog_bom.pn_category_map() if catalog_bom.available() else {}

    def _cat(pn: str) -> str:
        return (pncat.get(catalog_bom._norm(pn)) or {}).get("kategori") or "00"

    def _set(ll: dict) -> dict:
        out = {}
        for p in ll.get("parts", []):
            pn = p.get("pn")
            if not pn or (code and _cat(pn) != code):
                continue
            out[pn] = p
        return out

    A, B = _set(ll1), _set(ll2)
    sa, sb = set(A), set(B)
    only1, only2, same = sorted(sa - sb), sorted(sb - sa), sorted(sa & sb)

    # Nama Inggris lokal untuk SEMUA PN (sekali query), fallback ke kamus EPC.
    localn: dict[str, str] = {}
    for r in part_index.search_exact_pns(list(sa | sb)):
        pn = (r.get("part_number") or "").upper()
        if pn and pn not in localn:
            localn[pn] = r.get("part_name") or ""

    def _row(pn: str, src: dict) -> dict:
        p = src.get(pn, {})
        en = localn.get(pn) or epc_bom.translate_cn(p.get("nama_cn"))
        return {"pn": pn, "nama": " ".join((en or p.get("nama_cn") or "").split()),
                "nama_china": " ".join((p.get("nama_cn") or "").split()),
                "qty": p.get("qty")}

    return {
        "ok": True,
        "frame_1": ll1.get("frame_number"), "frame_2": ll2.get("frame_number"),
        "kat_nama": kat_nama,
        "total_1": len(A), "total_2": len(B),
        "same": [_row(pn, A) for pn in same],
        "only1": [_row(pn, A) for pn in only1],
        "only2": [_row(pn, B) for pn in only2],
    }


# ── Pembangun Excel ber-styling ──
def _style_table(ws, start_row: int, headers: list[str], rows: list[list],
                 head_fill: PatternFill, widths: list[int]) -> int:
    """Tulis satu tabel bergaya mulai dari start_row. Return baris SETELAH tabel."""
    for j, (h, w) in enumerate(zip(headers, widths), start=1):
        c = ws.cell(row=start_row, column=j, value=h)
        c.fill = head_fill
        c.font = _WHITE
        c.alignment = _CENTER
        c.border = _BORDER
        ws.column_dimensions[get_column_letter(j)].width = w
    r = start_row + 1
    for i, row in enumerate(rows):
        for j, val in enumerate(row, start=1):
            c = ws.cell(row=r, column=j, value=val)
            c.border = _BORDER
            c.alignment = _CENTER if j in (1, len(headers)) else _LEFT
            if j == 2:  # kolom PN → mono
                c.font = _MONO
            else:
                c.font = _INK
            if i % 2:
                c.fill = _ZEBRA
        r += 1
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    return r


def _title(ws, text: str, sub: str, ncol: int) -> int:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    t = ws.cell(row=1, column=1, value=text)
    t.fill = _HEAD_FILL
    t.font = _TITLE_FONT
    t.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    s = ws.cell(row=2, column=2 if False else 1, value=sub)
    s.font = Font(color="535B56", size=10, italic=True)
    s.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    return 4  # baris mulai konten


def banding_rangka_excel(rangka_1: str, rangka_2: str, kategori: str = "") -> tuple[bytes | None, str]:
    """Bangun workbook perbandingan. Return (bytes, filename) atau (None, pesan_error)."""
    d = compare_rangka(rangka_1, rangka_2, kategori)
    if not d.get("ok"):
        return None, d.get("error") or "Gagal membandingkan."

    f1, f2 = d["frame_1"], d["frame_2"]
    kat = d["kat_nama"]
    identik = not d["only1"] and not d["only2"]
    wb = Workbook()

    # ── Sheet 1: Ringkasan ──
    ws = wb.active
    ws.title = "Ringkasan"
    ws.sheet_view.showGridLines = False
    _title(ws, f"Perbandingan Part {kat} — {f1} vs {f2}",
           "Sumber: EPC Loading List per-VIN (Sinotruk) · MASPART Asisten AI", 4)
    rows = [
        ["Total part", d["total_1"], d["total_2"], ""],
        ["Part SAMA", len(d["same"]), len(d["same"]), ""],
        ["Hanya di unit ini", len(d["only1"]), len(d["only2"]), ""],
        ["Total BEDA", len(d["only1"]) + len(d["only2"]),
         "", "identik" if identik else "berbeda"],
    ]
    after = _style_table(
        ws, 4, ["Item", f"Rangka 1 ({f1})", f"Rangka 2 ({f2})", "Keterangan"],
        rows, _HEAD_FILL, [26, 22, 22, 16])
    # Kesimpulan
    verdict = ("✔ KEDUA UNIT SAMA PERSIS (semua part identik pada kategori ini)."
               if identik else
               f"✘ TIDAK SAMA PERSIS — {len(d['only1']) + len(d['only2'])} part berbeda "
               f"({len(d['only1'])} hanya di {f1}, {len(d['only2'])} hanya di {f2}).")
    ws.merge_cells(start_row=after + 1, start_column=1, end_row=after + 1, end_column=4)
    cc = ws.cell(row=after + 1, column=1, value=verdict)
    cc.font = Font(bold=True, color=(_BRAND_DK if identik else "B35C00"), size=11)
    cc.fill = _SUB1_FILL if identik else _SUB2_FILL
    cc.alignment = Alignment(horizontal="left", vertical="center", indent=1, wrap_text=True)
    ws.row_dimensions[after + 1].height = 26

    # ── Sheet 2 & 3: part beda per sisi ──
    def _diff_sheet(name: str, frame: str, items: list[dict], fill: PatternFill):
        w = wb.create_sheet(name)
        w.sheet_view.showGridLines = False
        _title(w, f"Part hanya di {frame} ({kat})",
               f"{len(items)} part — tidak ada di unit pembanding", 4)
        data = [[i + 1, it["pn"], it["nama"] or it["nama_china"], it["qty"]]
                for i, it in enumerate(items)]
        _style_table(w, 4, ["No", "Part Number", "Nama Part", "Qty"],
                     data or [["", "(tidak ada)", "", ""]], fill, [6, 24, 60, 8])

    _diff_sheet("Hanya Rangka 1", f1, d["only1"], _HEAD_FILL)
    _diff_sheet("Hanya Rangka 2", f2, d["only2"],
                PatternFill("solid", fgColor="B35C00"))

    # ── Sheet 4: part sama (referensi) ──
    ws4 = wb.create_sheet("Part Sama")
    ws4.sheet_view.showGridLines = False
    _title(ws4, f"Part SAMA di kedua unit ({kat})",
           f"{len(d['same'])} part identik pada {f1} & {f2}", 4)
    same_data = [[i + 1, it["pn"], it["nama"] or it["nama_china"], it["qty"]]
                 for i, it in enumerate(d["same"])]
    _style_table(ws4, 4, ["No", "Part Number", "Nama Part", "Qty"],
                 same_data or [["", "(tidak ada)", "", ""]], _HEAD_FILL, [6, 24, 60, 8])

    buf = io.BytesIO()
    wb.save(buf)
    kat_sfx = "" if kat == "SEMUA part" else "_" + re.sub(r"[^A-Za-z0-9]+", "", kat)[:20]
    fname = f"Perbandingan_{f1}_vs_{f2}{kat_sfx}.xlsx"
    return buf.getvalue(), fname
