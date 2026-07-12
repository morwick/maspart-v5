"""Router Admin: kontrol akses Menu, Kolom, Sub-tab Harga per user."""
from __future__ import annotations

import io
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.security import hash_password
from ..deps import require_admin
from ..services import ai_chat_log, ai_sinonim_learn, catalog_bom, gudang, gudang_config, harga, image_search, login_history, orders, part_index, permissions, populasi, presence, reservations, search_log, session_policy, sinonim
from ..services import supabase_client as sb
from ..services.supabase_client import upload_storage_object

router = APIRouter(prefix="/api/admin", tags=["admin"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# kind dataset → nama file di Storage bucket "data"
_DATASETS = {"stok": "stok.xlsx", "harga": "harga.xlsx", "populasi": "populasi.xlsx"}

# Plafon ukuran file Excel. .xlsx = arsip ZIP: file kecil bisa mengembang jadi
# ratusan MB saat di-parse (pandas butuh ~5-20x ukuran file). Server hanya punya
# 3,8 GB RAM, jadi baca file BERTAHAP dan tolak begitu melewati plafon — jangan
# `await file.read()` polos yang menelan seluruh isi lebih dulu.
_MAX_XLSX_BYTES = 25 * 1024 * 1024   # 25 MB (stok.xlsx nyata ~2 MB)
_XLSX_CHUNK = 1024 * 1024


async def _read_capped(file: UploadFile, limit: int = _MAX_XLSX_BYTES) -> bytes:
    """Baca UploadFile per-potongan, batalkan bila melewati `limit`.
    Raise HTTPException 413 — pemanggil di loop multi-file boleh menangkapnya."""
    buf = bytearray()
    while chunk := await file.read(_XLSX_CHUNK):
        buf.extend(chunk)
        if len(buf) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File terlalu besar (maksimum {limit // (1024 * 1024)} MB).",
            )
    return bytes(buf)


class SetPermRequest(BaseModel):
    username: str
    keys: list[str]


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class UpdateUserRequest(BaseModel):
    role: str | None = None
    password: str | None = None
    is_active: bool | None = None


def _check_kind(kind: str):
    if not permissions.is_valid_kind(kind):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"kind '{kind}' tidak dikenal")


@router.get("/perms/{kind}")
def perms_overview(kind: str, _admin: dict = Depends(require_admin)):
    _check_kind(kind)
    return permissions.overview(kind)


@router.put("/perms/{kind}")
def perms_set(kind: str, body: SetPermRequest, _admin: dict = Depends(require_admin)):
    _check_kind(kind)
    if not body.username.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "username kosong")
    ok = permissions.set_perm(kind, body.username.strip().lower(), body.keys)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal menyimpan ke Supabase")
    session_policy.invalidate_cache()   # centang 'sesi' berlaku seketika
    return {"ok": True}


@router.delete("/perms/{kind}/{username}")
def perms_reset(kind: str, username: str, _admin: dict = Depends(require_admin)):
    _check_kind(kind)
    permissions.reset_perm(kind, username.strip().lower())
    session_policy.invalidate_cache()
    return {"ok": True}


# ── Upload Data (stok/harga/populasi → Supabase Storage) ─────────────
@router.post("/upload/{kind}")
async def upload_data(
    kind: str,
    file: UploadFile = File(...),
    _admin: dict = Depends(require_admin),
):
    if kind not in _DATASETS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"dataset '{kind}' tidak dikenal")
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xls", ".xlsm")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File harus Excel (.xlsx/.xls/.xlsm).")
    data = await _read_capped(file)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    # Validasi ringan: pastikan bisa dibaca sebagai Excel.
    try:
        import pandas as pd
        pd.read_excel(io.BytesIO(data), nrows=1)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File bukan Excel yang valid.")

    ok, msg = upload_storage_object(_DATASETS[kind], data, _XLSX_MIME)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upload gagal: {msg}")

    # Refresh index/lookup terkait supaya data baru langsung dipakai.
    try:
        if kind in ("stok", "harga"):
            part_index.refresh_index()
            harga.refresh()
            # ⛔ JANGAN reservations.clear_all() di sini lagi. Dulu stok.xlsx = sumber
            # stok, jadi upload = snapshot baru & reservasi lama di-reset. Sejak stok
            # HANYA dari indeks Accurate (aturan pemilik 2026-07-12), upload stok.xlsx
            # tak mengubah stok apa pun — clear_all hanya akan MENGHAPUS tahanan stok
            # order aktif (termasuk yang sudah DIBAYAR) dan membuka pintu oversell.
        elif kind == "populasi":
            populasi.refresh()
    except Exception as e:
        return {"ok": True, "kind": kind, "size": len(data), "refresh_warning": str(e)}

    return {"ok": True, "kind": kind, "size": len(data)}


