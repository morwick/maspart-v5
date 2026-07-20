"""Ekstraksi isi berkas → "bagian" mentah untuk diindeks jadi chunk.

Satu bagian = {teks, tabel, halaman, sub, gambar[]} di mana `gambar` = list bytes
PNG/JPG yang ditemukan di bagian itu. Modul ini SENGAJA bodoh: ia hanya membaca &
memotong, tidak menyentuh store, tidak memanggil LLM.

Semua parser berat di-LAZY IMPORT: server 3,8 GB dengan torch/DINOv2 ~1 GB sudah
sesak, jadi RSS idle tak boleh naik hanya karena fitur ini terpasang. Guard ukuran
& jumlah halaman/sheet/gambar ada di sini, bukan di pemanggil.

Pakai ulang: `ai_sheet._read_csv` (decode utf-8-sig→cp1252 + sniffer delimiter)
dan konstanta MAX_COLS/MAX_ROWS-nya, supaya perilaku CSV sama persis dengan
fitur unggah Excel ke chat yang sudah dipakai.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from . import ai_sheet

# ── plafon (RAM & waktu) ─────────────────────────────────────────────
MAX_TXT_BYTES = 2 * 1024 * 1024
MAX_SHEET = 12
MAX_BARIS_TABEL = 30          # baris per chunk tabel
MAX_KOLOM_TABEL = 12
MAX_GAMBAR = 60
MAX_HALAMAN_PDF = 200
# .docx/.xlsx = arsip ZIP — file kecil bisa mengembang jadi ratusan MB saat
# di-ekstrak (zip-bomb). Cek ukuran TERURAI sebelum membuka isinya.
MAX_ZIP_URAI = 100 * 1024 * 1024
MAX_ZIP_ENTRI = 2000

EKSTENSI = (".pdf", ".xlsx", ".xlsm", ".csv", ".docx", ".txt",
            ".png", ".jpg", ".jpeg")

# ── chunking ─────────────────────────────────────────────────────────
TARGET_CHUNK = 1200
MAX_CHUNK = 1800
OVERLAP = 120


def _potong_di_batas(s: str, batas: int) -> int:
    """Titik potong terbaik ≤ batas: paragraf > baris > kalimat > batas kata."""
    for pola in ("\n\n", "\n", ". "):
        i = s.rfind(pola, batas // 2, batas)
        if i > 0:
            return i + len(pola)
    i = s.rfind(" ", batas // 2, batas)
    return i + 1 if i > 0 else batas


def potong_teks(teks: str, target: int = TARGET_CHUNK) -> list[str]:
    """Pecah prosa jadi potongan ~target char dengan overlap kecil, tak pernah
    memotong di tengah kata. Overlap menjaga kalimat yang terbelah tetap utuh
    di salah satu potongan."""
    s = (teks or "").strip()
    if not s:
        return []
    if len(s) <= MAX_CHUNK:
        return [s]
    out: list[str] = []
    pos = 0
    while pos < len(s):
        sisa = s[pos:]
        if len(sisa) <= MAX_CHUNK:
            out.append(sisa.strip())
            break
        cut = _potong_di_batas(sisa, target)
        out.append(sisa[:cut].strip())
        pos += max(cut - OVERLAP, 1)
    return [c for c in out if c]


def _bagian(teks="", tabel=None, halaman=0, sub="", gambar=None) -> dict:
    return {"teks": teks or "", "tabel": tabel or [], "halaman": halaman,
            "sub": sub or "", "gambar": gambar or []}


def _rapikan_baris(baris) -> list[str]:
    out = []
    for c in list(baris)[:MAX_KOLOM_TABEL]:
        out.append("" if c is None else str(c).strip())
    while out and not out[-1]:
        out.pop()
    return out


def _tabel_jadi_bagian(rows: list[list], halaman: int, sub: str) -> list[dict]:
    """Baris tabel → bagian ber-`tabel`, dipotong per MAX_BARIS_TABEL dengan
    header diulang di tiap potongan supaya tiap chunk berdiri sendiri."""
    bersih = [_rapikan_baris(r) for r in rows]
    bersih = [r for r in bersih if any(r)]
    if not bersih:
        return []
    header = bersih[0]
    isi = bersih[1:] or []
    if not isi:
        return [_bagian(tabel=[header], halaman=halaman, sub=sub)]
    out = []
    for i in range(0, len(isi), MAX_BARIS_TABEL):
        potong = isi[i:i + MAX_BARIS_TABEL]
        out.append(_bagian(tabel=[header, *potong],
                           halaman=halaman + i if halaman else i + 2, sub=sub))
    return out


# ── format sederhana ─────────────────────────────────────────────────
def dari_txt(data: bytes) -> list[dict]:
    if len(data) > MAX_TXT_BYTES:
        data = data[:MAX_TXT_BYTES]
    teks = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            teks = data.decode(enc)
            break
        except Exception:
            teks = None
    if teks is None:
        raise ValueError("Berkas teks tak terbaca (encoding tak dikenal).")
    return [_bagian(teks=t) for t in potong_teks(teks)]


def dari_csv(data: bytes) -> list[dict]:
    rows = ai_sheet._read_csv(data)
    if rows is None:
        raise ValueError("Berkas CSV tak terbaca (encoding/format tak dikenal).")
    return _tabel_jadi_bagian(rows, 0, "")


def _cek_zip(data: bytes) -> None:
    """Guard zip-bomb untuk .xlsx/.docx — cek ukuran TERURAI sebelum parse."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRI:
                raise ValueError("Berkas ditolak: terlalu banyak isi di dalamnya.")
            if sum(i.file_size for i in infos) > MAX_ZIP_URAI:
                raise ValueError("Berkas ditolak: isinya mengembang terlalu besar.")
    except zipfile.BadZipFile:
        raise ValueError("Berkas rusak / bukan format yang valid.")


