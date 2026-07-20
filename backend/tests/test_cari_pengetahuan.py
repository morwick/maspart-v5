"""Tool cari_pengetahuan — bentuk hasil, invarian ekor, gating pembeli.

Invarian yang dikunci di sini:
  - `catatan` WAJIB key TERAKHIR (_cap_tool_content memotong bagian TENGAH;
    kalau catatan ada di kepala, instruksi anti-ngarang hilang senyap);
  - pengetahuan internal TIDAK PERNAH sampai ke role pembeli;
  - system prompt TIDAK berubah walau store diisi (prompt-cache DeepSeek).
"""
import pytest

from app.services import ai_assistant as ai
from app.services import knowledge_util, pengetahuan, sinonim


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sinonim, "entries", lambda: [])
    knowledge_util._LOAD_CACHE.clear()
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()


def _chunk(**kw):
    d = {
        "id": "aa#0001", "dok_id": "aa", "judul": "Prosedur Retur",
        "judul_id": "Cara mengajukan retur", "kata_kunci": ["retur", "pengembalian"],
        "ringkasan": "Retur diajukan maksimal 7 hari.", "teks": "Barang retur wajib utuh.",
        "tabel": [], "gambar_ref": [], "sumber": "kebijakan.pdf", "halaman": 3,
        "tipe": "pdf", "untuk_pembeli": False, "dicari": True, "kode": [],
    }
    d.update(kw)
    return d


def _isi(rows):
    pengetahuan._save_chunks(rows)
    knowledge_util._LOAD_CACHE.clear()


ADMIN = {"username": "admin", "role": "admin"}
PEMBELI = {"username": "budi", "role": "pembeli"}


def test_hasil_lengkap_dan_catatan_di_ekor():
    _isi([_chunk()])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert res["found"] is True and res["jumlah"] == 1
    h = res["hasil"][0]
    assert h["judul"] == "Cara mengajukan retur"
    assert h["dokumen"] == "Prosedur Retur"
    assert h["sumber"] == "kebijakan.pdf" and h["halaman"] == 3
    # INVARIAN: catatan wajib terakhir
    assert list(res)[-1] == "catatan"


def test_topik_kosong_dan_store_kosong():
    assert "error" in ai._t_cari_pengetahuan({"topik": "  "}, ADMIN)
    assert "error" in ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)


def test_nihil_found_false_dengan_catatan_terakhir():
    _isi([_chunk()])
    res = ai._t_cari_pengetahuan({"topik": "zebra kutub utara"}, ADMIN)
    assert res["found"] is False and res["jumlah"] == 0
    assert list(res)[-1] == "catatan"
    assert "mengarang" in res["catatan"].lower()


def test_pembeli_tidak_melihat_pengetahuan_internal():
    _isi([_chunk(untuk_pembeli=False)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, PEMBELI)
    assert res["found"] is False


def test_pembeli_melihat_yang_dipublikasikan():
    _isi([_chunk(untuk_pembeli=True)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, PEMBELI)
    assert res["found"] is True and res["hasil"][0]["dokumen"] == "Prosedur Retur"


def test_lapis_kedua_menyaring_walau_search_bocor(monkeypatch):
    """Bila filter search() gagal/di-bypass, handler tetap menahan record internal."""
    _isi([_chunk(untuk_pembeli=False)])
    monkeypatch.setattr(pengetahuan, "search",
                        lambda *a, **k: [_chunk(untuk_pembeli=False)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, PEMBELI)
    assert res["found"] is False


def test_isi_dipotong_1200_char():
    _isi([_chunk(teks="retur " + "x" * 4000)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert len(res["hasil"][0]["isi"]) == 1200


def test_peringatan_tabel_pdf_rekonstruksi():
    _isi([_chunk(tabel=[["a", "b"]], tipe="pdf")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "REKONSTRUKSI" in res["catatan"]


def test_tabel_excel_tanpa_peringatan_rekonstruksi():
    _isi([_chunk(tabel=[["a", "b"]], tipe="excel")])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert "REKONSTRUKSI" not in res["catatan"]


def test_gambar_dibatasi_8_dan_catatan_tetap_terakhir(_tmp_store):
    media = _tmp_store / "media"
    media.mkdir(parents=True, exist_ok=True)
    refs = []
    for i in range(9):
        f = f"aa_{i:03d}.png"
        (media / f).write_bytes(b"PNG")
        refs.append(f)
    _isi([_chunk(id=f"aa#{i:04d}", gambar_ref=refs[i * 3:i * 3 + 3]) for i in range(3)])
    res = ai._t_cari_pengetahuan({"topik": "retur"}, ADMIN)
    assert len(res["gambar"]) <= 8
    assert list(res)[-1] == "catatan"


def test_terdaftar_di_dispatch_dan_spec():
    assert "cari_pengetahuan" in ai._DISPATCH
    names = {s["function"]["name"] for s in ai._tool_specs(ADMIN)}
    assert "cari_pengetahuan" in names
    # pembeli tetap boleh memanggil (isinya yang disaring, bukan tool-nya)
    assert "cari_pengetahuan" in ai._allowed_tool_names(PEMBELI)


def test_prompt_tidak_berubah_walau_store_diisi():
    """Prompt-cache DeepSeek hanya kena bila prefix identik byte-per-byte."""
    sebelum = ai._system_prompt(ADMIN)
    _isi([_chunk(), _chunk(id="aa#0002", judul_id="Klaim garansi")])
    assert ai._system_prompt(ADMIN) == sebelum