# ── Upload KATALOG part (Excel per unit/model → folder /data lokal) ───
# Catatan: katalog dibaca dari folder data lokal (bind-mount), beda dari
# stok/harga/populasi yang ke Supabase Storage. Folder data harus writable
# (mount :rw di Coolify compose).
def _catalog_base() -> Path:
    return get_settings().data_path.resolve()


def _safe_catalog_dir(subdir: str) -> Path:
    """Resolve subdir di dalam DATA_DIR dengan aman (cegah path traversal &
    folder non-katalog)."""
    base = _catalog_base()
    raw = (subdir or "").strip().replace("\\", "/").strip("/")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Folder tujuan kosong.")
    if any(p == ".." for p in parts):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Folder tidak valid.")
    if parts[0].lower() in part_index._NON_PART_DIRS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Folder '{parts[0]}' bukan area katalog (itu untuk stok/harga/populasi).",
        )
    target = (base / Path(*parts)).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Folder di luar area data.")
    return target


@router.get("/catalog/folders")
def catalog_folders(_admin: dict = Depends(require_admin)):
    """Daftar folder katalog yang sudah ada (untuk pilihan tujuan upload)."""
    base = _catalog_base()
    folders: list[str] = []
    if base.exists():
        for root, dirs, _files in os.walk(base):
            rel = Path(root).relative_to(base)
            top = rel.parts[0].lower() if rel.parts else ""
            if top in part_index._NON_PART_DIRS:
                dirs[:] = []
                continue
            if rel.parts:
                folders.append(str(rel).replace("\\", "/"))
    folders.sort(key=str.lower)
    return {"folders": folders}


@router.post("/upload-catalog")
async def upload_catalog(
    subdir: str = Form(..., description="Folder tujuan di dalam /data, mis. 'Sinotruk/NX380HP'"),
    files: list[UploadFile] = File(..., description="Satu atau beberapa file Excel katalog."),
    _admin: dict = Depends(require_admin),
):
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada file diunggah.")

    target_dir = _safe_catalog_dir(subdir)  # validasi folder tujuan sekali

    saved: list[dict] = []
    errors: list[dict] = []
    for file in files:
        name = (file.filename or "").strip()
        safe_name = Path(name).name  # buang komponen path dari nama file
        if not name.lower().endswith((".xlsx", ".xls", ".xlsm")):
            errors.append({"file": name or "(tanpa nama)", "error": "Bukan Excel (.xlsx/.xls/.xlsm)."})
            continue
        if not safe_name:
            errors.append({"file": name, "error": "Nama file tidak valid."})
            continue
        try:
            data = await _read_capped(file)
        except HTTPException as e:
            errors.append({"file": safe_name, "error": str(e.detail)})
            continue
        if not data:
            errors.append({"file": safe_name, "error": "File kosong."})
            continue
        try:
            import pandas as pd
            pd.read_excel(io.BytesIO(data), nrows=1)
        except Exception:
            errors.append({"file": safe_name, "error": "Bukan Excel yang valid."})
            continue
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / safe_name).write_bytes(data)
        except OSError as e:
            errors.append({"file": safe_name, "error": f"Gagal simpan (folder writable?): {e}"})
            continue
        rel = str((target_dir / safe_name).relative_to(_catalog_base())).replace("\\", "/")
        saved.append({"path": rel, "size": len(data)})

    if not saved:
        detail = "Tidak ada file yang berhasil diunggah."
        if errors:
            detail += " " + "; ".join(f"{e['file']}: {e['error']}" for e in errors)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)

    # Bangun ulang index katalog SEKALI (setelah semua file tersimpan).
    out: dict = {"ok": True, "saved": saved, "count": len(saved), "errors": errors}
    try:
        part_index.refresh_index()
    except Exception as e:
        out["refresh_warning"] = str(e)
    return out


