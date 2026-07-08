"""Cari by Foto — akurasi & galeri belajar (2026-07-08).

- _local_search: ranking cosine polos (benchmark leave-one-out prod MEMBUKTIKAN
  αQE merusak top-1 41.7%→31.7% — jangan ditambahkan lagi tanpa bukti baru).
- Agregasi: foto SIMS (http) diutamakan utk TAMPILAN di atas foto belajar
  ('learned://'), skor tetap dari kecocokan terbaik.
- /search-image: overlay stok/harga (Accurate/Excel) + statistik galeri + pesan 0-hasil.
- /search-image/learn: hanya admin/'mas'; learn_from_photo menyimpan file + CSV.

Tanpa torch/jaringan: embedding & galeri disintesis (vektor kecil), numpy wajib.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi import HTTPException

from app.routers import parts as parts_router
from app.services import accurate, image_search, part_index


def _seed_gallery(monkeypatch, metas, vecs):
    mat = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    monkeypatch.setattr(image_search, "_local_matrix", mat)
    monkeypatch.setattr(image_search, "_local_meta", list(metas))
    monkeypatch.setattr(image_search, "_local_loaded", True)
    monkeypatch.setattr(image_search, "_local_error", "")


# ── _local_search: cosine polos, TANPA query-expansion (bukti benchmark) ─────

def test_local_search_skor_asli_tanpa_ekspansi(monkeypatch):
    # Urutan & skor = cosine murni pass-1; tak ada pass-2 yang mengubah skor.
    a1 = [0.95, 0.312, 0.0, 0.0]     # kuat (0.95) — dulu memicu αQE
    a2 = [0.5, 0.866, 0.0, 0.0]
    b = [0.55, 0.0, 0.835, 0.0]
    _seed_gallery(monkeypatch, [("A", "http://a1"), ("A", "http://a2"), ("B", "http://b")],
                  [a1, a2, b])
    out = image_search._local_search([1.0, 0.0, 0.0, 0.0], distance_threshold=1.0, fetch_count=3)
    assert [r["sims_url"] for r in out] == ["http://a1", "http://b", "http://a2"]
    assert out[0]["similarity"] == pytest.approx(0.95, abs=0.01)
    assert out[1]["similarity"] == pytest.approx(0.55, abs=0.01)


# ── Agregasi: URL tampilan utamakan foto SIMS ────────────────────────────────

def test_aggregasi_utamakan_url_sims_di_atas_learned(monkeypatch):
    monkeypatch.setattr(image_search, "local_index_available", lambda: True)
    monkeypatch.setattr(image_search, "compute_embedding", lambda b: [1.0, 0.0])
    monkeypatch.setattr(image_search, "_fetch_candidates", lambda q, d, f: [
        {"part_number": "P1", "sims_url": "learned://P1-abc.jpg", "similarity": 0.95},
        {"part_number": "P1", "sims_url": "http://sims/p1.jpg", "similarity": 0.80},
    ])
    monkeypatch.setattr(image_search._part_index, "name_for", lambda pn: "Part Satu")
    out = image_search.search_by_image(b"x", top_k=5, threshold=0.3)
    assert len(out) == 1
    assert out[0]["raw_similarity"] == pytest.approx(0.95)   # skor dari foto belajar
    assert out[0]["sims_url"] == "http://sims/p1.jpg"        # tampilan dari foto SIMS


# ── Overlay stok/harga di hasil ──────────────────────────────────────────────

def test_overlay_stok_harga_image(monkeypatch):
    monkeypatch.setattr(accurate, "available", lambda: True)
    monkeypatch.setattr(accurate, "snapshot", lambda: {
        "WG111": {"stok": 12.0, "harga": 150000.0, "unit": "Pc"}})
    monkeypatch.setattr(part_index, "search_part_number", lambda pn: [
        {"part_number": "WG222", "stok": "3", "harga": "Rp 9.000"}] if pn == "WG222" else [])
    hasil = [{"part_number": "WG111"}, {"part_number": "WG222"}, {"part_number": "WG999"}]
    parts_router._overlay_stok_harga_image(hasil)
    assert hasil[0]["stok"] == "12" and hasil[0]["harga"] == "Rp 150.000" and hasil[0]["tersedia"]
    assert hasil[1]["stok"] == "3" and hasil[1]["tersedia"]          # fallback Excel
    assert hasil[2]["stok"] == "—" and not hasil[2]["tersedia"]      # tak dikenal


# ── learn: guard peran + simpan file & CSV ───────────────────────────────────

@pytest.mark.anyio
async def test_learn_ditolak_untuk_user_biasa():
    class F:  # UploadFile palsu — tak sampai dibaca (guard duluan)
        async def read(self):
            return b"img"

    with pytest.raises(HTTPException) as e:
        await parts_router.search_image_learn(
            file=F(), pn="WG123", user={"username": "budi", "role": "user"})
    assert e.value.status_code == 403


def test_learn_from_photo_menyimpan_file_dan_index(monkeypatch, tmp_path):
    monkeypatch.setattr(image_search, "_TORCH_OK", True)
    monkeypatch.setattr(image_search, "compute_embedding", lambda b: [0.1, 0.2, 0.3])
    monkeypatch.setattr(image_search, "learned_dir", lambda: tmp_path)
    tulis = {}

    def fake_append(rows):
        tulis["rows"] = rows
        return {"appended": len(rows), "skipped_dup": 0, "error": None}

    monkeypatch.setattr(image_search, "append_local_index", fake_append)
    monkeypatch.setattr(image_search, "index_stats", lambda: {"total": 100, "parts": 50})

    # PNG 1×1 valid supaya PIL bisa menyimpan JPEG.
    import io as _io
    from PIL import Image as _Img
    buf = _io.BytesIO()
    _Img.new("RGB", (8, 8), (200, 30, 30)).save(buf, "PNG")

    res = image_search.learn_from_photo(buf.getvalue(), "wg9725220536", indexed_by="admin")
    assert res["ok"] and res["pn"] == "WG9725220536"
    assert (tmp_path / res["file"]).is_file()                      # foto tersimpan
    row = tulis["rows"][0]
    assert row["part_number"] == "WG9725220536"
    assert row["sims_url"].startswith("learned://")
    assert res["galeri_total"] == 100


def test_learned_photo_path_tolak_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(image_search, "learned_dir", lambda: tmp_path)
    (tmp_path / "ok.jpg").write_bytes(b"x")
    assert image_search.learned_photo_path("ok.jpg") is not None
    assert image_search.learned_photo_path("../secrets.toml") is None
    assert image_search.learned_photo_path("a/b.jpg") is None
    assert image_search.learned_photo_path("") is None
