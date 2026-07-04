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
import threading
import time
import uuid
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


# ── Export GENERIK dari asisten (tool AI `buat_excel`) ──────────────────────
# Asisten menyusun judul+kolom+baris dari HASIL TOOL percakapan; payload disimpan
# in-memory ber-TTL di sini, frontend mengunduh via GET /api/ai/excel/{id}.
# Uvicorn berjalan 1 worker (lihat Dockerfile) → dict module-level aman.
_STASH_TTL_SEC = 24 * 3600.0
_STASH_MAX = 200               # plafon entri (jaga memori)
_stash_lock = threading.Lock()
_stash: dict[str, dict] = {}   # id -> {at, judul, kolom, baris, filename}

# Header kolom yang isinya kode/PN → font mono di Excel.
_MONO_HEAD_RE = re.compile(r"\b(part\s*number|part\s*no|pn|nomor\s*part|kode)\b", re.IGNORECASE)


def _stash_put(entry: dict, judul: str) -> tuple[str, str]:
    now = time.monotonic()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", judul).strip("_")[:60] or "Data_MASPART"
    filename = f"{slug}.xlsx"
    export_id = uuid.uuid4().hex
    with _stash_lock:
        for k in [k for k, v in _stash.items() if now - v["at"] > _STASH_TTL_SEC]:
            _stash.pop(k, None)
        while len(_stash) >= _STASH_MAX:   # buang paling tua
            _stash.pop(min(_stash, key=lambda k: _stash[k]["at"]), None)
        _stash[export_id] = {"at": now, "judul": judul, "filename": filename, **entry}
    return export_id, filename


def stash_export(judul: str, kolom: list[str], baris: list[list[str]]) -> tuple[str, str]:
    """Simpan payload export tabel polos → (export_id, filename)."""
    return _stash_put({"kolom": kolom, "baris": baris}, judul)


def stash_builder(judul: str, builder: dict) -> tuple[str, str]:
    """Simpan RESEP export yang dibangun SAAT DIUNDUH (mis. katalog bergambar —
    berat, jadi dibangun ketika kartu diklik lalu bytes-nya di-cache di entri)."""
    return _stash_put({"builder": builder}, judul)


def generic_excel(export_id: str) -> tuple[bytes | None, str]:
    """Bangun workbook dari payload tersimpan. (bytes, filename) atau (None, pesan)."""
    with _stash_lock:
        d = _stash.get(export_id)
        if d and time.monotonic() - d["at"] > _STASH_TTL_SEC:
            _stash.pop(export_id, None)
            d = None
    if not d:
        return None, ("File sudah kedaluwarsa / tidak ditemukan. Minta asisten "
                      "buatkan Excel-nya lagi.")

    # Entri ber-'builder' = dibangun saat diunduh (berat); bytes di-cache di entri
    # supaya klik berikutnya instan.
    if d.get("builder"):
        cached = d.get("_bytes")
        if cached:
            return cached, d["filename"]
        b = d["builder"]
        if b.get("kind") == "katalog":
            data, err = katalog_excel(b.get("rangka", ""), b.get("kategori", ""))
            if data is None:
                return None, err
            with _stash_lock:
                d["_bytes"] = data
            return data, d["filename"]
        return None, "Jenis export tidak dikenal."

    kolom: list[str] = d["kolom"]
    baris: list[list[str]] = d["baris"]
    mono_cols = {j for j, h in enumerate(kolom, start=1) if _MONO_HEAD_RE.search(h or "")}
    # Lebar kolom mengikuti isi (min 8, maks 60).
    widths = []
    for j, h in enumerate(kolom):
        w = max([len(h or "")] + [len(r[j]) for r in baris if j < len(r)] or [0])
        widths.append(max(8, min(60, w + 4)))

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.sheet_view.showGridLines = False
    start = _title(ws, d["judul"],
                   f"{len(baris)} baris · MASPART Asisten AI", max(2, len(kolom)))
    for j, (h, w) in enumerate(zip(kolom, widths), start=1):
        c = ws.cell(row=start, column=j, value=h)
        c.fill = _HEAD_FILL
        c.font = _WHITE
        c.alignment = _CENTER
        c.border = _BORDER
        ws.column_dimensions[get_column_letter(j)].width = w
    r = start + 1
    for i, row in enumerate(baris):
        for j in range(1, len(kolom) + 1):
            val = row[j - 1] if j - 1 < len(row) else ""
            c = ws.cell(row=r, column=j, value=val)
            c.border = _BORDER
            c.alignment = _CENTER if j == 1 or j in mono_cols else _LEFT
            c.font = _MONO if j in mono_cols else _INK
            if i % 2:
                c.fill = _ZEBRA
        r += 1
    ws.freeze_panes = ws.cell(row=start + 1, column=1)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), d["filename"]


# ── KATALOG BERGAMBAR per kategori (exploded view EPC) ──────────────────────
# Plafon seksi figure — katalog LENGKAP unit besar bisa ~500 (PB087964: 477).
_KATALOG_MAX_FIGURES = 800
_KATALOG_IMG_WIDTH = 1400      # px render SVG→PNG (tajam saat di-zoom)
_KATALOG_IMG_VIEW = 660        # px lebar tampil di sheet