# ── Manajemen User ───────────────────────────────────────────────────
@router.get("/users")
def list_users(_admin: dict = Depends(require_admin)):
    return {"users": sb.list_users_full()}


# Ambang "kemungkinan dipakai ramai" dalam 30 hari terakhir. Sengaja longgar:
# IP berubah sendiri saat pindah WiFi/kuota, jadi 2-3 IP itu normal untuk 1 orang.
_SHARE_IP_MIN = 4
_SHARE_DEVICE_MIN = 3
_SHARE_DAYS = 30


@router.get("/monitoring")
def monitoring(_admin: dict = Depends(require_admin)):
    """Panel Monitoring: status ONLINE/OFFLINE, IP & perangkat terakhir, plus
    indikasi akun dipakai ramai-ramai.

    Online = ada request terautentikasi dalam `presence.ONLINE_WINDOW_SEC` (5 mnt)
    terakhir (dilacak in-memory di `services/presence`, di-update tiap request &
    saat login). Roster user diambil dari Supabase; kolom DB last_login/last_active
    dipakai sebagai fallback bila presence belum punya data (mis. setelah restart).

    IP/perangkat: presence (in-memory, hilang saat restart) → fallback riwayat
    permanen `login_history`. Bila tabel login_history belum dibuat, ringkasannya
    kosong dan panel tetap jalan (hanya tanpa kolom sharing)."""
    users = sb.list_users_full()
    share = login_history.sharing_summary(_SHARE_DAYS)   # {} bila belum ada login
    riwayat_siap = login_history.table_ready()           # tabel ADA (walau 0 baris)
    out_users: list[dict] = []
    online_count = 0
    for u in users:
        uname = (u.get("username") or "").strip()
        if not uname:
            continue
        p = presence.get(uname)
        s = share.get(uname.lower(), {})
        if p["online"]:
            online_count += 1
        ip_n = s.get("ip_count", 0)
        dev_n = s.get("device_count", 0)
        out_users.append({
            "username": uname,
            "role": u.get("role") or "user",
            "online": p["online"],
            "is_active": bool(u.get("is_active", True)),
            "last_login_at": p["last_login_at"] or u.get("last_login_at"),
            "last_active_at": p["last_active_at"] or u.get("last_active_at"),
            # Presence hilang saat restart → jatuh ke riwayat permanen.
            "last_ip": p["last_ip"] or s.get("last_ip"),
            "last_device": p["last_device"] or s.get("last_device"),
            "ip_count": ip_n,
            "device_count": dev_n,
            "login_count": s.get("login_count", 0),
            "ips": s.get("ips", [])[:8],
            "devices": s.get("devices", [])[:8],
            # SINYAL, bukan vonis: IP bisa berubah sendiri, kantor berbagi 1 IP.
            "kemungkinan_dipakai_ramai": ip_n >= _SHARE_IP_MIN or dev_n >= _SHARE_DEVICE_MIN,
        })
    # Yang mencurigakan dulu, lalu online, lalu alfabet.
    out_users.sort(key=lambda x: (not x["kemungkinan_dipakai_ramai"], not x["online"], x["username"]))
    # Aktivitas terbaru dari presence (login); fallback ke DB user_activity.
    activity = presence.recent(50) or sb.fetch_recent_activity(50)
    return {
        "online_count": online_count,
        "total_users": len(out_users),
        "online_window_minutes": presence.ONLINE_WINDOW_SEC // 60,
        "share_days": _SHARE_DAYS,
        "share_ip_min": _SHARE_IP_MIN,
        "share_device_min": _SHARE_DEVICE_MIN,
        # Tabel ADA (walau masih 0 baris) — bedakan dari "tabel belum dibuat",
        # supaya panduan DDL tak muncul terus di hari pertama.
        "riwayat_tersedia": riwayat_siap,
        "users": out_users,
        "recent_activity": activity,
    }


