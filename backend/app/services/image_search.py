"""
Service: Cari by Foto (DINOv2 + pgvector) — DECOUPLED dari Streamlit.

Galeri embedding part SUDAH ada di Supabase (tabel `part_image_index` + RPC
`match_part_images`, di-index lewat app Streamlit). Backend cukup:
  1. hitung embedding query (DINOv2-base, identik image_search.py)
  2. panggil RPC match_part_images (pakai config Supabase backend)
  3. agregasi per part_number + confidence boost (identik image_search.py)

Logika embedding & agregasi disalin persis dari image_search.py agar hasil
sama dengan app Streamlit. TIDAK ada `st.*` di sini.
"""
from __future__ import annotations

import csv
import io
import re
import threading
from pathlib import Path

import requests

from ..core.config import get_settings
from . import part_index as _part_index, sims

try:
    import numpy as np
    _NUMPY_OK = True
except Exception:  # pragma: no cover
    np = None
    _NUMPY_OK = False

# ── Lazy import torch (model berat) ──────────────────────────────────
try:
    import torch
    from torch import nn
    from torchvision import transforms
    from PIL import Image, ImageOps
    _TORCH_OK = True
    _TORCH_ERR = ""
except Exception as e:  # pragma: no cover
    _TORCH_OK = False
    _TORCH_ERR = str(e)

# ── Konstanta (disalin dari image_search.py) ─────────────────────────
DINOV2_REPO = "facebookresearch/dinov2"
DINOV2_MODEL_NAME = "dinov2_vitb14"
DINOV2_INPUT_SIZE = 224
_EMBED_PRE_RESIZE_MAX = 1024

RPC_SEARCH = "match_part_images"
SEARCH_HTTP_TIMEOUT = 60
SEARCH_RPC_RETRIES = 1

_AGG_FETCH_MULT = 5
_AGG_FETCH_MIN = 30
_AGG_STRONG_TH = 0.70
_AGG_BOOST_PER_MATCH = 0.04
_AGG_BOOST_CAP = 0.10

# ── Model singleton ──────────────────────────────────────────────────
_model_lock = threading.Lock()
_model = None
_preprocess = None


def torch_available() -> bool:
    return _TORCH_OK