def _svg_to_png(svg: bytes, width: int = _KATALOG_IMG_WIDTH) -> bytes | None:
    """Render SVG exploded view EPC (Creo Illustrate) → PNG via resvg.
    resvg menolak width/height ber-satuan mm di tag root → buang atributnya
    (viewBox tetap menjaga proporsi). None bila gagal/resvg tak terpasang."""
    if not svg:
        return None
    try:
        import resvg_py
    except Exception:
        return None
    try:
        i = svg.find(b"<svg")
        if i < 0:
            return None
        txt = svg[i:].decode("utf-8", "replace")
        head, rest = txt.split(">", 1)
        head = re.sub(r'\s(width|height)="[^"]*"', "", head)
        return bytes(resvg_py.svg_to_bytes(svg_string=head + ">" + rest, width=width))
    except Exception:
        return None


def katalog_excel(rangka: str, kategori: str) -> tuple[bytes | None, str]:
    """KATALOG PART BERGAMBAR satu kategori per-VIN: satu sheet per FIGURE
    (gambar exploded view EPC + tabel part ber-nomor balon + stok/harga lokal)
    + sheet Ringkasan. Return (bytes, filename) atau (None, pesan_error)."""
    from . import epc_bom as _epc

    d = _epc.catalog_walk(rangka, kategori)
    if not d.get("found"):
        return None, (d.get("message") or "Gagal mengambil katalog dari EPC. Coba lagi.")
    figures = (d.get("figures") or [])[:_KATALOG_MAX_FIGURES]
    if not figures:
        return None, f"Tidak ada figure untuk kategori '{kategori}' di unit ini."
    frame = d.get("frame_number") or ""

    # 1) Unduh + render SEMUA gambar paralel (nama file → PNG bytes).
    svg_names = list(dict.fromkeys(f["svg"] for f in figures if f.get("svg")))

    def _render(name: str) -> tuple[str, bytes | None]:
        return name, _svg_to_png(_epc.fetch_file(name))

    pngs: dict[str, bytes] = {}
    if svg_names:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for name, png in ex.map(_render, svg_names):
                if png:
                    pngs[name] = png

    # 2) Stok/harga lokal utk SEMUA PN (sekali query).
    all_pns = list({it["pn"] for f in figures for it in f["items"]})
    local: dict[str, dict] = {}
    for r in part_index.search_exact_pns(all_pns):
        pn = (r.get("part_number") or "").upper()
        if pn and pn not in local:
            local[pn] = r

    # ── SATU SHEET, alur vertikal per figure: bar seksi → GAMBAR → TABEL.
    #    Plus DAFTAR ISI ber-hyperlink di atas & link "↑ Daftar Isi" di tiap seksi,
    #    supaya 60+ seksi mudah dinavigasi dan urutan bacanya tak membingungkan. ──
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.worksheet.hyperlink import Hyperlink

    wb = Workbook()
    ws = wb.active
    ws.title = "Katalog"
    ws.sheet_view.showGridLines = False
    n_part = sum(len(f["items"]) for f in figures)

    headers = ["No. Balon", "Part Number", "Nama Part", "Qty", "Stok", "Harga", "Pengganti"]
    widths = [10, 20, 52, 6, 8, 14, 20]
    ncol = len(headers)
    for j, wd in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = wd

    kat_title = ("Lengkap (Semua Kategori)" if d.get("lengkap") else kategori.title())
    _title(ws, f"Katalog {kat_title} — Unit {frame}",
           f"{len(figures)} figure · {n_part} part · gambar exploded view resmi EPC "
           "(Parts Atlas per-VIN) · MASPART Asisten AI", ncol)
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=ncol)
    note = ws.cell(row=3, column=1,
                   value="ℹ️ Cara baca: tiap figure = GAMBAR lalu TABEL part-nya di bawah. "
                         "Angka pada gambar = kolom 'No. Balon' — baris itulah Part Number-nya.")
    note.font = Font(color=_BRAND_DK, size=10, bold=True)
    note.fill = _SUB1_FILL
    note.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.freeze_panes = "A4"   # judul + cara baca tetap terlihat saat scroll

    _ROW_PX = 19.0    # tinggi baris default Excel ±19px — utk melewati tinggi gambar
    _LINK_FONT = Font(color="0563C1", underline="single", size=10)

    def _num(v):
        """Angka murni ditulis sbg NUMBER (hindari segitiga hijau 'number as text')."""
        if isinstance(v, (int, float)):
            return v
        s = str(v or "").strip()
        return int(s) if s.isdigit() else (s or None)

    # ── DAFTAR ISI (target hyperlink diisi setelah posisi seksi diketahui) ──
    TOC_BAR = 5
    ws.merge_cells(start_row=TOC_BAR, start_column=1, end_row=TOC_BAR, end_column=ncol)
    tb = ws.cell(row=TOC_BAR, column=1,
                 value="DAFTAR ISI — klik nama figure untuk melompat ke bagiannya")
    tb.fill = _HEAD_FILL
    tb.font = Font(color="FFFFFF", bold=True, size=12)
    tb.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[TOC_BAR].height = 22
    toc_first = TOC_BAR + 1
    for i, f in enumerate(figures):
        r = toc_first + i
        cno = ws.cell(row=r, column=1, value=i + 1)
        cno.alignment = _CENTER
        cno.border = _BORDER
        cno.font = _INK
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        cn = ws.cell(row=r, column=2, value=f["nama"] + ("" if f.get("svg") else "  (tanpa gambar di EPC)"))
        cn.font = _LINK_FONT
        cn.alignment = _LEFT
        cn.border = _BORDER
        cq = ws.cell(row=r, column=4, value=len(f["items"]))
        cq.alignment = _CENTER
        cq.border = _BORDER
        cq.font = _INK
        # Kolom kelompok — penting utk katalog lengkap (kabin/sasis/kelistrikan/…).
        ws.merge_cells(start_row=r, start_column=5, end_row=r, end_column=7)
        ck = ws.cell(row=r, column=5, value=f.get("kategori") or "")
        ck.font = Font(color="535B56", size=9)
        ck.alignment = _LEFT
        ck.border = _BORDER
        if i % 2:
            for j in range(1, 8):
                ws.cell(row=r, column=j).fill = _ZEBRA
    row = toc_first + len(figures) + 2

    # ── Seksi per figure: bar → gambar → tabel ──
    sec_rows: list[int] = []
    for i, f in enumerate(figures):
        sec_rows.append(row)
        # Bar seksi: judul (A..F) + link kembali ke daftar isi (G).
        kat_lbl = (f.get("kategori") or "").strip()
        kat_sfx = f"  ·  {kat_lbl}" if kat_lbl and kat_lbl not in f["nama"] else ""
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncol - 1)
        c = ws.cell(row=row, column=1,
                    value=f"{i + 1:02d}. {f['nama']}  ·  Figure {f['kode']} · {len(f['items'])} part"
                          + kat_sfx + ("" if f.get("svg") else "  ·  (tanpa gambar di EPC)"))
        c.fill = _HEAD_FILL
        c.font = Font(color="FFFFFF", bold=True, size=12)
        c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        back = ws.cell(row=row, column=ncol, value="↑ Daftar Isi")
        back.fill = _HEAD_FILL
        back.font = Font(color="FFFFFF", underline="single", size=10)
        back.alignment = _CENTER
        back.hyperlink = Hyperlink(ref=f"{get_column_letter(ncol)}{row}",
                                   location=f"Katalog!A{TOC_BAR}")
        ws.row_dimensions[row].height = 22
        row += 1

        # GAMBAR tepat di bawah bar seksi (kolom B) — alur baca vertikal.
        png = pngs.get(f.get("svg") or "")
        if png:
            img = XLImage(io.BytesIO(png))
            ratio = img.height / img.width if img.width else 1
            img.width = _KATALOG_IMG_VIEW
            img.height = int(_KATALOG_IMG_VIEW * ratio)
            ws.add_image(img, f"B{row}")
            row += int(img.height / _ROW_PX) + 2

        # TABEL part figure ini.
        for j, h in enumerate(headers, start=1):
            hc = ws.cell(row=row, column=j, value=h)
            hc.fill = _SUB1_FILL
            hc.font = Font(bold=True, color=_BRAND_DK, size=10)
            hc.alignment = _CENTER
            hc.border = _BORDER
        row += 1
        items = sorted(f["items"], key=lambda x: (x.get("balon") is None, x.get("balon") or 0))
        for k, it in enumerate(items):
            lr = local.get(it["pn"], {})
            nama = " ".join((lr.get("part_name") or it["nama"] or it["nama_cn"]).split())
            vals = [_num(it.get("balon")), it["pn"], nama, _num(it.get("qty")),
                    _num(lr.get("stok")) if lr else None,
                    (lr.get("harga") or None) if lr else None,
                    ", ".join(it.get("pengganti") or []) or None]
            for j, v in enumerate(vals, start=1):
                dc = ws.cell(row=row, column=j, value=v)
                dc.border = _BORDER
                dc.font = _MONO if j == 2 else _INK
                dc.alignment = _CENTER if j in (1, 4, 5) else _LEFT
                if k % 2:
                    dc.fill = _ZEBRA
            row += 1
        row += 2   # jeda antar seksi

    # Isi hyperlink DAFTAR ISI → baris bar tiap seksi.
    for i, target in enumerate(sec_rows):
        r = toc_first + i
        ws.cell(row=r, column=2).hyperlink = Hyperlink(
            ref=f"B{r}", location=f"Katalog!A{target}")

    buf = io.BytesIO()
    wb.save(buf)
    kat_slug = re.sub(r"[^A-Za-z0-9]+", "_", kategori).strip("_")[:24] or "Kategori"
    return buf.getvalue(), f"Katalog_{kat_slug}_{frame}.xlsx"