@router.get("/monitoring/login-history")
def monitoring_login_history(
    username: str = "",
    limit: int = 200,
    _admin: dict = Depends(require_admin),
):
    """Riwayat login mentah (kapan, siapa, IP, perangkat) — untuk menelusuri akun
    yang ditandai 'kemungkinan dipakai ramai'. Kosong bila tabel belum dibuat."""
    rows = login_history.list_logins(username=username, limit=limit)
    return {"jumlah": len(rows), "riwayat": rows, "tabel_siap": login_history.table_ready()}


@router.get("/monitoring/sql")
def monitoring_sql(_admin: dict = Depends(require_admin)):
    """DDL tabel login_history — ditampilkan admin bila tabel belum dibuat."""
    return {"sql": login_history.create_table_sql()}


@router.post("/users")
def create_user(body: CreateUserRequest, _admin: dict = Depends(require_admin)):
    uname = body.username.strip().lower()
    if not uname or not body.password.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Username & password wajib diisi.")
    if body.role not in ("admin", "user", "pembeli"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Role harus 'admin', 'user', atau 'pembeli'.")
    pw_hash = hash_password(body.password)
    if not pw_hash:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Gagal hash password.")
    ok, msg = sb.create_user(uname, pw_hash, body.role)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)
    return {"ok": True}


@router.put("/users/{username}")
def update_user(username: str, body: UpdateUserRequest, _admin: dict = Depends(require_admin)):
    data: dict = {}
    if body.role is not None:
        if body.role not in ("admin", "user", "pembeli"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Role tidak valid.")
        data["role"] = body.role
    if body.is_active is not None:
        data["is_active"] = body.is_active
    if body.password:
        pw_hash = hash_password(body.password)
        if not pw_hash:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Gagal hash password.")
        data["password_hash"] = pw_hash
        data["password"] = None  # hapus plaintext legacy
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada perubahan.")
    ok, msg = sb.update_user(username, data)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, msg)
    return {"ok": True}


# ── Laporan Penjualan (khusus admin) ────────────────────────────────
@router.get("/sales")
def sales_report(_admin: dict = Depends(require_admin)):
    return orders.sales_recap()


# ── Lokasi Gudang (koordinat + lokasi yang bisa dipilih pembeli) ─────
class GudangItem(BaseModel):
    label: str
    lat: float | None = None
    lon: float | None = None
    selectable: bool = False
    key: str | None = None       # key/akun cabang bila bisa dipilih pembeli
    pic: str | None = None       # nomor PIC/kontak gudang
    origin_postal: str | None = None  # kode pos ASAL ongkir (auto-isi dari koordinat di UI)
    can_ship: bool = True        # boleh jadi gudang PENGIRIM pesanan online?


class SaveGudangRequest(BaseModel):
    items: list[GudangItem]


@router.get("/gudang")
def get_gudang(_admin: dict = Depends(require_admin)):
    """Daftar gudang (dari index stok ∪ config) + koordinat + status pembeli +
    urutan gudang terdekat (terhitung otomatis dari koordinat)."""
    coords = gudang_config.coords_map()
    buyer = gudang_config.buyer_locations()
    pic = gudang_config.pic_map()
    postals = gudang_config.postal_map()
    no_ship = gudang_config.no_ship_labels()
    # label → (key, origin_postal) untuk lokasi yang bisa dipilih pembeli
    by_label = {v["label"]: (k, v.get("origin_postal", "")) for k, v in buyer.items()}

    labels = sorted(set(part_index.gudang_names()) | set(coords) | set(by_label) | set(pic))
    items = []
    for lb in labels:
        c = coords.get(lb)
        key, legacy_postal = by_label.get(lb, (None, ""))
        # Kode pos SEMUA gudang (map postal); config lama → nilai di entri pembeli.
        postal = postals.get(lb) or legacy_postal
        items.append({
            "label": lb,
            "display": gudang.gudang_label(lb),
            "lat": c[0] if c else None,
            "lon": c[1] if c else None,
            "selectable": lb in by_label,
            "key": key,
            "origin_postal": postal,
            "can_ship": lb not in no_ship,
            "pic": pic.get(lb, ""),
            "nearest": [gudang.gudang_label(g) for g in gudang.fallback_order(lb, labels)[:5]],
        })
    return {"gudang": items}


