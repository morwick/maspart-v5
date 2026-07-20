"""Ekstraksi PDF & DOCX untuk indexing pengetahuan.

Fixture PDF dibuat pakai reportlab (sudah ada di requirements runtime — tak
perlu menambah dependency test), DOCX pakai python-docx.
"""
import io
import zipfile

import pytest

from app.services import knowledge_util, pengetahuan
from app.services import pengetahuan_extract as ext
from app.services import pengetahuan_index as idx


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    monkeypatch.setattr(idx, "get_settings",
                        lambda: type("S", (), {"ai_configured": False})())
    knowledge_util._LOAD_CACHE.clear()
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()


def _pdf(halaman: list[list[str]]) -> bytes:
    """PDF sederhana: tiap elemen = daftar baris untuk satu halaman."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for baris in halaman:
        y = 800
        for ln in baris:
            c.drawString(60, y, ln)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


def _docx(paragraf=(), tabel=None, heading=""):
    import docx
    d = docx.Document()
    if heading:
        d.add_heading(heading, level=1)
    for p in paragraf:
        d.add_paragraph(p)
    if tabel:
        t = d.add_table(rows=len(tabel), cols=len(tabel[0]))
        for i, baris in enumerate(tabel):
            for j, sel in enumerate(baris):
                t.cell(i, j).text = str(sel)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ── PDF ──────────────────────────────────────────────────────────────
def test_pdf_teks_dan_nomor_halaman_benar():
    data = _pdf([["Prosedur retur barang"], ["Syarat klaim garansi"]])
    bagian = ext.ekstrak(data, "kebijakan.pdf")
    teks = {b["halaman"]: b["teks"] for b in bagian if b["teks"]}
    assert "retur" in teks[1].lower()
    assert "garansi" in teks[2].lower()


def test_pdf_rusak_melempar_pesan_indonesia():
    with pytest.raises(ValueError, match="rusak"):
        ext.ekstrak(b"%PDF-1.7 tapi isinya sampah", "rusak.pdf")


def test_pdf_batas_halaman_dihormati(monkeypatch):
    monkeypatch.setattr(ext, "MAX_HALAMAN_PDF", 2)
    data = _pdf([[f"halaman {i}"] for i in range(6)])
    bagian = ext.ekstrak(data, "tebal.pdf")
    assert max(b["halaman"] for b in bagian) <= 2


def test_pdf_tanpa_lapisan_teks_dirender_jadi_gambar():
    """Halaman kosong = tak ada lapisan teks (mirip hasil pindaian) → gambar."""
    data = _pdf([[]])
    bagian = ext.ekstrak(data, "pindaian.pdf")
    assert ext.pdf_tanpa_teks(bagian)
    assert any(b["gambar"] for b in bagian)


def test_pdf_pindaian_memberi_peringatan_ke_admin(_tmp_store):
    dok = pengetahuan.add_dokumen("Hasil Pindai", teks_admin="x")
    d = pengetahuan.berkas_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dok['id']}_0.pdf").write_bytes(_pdf([[]]))
    pengetahuan.update_dokumen(dok["id"], teks_admin="", berkas=[
        {"nama": "pindaian.pdf", "nama_simpan": f"{dok['id']}_0.pdf", "ext": ".pdf"}])
    knowledge_util._LOAD_CACHE.clear()
    idx.proses(dok["id"])
    doc = pengetahuan.get_dokumen(dok["id"])
    assert doc["status"] == "selesai_sebagian"
    assert "pindaian" in doc["error"].lower()


def test_tabel_pdf_direkonstruksi_dari_whitespace():
    baris = ["Part      Torsi     Satuan",
             "baut roda      600       Nm",
             "baut as        400       Nm"]
    tabel = ext._baris_tabel_pdf("\n".join(baris))
    assert tabel and tabel[0][0].startswith("Part")
    assert any("baut roda" in r[0] for r in tabel)


def test_teks_biasa_tidak_disalahartikan_sebagai_tabel():
    prosa = "Retur barang diajukan maksimal tujuh hari setelah barang diterima."
    assert ext._baris_tabel_pdf(prosa) == []


# ── DOCX ─────────────────────────────────────────────────────────────
def test_docx_paragraf_heading_dan_tabel():
    data = _docx(paragraf=["Retur diajukan maksimal 7 hari."],
                 tabel=[["Part", "Torsi"], ["baut roda", "600"]],
                 heading="Kebijakan Retur")
    bagian = ext.ekstrak(data, "kebijakan.docx")
    teks = " ".join(b["teks"] for b in bagian)
    assert "Retur diajukan" in teks
    assert any(b["sub"] == "Kebijakan Retur" for b in bagian)
    assert any(b["tabel"] and b["tabel"][0] == ["Part", "Torsi"] for b in bagian)


def test_docx_rusak_melempar_pesan_indonesia():
    with pytest.raises(ValueError):
        ext.ekstrak(b"bukan zip", "rusak.docx")


def test_docx_fallback_stdlib_saat_python_docx_absen(monkeypatch):
    """Fitur harus tetap jalan bila dependency python-docx tak terpasang."""
    data = _docx(paragraf=["Retur diajukan maksimal 7 hari."])
    asli = __import__

    def tanpa_docx(nama, *a, **kw):
        if nama == "docx":
            raise ImportError("tidak terpasang")
        return asli(nama, *a, **kw)
    monkeypatch.setattr("builtins.__import__", tanpa_docx)
    bagian = ext.ekstrak(data, "kebijakan.docx")
    assert "Retur diajukan" in " ".join(b["teks"] for b in bagian)


def test_docx_zip_bomb_ditolak(monkeypatch):
    monkeypatch.setattr(ext, "MAX_ZIP_URAI", 100)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "x" * 5000)
    with pytest.raises(ValueError, match="mengembang"):
        ext.ekstrak(buf.getvalue(), "bom.docx")


# ── end-to-end lewat job ─────────────────────────────────────────────
def test_pdf_terindeks_dan_bisa_dicari(_tmp_store):
    dok = pengetahuan.add_dokumen("Kebijakan", teks_admin="x")
    d = pengetahuan.berkas_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{dok['id']}_0.pdf").write_bytes(
        _pdf([["Prosedur retur barang dagangan"]]))
    pengetahuan.update_dokumen(dok["id"], teks_admin="", berkas=[
        {"nama": "kebijakan.pdf", "nama_simpan": f"{dok['id']}_0.pdf", "ext": ".pdf"}])
    knowledge_util._LOAD_CACHE.clear()
    idx.proses(dok["id"])
    knowledge_util._LOAD_CACHE.clear()
    hasil = pengetahuan.search("retur")
    assert hasil and hasil[0]["sumber"] == "kebijakan.pdf"
    assert hasil[0]["tipe"] == "pdf" and hasil[0]["halaman"] == 1
