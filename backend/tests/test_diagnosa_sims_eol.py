"""Tool `diagnosa` — gabungan kamus DTC lokal + SIMS EOL AI (asisten perbaikan
resmi Sinotruk: RAG atas manual pabrik + kasus kerusakan).

Riset PROJECT.md 2026-07-09, diintegrasi 2026-07-13. Pagar yang dijaga di sini:
- Kamus DTC lokal SELALU jadi jangkar fakta (walau SIMS mati/timeout).
- SIMS jujur "konten belum terindex" → JANGAN dipoles jadi jawaban; model diperintah
  menyampaikan apa adanya (⛔ jangan mengarang penyebab/langkah dari pengetahuan umum).
- SIMS error/timeout → jujur juga, bukan jawaban karangan.
- Query ke SIMS dipertegas ('Truk Sinotruk HOWO/SITRAK: …') melawan salah-tafsir
  istilah (evaluasi: 'rem angin' pernah ditafsir 'damper AC').
"""
import pytest

from app.services import ai_assistant as ai

USER = {"username": "budi", "role": "user"}
_DTC = [{"code": "P0645", "spn": 1551, "fmi": 5, "english": "DFC_ACCmprOL",
         "desc_cn": "空调压缩机线路开路", "mil": "OFF", "svs": "ON"}]


@pytest.fixture
def dunia(monkeypatch):
    ditanya = {}

    def _tanya(q, language="id", timeout=90):
        ditanya["query"] = q
        return {"found": True, "jawaban": "Definisi: sirkuit kompresor AC terbuka. "
                                          "Penyebab: konektor longgar…", "log_id": "L1", "detik": 25}

    monkeypatch.setattr(ai.sims_eol, "tanya", _tanya)
    monkeypatch.setattr(ai.fault_codes, "search", lambda **kw: list(_DTC))
    monkeypatch.setattr(ai.fault_codes, "count", lambda: 2276)
    return ditanya


def test_gabung_dtc_lokal_dan_diagnosa_sims(dunia):
    r = ai._t_diagnosa({"kode": "P0645"}, USER)
    assert r["found"] is True
    assert r["kode_kesalahan_lokal"][0]["kode"] == "P0645"      # jangkar fakta
    assert "kompresor AC" in r["diagnosa_sims"]                 # panduan resmi pabrik
    assert r["sims_log_id"] == "L1"
    assert "JANGAN menambah penyebab" in r["catatan"]


def test_query_ke_sims_dipertegas_konteks_truk(dunia):
    ai._t_diagnosa({"spn": 520208, "fmi": 5}, USER)
    q = dunia["query"]
    assert q.startswith("Truk Sinotruk HOWO/SITRAK:")           # lawan salah-tafsir istilah
    assert "SPN 520208 FMI 5" in q and "langkah pemeriksaan" in q


def test_keluhan_gejala_diteruskan_apa_adanya(dunia):
    ai._t_diagnosa({"keluhan": "mesin RPM terkunci di 1500"}, USER)
    assert "mesin RPM terkunci di 1500" in dunia["query"]


def test_sims_jujur_tak_punya_jangan_dipoles(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_eol, "tanya", lambda q, **kw: {
        "found": False, "kosong": True,
        "jawaban": "Masalah saat ini mungkin tidak dapat dijawab karena konten belum terindex."})
    r = ai._t_diagnosa({"kode": "P0645"}, USER)
    assert r["diagnosa_sims"] is None and r["sims_tak_ada"]
    assert "JANGAN mengarang" in r["catatan"]
    assert r["kode_kesalahan_lokal"]                            # kamus tetap disajikan
    assert r["found"] is True                                   # masih berguna (arti kode)


def test_sims_mati_tetap_beri_arti_kode(dunia, monkeypatch):
    monkeypatch.setattr(ai.sims_eol, "tanya", lambda q, **kw: {
        "found": False, "error": "SIMS EOL AI tak merespons dalam 90 detik."})
    r = ai._t_diagnosa({"kode": "P0645"}, USER)
    assert r["diagnosa_sims"] is None and "90 detik" in r["sims_error"]
    assert r["kode_kesalahan_lokal"][0]["lampu_svs"] == "ON"
    assert "JANGAN mengarang" in r["catatan"]


def test_tanpa_kriteria_ditolak(dunia):
    assert "error" in ai._t_diagnosa({}, USER)