@router.put("/gudang")
def save_gudang(body: SaveGudangRequest, _admin: dict = Depends(require_admin)):
    # Kode pos lama per label (fallback bila UI tidak mengirim nilainya).
    prev_postal = dict(gudang_config.postal_map())
    for v in gudang_config.buyer_locations().values():
        prev_postal.setdefault(v["label"], v.get("origin_postal", ""))

    coords: dict = {}
    buyer: dict = {}
    pic: dict = {}
    postal: dict = {}
    no_ship: list[str] = []
    seen_keys: set[str] = set()
    for it in body.items:
        label = (it.label or "").strip()
        if not label:
            continue
        if it.lat is not None and it.lon is not None:
            coords[label] = [float(it.lat), float(it.lon)]
        if (it.pic or "").strip():
            pic[label] = it.pic.strip()
        # Gudang yang TIDAK boleh mengirim pesanan online (mis. gudang internal B80).
        # Yang disimpan hanya yang dimatikan → gudang baru otomatis boleh mengirim.
        if not it.can_ship:
            no_ship.append(label)
        # Kode pos ASAL ongkir disimpan untuk SETIAP gudang — bukan hanya lokasi
        # pilihan pembeli — karena gudang PEMENUH (fallback terdekat) sering gudang
        # lain. None (UI lama) → pertahankan nilai lama; selain itu digit saja.
        if it.origin_postal is None:
            code = prev_postal.get(label, "")
        else:
            code = "".join(ch for ch in it.origin_postal if ch.isdigit())[:10]
        if code:
            postal[label] = code
        if it.selectable:
            key = (it.key or "").strip().lower()
            if not key:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{label}' ditandai bisa dipilih pembeli tapi key/akun cabang kosong.")
            if key in seen_keys:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Key '{key}' dipakai lebih dari satu gudang.")
            seen_keys.add(key)
            buyer[key] = {"label": label, "origin_postal": code}

    ok, msg = gudang_config.save(coords, buyer, pic, postal, no_ship)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal simpan: {msg}")
    return {"ok": True}


# ── Foto Part (kelola part_photos) ───────────────────────────────────
_IMG_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


@router.get("/photos")
def list_photos(pn: str, _admin: dict = Depends(require_admin)):
    return {"part_number": pn.strip().upper(), "photos": sb.fetch_part_photos_full(pn)}


@router.post("/photos")
async def upload_photo(
    pn: str = Form(...),
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    pn_clean = pn.strip().upper()
    if not pn_clean:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Part Number wajib.")
    fname = (file.filename or "foto").strip()
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in _IMG_MIME:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hanya jpg/jpeg/png/webp.")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Maksimum 10 MB.")

    pn_safe = pn_clean.replace("/", "_").replace(" ", "_")
    storage_path = f"{pn_safe}/{fname}"
    ok, msg = upload_storage_object(storage_path, data, _IMG_MIME[ext], bucket=sb.PHOTO_BUCKET)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upload gagal: {msg}")

    saved = sb.insert_part_photo({
        "part_number": pn_clean,
        "file_name": fname,
        "storage_path": storage_path,
        "storage_url": sb.photo_public_url(storage_path),
        "file_size": len(data),
        "uploaded_by": admin.get("username", ""),
    })
    if not saved:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal simpan metadata foto.")
    return {"ok": True, "url": sb.photo_public_url(storage_path)}


