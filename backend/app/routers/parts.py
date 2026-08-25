"""Router parts: pencarian Part Number + status/refresh index."""
from __future__ import annotations

import asyncio
import base64
import logging
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
from pydantic import BaseModel

from ..core.ratelimit import limit
from ..deps import get_current_user, require_admin
from ..schemas import (
    CompareResponse,
    ImageLearnResponse,
    ImageSearchResponse,
    IndexStatus,
    PartPhotos,
    SearchResponse,
)
from ..services import (accurate, ai_export, catalog, compare, epc_bom, exploded_view, gudang,
                        image_search, part_index, permissions, reservations, search_log, sims,
                        weichai_stock)
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


def _scope_bd(bd: dict, pn: str, user: dict, names: list[str],
              own: str | None, resv: dict) -> dict:
    """Aturan WILAYAH untuk SATU breakdown {gudang: qty} — badan lama loop
    `_scope_gudang`, dipisah agar endpoint /varian memakai aturan yang PERSIS
    SAMA (mengarang aturan wilayah kedua = pembeli bisa melihat gudang yang
    salah, atau melihat stok yang sudah direservasi orang lain)."""
    uname, role = user.get("username", ""), user.get("role", "user")
    if role == "pembeli":
        bd = gudang.shippable(bd)       # gudang non-kirim tak dijual ke pembeli
        if resv:
            bd = {g: q - resv.get((pn, g), 0) for g, q in bd.items()}
            bd = {g: q for g, q in bd.items() if q > 0}  # buang yang habis (tersisa ≤ 0)
    scoped = gudang.scope_breakdown(bd, uname, role, names, own=own)
    if role == "pembeli":
        # Sembunyikan label gudang BERNOMOR (mis. '01.Jakarta','06.B80 H1')
        # — pembeli hanya boleh lihat nama kota, konsisten dgn etalase.
        # scope_breakdown mengembalikan SATU gudang pemenuh utk pembeli →
        # tak ada tabrakan kunci saat di-relabel.
        scoped = {gudang.gudang_label(g): q for g, q in scoped.items()}
    return scoped


def _scope_gudang(results: list[dict], user: dict) -> list[dict]:
    """Filter breakdown gudang tiap hasil sesuai cabang user (stok total tetap).

    Pembeli: reservasi dikurangkan SEBELUM scoping supaya fallback gudang terdekat
    tetap bekerja saat gudang sendiri habis direservasi. WAJIB konsisten dengan
    etalase (buyer_catalog._scoped_stock) — kalau tidak, katalog bisa READY tapi
    detail 'habis' (bug urutan reserve-vs-scope)."""
    names = part_index.gudang_names()
    role = user.get("role", "user")
    # Pembeli: scope ke gudang yang DIPILIH (bukan mapping per-username).
    own = (gudang.buyer_label(get_user_gudang(user.get("username", "")))
           if role == "pembeli" else None)
    resv = reservations.reserved_map() if role == "pembeli" else {}
    for r in results:
        r["gudang"] = _scope_bd(dict(r.get("gudang") or {}),
                                str(r.get("part_number", "")).upper(),
                                user, names, own, resv)
    return results