def dari_excel(data: bytes) -> list[dict]:
    """Semua sheet (maks MAX_SHEET) → bagian tabel. `read_only=True` supaya
    openpyxl streaming, tak memuat seluruh workbook ke RAM."""
    _cek_zip(data)
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValueError("Berkas bukan Excel yang valid / rusak.")
    out: list[dict] = []
    try:
        for nama in list(wb.sheetnames)[:MAX_SHEET]:
            try:
                ws = wb[nama]
                rows: list[list] = []
                for r in ws.iter_rows(values_only=True):
                    rows.append(list(r[:ai_sheet.MAX_COLS]))
                    if len(rows) > ai_sheet.MAX_ROWS:
                        break
                out.extend(_tabel_jadi_bagian(rows, 0, nama))
            except Exception:
                continue    # satu sheet rusak tak boleh menjatuhkan sisanya
    finally:
        wb.close()
    return out


def dari_gambar(data: bytes) -> list[dict]:
    return [_bagian(gambar=[optimalkan_gambar(data)])]


# ── gambar ───────────────────────────────────────────────────────────
def optimalkan_gambar(data: bytes, maks_sisi: int = 1600) -> bytes:
    """Normalkan ke PNG, perkecil bila raksasa. `MAX_IMAGE_PIXELS` dibatasi
    supaya gambar decompression-bomb ditolak Pillow, bukan menghabiskan RAM."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 64_000_000
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ValueError("Gambar rusak / format tak dikenal.")
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if max(im.size) > maks_sisi:
        rasio = maks_sisi / max(im.size)
        im = im.resize((max(int(im.width * rasio), 1), max(int(im.height * rasio), 1)))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── PDF (lazy import pypdfium2) ──────────────────────────────────────
_SPASI2 = re.compile(r"\s{2,}")


def _baris_tabel_pdf(teks: str) -> list[list[str]]:
    """Rekonstruksi tabel dari tata letak whitespace: baris dengan ≥3 pemisah
    '2+ spasi' dianggap baris tabel. Ini heuristik — `catatan` tool memberi tahu
    model agar tidak mengklaim presisi kolom. PDF yang tabelnya penting sebaiknya
    diunggah admin dalam bentuk Excel/CSV (jalur akurat)."""
    baris = []
    for ln in (teks or "").splitlines():
        # 3 kolom = 2 pemisah; ambang di JUMLAH KOLOM, bukan jumlah pemisah
        if len(_SPASI2.findall(ln)) >= 2:
            sel = [c.strip() for c in _SPASI2.split(ln.strip()) if c.strip()]
            if len(sel) >= 3:
                baris.append(sel[:MAX_KOLOM_TABEL])
                continue
        baris.append(None)   # penanda putus
    out, blok = [], []
    for b in baris:
        if b is None:
            if len(blok) >= 2:
                out.append(blok)
            blok = []
        else:
            blok.append(b)
    if len(blok) >= 2:
        out.append(blok)
    return out[0] if len(out) == 1 else [r for blok in out for r in blok]


def dari_pdf(data: bytes) -> list[dict]:
    """Teks + tabel + gambar per halaman. Halaman ditutup tiap iterasi — itu
    kunci supaya PDF 200 halaman tidak menumpuk di RAM."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise ValueError("Dukungan PDF belum terpasang di server (pypdfium2).")
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(data))
    except Exception:
        raise ValueError("Berkas PDF rusak / terkunci sandi.")
    out: list[dict] = []
    n_gambar = 0
    try:
        total = min(len(pdf), MAX_HALAMAN_PDF)
        for i in range(total):
            try:
                page = pdf[i]
                try:
                    teks = page.get_textpage().get_text_bounded() or ""
                except Exception:
                    teks = ""
                gambar: list[bytes] = []
                if not teks.strip() and n_gambar < MAX_GAMBAR:
                    # Halaman tanpa lapisan teks (hasil pindaian) → render jadi
                    # gambar @150 DPI supaya isinya tetap bisa dilihat manusia.
                    try:
                        pil = page.render(scale=150 / 72).to_pil()
                        buf = io.BytesIO()
                        pil.save(buf, format="PNG", optimize=True)
                        gambar.append(buf.getvalue())
                        n_gambar += 1
                    except Exception:
                        pass
                tabel = _baris_tabel_pdf(teks)
                if tabel:
                    out.extend(_tabel_jadi_bagian(tabel, i + 1, ""))
                for t in potong_teks(teks):
                    out.append(_bagian(teks=t, halaman=i + 1))
                if gambar:
                    out.append(_bagian(halaman=i + 1, gambar=gambar))
            except Exception:
                continue    # satu halaman rusak tak menjatuhkan sisanya
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return out