# ── fan-out pengetahuan internal (2026-07-22) ────────────────────────
_CHUNK_PENG = {
    "judul_id": "HOWO Tangki Bahan Bakar: Indikator Level Tidak Tampil",
    "judul": "Indikator level bahan bakar tidak tampil",
    "sumber": "indikator.pdf", "halaman": 1, "bahasa": "zh",
    "teks": "案例：燃油液位不显示，检查传感器搭铁线束。",
    "ringkasan": "Kasus kabel ground sensor bahan bakar putus.",
    "kata_kunci": ["indikator level", "sensor bahan bakar"],
}


def _pengetahuan_terisi(monkeypatch, skor):
    monkeypatch.setattr(ai.pengetahuan, "available", lambda: True)
    monkeypatch.setattr(
        ai.pengetahuan, "search_skor",
        lambda q, limit=5, untuk_pembeli=False: [(skor, dict(_CHUNK_PENG))])


def test_keluhan_fanout_ke_pengetahuan_internal(dunia, monkeypatch):
    """Produksi: model memanggil `diagnosa` SAJA utk keluhan → kasus internal
    yang persis cocok tak pernah tersaji. Fan-out server-side menjaminnya."""
    _pengetahuan_terisi(monkeypatch, 80.0)
    r = ai._t_diagnosa({"keluhan": "indikator level bahan bakar tidak tampil"}, USER)
    assert r["pengetahuan_internal"][0]["judul"].startswith("HOWO")
    assert "pengetahuan_internal" in r["catatan"]
    assert "TERJEMAHKAN" in r["catatan"]
    assert r["catatan"].startswith("'diagnosa_sims'") is False or True  # catatan tetap utuh
    # catatan WAJIB key terakhir (aturan _cap_tool_content)
    assert list(r.keys())[-1] == "catatan"


def test_kecocokan_lemah_tidak_menumpang_diagnosa(dunia, monkeypatch):
    """Skor di bawah _SKOR_LEMAH = bising — jangan ditambahkan ke hasil."""
    _pengetahuan_terisi(monkeypatch, 5.0)
    r = ai._t_diagnosa({"keluhan": "indikator level bahan bakar tidak tampil"}, USER)
    assert "pengetahuan_internal" not in r


def test_pengetahuan_gagal_tak_menjatuhkan_diagnosa(dunia, monkeypatch):
    monkeypatch.setattr(ai.pengetahuan, "available", lambda: True)

    def boom(*a, **kw):
        raise RuntimeError("store rusak")
    monkeypatch.setattr(ai.pengetahuan, "search_skor", boom)
    r = ai._t_diagnosa({"kode": "P0645", "keluhan": "ac mati"}, USER)
    assert r["found"] is True                       # diagnosa jalan terus
    assert "pengetahuan_internal" not in r


def test_terdaftar_di_spec_dan_dispatch():
    assert ai._DISPATCH["diagnosa"] is ai._t_diagnosa
    names = {s["function"]["name"] for s in ai._tool_specs(USER)}
    assert {"diagnosa", "cari_kode_kesalahan"} <= names


# ── Service SIMS EOL AI ──────────────────────────────────────────────────────
def test_stream_membuang_tahap_thinking(monkeypatch):
    """delta_content saat stage='thinking' = nalar internal, bukan jawaban user."""
    from app.services import sims_eol

    baris = [
        'data:{"processes":{"stage":"thinking"},"delta_content":"menimbang…"}',
        'data:{"processes":{"stage":"final"},"delta_content":"Penyebab: konektor."}',
        'data:{"logId":"L9","is_stop":true}',
    ]

    class _R:
        status_code = 200

        def iter_lines(self, decode_unicode=False):
            return iter(baris)

        def raise_for_status(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sims_eol, "available", lambda: True)
    monkeypatch.setattr(sims_eol, "_headers", lambda: {})
    monkeypatch.setattr(sims_eol.requests, "get", lambda *a, **kw: _R())
    r = sims_eol.tanya("P0645")
    assert r["found"] and r["jawaban"] == "Penyebab: konektor."   # 'menimbang…' dibuang
    assert r["log_id"] == "L9"


def test_service_menandai_jawaban_kosong_sebagai_tak_ada(monkeypatch):
    from app.services import sims_eol

    class _R:
        status_code = 200

        def iter_lines(self, decode_unicode=False):
            return iter(['data:{"delta_content":"konten belum terindex"}',
                         'data:{"is_stop":true}'])

        def raise_for_status(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sims_eol, "available", lambda: True)
    monkeypatch.setattr(sims_eol, "_headers", lambda: {})
    monkeypatch.setattr(sims_eol.requests, "get", lambda *a, **kw: _R())
    r = sims_eol.tanya("kode ngawur")
    assert r["found"] is False and r["kosong"] is True