def _overlay_accurate(results: list[dict]) -> list[dict]:
    """Overlay STOK (total) + HARGA dari indeks Accurate bersama (tarikan terjadwal —
    sumber sama dgn menu Stok, detail part, & etalase).

    ⛔ HARGA: Accurate SATU-SATUNYA sumber (aturan keras pemilik). Baris hasil datang
    dari part_index dengan harga bawaan dari harga.xlsx — itu DIBUANG di sini, supaya
    harga yang dilihat = harga yang ditagih saat checkout (harga.price_for_buyer).
    Tanpa ini, part berharga di harga.xlsx tapi tak ada di Accurate tampil berharga,
    masuk keranjang, lalu ditolak saat bayar.

    Non-blocking: snapshot() cuma view dict di memori. Rincian per-gudang (`gudang`)
    tetap dari Excel (indeks Accurate hanya agregat)."""
    try:
        snap = accurate.snapshot() if accurate.available() else {}
    except Exception:
        snap = {}
    for r in results:
        r["harga"] = "—"        # buang harga bawaan harga.xlsx
        pn = r.get("part_number") or ""
        # KELUARGA VARIAN PEMASOK (2026-08-05): part yang di Accurate dipecah jadi
        # beberapa KARTU per pemasok ('…/SN','…/SH') → stok TOTAL sekeluarga +
        # harga RENTANG, lewat helper yang sama dengan baris chat/Excel
        # (part_index._acc_stok_harga) agar web & asisten tak pernah beda angka.
        fam = part_index.keluarga_acc(pn)
        if fam:
            # (stok_str, harga_str, …) — dua angka mentah di ekor tuple hanya
            # dipakai jalur Excel/chat, baris web memang cuma butuh stringnya.
            r["stok"], r["harga"] = part_index.label_keluarga(fam)[:2]
            continue
        # index_key: pemaaf suffix varian ("WG…/2" → PN dasar di Accurate).
        e = snap.get(accurate.index_key(pn))
        if not e:
            continue
        if e.get("stok") is not None:
            r["stok"] = f"{int(e['stok']):,}".replace(",", ".")
        if e.get("harga"):
            r["harga"] = "Rp " + f"{int(e['harga']):,}".replace(",", ".")
    return results


