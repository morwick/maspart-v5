"""Router parts: pencarian Part Number + status/refresh index."""
from __future__ import annotations

import re
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
    ImageLearnResponse,
    ImageSearchResponse,
    IndexStatus,
    PartPhotos,
    SearchResponse,
)
from ..services import accurate, catalog, compare, gudang, image_search, part_index, reservations, search_log, sims
from ..services.supabase_client import fetch_part_photos, get_user_gudang

_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024   # 8 MB — cap upload Excel/CSV batch (anti zip-bomb/OOM)
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


def _paginate(term: str, results: list, page: int, page_size: int,
              saran: list[dict] | None = None) -> SearchResponse:
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
        saran=saran or [],
    )


def _scope_gudang(results: list[dict], user: dict) -> list[dict]:
    """Filter breakdown gudang tiap hasil sesuai cabang user (stok total tetap).

    Pembeli: reservasi dikurangkan SEBELUM scoping supaya fallback gudang terdekat
    tetap bekerja saat gudang sendiri habis direservasi. WAJIB konsisten dengan
    etalase (buyer_catalog._scoped_stock) — kalau tidak, katalog bisa READY tapi
    detail 'habis' (bug urutan reserve-vs-scope)."""
    names = part_index.gudang_names()
    uname, role = user.get("username", ""), user.get("role", "user")
    # Pembeli: scope ke gudang yang DIPILIH (bukan mapping per-username).
    own = gudang.buyer_label(get_user_gudang(uname)) if role == "pembeli" else None
    resv = reservations.reserved_map() if role == "pembeli" else {}
    for r in results:
        bd = dict(r.get("gudang") or {})
        if role == "pembeli" and resv:
            pn = str(r.get("part_number", "")).upper()
            bd = {g: q - resv.get((pn, g), 0) for g, q in bd.items()}
            bd = {g: q for g, q in bd.items() if q > 0}  # buang yang habis (tersisa ≤ 0)
        r["gudang"] = gudang.scope_breakdown(bd, uname, role, names, own=own)
    return results


def _overlay_accurate(results: list[dict]) -> list[dict]:
    """Overlay STOK (total) + HARGA dari indeks Accurate bersama (tarikan terjadwal
    tiap 5 jam — sumber sama dgn menu Stok & detail part) = sumber UTAMA; Excel =
    fallback bila PN tak ada / indeks belum siap. Non-blocking: snapshot() cuma view
    dict di memori, request tak pernah menunggu tarikan Accurate. Rincian per-gudang
    (`gudang`) tetap Excel (indeks hanya agregat)."""
    if not accurate.available():
        return results
    try:
        snap = accurate.snapshot()
    except Exception:
        return results
    if not snap:
        return results
    for r in results:
        e = snap.get(accurate.norm_pn(r.get("part_number") or ""))
        if not e:
            continue
        if e.get("stok") is not None:
            r["stok"] = f"{int(e['stok']):,}".replace(",", ".")
        if e.get("harga"):
            r["harga"] = "Rp " + f"{int(e['harga']):,}".replace(",", ".")
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
        # PN "pemaaf": buang suffix qty/halaman ('WG…0011/7'), cocokkan tanpa
        # pemisah, atau temukan BASIS PN dari PN varian panjang.
        results, _smart_note = part_index.smart_pn_search(q)
    if not results:
        # Tidak ketemu di database lokal → cari nama part dari SIMS.
        results = _sims_fallback(q)
    elif not part_index.is_exact_match_found(q):
        # Ada hasil substring (mis. "080V05504-6096/2") tapi tidak exact match →
        # tampilkan hasil lokal + tambahkan data SIMS di bawah (jika ada).
        sims_results = _sims_fallback(q)
        if sims_results:
            results = results + sims_results
    saran: list[dict] = []
    if not results:
        # "Mungkin maksud Anda" — PN katalog selisih 1-2 karakter + nama mirip
        # (mesin saran yang sama dengan asisten AI).
        saran = (part_index.suggest_pns(q) + part_index.suggest_names(q, limit=6))[:6]
        if page == 1:
            search_log.record_miss(q, "pn", "search")
    return _paginate(q, _overlay_accurate(_scope_gudang(results, user)), page, page_size,
                     saran=saran)