@router.delete("/photos/{photo_id}")
def delete_photo(photo_id: str, _admin: dict = Depends(require_admin)):
    row = sb.get_part_photo(photo_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Foto tidak ditemukan.")
    sb.delete_storage_object(sb.PHOTO_BUCKET, row.get("storage_path", ""))
    if not sb.delete_part_photo(photo_id):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal hapus metadata.")
    return {"ok": True}


# ── Image Index (embedding SIMS → part_image_index) ──────────────────
class IndexRequest(BaseModel):
    pn: str
    reindex: bool = False


class BulkIndexRequest(BaseModel):
    part_numbers: list[str] = []
    text: str = ""
    reindex: bool = False


@router.get("/index/status")
def index_status(_admin: dict = Depends(require_admin)):
    return {
        "torch": image_search.torch_available(),
        "model_ready": image_search.model_ready(),
        "total_indexed": image_search.index_count(),
        "gallery_local": image_search.local_index_available(),
    }


@router.post("/index/reload-gallery")
def index_reload_gallery(_admin: dict = Depends(require_admin)):
    """Muat ulang galeri Cari-by-Foto dari file CSV (setelah CSV diperbarui),
    tanpa perlu restart server."""
    return image_search.reload_local_index()


@router.get("/catalog-bom/status")
def catalog_bom_status(_admin: dict = Depends(require_admin)):
    """Status data Catalog BOM (banding part per kategori & per assy, §3.5.5b)."""
    cats = catalog_bom.categories()
    return {
        "available": catalog_bom.available(),
        "unit": len(catalog_bom.list_units()),
        "kategori": len(cats),
    }


@router.post("/catalog-bom/rebuild")
def catalog_bom_rebuild(_admin: dict = Depends(require_admin)):
    """Bangun ulang data Catalog BOM dari sheet kategori semua file katalog
    (setelah menambah/ubah katalog). In-process, tanpa restart — fitur banding/
    isi kategori langsung pakai data baru. Lihat §3.5.5b."""
    try:
        return catalog_bom.rebuild()
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Gagal rebuild BOM: {e}")


@router.post("/index")
def index_one(body: IndexRequest, admin: dict = Depends(require_admin)):
    if not image_search.torch_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model AI tidak tersedia.")
    if not body.pn.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Part Number wajib.")
    return image_search.index_part(body.pn, indexed_by=admin.get("username", "admin"), reindex=body.reindex)


@router.post("/index/bulk")
def index_bulk(body: BulkIndexRequest, admin: dict = Depends(require_admin)):
    if not image_search.torch_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Model AI tidak tersedia.")
    raw = body.part_numbers or [ln.strip() for ln in body.text.splitlines() if ln.strip()]
    seen, pns = set(), []
    for p in raw:
        u = p.strip().upper()
        if u and u not in seen:
            seen.add(u)
            pns.append(p.strip())
    if not pns:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada Part Number.")
    if len(pns) > 50:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Maksimum 50 PN per batch index.")
    results = [
        image_search.index_part(p, indexed_by=admin.get("username", "admin"), reindex=body.reindex)
        for p in pns
    ]
    total = sum(r["indexed"] for r in results)
    return {"total_indexed": total, "results": results}


@router.delete("/users/{username}")
def delete_user(username: str, admin: dict = Depends(require_admin)):
    if username.strip().lower() == admin["username"].strip().lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak bisa menghapus akun sendiri.")
    ok, msg = sb.delete_user(username)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, msg)
    return {"ok": True}


# ── Pencarian Nihil (umpan sinonim) ──────────────────────────────────────────
@router.get("/search-misses")
def search_misses(limit: int = 300, _admin: dict = Depends(require_admin)):
    """Query pencarian yang 0 hasil, tersering dulu — kandidat istilah lapangan
    yang belum ada di sinonim.json. Untuk halaman admin 'Pencarian Nihil'."""
    rows = search_log.top_misses(max(1, min(limit, 1000)))
    return {"total": search_log.total_count(), "jumlah": len(rows), "misses": rows}


class ResolveMissRequest(BaseModel):
    query: str


@router.post("/search-misses/resolve")
def resolve_search_miss(body: ResolveMissRequest, _admin: dict = Depends(require_admin)):
    """Tandai satu query selesai (mis. sudah ditambahkan ke sinonim.json) → hapus."""
    ok = search_log.resolve_miss(body.query)
    return {"ok": ok}


# ── Kamus Sinonim (istilah lapangan → kata kunci katalog) ────────────────────
# Menulis data/sinonim/sinonim.json; asisten AI memuat ulang otomatis per-mtime,
# jadi entri baru LANGSUNG dimengerti tanpa restart.
class SinonimEntryRequest(BaseModel):
    grup: str = ""
    triggers: list[str]
    keywords: list[str]


@router.get("/sinonim")
def sinonim_list(_admin: dict = Depends(require_admin)):
    entries = sinonim.load()
    return {"jumlah": len(entries), "entries": entries}


