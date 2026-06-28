"""Router parts: pencarian Part Number + status/refresh index."""
from __future__ import annotations

from urllib.parse import urlparse

import requests
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response

from ..deps import get_current_user, require_admin
from ..schemas import (
    CompareResponse,
    ImageSearchResponse,
    IndexStatus,
    PartPhotos,
    SearchResponse,
)
from ..services import catalog, compare, gudang, image_search, part_index, reservations, sims
from ..services.supabase_client import fetch_part_photos, get_user_gudang

_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_SIMS_MIN_LEN = 4  # panjang minimum query untuk fallback SIMS (hindari lookup query pendek)

router = APIRouter(prefix="/api/parts", tags=["parts"])


def _sims_fallback(term: str) -> list[dict]:
    """Bila Part Number tak ada di database lokal, ambil Nama Part dari SIMS
    (seperti app Streamlit lama). Kembalikan satu hasil sintetis atau []."""
    pn = (term or "").strip()
    if len(pn) < _SIMS_MIN_LEN or not sims.available():
        return []
    info = sims.get_part_info(pn)
    name = (str(info.get("partName") or "")).strip() if info else ""
    if not name:
        return []
    return [{
        "file": "SIMS",
        "path": "",
        "sheet": "",
        "part_number": pn.upper(),
        "part_name": name,
        "quantity": "—",
        "stok": "—",
        "harga": "—",
        "gudang": {},
        "excel_row": 0,
        "source": "sims",
    }]


def _paginate(term: str, results: list, page: int, page_size: int) -> SearchResponse:
    total = len(results)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    return SearchResponse(
        term=term.strip(),
        count=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        results=results[start : start + page_size],
    )


def _scope_gudang(results: list[dict], user: dict) -> list[dict]:
    """Filter breakdown gudang tiap hasil sesuai cabang user (stok total tetap)."""
    names = part_index.gudang_names()
    uname, role = user.get("username", ""), user.get("role", "user")
    # Pembeli: scope ke gudang yang DIPILIH (bukan mapping per-username).
    own = gudang.buyer_label(get_user_gudang(uname)) if role == "pembeli" else None
    resv = reservations.reserved_map() if role == "pembeli" else {}
    for r in results:
        bd = gudang.scope_breakdown(r.get("gudang") or {}, uname, role, names, own=own)
        if role == "pembeli" and resv:
            pn = str(r.get("part_number", "")).upper()
            bd = {g: q - resv.get((pn, g), 0) for g, q in bd.items()}
            bd = {g: q for g, q in bd.items() if q > 0}  # buang yang habis (tersisa ≤ 0)
        r["gudang"] = bd
    return results


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, description="Part number (cocok substring, uppercase)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    results = part_index.search_part_number(q)
    if not results:
        # Tidak ketemu di database lokal → cari nama part dari SIMS.
        results = _sims_fallback(q)
    elif not part_index.is_exact_match_found(q):
        # Ada hasil substring (mis. "080V05504-6096/2") tapi tidak exact match →
        # tampilkan hasil lokal + tambahkan data SIMS di bawah (jika ada).
        sims_results = _sims_fallback(q)
        if sims_results:
            results = results + sims_results
    return _paginate(q, _scope_gudang(results, user), page, page_size)


@router.get("/search-name", response_model=SearchResponse)
def search_name(
    q: str = Query(..., min_length=1, description="Part name (cocok per kata)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    return _paginate(q, _scope_gudang(part_index.search_part_name(q), user), page, page_size)


@router.get("/batch-template")
def batch_template(_user: dict = Depends(get_current_user)):
    data = catalog.make_template_excel()
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="template_batch_input.xlsx"'},
    )


@router.post("/batch-catalog")
async def batch_catalog(
    text: str = Form("", description="Daftar part number, 1 per baris"),
    file: UploadFile | None = File(None, description="File Excel/CSV berisi PN di kolom A"),
    _user: dict = Depends(get_current_user),
):
    # Kumpulkan PN dari file (kolom A) atau teks manual.
    if file is not None:
        try:
            data = await file.read()
            raw = catalog.parse_part_numbers_from_file(file.filename or "", data)
        except Exception as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gagal membaca file: {e}")
        seen, part_numbers = set(), []
        for pn in raw:
            up = pn.upper()
            if up not in seen:
                seen.add(up)
                part_numbers.append(pn)
    else:
        part_numbers, _dups = catalog.parse_part_numbers(text)

    if not part_numbers:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada Part Number yang valid.")
    if len(part_numbers) > catalog._MAX_BATCH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Maksimum {catalog._MAX_BATCH} PN per batch (diberikan {len(part_numbers)}).",
        )

    try:
        xls = catalog.build_catalog_excel(part_numbers)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gagal membuat katalog: {e}")

    return Response(
        content=xls,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="catalog.xlsx"'},
    )