@router.get("/search-name", response_model=SearchResponse)
def search_name(
    q: str = Query(..., min_length=1, description="Part name (cocok per kata)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    results = part_index.search_part_name(q)
    if not results:
        # EKSPANSI SINONIM (kamus yang sama dengan asisten AI): istilah lapangan
        # Indonesia ('spion', 'kampas rem') → kata kunci katalog Inggris. Fallback
        # saja (0 hasil) agar pencarian frasa persis tetap presisi. Kasus nyata
        # log Pencarian Nihil: 'spion' miss padahal kamus punya spion→mirror —
        # halaman search belum memakai kamus, baru asisten.
        try:
            from ..services.ai_assistant import _expand_query
            terms, matched = _expand_query(q)
            if matched:
                seen: set = set()
                for t in terms[1:]:
                    for r in part_index.search_part_name(t):
                        key = (r.get("part_number"), r.get("file"))
                        if key not in seen:
                            seen.add(key)
                            results.append(r)
        except Exception:
            pass
    saran: list[dict] = []
    if not results:
        saran = part_index.suggest_names(q, limit=6)
        if page == 1:
            search_log.record_miss(q, "name", "search")
    return _paginate(q, _overlay_accurate(_scope_gudang(results, user)), page, page_size,
                     saran=saran)


@router.get("/accurate-stock")
def accurate_stock(
    pn: str = Query(..., min_length=1, description="Part Number persis untuk cek stok Accurate"),
    user: dict = Depends(get_current_user),
):
    """Stok ERP Accurate untuk 1 Part Number (agregat+harga dari indeks sinkron
    tiap 5 jam; rincian per-gudang live per-PN). Non-fatal: kegagalan sesi/koneksi
    dikembalikan sebagai status, bukan error — frontend menampilkan seadanya."""
    if not accurate.available():
        return {"configured": False, "reason": "no_session"}
    try:
        hit = accurate.stock_full(pn)
    except accurate.AccurateSessionExpired:
        return {"configured": True, "session_expired": True}
    except accurate.AccurateError:
        return {"configured": True, "error": True}
    if not hit:
        return {"configured": True, "found": False}
    return {
        "configured": True,
        "found": True,
        "stock": {
            "available_to_sell": hit["available_to_sell"],
            "quantity": hit["quantity"],
            "unit": hit["unit"],
            "name": hit["name"],
            "no": hit["no"],
            "item_type": hit["item_type"],
            "harga": hit.get("price") or 0,
            "per_gudang": hit.get("per_gudang") or [],
        },
    }


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
    columns: str = Form("", description="Kolom dipilih, pisah koma (nama,foto,stok,harga_sims,harga_accurate,harga_daftar)"),
    user: dict = Depends(get_current_user),
):
    # Kumpulkan PN dari file (kolom A) atau teks manual.
    if file is not None:
        try:
            data = await file.read()
            if len(data) > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"File maksimal {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                )
            raw = catalog.parse_part_numbers_from_file(file.filename or "", data)
        except HTTPException:
            raise
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

    col_list = [c.strip() for c in columns.split(",") if c.strip()]
    # Kolom harga (SIMS/jual Accurate/daftar) HANYA untuk admin & akun 'mas'.
    # Akun lain: diam-diam di-strip agar tak bisa mengekspor harga via batch.
    if not gudang.can_see_price(user.get("username", ""), user.get("role", "")):
        col_list = [c for c in col_list if c not in catalog.PRICE_COLUMNS]
    try:
        xls = catalog.build_catalog_excel(part_numbers, columns=col_list)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gagal membuat katalog: {e}")

    return Response(
        content=xls,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="catalog.xlsx"'},
    )


def _overlay_stok_harga_image(results: list[dict]) -> None:
    """Isi stok/harga/tersedia tiap hasil Cari-by-Foto — user langsung tahu mana
    yang READY tanpa membuka detail satu-satu. Sumber sama dgn pencarian teks:
    indeks Accurate bersama (utama) → Excel katalog (fallback). In-place, non-fatal."""
    snap: dict = {}
    if accurate.available():
        try:
            snap = accurate.snapshot()
        except Exception:
            snap = {}
    for r in results:
        pn = r.get("part_number") or ""
        stok, harga = "", ""
        try:
            row = next((x for x in part_index.search_part_number(pn)
                        if (x.get("part_number") or "").upper() == pn.upper()), None)
        except Exception:
            row = None
        if row:
            stok = str(row.get("stok") or "")
            harga = str(row.get("harga") or "")
        e = snap.get(accurate.norm_pn(pn))
        if e:
            if e.get("stok") is not None:
                stok = f"{int(e['stok']):,}".replace(",", ".")
            if e.get("harga"):
                harga = "Rp " + f"{int(e['harga']):,}".replace(",", ".")
        r["stok"] = stok or "—"
        r["harga"] = harga or "—"
        try:
            r["tersedia"] = int(re.sub(r"[^\d-]", "", stok) or 0) > 0
        except ValueError:
            r["tersedia"] = False


@router.post("/search-image", response_model=ImageSearchResponse)
async def search_image(
    file: UploadFile = File(..., description="Foto part (jpg/png/webp)"),
    top_k: int = Query(12, ge=1, le=50),
    threshold: float = Query(0.30, ge=0.0, le=1.0),
    use_tta: bool = Query(True, description="Mode akurat (TTA multi-varian, sedikit lebih lambat)"),
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
    _overlay_stok_harga_image(results)
    stats = image_search.index_stats()
    pesan = None
    if not results:
        pesan = (
            f"Tidak ada yang mirip di galeri ({stats['total']} foto dari {stats['parts']} part) — "
            "part di luar galeri tidak akan ketemu lewat foto. Tips: CROP foto ke part-nya saja "
            "(buang latar), coba sudut/pencahayaan lain, atau cari via teks (nama/PN)."
        )
    return ImageSearchResponse(count=len(results), results=results,
                               galeri_total=stats["total"], galeri_parts=stats["parts"],
                               pesan=pesan)


@router.post("/search-image/learn", response_model=ImageLearnResponse)
async def search_image_learn(
    file: UploadFile = File(..., description="Foto query yang dikonfirmasi"),
    pn: str = Form(..., min_length=3, description="Part Number yang benar untuk foto ini"),
    user: dict = Depends(get_current_user),
):
    """GALERI BELAJAR: user mengonfirmasi 'foto ini = PN X' → foto diindeks ke
    galeri sehingga pencarian berikutnya makin akurat untuk foto lapangan serupa.
    Hanya admin/'mas' (kurasi — label salah justru meracuni galeri)."""
    if user.get("role") != "admin" and (user.get("username") or "").lower() != "mas":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Hanya admin yang boleh menambah galeri belajar.")
    if not image_search.torch_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Model AI (torch) tidak tersedia di server.")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    if len(data) > _MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Ukuran foto > 15 MB.")
    res = image_search.learn_from_photo(data, pn, indexed_by=user.get("username") or "admin")
    if res.get("error"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, res["error"])
    return ImageLearnResponse(ok=True, pn=res["pn"], duplikat=bool(res.get("duplikat")),
                              galeri_total=int(res.get("galeri_total") or 0))


@router.get("/learned-photo/{fname}")
def learned_photo(fname: str):
    """Sajikan foto galeri belajar (sims_url 'learned://<fname>'). TANPA auth
    (agar <img> bisa memuat, spt image-proxy) — nama file tervalidasi ketat &
    hanya dari folder learned_photos."""
    p = image_search.learned_photo_path(fname)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Foto tidak ditemukan.")
    return Response(content=p.read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


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


@router.get("/spec")
def part_spec(
    pn: str = Query(..., min_length=1, description="Part number"),
    _user: dict = Depends(get_current_user),
):
    """Spesifikasi fisik resmi dari SIMS untuk satu PN: berat (kg), dimensi (cm),
    satuan, kemasan minimum, merek. Ikut menghangatkan cache part_info → berat
    untuk ongkir tersedia setelahnya. {} bila SIMS tak punya data / tak aktif."""
    pn = (pn or "").strip()
    spec = sims.get_part_spec(pn) if sims.available() else {}
    berat_gram = sims.get_part_weight_grams_cached(pn)
    return {"part_number": pn.upper(), "spec": spec, "berat_gram": berat_gram}


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