@router.post("/sinonim")
def sinonim_add(body: SinonimEntryRequest, _admin: dict = Depends(require_admin)):
    try:
        entry = sinonim.add(body.grup, body.triggers, body.keywords)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))
    return {"ok": True, "entry": entry}


@router.put("/sinonim/{index}")
def sinonim_update(index: int, body: SinonimEntryRequest, _admin: dict = Depends(require_admin)):
    try:
        entry = sinonim.update(index, body.grup, body.triggers, body.keywords)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))
    except IndexError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    return {"ok": True, "entry": entry}


@router.delete("/sinonim/{index}")
def sinonim_delete(index: int, _admin: dict = Depends(require_admin)):
    try:
        entry = sinonim.delete(index)
    except IndexError as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err))
    return {"ok": True, "entry": entry}


# ── Observabilitas Asisten AI (ai_chat_log) ─────────────────────────────────
@router.get("/chat-log")
def chat_log_list(limit: int = 200, _admin: dict = Depends(require_admin)):
    """Log ringkas per giliran chat (terbaru dulu) + ringkasan agregat. Untuk
    halaman admin 'Observabilitas AI'. Kosong bila tabel belum dibuat."""
    return {"ringkasan": ai_chat_log.summary(),
            "log": ai_chat_log.list_logs(max(1, min(limit, 1000)))}


@router.delete("/chat-log")
def chat_log_delete(before_days: int | None = None, _admin: dict = Depends(require_admin)):
    """Hapus log observabilitas AI. `before_days` = hapus yang LEBIH TUA dari N hari;
    tanpa arg (atau <=0) = hapus SEMUA. (Retensi 30-hari juga jalan otomatis di latar.)"""
    ok, n = ai_chat_log.delete_before(before_days) if (before_days and before_days > 0) \
        else ai_chat_log.delete_all()
    if not ok:
        raise HTTPException(status_code=500, detail="Gagal menghapus log (Supabase/tabel).")
    return {"ok": True, "dihapus": n}


# ── Usulan Sinonim OTOMATIS (loop belajar: miss → LLM → validasi → approve) ──
class UsulanGenerateRequest(BaseModel):
    limit: int = 10
    auto_approve: bool = False


class UsulanDecisionRequest(BaseModel):
    id: str
    triggers: list[str] | None = None
    keywords: list[str] | None = None


@router.get("/sinonim/usulan")
def sinonim_usulan_list(status_filter: str | None = "pending",
                        _admin: dict = Depends(require_admin)):
    """Daftar usulan sinonim hasil LLM. status_filter: pending/approved/rejected,
    kosongkan untuk semua."""
    rows = ai_sinonim_learn.list_usulan(status_filter or None)
    return {"jumlah": len(rows), "usulan": rows}


@router.post("/sinonim/usulan/generate")
def sinonim_usulan_generate(body: UsulanGenerateRequest,
                            _admin: dict = Depends(require_admin)):
    """Minta LLM mengusulkan sinonim dari pencarian nihil tersering. Keyword
    divalidasi ke katalog nyata sebelum disimpan. auto_approve=True → usulan
    yang semua keyword-nya valid & confidence tinggi langsung masuk kamus."""
    return ai_sinonim_learn.generate(limit=body.limit, auto_approve=body.auto_approve)


@router.post("/sinonim/usulan/approve")
def sinonim_usulan_approve(body: UsulanDecisionRequest,
                           _admin: dict = Depends(require_admin)):
    """Setujui usulan → masuk sinonim.json (langsung aktif) + miss di-resolve.
    triggers/keywords opsional untuk edit sebelum masuk kamus."""
    try:
        u = ai_sinonim_learn.approve(body.id, body.triggers, body.keywords)
    except KeyError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))
    except RuntimeError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))
    return {"ok": True, "usulan": u}


@router.post("/sinonim/usulan/reject")
def sinonim_usulan_reject(body: UsulanDecisionRequest,
                          _admin: dict = Depends(require_admin)):
    """Tolak usulan (tidak akan diusulkan ulang; miss ikut dianggap selesai)."""
    try:
        u = ai_sinonim_learn.reject(body.id)
    except KeyError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))
    return {"ok": True, "usulan": u}
