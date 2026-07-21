"""Upgrade mesin cari pengetahuan — typo, stem Indonesia, PN ternormal, diversitas.

Empat kemampuan baru yang dikunci di sini (semuanya ADITIF — golden ranking
di test_pengetahuan_store.py yang mengunci perilaku lama tetap harus hijau):
  - koreksi typo kueri terhadap kosakata store (varian teredam 0.8);
  - fallback stem Indonesia dua arah ('pemasangan' ↔ 'pasang'), bobot DI BAWAH
    kecocokan teks eksak;
  - PN/kode berpemisah ('WG-9725.190102') & prefiks ≥6 char cocok ke `kode`;
  - pagar diversitas: satu dokumen maks 2 chunk di lintasan pertama, kursi
    sisa diisi ulang (limit tak pernah dikorbankan).
"""
import pytest

from app.services import knowledge_util, pengetahuan, sinonim


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(pengetahuan, "_dir", lambda: tmp_path)
    monkeypatch.setattr(sinonim, "entries", lambda: [])
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    pengetahuan._VOCAB_CACHE.update(kunci=None, ember=None)
    yield tmp_path
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    pengetahuan._VOCAB_CACHE.update(kunci=None, ember=None)


def _chunk(**kw):
    d = {
        "id": "aa#0001", "dok_id": "aa", "judul": "Dokumen", "judul_id": "",
        "kata_kunci": [], "ringkasan": "", "teks": "", "tabel": [],
        "gambar_ref": [], "sumber": "teks-admin", "halaman": 0, "tipe": "teks",
        "untuk_pembeli": False, "dicari": True, "kode": [],
    }
    d.update(kw)
    return d


def _tulis(rows):
    pengetahuan._save_chunks(rows)
    knowledge_util._LOAD_CACHE.clear()
    pengetahuan._HAY_CACHE.update(mtime=None, rows=None)
    pengetahuan._VOCAB_CACHE.update(kunci=None, ember=None)


# ── koreksi typo ─────────────────────────────────────────────────────
def test_typo_satu_huruf_tetap_ketemu():
    """'retor' (salah ketik 'retur') harus tetap menemukan dokumennya."""
    _tulis([_chunk(judul_id="Prosedur retur barang", kata_kunci=["retur"])])
    hasil = pengetahuan.search("prosedur retor barang")
    assert [h["id"] for h in hasil] == ["aa#0001"]


def test_kueri_asli_menang_atas_koreksi_typo():
    """Bila kata yang 'mirip typo' ternyata memang ada di store, kecocokan
    LANGSUNG harus menang — koreksi diredam 0.8."""
    _tulis([
        _chunk(id="aa#0001", judul_id="pengaturan retor mesin"),
        _chunk(id="aa#0002", judul_id="pengaturan retur mesin"),
    ])
    assert pengetahuan.search("retor")[0]["id"] == "aa#0001"


def test_koreksi_tipo_tidak_mengarang():
    """Kata yang jauh dari seluruh kosakata tidak dikoreksi paksa."""
    _tulis([_chunk(teks="prosedur retur barang")])
    assert pengetahuan.search("zebrakutub") == []


# ── stemming Indonesia ───────────────────────────────────────────────
def test_stem_kueri_berimbuhan_menemukan_akar():
    """'pemasangan' di kueri ketemu teks 'pasang' (nasal pem- → p)."""
    _tulis([_chunk(teks="cara pasang filter udara di unit")])
    assert [h["id"] for h in pengetahuan.search("pemasangan filter")] == ["aa#0001"]


def test_stem_teks_berimbuhan_ditemukan_kueri_akar():
    """Arah sebaliknya: kueri 'pasang' ketemu teks 'pemasangan'."""
    _tulis([_chunk(teks="cara pemasangan filter udara di unit")])
    assert [h["id"] for h in pengetahuan.search("pasang filter")] == ["aa#0001"]


def test_stem_kalah_dari_kecocokan_eksak():
    """Fallback stem (0.7) tak boleh menggeser kecocokan teks eksak (1.0)."""
    eksak = _chunk(id="aa#0001", teks="jadwal pasang ban")
    stem = _chunk(id="aa#0002", teks="jadwal pemasangan ban")
    _tulis([eksak, stem])
    assert pengetahuan.search("pasang")[0]["id"] == "aa#0001"
    assert (pengetahuan._score(eksak, "pasang", ["pasang"])
            > pengetahuan._score(stem, "pasang", ["pasang"]))


def test_stem_kandidat_nasal():
    assert "pasang" in pengetahuan._stem_kandidat("memasang")
    assert "tentu" in pengetahuan._stem_kandidat("menentukan")
    assert pengetahuan._stem_kandidat("ban") == ()          # terlalu pendek
    assert pengetahuan._stem_kandidat("wg9725") == ()       # berangka bukan kata


# ── PN/kode ternormalisasi ───────────────────────────────────────────
def test_pn_berpemisah_menemukan_kode_utuh():
    """User menyalin PN bergaya katalog 'WG-9725.190102' — pemecah kata Latin
    mencerainya, token gabungan-ulang yang mencocokkannya ke `kode`."""
    _tulis([_chunk(kode=["WG9725190102"], teks="tanpa kata lain")])
    assert [h["id"] for h in pengetahuan.search("WG-9725.190102")] == ["aa#0001"]


def test_prefiks_pn_menemukan_kode():
    _tulis([_chunk(kode=["WG9725190102"], teks="tanpa kata lain")])
    assert [h["id"] for h in pengetahuan.search("wg97251901")] == ["aa#0001"]


def test_words_menambah_token_pn_gabungan():
    assert "wg9725190102" in pengetahuan._words("wg-9725-190102")
    # tanpa pemisah → tidak ada token tambahan (perilaku lama utuh)
    assert pengetahuan._words("wg9725") == ["wg9725"]


def test_prefiks_pn_terlalu_pendek_tidak_cocok():
    """Prefiks < 6 char terlalu ambigu — jangan cocok-cocokkan."""
    _tulis([_chunk(kode=["WG9725190102"], teks="tanpa kata lain")])
    assert pengetahuan.search("wg972") == []


# ── diversitas dokumen ───────────────────────────────────────────────
def test_dokumen_tebal_tak_memonopoli_hasil():
    """3 chunk kuat dari satu dokumen + 1 chunk relevan dokumen lain, limit 3:
    dokumen lain harus tetap dapat kursi."""
    _tulis([
        _chunk(id="aa#0001", dok_id="aa", judul_id="Prosedur retur gudang A"),
        _chunk(id="aa#0002", dok_id="aa", judul_id="Prosedur retur gudang B"),
        _chunk(id="aa#0003", dok_id="aa", judul_id="Prosedur retur gudang C"),
        _chunk(id="bb#0001", dok_id="bb", judul="Kebijakan Lain",
               teks="prosedur retur juga dibahas di dokumen ini secara ringkas"),
    ])
    hasil = pengetahuan.search("prosedur retur", limit=3)
    assert len(hasil) == 3
    assert {h["dok_id"] for h in hasil} == {"aa", "bb"}


def test_kursi_sisa_diisi_ulang_dokumen_sama():
    """Bila kandidat hanya dari SATU dokumen, limit tetap terpenuhi."""
    _tulis([_chunk(id=f"aa#{i:04d}", teks=f"prosedur retur varian {i}")
            for i in range(6)])
    assert len(pengetahuan.search("retur", limit=4)) == 4