def pdf_tanpa_teks(bagian: list[dict]) -> bool:
    """True bila PDF sama sekali tak punya lapisan teks (hasil pindaian) —
    pemanggil memberi tahu admin agar menambah keterangan manual."""
    return bool(bagian) and not any((b.get("teks") or "").strip() for b in bagian)


# ── DOCX (lazy import python-docx, fallback stdlib) ──────────────────
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_stdlib(data: bytes) -> list[dict]:
    """Fallback tanpa dependency: .docx = ZIP, ambil teks dari document.xml.
    Kualitas lebih rendah (tanpa tabel/heading) tapi fitur tetap jalan."""
    from xml.etree import ElementTree
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except Exception:
        raise ValueError("Berkas Word rusak / bukan .docx yang valid.")
    root = ElementTree.fromstring(xml)
    paragraf = []
    for p in root.iter(f"{_W_NS}p"):
        teks = "".join(t.text or "" for t in p.iter(f"{_W_NS}t"))
        if teks.strip():
            paragraf.append(teks.strip())
    return [_bagian(teks=t) for t in potong_teks("\n".join(paragraf))]


def dari_docx(data: bytes) -> list[dict]:
    _cek_zip(data)
    try:
        import docx  # type: ignore
    except ImportError:
        return _docx_stdlib(data)
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception:
        raise ValueError("Berkas Word rusak / bukan .docx yang valid.")
    out: list[dict] = []
    judul_aktif = ""
    buf: list[str] = []

    def _flush():
        if buf:
            for t in potong_teks("\n".join(buf)):
                out.append(_bagian(teks=t, sub=judul_aktif))
            buf.clear()

    for p in d.paragraphs:
        teks = (p.text or "").strip()
        gaya = (getattr(p.style, "name", "") or "")
        if gaya.startswith("Heading") and teks:
            _flush()
            judul_aktif = teks           # jadi seed judul_id chunk berikutnya
            buf.append(teks)
            continue
        if teks:
            buf.append(teks)
    _flush()

    for t in d.tables:
        rows = [[c.text.strip() for c in r.cells] for r in t.rows]
        out.extend(_tabel_jadi_bagian(rows, 0, judul_aktif))

    n = 0
    for rel in d.part.related_parts.values():
        if n >= MAX_GAMBAR:
            break
        try:
            if "image" not in (getattr(rel, "content_type", "") or ""):
                continue
            out.append(_bagian(gambar=[optimalkan_gambar(rel.blob)]))
            n += 1
        except Exception:
            continue
    return out


# ── dispatcher ───────────────────────────────────────────────────────
def ekstrak(data: bytes, nama: str) -> list[dict]:
    """Berkas → daftar bagian. Melempar ValueError berpesan Indonesia bila
    formatnya tak didukung atau berkasnya rusak."""
    ext = Path(nama or "").suffix.lower()
    if ext == ".txt":
        return dari_txt(data)
    if ext == ".csv":
        return dari_csv(data)
    if ext in (".xlsx", ".xlsm"):
        return dari_excel(data)
    if ext == ".pdf":
        return dari_pdf(data)
    if ext == ".docx":
        return dari_docx(data)
    if ext in (".png", ".jpg", ".jpeg"):
        return dari_gambar(data)
    raise ValueError(f"Format '{ext or nama}' belum didukung.")