def _gate_kolom(results: list[dict], user: dict) -> list[dict]:
    """Menu Control server-side: mask harga/stok untuk staf yang centang kolomnya
    dimatikan. Skema PartResult mewajibkan str → MASK '—' (frontend sudah
    merendernya), bukan pop. `quantity` TIDAK disentuh — itu qty BOM, bukan stok."""
    if not permissions.boleh_harga(user):
        for r in results:
            r["harga"] = "—"
    if not permissions.boleh_stok(user):
        for r in results:
            r["stok"], r["gudang"] = "—", {}
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
    if not results:
        # Kotak PN sering diisi NAMA part. Bukti log Pencarian Nihil: 'fuel
        # filter' tercatat GAGAL 5× di sini, padahal pencarian NAMA memberi 571
        # hasil — user tak punya cara tahu ia salah kotak. Fallback ini dijalankan
        # PALING AKHIR supaya pencarian PN tetap presisi.
        results = part_index.search_part_name(q)
    saran: list[dict] = []
    if not results:
        # "Mungkin maksud Anda" — PN katalog selisih 1-2 karakter + nama mirip
        # (mesin saran yang sama dengan asisten AI).
        saran = (part_index.suggest_pns(q) + part_index.suggest_names(q, limit=6))[:6]
        if page == 1:
            search_log.record_miss(q, "pn", "search")
    elif page == 1:
        # Query ini KETEMU sekarang → cabut dari daftar Pencarian Nihil bila
        # pernah tercatat. Tanpa ini daftar bukti admin makin lama makin basi:
        # 13% entri (38 kejadian) ternyata sudah bisa ditemukan hari ini, dan
        # miss basi itu menyesatkan saat dipakai memprioritaskan pekerjaan.
        try:
            search_log.resolve_miss(q)
        except Exception:
            pass
    return _paginate(q, _gate_kolom(_overlay_accurate(_scope_gudang(results, user)), user),
                     page, page_size, saran=saran)


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
    elif page == 1:
        try:
            search_log.resolve_miss(q)      # lihat catatan di endpoint /search
        except Exception:
            pass
    return _paginate(q, _gate_kolom(_overlay_accurate(_scope_gudang(results, user)), user),
                     page, page_size, saran=saran)


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
    resp = {
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
    # KELUARGA VARIAN PEMASOK (2026-08-05): kartu SAUDARA part ini (harga & stok
    # beda per pemasok). Diteruskan ke SEMUA peran — isinya stok/harga varian,
    # BUKAN sebaran antar-gudang, jadi tak melanggar "gudang disembunyikan dari
    # pembeli". Field di-rename ke 'kode/stok/harga' supaya penjaga strip terpusat
    # (yang mengenali nama key) ikut menjangkaunya.
    if hit.get("varian_lain"):
        resp["stock"]["varian_lain"] = [
            {"kode": v.get("pn"), "no": v.get("no"),
             "stok": v.get("available_to_sell"), "harga": int(v.get("price") or 0)}
            for v in hit["varian_lain"]
        ]
        resp["stock"]["stok_semua_varian"] = hit.get("stok_semua_varian")
    # Menu Control server-side: dict bebas → STRIP key (pola gerbang asisten).
    if not permissions.boleh_harga(user):
        permissions.strip_harga(resp)
    if not permissions.boleh_stok(user):
        # 'stok_semua_varian' bukan key standar STOK_KEYS → sebut eksplisit, kalau
        # tidak total stok bocor lewat pintu belakang ke staf tanpa centang stok.
        permissions.strip_stok(resp, extra=permissions.ROUTER_STOK_EXTRA
                               + ("stok_semua_varian",))
    # Pembeli boleh_stok=True (perlu tahu ketersediaan) TAPI rincian antar-gudang
    # (per_gudang: distribusi stok semua cabang, termasuk gudang internal) TIDAK
    # boleh bocor — aturan pemilik "gudang disembunyikan dari pembeli".
    if (user.get("role") or "").lower() == "pembeli":
        resp.get("stock", {}).pop("per_gudang", None)
    return resp


@router.get("/weichai-stock")
def weichai_stock_lookup(
    pn: str = Query(..., min_length=1, description="Part Number untuk cek stok PEMASOK Weichai"),
    user: dict = Depends(get_current_user),
):
    """Stok PEMASOK Weichai (portal tci-pnp) untuk 1 PN — diambil LIVE saat diminta.

    Berbeda dari `/accurate-stock` (stok KITA): ini ketersediaan di PEMASOK untuk
    restok. Portal tak memberi harga → STOK saja. Non-fatal: kegagalan login/koneksi
    dibalas sebagai status. Lambat (~5-8 dtk) → frontend memanggil on-demand (tombol),
    bukan tiap buka halaman; service men-cache per-PN 5 menit.

    ⛔ Internal-only: pembeli tak boleh melihat stok pemasok (sejalan "gudang
    disembunyikan dari pembeli"), dan hormati gerbang kolom stok Menu Control.
    """
    if (user.get("role") or "").lower() == "pembeli" or not permissions.boleh_stok(user):
        return {"configured": True, "found": False, "blocked": True}
    return weichai_stock.stok(pn)


@router.get("/varian")
def parts_varian(
    pn: str = Query(..., min_length=1, description="Part Number — semua kartu varian pemasoknya"),
    user: dict = Depends(get_current_user),
):
    """SEMUA kartu barang Accurate untuk satu part fisik (KELUARGA VARIAN PEMASOK).

    LATAR LIVE 2026-08-05: satu part dipecah per PEMASOK di Accurate dengan suffix
    huruf — PN 1000442956 punya base 482 pc @Rp 300rb, '/SN' 1.069 pc @Rp 285rb,
    '/SH' 2 pc @Rp 455rb. `/accurate-stock` hanya bisa menunjuk SATU kartu, jadi
    halaman part memperlihatkan stok/harga kartu yang kebetulan terpilih (kerap
    kartu mati 2 pc). Di sini semuanya disajikan apa adanya.

    ⛔ Aturan pemilik: harga TIDAK dirata-rata/digabung — `harga_min`/`harga_max`
    murni LABEL rentang; penjualan/penawaran selalu menunjuk `kode` varian yang
    dipilih user secara eksplisit.

    Baca INDEKS saja (nol panggilan Accurate live) → aman dipanggil tiap buka part.
    """
    if not accurate.available():
        return {"configured": False, "reason": "no_session"}
    try:
        fam = accurate.stock_family(pn)
    except accurate.AccurateSessionExpired:
        return {"configured": True, "session_expired": True}
    except accurate.AccurateError:
        return {"configured": True, "error": True}
    if not fam.get("found"):
        return {"configured": True, "found": False}
    varian = [{
        # 'kode' = PN kartu APA ADANYA, suffix ikut ('1000442956/SN') — itulah
        # yang harus disebut saat memesan/menawar, jangan dinormalisasi.
        "kode": v.get("pn") or "",
        "no": v.get("no") or "",
        "nama": v.get("name") or "",
        "stok": v.get("available_to_sell"),
        "harga": int(v.get("price") or 0),
        "unit": v.get("unit") or "",
        "per_gudang": v.get("per_gudang") or [],
    } for v in fam["varian"]]
    resp: dict = {
        "configured": True,
        "found": True,
        "base": fam["base"],
        "total_available": fam["total_available"],
        "harga_min": fam["harga_min"],
        "harga_max": fam["harga_max"],
        "varian": varian,
    }
    # Pembeli: sebaran antar-gudang TIDAK boleh bocor (aturan pemilik) → diganti
    # SATU angka wilayahnya sendiri, lewat aturan scoping yang sama persis dengan
    # hasil pencarian (_scope_bd: gudang non-kirim dibuang → reservasi dikurangkan
    # → gudang sendiri, kalau kosong fallback gudang terdekat).
    if (user.get("role") or "").lower() == "pembeli":
        names = part_index.gudang_names()
        own = gudang.buyer_label(get_user_gudang(user.get("username", "")))
        resv = reservations.reserved_map()
        for v in varian:
            bd = {g["gudang"]: g["qty"] for g in (v.pop("per_gudang", None) or [])}
            v["stok_wilayah"] = sum(
                _scope_bd(bd, str(v["kode"]).upper(), user, names, own, resv).values())
    # Menu Control server-side (pola sama dgn accurate_stock): key harga/stok yang
    # bukan nama standar disebut eksplisit di `extra`.
    if not permissions.boleh_harga(user):
        permissions.strip_harga(resp, extra=("harga_min", "harga_max"))
    if not permissions.boleh_stok(user):
        permissions.strip_stok(resp, extra=permissions.ROUTER_STOK_EXTRA
                               + ("total_available", "stok_wilayah"))
    return resp


@router.get("/batch-template")
def batch_template(_user: dict = Depends(get_current_user)):
    data = catalog.make_template_excel()
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="template_batch_input.xlsx"'},
    )