def _load_model():
    global _model, _preprocess
    if not _TORCH_OK:
        raise RuntimeError(f"torch/torchvision belum terinstall: {_TORCH_ERR}")
    if _model is not None:
        return _model, _preprocess
    with _model_lock:
        if _model is None:
            try:
                m = torch.hub.load(
                    DINOV2_REPO, DINOV2_MODEL_NAME, trust_repo=True, skip_validation=True
                )
            except TypeError:
                m = torch.hub.load(DINOV2_REPO, DINOV2_MODEL_NAME, trust_repo=True)
            m.eval()
            _model = m
            _preprocess = transforms.Compose([
                transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
                transforms.CenterCrop(DINOV2_INPUT_SIZE),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
    return _model, _preprocess


def model_ready() -> bool:
    return _model is not None


def preload_model() -> None:
    """Muat model sekarang (mis. saat startup) supaya request pertama cepat."""
    if _TORCH_OK:
        _load_model()


# ── Embedding (identik image_search.py) ──────────────────────────────
def compute_embedding(image_bytes: bytes) -> list[float]:
    model, preprocess = _load_model()
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > _EMBED_PRE_RESIZE_MAX:
        img.thumbnail((_EMBED_PRE_RESIZE_MAX, _EMBED_PRE_RESIZE_MAX), Image.LANCZOS)
    tensor = preprocess(img).unsqueeze(0)
    with torch.no_grad():
        feat = model(tensor)
        feat = nn.functional.normalize(feat, p=2, dim=1)
        if not torch.isfinite(feat).all():
            raise ValueError("embedding non-finite (NaN/Inf)")
        return feat.squeeze(0).cpu().tolist()


def compute_embedding_tta(image_bytes: bytes) -> list[float]:
    model, preprocess = _load_model()
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > _EMBED_PRE_RESIZE_MAX:
        img.thumbnail((_EMBED_PRE_RESIZE_MAX, _EMBED_PRE_RESIZE_MAX), Image.LANCZOS)
    img_flipped = ImageOps.mirror(img)
    tensor = torch.stack([preprocess(img), preprocess(img_flipped)])
    with torch.no_grad():
        feats = model(tensor)
        feats = nn.functional.normalize(feats, p=2, dim=1)
        mean = feats.mean(dim=0, keepdim=True)
        mean = nn.functional.normalize(mean, p=2, dim=1)
        if not torch.isfinite(mean).all():
            raise ValueError("embedding non-finite (NaN/Inf)")
        return mean.squeeze(0).cpu().tolist()


# ── Supabase RPC (config backend) ────────────────────────────────────
def _vec_to_str(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"


def _base_url() -> str:
    url = get_settings().supabase_url.rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url


def _rpc(query_vec: list[float], distance_threshold: float, fetch_count: int) -> list[dict]:
    s = get_settings()
    key = s.storage_key  # service_key (fallback anon)
    resp = requests.post(
        f"{_base_url()}/rest/v1/rpc/{RPC_SEARCH}",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "query_embedding": _vec_to_str(query_vec),
            "match_threshold": distance_threshold,
            "match_count": fetch_count,
        },
        timeout=SEARCH_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json() or []


# ── Galeri lokal (CSV part_image_index) — cocokkan tanpa database ─────
# Hasil export tabel `part_image_index` (kolom: id, part_number, sims_url,
# embedding, indexed_by, indexed_at). Kalau file ada, pencarian foto dicocokkan
# langsung dari memori (cosine similarity) — tidak query Supabase sama sekali.
_local_lock = threading.Lock()
_local_matrix = None          # np.ndarray (N, DIM) float32, sudah dinormalisasi L2
_local_meta: list[tuple[str, str]] | None = None  # paralel: (part_number, sims_url)
_local_loaded = False         # True setelah percobaan load (sukses/gagal)
_local_error = ""

# csv default field-size limit (128 KB) cukup besar untuk satu embedding (~10 KB),
# tapi naikkan untuk aman dari baris panjang tak terduga.
try:
    csv.field_size_limit(16 * 1024 * 1024)
except Exception:  # pragma: no cover
    pass


def _load_local_index() -> None:
    """Muat CSV galeri ke matriks numpy (lazy, sekali). Aman dipanggil berkali-kali."""
    global _local_matrix, _local_meta, _local_loaded, _local_error
    if _local_loaded:
        return
    with _local_lock:
        if _local_loaded:
            return
        path = get_settings().image_index_csv_path
        if not path:
            _local_error = "File CSV galeri (part_image_index_rows.csv) tidak ditemukan"
            _local_loaded = True
            return
        if not _NUMPY_OK:
            _local_error = "numpy tidak tersedia untuk pencocokan lokal"
            _local_loaded = True
            return
        try:
            metas: list[tuple[str, str]] = []
            vecs: list = []
            dim = None
            skipped = 0
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pn = (row.get("part_number") or "").strip()
                    url = (row.get("sims_url") or "").strip()
                    emb = (row.get("embedding") or "").strip()
                    if not pn or not emb:
                        skipped += 1
                        continue
                    v = np.fromstring(emb.strip("[] \t"), sep=",", dtype=np.float32)
                    if dim is None:
                        dim = v.size
                    if v.size != dim or v.size == 0:
                        skipped += 1
                        continue
                    metas.append((pn, url))
                    vecs.append(v)
            if not vecs:
                _local_error = "CSV galeri kosong / tidak ada embedding valid"
                _local_loaded = True
                return
            mat = np.vstack(vecs).astype(np.float32)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            mat /= norms
            _local_matrix = mat
            _local_meta = metas
            print(
                f"[image_search] galeri lokal dimuat: {mat.shape[0]} embedding "
                f"(dim={dim}) dari {path}" + (f", {skipped} baris dilewati" if skipped else "")
            )
        except Exception as e:  # pragma: no cover
            _local_error = f"gagal memuat CSV galeri: {e}"
            print(f"[image_search] {_local_error}")
        finally:
            _local_loaded = True


def local_index_available() -> bool:
    _load_local_index()
    return _local_matrix is not None


def preload_local_index() -> None:
    """Muat galeri lokal sekarang (mis. saat startup) supaya request pertama cepat."""
    _load_local_index()


def reload_local_index() -> dict:
    """Muat ULANG galeri dari CSV (mis. setelah file diperbarui) tanpa restart server.
    Mengembalikan ringkasan: berhasil/tidak, jumlah embedding, path, error."""
    global _local_matrix, _local_meta, _local_loaded, _local_error
    with _local_lock:
        _local_matrix = None
        _local_meta = None
        _local_loaded = False
        _local_error = ""
    _load_local_index()
    path = get_settings().image_index_csv_path
    return {
        "ok": _local_matrix is not None,
        "total": int(_local_matrix.shape[0]) if _local_matrix is not None else 0,
        "path": str(path) if path else None,
        "error": _local_error or None,
    }


def append_local_index(rows: list[dict]) -> dict:
    """Tambahkan baris index BARU langsung ke galeri lokal (CSV + memori).

    rows: [{"part_number", "sims_url", "embedding"(str "[..]"), "indexed_by"?}]
    - Tulis ke CSV galeri (persisten, append aman seperti tools/append_gallery.py:
      hanya menambah di akhir, isi lama tak pernah ditimpa).
    - Update matriks in-memory (bila sudah dimuat) supaya hasil index LANGSUNG
      kepakai di "Cari by Foto" tanpa perlu klik Reload Galeri.
    Dedup berdasar (part_number, sims_url). Return ringkasan.
    """
    global _local_matrix, _local_meta, _local_loaded, _local_error
    out = {"appended": 0, "skipped_dup": 0, "error": None}
    if not rows:
        return out
    if not _NUMPY_OK:
        out["error"] = "numpy tidak tersedia"
        return out
    path = get_settings().image_index_csv_path
    if not path:
        out["error"] = "CSV galeri tidak ditemukan (set IMAGE_INDEX_CSV / data/)"
        return out
    path = Path(path)

    # Pastikan galeri lokal sudah dimuat (untuk dedup + update memori).
    _load_local_index()

    with _local_lock:
        existing = set(_local_meta or [])

        # Header CSV: ikuti file lama bila ada, supaya urutan kolom tetap selaras.
        header = None
        if path.exists() and path.stat().st_size > 0:
            with open(path, newline="", encoding="utf-8") as f:
                header = next(csv.reader(f), None)
        if not header:
            header = ["part_number", "sims_url", "embedding", "indexed_by", "indexed_at"]

        new_vecs: list = []
        new_meta: list[tuple[str, str]] = []
        to_write: list[dict] = []
        dim = _local_matrix.shape[1] if _local_matrix is not None else None
        for r in rows:
            pn = (r.get("part_number") or "").strip()
            url = (r.get("sims_url") or "").strip()
            emb = (r.get("embedding") or "").strip()
            if not pn or not emb:
                continue
            if (pn, url) in existing:
                out["skipped_dup"] += 1
                continue
            v = np.fromstring(emb.strip("[] \t"), sep=",", dtype=np.float32)
            if v.size == 0 or (dim is not None and v.size != dim):
                continue
            if dim is None:
                dim = v.size
            existing.add((pn, url))
            new_vecs.append(v)
            new_meta.append((pn, url))
            to_write.append(r)

        if not to_write:
            return out

        # 1) Tulis ke CSV (persisten). Append-only; isi lama tak tersentuh.
        write_header = not path.exists() or path.stat().st_size == 0
        if not write_header:
            # pastikan file diakhiri newline agar baris baru tak menyambung baris lama
            try:
                with open(path, "rb+") as f:
                    f.seek(-1, 2)
                    if f.read(1) != b"\n":
                        f.write(b"\n")
            except OSError:
                pass
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            if write_header:
                w.writeheader()
            for r in to_write:
                w.writerow({k: r.get(k, "") for k in header})

        # 2) Update matriks in-memory (L2-normalize) → langsung kepakai.
        if _local_matrix is not None:
            add = np.vstack(new_vecs).astype(np.float32)
            norms = np.linalg.norm(add, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            add /= norms
            _local_matrix = np.vstack([_local_matrix, add])
            _local_meta = (_local_meta or []) + new_meta
        else:
            # Galeri belum termuat (mis. CSV baru saja dibuat) → paksa muat ulang
            # pada pencarian berikutnya supaya baris baru kebaca.
            _local_loaded = False
            _local_error = ""

        out["appended"] = len(to_write)
    return out


def _local_search(query_vec: list[float], distance_threshold: float, fetch_count: int) -> list[dict]:
    """Top-N kandidat dari galeri lokal — bentuk hasil identik dengan RPC Supabase
    (list dict dengan part_number, sims_url, similarity)."""
    _load_local_index()
    if _local_matrix is None:
        raise RuntimeError(_local_error or "galeri lokal tidak siap")
    q = np.asarray(query_vec, dtype=np.float32)
    qn = float(np.linalg.norm(q))
    if qn:
        q = q / qn
    if q.shape[0] != _local_matrix.shape[1]:
        raise RuntimeError(
            f"dimensi embedding query ({q.shape[0]}) != galeri ({_local_matrix.shape[1]})"
        )
    sims_vec = _local_matrix @ q  # cosine similarity (kedua sisi sudah dinormalisasi)
    min_sim = max(0.0, 1.0 - float(distance_threshold))
    n = sims_vec.shape[0]
    k = min(int(fetch_count), n)
    if k <= 0:
        return []
    # Ambil top-k via argpartition lalu urutkan k itu saja (hemat dibanding full sort).
    top_idx = np.argpartition(-sims_vec, k - 1)[:k] if k < n else np.arange(n)
    top_idx = top_idx[np.argsort(-sims_vec[top_idx])]
    out: list[dict] = []
    for i in top_idx:
        s = float(sims_vec[i])
        if s < min_sim:
            break
        pn, url = _local_meta[i]
        out.append({"part_number": pn, "sims_url": url, "similarity": s})
    return out


def _fetch_candidates(query_vec: list[float], distance_threshold: float, fetch_count: int) -> list[dict]:
    """Ambil kandidat foto termirip. Utamakan galeri lokal (CSV); fallback ke RPC
    Supabase hanya bila file lokal tidak ada."""
    if local_index_available():
        try:
            return _local_search(query_vec, distance_threshold, fetch_count)
        except Exception as e:
            print(f"[image_search] pencarian lokal gagal, fallback ke RPC: {e}")

    if not get_settings().supabase_configured:
        return []
    rows: list[dict] = []
    for attempt in range(SEARCH_RPC_RETRIES + 1):
        try:
            rows = _rpc(query_vec, distance_threshold, fetch_count)
        except Exception as e:
            print(f"[image_search] RPC error: {e}")
            rows = []
        # pgvector cold-start: kadang 200 + kosong di call pertama → retry sekali
        if rows or attempt >= SEARCH_RPC_RETRIES:
            break
    return rows


# ── Search (agregasi per PN — identik image_search.py) ───────────────
def search_by_image(
    image_bytes: bytes,
    top_k: int = 12,
    threshold: float = 0.30,
    use_tta: bool = False,
) -> list[dict]:
    # Butuh galeri lokal (CSV) ATAU Supabase terkonfigurasi.
    if not local_index_available() and not get_settings().supabase_configured:
        return []

    query_vec = compute_embedding_tta(image_bytes) if use_tta else compute_embedding(image_bytes)

    distance_threshold = max(0.0, min(1.0 - threshold, 2.0))
    fetch_count = max(int(top_k) * _AGG_FETCH_MULT, _AGG_FETCH_MIN)

    rows = _fetch_candidates(query_vec, distance_threshold, fetch_count)

    # Group per PN: foto terbaik + statistik
    by_pn: dict[str, dict] = {}
    for r in rows:
        pn = r.get("part_number", "")
        if not pn:
            continue
        sim = float(r.get("similarity") or 0.0)
        url = r.get("sims_url", "")
        entry = by_pn.setdefault(pn, {"best_url": url, "best_sim": sim, "n_matches": 0, "n_strong": 0})
        if sim > entry["best_sim"]:
            entry["best_sim"] = sim
            entry["best_url"] = url
        entry["n_matches"] += 1
        if sim >= _AGG_STRONG_TH:
            entry["n_strong"] += 1

    aggregated: list[dict] = []
    for pn, info in by_pn.items():
        extra_strong = max(0, info["n_strong"] - 1)
        boost = min(_AGG_BOOST_PER_MATCH * extra_strong, _AGG_BOOST_CAP)
        agg_score = min(info["best_sim"] + boost, 1.0)
        aggregated.append({
            "part_number": pn,
            "part_name": _part_index.name_for(pn),
            "sims_url": info["best_url"],
            "similarity": agg_score,
            "raw_similarity": info["best_sim"],
            "n_matches": info["n_matches"],
            "n_strong": info["n_strong"],
            "boost": boost,
            "distance": 1.0 - info["best_sim"],
        })

    aggregated.sort(key=lambda x: x["similarity"], reverse=True)
    if threshold > 0:
        aggregated = [a for a in aggregated if a["similarity"] >= threshold]
    return aggregated[: int(top_k)]


# ── Indexing: bangun embedding galeri (part_image_index) ─────────────
INDEX_TABLE = "part_image_index"
_INDEX_CHUNK = 50


def _index_headers(prefer: str = "") -> dict:
    key = get_settings().storage_key
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def index_count() -> int:
    """Jumlah baris di part_image_index (perkiraan total foto terindeks).
    Utamakan galeri lokal (CSV); fallback ke COUNT di Supabase."""
    if local_index_available():
        return int(_local_matrix.shape[0])
    s = get_settings()
    if not s.supabase_configured:
        return 0
    try:
        resp = requests.get(
            f"{_base_url()}/rest/v1/{INDEX_TABLE}",
            headers={**_index_headers(), "Prefer": "count=exact", "Range": "0-0"},
            params={"select": "id"},
            timeout=15,
        )
        cr = resp.headers.get("content-range", "")
        if "/" in cr:
            return int(cr.split("/")[-1])
    except Exception:
        pass
    return 0


def indexed_urls(pn: str) -> list[str]:
    """URL foto SIMS yang tersimpan di index Cari-by-Foto (part_image_index).
    Utamakan galeri lokal (CSV); fallback ke query Supabase."""
    if local_index_available():
        key = (pn or "").strip().upper()
        return sorted({url for (p, url) in _local_meta if url and p.strip().upper() == key})
    return sorted(_index_existing_urls(pn))


def _index_existing_urls(pn: str) -> set[str]:
    try:
        resp = requests.get(
            f"{_base_url()}/rest/v1/{INDEX_TABLE}",
            headers=_index_headers(),
            params={"select": "sims_url", "part_number": f"eq.{pn.strip().upper()}"},
            timeout=15,
        )
        resp.raise_for_status()
        return {r["sims_url"] for r in (resp.json() or []) if r.get("sims_url")}
    except Exception:
        return set()


def _dl(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


def _local_existing_urls(pn: str) -> set[str]:
    """URL foto yang SUDAH ada di galeri lokal untuk part ini (untuk dedup index)."""
    _load_local_index()
    if not _local_meta:
        return set()
    key = (pn or "").strip().upper()
    return {url for (p, url) in _local_meta if url and p.strip().upper() == key}


def index_part(pn: str, indexed_by: str = "admin", reindex: bool = False) -> dict:
    """
    Index 1 part number: ambil foto SIMS → embedding DINOv2 → simpan ke galeri
    LOKAL (CSV part_image_index_rows.csv + memori) supaya langsung kepakai di
    "Cari by Foto". Lewati URL yang sudah terindeks (lokal) kecuali reindex=True.

    Catatan: TIDAK menulis ke Supabase — galeri Cari by Foto dikelola lokal.
    """
    pn = (pn or "").strip().upper()
    out = {"pn": pn, "found": 0, "already": 0, "indexed": 0, "failed": 0, "error": None}
    if not _TORCH_OK:
        out["error"] = "torch tidak tersedia"
        return out

    urls = sims.get_images(pn)
    # Fallback: kalau PN berakhiran "/<angka>" (mis. 080-02400-6055/1) dan SIMS tak
    # punya gambar, coba lagi tanpa akhiran itu (080-02400-6055). Gambar diambil dari
    # nomor dasar, tetapi tetap DISIMPAN atas part number asli supaya cocok katalog.
    if not urls:
        base = re.sub(r"/\d+$", "", pn)
        if base != pn:
            alt = sims.get_images(base)
            if alt:
                urls = alt
                out["fallback_pn"] = base
    out["found"] = len(urls)
    if not urls:
        out["error"] = "Tidak ada gambar SIMS"
        return out

    existing = set() if reindex else _local_existing_urls(pn)
    targets = [u for u in urls if u not in existing]
    out["already"] = len(urls) - len(targets)

    rows = []
    for u in targets:
        b = _dl(u)
        if not b:
            out["failed"] += 1
            continue
        try:
            vec = compute_embedding(b)
            rows.append({"part_number": pn, "sims_url": u, "embedding": _vec_to_str(vec), "indexed_by": indexed_by})
        except Exception:
            out["failed"] += 1

    # Simpan ke galeri lokal (CSV + memori) → langsung kepakai di Cari by Foto.
    if rows:
        try:
            local = append_local_index(rows)
            out["indexed"] = local.get("appended", 0)
            if local.get("skipped_dup"):
                out["already"] += local["skipped_dup"]
            if local.get("error"):
                out["error"] = local["error"]
        except Exception as e:
            out["error"] = str(e)
    return out
