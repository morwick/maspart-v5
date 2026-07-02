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
from ..services import catalog_bom, gudang, gudang_config, harga, image_search, orders, part_index, permissions, populasi, presence, reservations
from ..services import supabase_client as sb
from ..services.supabase_client import upload_storage_object

router = APIRouter(prefix="/api/admin", tags=["admin"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# kind dataset → nama file di Storage bucket "data"
_DATASETS = {"stok": "stok.xlsx", "harga": "harga.xlsx", "populasi": "populasi.xlsx"}


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
    return {"ok": True}


@router.delete("/perms/{kind}/{username}")
def perms_reset(kind: str, username: str, _admin: dict = Depends(require_admin)):
    _check_kind(kind)
    permissions.reset_perm(kind, username.strip().lower())
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
    data = await file.read()
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
            if kind == "stok":
                # Stok baru = snapshot terbaru → reset reservasi lama agar tak dobel-kurang.
                reservations.clear_all()
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
        data = await file.read()
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


@router.get("/monitoring")
def monitoring(_admin: dict = Depends(require_admin)):
    """Panel Monitoring: status ONLINE/OFFLINE + aktivitas terakhir tiap user.

    Online = ada request terautentikasi dalam `presence.ONLINE_WINDOW_SEC` (5 mnt)
    terakhir (dilacak in-memory di `services/presence`, di-update tiap request &
    saat login). Roster user diambil dari Supabase; kolom DB last_login/last_active
    dipakai sebagai fallback bila presence belum punya data (mis. setelah restart)."""
    users = sb.list_users_full()
    out_users: list[dict] = []
    online_count = 0
    for u in users:
        uname = (u.get("username") or "").strip()
        if not uname:
            continue
        p = presence.get(uname)
        if p["online"]:
            online_count += 1
        out_users.append({
            "username": uname,
            "role": u.get("role") or "user",
            "online": p["online"],
            "is_active": bool(u.get("is_active", True)),
            "last_login_at": p["last_login_at"] or u.get("last_login_at"),
            "last_active_at": p["last_active_at"] or u.get("last_active_at"),
        })
    # Online dulu, lalu alfabet — yang penting di atas.
    out_users.sort(key=lambda x: (not x["online"], x["username"]))
    # Aktivitas terbaru dari presence (login); fallback ke DB user_activity.
    activity = presence.recent(50) or sb.fetch_recent_activity(50)
    return {
        "online_count": online_count,
        "total_users": len(out_users),
        "online_window_minutes": presence.ONLINE_WINDOW_SEC // 60,
        "users": out_users,
        "recent_activity": activity,
    }


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


class SaveGudangRequest(BaseModel):
    items: list[GudangItem]


@router.get("/gudang")
def get_gudang(_admin: dict = Depends(require_admin)):
    """Daftar gudang (dari index stok ∪ config) + koordinat + status pembeli +
    urutan gudang terdekat (terhitung otomatis dari koordinat)."""
    coords = gudang_config.coords_map()
    buyer = gudang_config.buyer_locations()
    pic = gudang_config.pic_map()
    # label → (key, origin_postal) untuk lokasi yang bisa dipilih pembeli
    by_label = {v["label"]: (k, v.get("origin_postal", "")) for k, v in buyer.items()}

    labels = sorted(set(part_index.gudang_names()) | set(coords) | set(by_label) | set(pic))
    items = []
    for lb in labels:
        c = coords.get(lb)
        key, postal = by_label.get(lb, (None, ""))
        items.append({
            "label": lb,
            "display": gudang.gudang_label(lb),
            "lat": c[0] if c else None,
            "lon": c[1] if c else None,
            "selectable": lb in by_label,
            "key": key,
            "origin_postal": postal,
            "pic": pic.get(lb, ""),
            "nearest": [gudang.gudang_label(g) for g in gudang.fallback_order(lb, labels)[:5]],
        })
    return {"gudang": items}


@router.put("/gudang")
def save_gudang(body: SaveGudangRequest, _admin: dict = Depends(require_admin)):
    prev_buyer = gudang_config.buyer_locations()
    # label → origin_postal lama (dipertahankan; tidak diatur di UI ini)
    postal_by_label = {v["label"]: v.get("origin_postal", "") for v in prev_buyer.values()}

    coords: dict = {}
    buyer: dict = {}
    pic: dict = {}
    seen_keys: set[str] = set()
    for it in body.items:
        label = (it.label or "").strip()
        if not label:
            continue
        if it.lat is not None and it.lon is not None:
            coords[label] = [float(it.lat), float(it.lon)]
        if (it.pic or "").strip():
            pic[label] = it.pic.strip()
        if it.selectable:
            key = (it.key or "").strip().lower()
            if not key:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{label}' ditandai bisa dipilih pembeli tapi key/akun cabang kosong.")
            if key in seen_keys:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Key '{key}' dipakai lebih dari satu gudang.")
            seen_keys.add(key)
            buyer[key] = {"label": label, "origin_postal": postal_by_label.get(label, "")}

    ok, msg = gudang_config.save(coords, buyer, pic)
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