@router.post("/search-image", response_model=ImageSearchResponse)
async def search_image(
    file: UploadFile = File(..., description="Foto part (jpg/png/webp)"),
    top_k: int = Query(12, ge=1, le=50),
    threshold: float = Query(0.30, ge=0.0, le=1.0),
    use_tta: bool = Query(False, description="Test-time augmentation (lebih akurat, 2× lambat)"),
    _user: dict = Depends(get_current_user),
):
    if not image_search.torch_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model AI (torch) tidak tersedia di server.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Ukuran foto > 15 MB.")
    try:
        results = image_search.search_by_image(
            data, top_k=top_k, threshold=threshold, use_tta=use_tta
        )
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gagal memproses foto: {e}")
    return ImageSearchResponse(count=len(results), results=results)


@router.get("/compare", response_model=CompareResponse)
def compare_parts(
    pn1: str = Query(..., min_length=1),
    pn2: str = Query(..., min_length=1),
    _user: dict = Depends(get_current_user),
):
    if pn1.strip().upper() == pn2.strip().upper():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Part Number tidak boleh sama.")
    return compare.compare(pn1, pn2)


@router.get("/photos", response_model=PartPhotos)
def photos(
    pn: str = Query(..., min_length=1, description="Part number"),
    refresh: bool = Query(False, description="Paksa ambil ulang dari SIMS (lewati cache)"),
    _user: dict = Depends(get_current_user),
):
    # Utamakan SIMS; kalau kosong, pakai foto yang sudah terindeks di galeri
    # Cari-by-Foto (part_image_index); terakhir fallback ke tabel part_photos.
    sims_urls = sims.get_images(pn, force_refresh=refresh)
    if sims_urls:
        return PartPhotos(part_number=pn.strip(), photos=sims_urls, source="sims")
    idx_urls = image_search.indexed_urls(pn)
    if idx_urls:
        return PartPhotos(part_number=pn.strip(), photos=idx_urls, source="image_index")
    return PartPhotos(part_number=pn.strip(), photos=fetch_part_photos(pn), source="part_photos")


# Host SIMS yang boleh di-proxy (cegah open-proxy / SSRF).
_PROXY_ALLOWED_HOSTS = {"simscloud.cnhtcerp.com"}


@router.get("/image-proxy")
def image_proxy(url: str = Query(..., min_length=8, description="URL gambar SIMS (http)")):
    """Proxy gambar SIMS lewat backend supaya bisa tampil di halaman HTTPS tanpa
    kena blokir mixed-content. TANPA auth (agar <img> bisa memuat) tapi dibatasi
    HANYA ke host SIMS yang diizinkan."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "URL tidak valid.")
    if parsed.scheme not in ("http", "https") or parsed.hostname not in _PROXY_ALLOWED_HOSTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Host gambar tidak diizinkan.")
    try:
        r = requests.get(url, timeout=20)
    except Exception:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal mengambil gambar dari SIMS.")
    if r.status_code != 200 or not r.content:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"SIMS membalas {r.status_code}.")
    ctype = r.headers.get("Content-Type", "image/jpeg")
    if not ctype.startswith("image/"):
        ctype = "image/jpeg"
    return Response(
        content=r.content,
        media_type=ctype,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/index/status", response_model=IndexStatus)
def index_status(_user: dict = Depends(get_current_user)):
    part_index.ensure_index()
    return part_index.status()


@router.post("/index/refresh", response_model=IndexStatus)
def index_refresh(_admin: dict = Depends(require_admin)):
    return part_index.refresh_index()