_log_batch = logging.getLogger("maspart.batch")


def _log_progres_batch(i: int, total: int, pn: str) -> None:
    """Hidupkan `on_progress` builder yang selama ini tak pernah dipasang router —
    supaya request yang bisa 15 menit tetap bisa dipantau lewat `docker logs`."""
    if i % 5 == 0 or i == total - 1:
        _log_batch.info("batch-catalog %d/%d — %s", i + 1, total, pn)


@router.post("/batch-catalog",
             dependencies=[Depends(limit("batch_catalog", 6, 300))])
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
    # Menu Control: kolom stok ikut centang col_stok (harga sudah lebih ketat di atas).
    if not permissions.boleh_stok(user):
        col_list = [c for c in col_list if c != "stok"]

    # Cap KHUSUS kolom Exploded View — jauh lebih rendah dari 300. Satu PN yang
    # belum pernah dibuka terukur sampai 94 detik (PN umum dipakai 17.185 model)
    # dan server hanya 1 vCPU; 300 PN akan berjalan berjam-jam.
    if ("exploded" in {c.lower() for c in col_list}
            and len(part_numbers) > catalog.MAX_BATCH_EXPLODED):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Kolom Exploded View maksimum {catalog.MAX_BATCH_EXPLODED} PN per batch "
            f"(diberikan {len(part_numbers)}). Gambar exploded diambil satu per satu "
            f"dari server EPC — sekitar 30-90 detik untuk tiap PN yang belum pernah "
            f"dibuka. Kurangi jumlah PN, atau matikan kolom Exploded View untuk "
            f"memakai batas {catalog._MAX_BATCH} PN.",
        )

    try:
        # asyncio.to_thread: build_catalog_excel SINKRON dan berat (HTTP ke SIMS/
        # Accurate/EPC + openpyxl + PIL). Dulu dipanggil langsung di `async def`,
        # jadi satu batch membekukan SELURUH server — cacat yang jadi fatal begitu
        # kolom exploded bisa berjalan 15 menit.
        xls = await asyncio.to_thread(
            exploded_view.bangun_dengan_gerbang,
            catalog.build_catalog_excel, part_numbers,
            columns=col_list, on_progress=_log_progres_batch,
        )
    except exploded_view.SedangSibuk:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Sedang ada satu Batch Download yang berjalan di server (hanya boleh satu "
            "sekaligus — server 1 vCPU dan server EPC mudah menolak permintaan "
            "berbarengan). Tunggu sampai yang berjalan selesai, lalu coba lagi.",
            headers={"Retry-After": "300"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gagal membuat katalog: {e}")

    return Response(
        content=xls,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="catalog.xlsx"'},
    )


def _overlay_stok_harga_image(results: list[dict]) -> None:
    """Isi stok/harga/tersedia tiap hasil Cari-by-Foto — user langsung tahu mana
    yang READY tanpa membuka detail satu-satu. Stok: indeks Accurate (utama) → Excel.
    ⛔ HARGA: Accurate SAJA — harga.xlsx tak pernah dipakai sebagai harga jual."""
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
        e = snap.get(accurate.index_key(pn))
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
    user: dict = Depends(get_current_user),
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
        # asyncio.to_thread: SINKRON dan berat. Sejak 2026-08-17 model DINOv2 tak
        # lagi di-preload saat startup, jadi permintaan foto PERTAMA ikut menanggung
        # pemuatan model ±15-20 detik — di `async def` itu membekukan SELURUH server,
        # bukan cuma permintaan ini. (Inferensinya sendiri, 1-3 detik, sebetulnya
        # sudah lama memblokir event loop dengan cara yang sama.)
        results = await asyncio.to_thread(
            image_search.search_by_image,
            data, top_k=top_k, threshold=threshold, use_tta=use_tta,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gagal memproses foto: {e}")
    _overlay_stok_harga_image(results)
    # Menu Control server-side (skema ImageSearchResponse → mask, bukan pop).
    if not permissions.boleh_harga(user):
        for r in results:
            r["harga"] = "—"
    if not permissions.boleh_stok(user):
        for r in results:
            r["stok"], r["tersedia"] = "—", False
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
    # Sama seperti /search-image: menghitung embedding (dan mungkin MEMUAT model)
    # di `async def` akan membekukan seluruh server, bukan cuma permintaan ini.
    res = await asyncio.to_thread(
        image_search.learn_from_photo, data, pn,
        indexed_by=user.get("username") or "admin",
    )
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


# ── Exploded view TANPA nomor rangka (jalur GLOBAL EPC) ─────────────────────
# Komposisi + cache-nya kini MILIK services/exploded_view supaya DIBAGI dengan
# kolom "Exploded View" di Batch Download: membuka gambar di sini menghangatkan
# batch, dan sebaliknya. Alias di bawah menunjuk ke dict yang SAMA (bukan salinan)
# agar test/kode lama yang mem-clear-nya tetap sah.
_exploded_cache = exploded_view.CACHE


@router.get("/exploded-figure")
def exploded_figure_global(
    pn: str = Query(..., min_length=3, description="Part Number (tanpa nomor rangka)."),
    _user: dict = Depends(get_current_user),
):
    """Gambar exploded view untuk SATU PN TANPA nomor rangka.

    ⚠️ Sengaja dipanggil ON-DEMAND (tombol), bukan saat halaman dibuka: panggilan
    pertama terukur sampai 94 detik. ⚠️ Figure bersifat LINTAS MODEL — field
    `catatan` membawa peringatan itu ke UI."""
    if not exploded_view.kunci_pn(pn):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Part Number kosong.")
    try:
        d = exploded_view.figure_untuk_pn(pn)
    except Exception:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "EPC sedang tak bisa diakses. Coba lagi sebentar.")
    if not d.get("found"):
        return d
    # ⚠️ WAJIB salin: `d` BOLEH JADI objek di dalam cache. Tanpa dict(), pop("png")
    # mencabut gambarnya dari cache → pemakai berikutnya (Batch Download) dapat
    # cache-hit TANPA gambar.
    out = dict(d)
    out["png_base64"] = base64.b64encode(out.pop("png")).decode("ascii")
    return out


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

# ── Cek kecocokan part di UNIT pembeli (per nomor rangka, sumber EPC) ─────────
# Fitur halaman detail part akun pembeli: pembeli memasukkan no rangka unitnya →
# aplikasi mengecek ke BOM per-VIN EPC apakah part ini memang terpasang di unit
# itu (aturan keras pemilik: part per-unit SELALU diverifikasi EPC, jangan tebak).
# Reuse penuh mesin asisten: epc_bom.loading_list (cache+lock per-frame),
# exploded_figures (gambar figure + nomor balon), kamus sinonim (istilah lapangan).

def _istilah_lapangan(nama_en: str) -> str:
    """Istilah bengkel Indonesia untuk sebuah nama part EN — kamus sinonim DIBALIK
    (keyword katalog → trigger lapangan). '' bila tak ada padanan. Keyword terpanjang
    menang ('brake friction plate' mengalahkan 'friction plate' generik)."""
    nu = (nama_en or "").lower()
    if not nu:
        return ""
    best_kw, best_trg = "", ""
    try:
        from ..services.ai_assistant import _load_sinonim_entries
        for e in _load_sinonim_entries():
            trg = next((t for t in (e.get("triggers") or []) if t), "")
            if not trg:
                continue
            for kw in (e.get("keywords") or []):
                kl = (kw or "").lower()
                if kl and kl in nu and len(kl) > len(best_kw):
                    best_kw, best_trg = kl, trg
    except Exception:
        return ""
    return best_trg


class CekUnitRequest(BaseModel):
    part_number: str
    rangka: str


@router.post("/cek-unit", dependencies=[Depends(limit("cek_unit", 6, 60))])
def cek_part_di_unit(body: CekUnitRequest, _user: dict = Depends(get_current_user)):
    from ..services import ai_export, catalog_bom, epc_bom

    pn = (body.part_number or "").strip().upper()
    rangka = (body.rangka or "").strip().upper()
    if not pn or len(rangka) < 6:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Isi Part Number dan nomor rangka/VIN unit (min. 6 karakter).")

    # SATU panggilan pencarian terbalik EPC (home/reverse/part) memvonis cocok/tidak —
    # menemukan part yang tersembunyi DI DALAM assembly maupun part Loading List
    # (terverifikasi live di SJ346500). Menggantikan alur lama yang berat: fetch
    # Loading List + sisir 10 kategori Atlas satu-satu (bisa 1-2 menit).
    rev = epc_bom.reverse_find_in_unit(rangka, pn)
    err = rev.get("_err")
    if err in ("token_expired", "no_token"):
        return {"checked": False,
                "error": "Koneksi ke EPC sedang bermasalah (token). Coba lagi nanti atau tanya asisten."}
    if err == "network":
        return {"checked": False, "error": "Gagal menghubungi server EPC. Coba lagi."}
    if err in ("not_found", "input"):
        return {"checked": False,
                "error": "Nomor rangka tidak ditemukan di EPC — pastikan benar (hanya unit "
                         "Sinotruk/HOWO/SITRAK)."}
    frame = rev.get("frame_number") or rangka

    if not rev.get("found"):
        return {
            "checked": True, "cocok": False, "frame_number": frame, "part_number": pn,
            "pesan": (f"Part {pn} tidak ditemukan di unit {frame} menurut pencarian "
                      "menyeluruh EPC (seluruh katalog unit ini). Bisa jadi bukan untuk "
                      "unit ini (posisi depan/belakang pun bisa beda part) — tanyakan ke "
                      "Asisten AI atau chat gudang sebelum membeli."),
        }

    instances = rev.get("instances") or []
    induk = instances[0]
    norm = catalog_bom._norm
    nama_en = (part_index.name_for(pn) or "").strip()
    istilah = _istilah_lapangan(nama_en)

    # Kategori part (peta katalog) → nama Indonesia + istilah walk utk GAMBAR.
    kode = ""
    try:
        kode = (catalog_bom.pn_category_map().get(norm(pn)) or {}).get("kategori") or ""
    except Exception:
        pass
    kategori_nama = catalog_bom.KATEGORI_NAMA.get(kode, "") if kode else ""

    # Gambar exploded view + qty + balon — LANGSUNG dari alamat node hasil reverse
    # (satu panggilan tree/item per posisi, pola HAR pemilik). Tanpa tebakan
    # kategori: dulu poros transmisi gagal bergambar karena walk 'sasis' tak
    # menyaringnya. Best-effort: gagal semua → hasil cocok tetap dikirim.
    image_id = lokasi = None
    balon = None
    qty = ""
    for inst in instances[:3]:
        try:
            f = epc_bom.figure_for_instance(rangka, inst, pn)
        except Exception:
            continue
        if f.get("found"):
            balon, lokasi, qty = f.get("balon"), f.get("nama"), f.get("qty") or ""
            if not nama_en:
                nama_en = (f.get("nama_item") or "").strip()
                istilah = _istilah_lapangan(nama_en)
            try:
                image_id, _fn = ai_export.stash_builder(
                    f"Exploded {pn}", {"kind": "exploded", "svg": f["svg"], "balon": balon},
                    ext="png")
            except Exception:
                image_id = None
            break

    # Lokasi: figure bila ada; kalau tidak, assembly induk dari reverse
    # ("bagian dari 'Brake shoe assembly' (AZ450045001161)").
    if not lokasi and induk.get("parent_nama"):
        lokasi = induk["parent_nama"]
    n_lok = len({i.get("parent_pn") for i in instances if i.get("parent_pn")})
    induk_txt = ""
    if induk.get("parent_nama"):
        induk_txt = (f", bagian dari assembly '{induk['parent_nama']}'"
                     + (f" (PN {induk['parent_pn']})" if induk.get("parent_pn") else "")
                     + (f" — ditemukan di {n_lok} posisi" if n_lok > 1 else ""))

    bag = f" — {kategori_nama}" if kategori_nama else ""
    fig_txt = (f", pada figure '{lokasi}'" + (f" (nomor balon {balon})" if balon else "")
               ) if lokasi and balon is not None else ""
    ist = f" atau {istilah}" if istilah else ""
    penjelasan = (f"✅ Cocok — part ini terpasang di unit {frame}. "
                  f"Ini adalah {nama_en or pn}{ist}{bag}{induk_txt}{fig_txt}."
                  + (f" Qty di unit: {qty}." if qty else ""))
    return {
        "checked": True, "cocok": True, "frame_number": frame, "part_number": pn,
        "sumber": "epc_reverse", "nama": nama_en, "istilah_lapangan": istilah or None,
        "qty": qty, "kategori": kategori_nama or None, "lokasi": lokasi, "balon": balon,
        "assembly_induk": induk.get("parent_nama") or None,
        "assembly_induk_pn": induk.get("parent_pn") or None,
        "jumlah_posisi": n_lok,
        "image_id": image_id, "penjelasan": penjelasan,
    }


@router.get("/exploded/{export_id}")
def part_exploded_png(export_id: str, _user: dict = Depends(get_current_user)):
    """PNG exploded view untuk fitur cek-unit — SENGAJA terpisah dari
    /api/ai/excel/{id} yang digembok izin menu Asisten AI: pembeli yang asistennya
    dimatikan tetap berhak melihat gambar kecocokan part di unitnya."""
    from ..services import ai_export

    data, fname = ai_export.generic_excel(export_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, fname)
    return Response(content=data, media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{fname}"',
                             "Cache-Control": "private, max-age=86400"})
