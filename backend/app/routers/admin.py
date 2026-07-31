"""Router Admin: kontrol akses Menu, Kolom, Sub-tab Harga per user."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from ..core.config import get_settings
from ..core.security import hash_password
from ..deps import require_admin
from ..services import accurate, ai_chat_log, ai_sinonim_learn, app_config, catalog_bom, customer_map, gudang, gudang_config, harga, image_search, login_history, orders, part_index, pengetahuan, pengetahuan_extract, pengetahuan_index, permissions, populasi, presence, rak, reservations, search_log, session_policy, sinonim
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
    # Gudang yang boleh DITULIS akun ini di Rak & Kartu Stok (label penuh).
    # None = tak diubah; [] = dicabut. Sengaja list (bukan string) supaya UI
    # multi-centang tak perlu tahu format simpan koma-spasi.
    gudang_kelola: list[str] | None = None


class UpdateUserRequest(BaseModel):
    role: str | None = None
    password: str | None = None
    is_active: bool | None = None
    gudang_kelola: list[str] | None = None


def _validasi_gudang_kelola(labels: list[str]) -> str | None:
    """Label harus ada di daftar gudang yang DIKENAL (indeks Accurate ∪ config).

    Tanpa ini, satu salah ketik ('01.Jakartaa') memberi hak tulis ke gudang yang
    tak pernah muncul di mana pun — pemiliknya mengira sudah dapat akses, padahal
    tiap penyimpanan tetap 403. Return bentuk simpan koma-spasi (None bila kosong)."""
    bersih = rak.parse_kelola(", ".join(str(x) for x in (labels or [])))
    if bersih:
        dikenal = set(part_index.gudang_names()) | set(gudang_config.coords_map())
        # Set kosong = indeks & config belum siap → jangan menolak (admin tetap
        # bisa menugaskan saat cold start; nilai yang salah tetap tak berefek).
        asing = [lb for lb in bersih if lb not in dikenal] if dikenal else []
        if asing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Gudang tidak dikenal: {', '.join(asing)}")
    return rak.format_kelola(bersih)


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
    permissions.invalidate_cache()      # centang kolom/menu berlaku seketika
    session_policy.invalidate_cache()   # centang 'sesi' berlaku seketika
    return {"ok": True}


@router.delete("/perms/{kind}/{username}")
def perms_reset(kind: str, username: str, _admin: dict = Depends(require_admin)):
    _check_kind(kind)
    permissions.reset_perm(kind, username.strip().lower())
    permissions.invalidate_cache()
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
    simpan_kelola = _validasi_gudang_kelola(body.gudang_kelola or []) if body.gudang_kelola is not None else None
    ok, msg = sb.create_user(uname, pw_hash, body.role)
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)
    if body.gudang_kelola is not None:
        # Ditulis TERPISAH setelah akun jadi: create_user memakai INSERT dengan
        # kolom tetap, dan kolom baru yang belum dimigrasi akan menggagalkan
        # SELURUH pembuatan akun. Kalau langkah ini gagal, akunnya tetap ada —
        # katakan apa adanya supaya admin tak mengira hak tulisnya tersimpan.
        ok2, msg2 = sb.update_user(uname, {"gudang_kelola": simpan_kelola})
        rak.invalidate(uname)
        if not ok2:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Akun dibuat, tapi gudang kelola gagal disimpan: {msg2}")
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
    if body.gudang_kelola is not None:
        data["gudang_kelola"] = _validasi_gudang_kelola(body.gudang_kelola)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada perubahan.")
    ok, msg = sb.update_user(username, data)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, msg)
    if body.gudang_kelola is not None:
        # Cache 60 dtk di services/rak → tanpa ini pencabutan hak baru berlaku
        # semenit kemudian, tepat saat admin sedang memeriksa hasilnya.
        rak.invalidate(username.strip().lower())
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


# ── Gap Pengetahuan (miner ai_belajar — pertanyaan gagal berulang) ───────────
@router.get("/ai-belajar/gap")
def ai_belajar_gap(_admin: dict = Depends(require_admin)):
    """Kelompok pertanyaan yang berulang GAGAL dijawab asisten (outcome != ok,
    ≥3 kali) — daftar prioritas topik untuk admin menulis Pengetahuan AI.
    Diisi otomatis oleh miner harian ai_belajar (baca-saja)."""
    from ..services import ai_belajar
    rows = ai_belajar.gaps()
    return {"jumlah": len(rows), "gap": rows}


class ResolveGapRequest(BaseModel):
    topik: str


@router.post("/ai-belajar/gap/resolve")
def ai_belajar_gap_resolve(body: ResolveGapRequest,
                           _admin: dict = Depends(require_admin)):
    """Tandai satu topik gap selesai ditangani → hapus dari daftar."""
    from ..services import ai_belajar
    return {"ok": ai_belajar.resolve_gap(body.topik)}


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


# ── Pengetahuan Asisten AI (data/ai_pengetahuan) ────────────────────────────
# Admin menulis/mengunggah pengetahuan internal; job latar mengindeksnya jadi
# chunk yang dicari tool `cari_pengetahuan`. Berkas asli disimpan agar re-index
# tak perlu unggah ulang.
_MAX_PENGETAHUAN_BYTES = 20 * 1024 * 1024     # per berkas
_MAX_PENGETAHUAN_TOTAL = 60 * 1024 * 1024     # seluruh unggahan satu dokumen
_MAX_PENGETAHUAN_FILE = 10
# Magic byte per ekstensi — tolak berkas yang isinya tak sesuai namanya.
_MAGIC = {
    ".pdf": (b"%PDF-",),
    ".xlsx": (b"PK\x03\x04",), ".xlsm": (b"PK\x03\x04",), ".docx": (b"PK\x03\x04",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
}


def _cek_berkas(nama: str, data: bytes) -> str:
    """Validasi ekstensi + magic byte. Return ekstensi ternormalisasi."""
    ekst = Path(nama or "").suffix.lower()
    if ekst not in pengetahuan_extract.EKSTENSI:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Format '{ekst or nama}' tidak didukung. Yang bisa: "
            "PDF, Excel (.xlsx/.xlsm), Word (.docx), CSV, TXT, PNG/JPG.")
    sig = _MAGIC.get(ekst)
    if sig and not any(data.startswith(s) for s in sig):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Isi berkas '{nama}' tidak cocok dengan ekstensinya — mungkin rusak "
            "atau salah rename.")
    return ekst


class PengetahuanPatch(BaseModel):
    judul: str | None = None
    deskripsi: str | None = None
    tag: list[str] | None = None
    untuk_pembeli: bool | None = None
    aktif: bool | None = None
    pakai_ai: bool | None = None


class PengetahuanChunkPatch(BaseModel):
    judul_id: str | None = None
    kata_kunci: list[str] | None = None
    dicari: bool | None = None


class PengetahuanCari(BaseModel):
    q: str


@router.get("/pengetahuan")
def pengetahuan_list(_admin: dict = Depends(require_admin)):
    dok = pengetahuan.load_dokumen()
    # `perlu_reindex` = masih berskema lama (belum punya gambar embedded,
    # breadcrumb, metadata kolom). Re-index TIDAK dipaksa — admin memutuskan.
    # load_dokumen() membaca ulang dari disk (tanpa cache), jadi menambah field
    # di sini tak mengotori store.
    for d in dok:
        d["perlu_reindex"] = pengetahuan.perlu_reindex(d.get("id") or "")
    return {"jumlah": len(dok), "jumlah_chunk": pengetahuan.count(), "dokumen": dok}


@router.get("/pengetahuan/{dok_id}")
def pengetahuan_detail(dok_id: str, _admin: dict = Depends(require_admin)):
    d = pengetahuan.get_dokumen(dok_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dokumen tidak ditemukan.")
    return {"dokumen": d, "chunk": pengetahuan.chunks_dokumen(dok_id)}


@router.get("/pengetahuan/{dok_id}/status")
def pengetahuan_status(dok_id: str, _admin: dict = Depends(require_admin)):
    d = pengetahuan.get_dokumen(dok_id)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dokumen tidak ditemukan.")
    return {k: d.get(k) for k in
            ("id", "status", "progres", "jumlah_chunk", "pengayaan", "error")}


@router.post("/pengetahuan", status_code=status.HTTP_202_ACCEPTED)
async def pengetahuan_add(
    judul: str = Form(...),
    deskripsi: str = Form(""),
    teks: str = Form(""),
    tabel_json: str = Form(""),
    tag: str = Form(""),
    untuk_pembeli: bool = Form(False),
    pakai_ai: bool = Form(True),
    files: list[UploadFile] = File(None),
    admin: dict = Depends(require_admin),
):
    """Simpan dokumen + berkasnya, lalu antre indexing di latar (202)."""
    berkas_in = [f for f in (files or []) if f and f.filename]
    if len(berkas_in) > _MAX_PENGETAHUAN_FILE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Maksimum {_MAX_PENGETAHUAN_FILE} berkas per pengetahuan.")
    tabel = []
    if tabel_json.strip():
        try:
            tabel = json.loads(tabel_json)
            if not isinstance(tabel, list):
                raise ValueError
        except Exception:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Format tabel tidak valid.")

    # Baca & validasi SEMUA berkas dulu — jangan daftarkan dokumen kalau ada yang
    # ditolak (hindari dokumen setengah jadi di daftar admin).
    muatan: list[tuple[str, str, bytes]] = []
    total = 0
    for f in berkas_in:
        data = await _read_capped(f, _MAX_PENGETAHUAN_BYTES)
        total += len(data)
        if total > _MAX_PENGETAHUAN_TOTAL:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Total unggahan melebihi {_MAX_PENGETAHUAN_TOTAL // (1024 * 1024)} MB.")
        nama = Path(f.filename).name          # buang komponen path dari klien
        muatan.append((nama, _cek_berkas(nama, data), data))

    try:
        # `berkas` sementara (nama+ext) sudah cukup untuk validasi "ada isi";
        # nama_simpan baru bisa dihitung setelah id dokumen terbit.
        dok = pengetahuan.add_dokumen(
            judul, deskripsi, [t for t in tag.split(",") if t.strip()],
            untuk_pembeli, pakai_ai, admin.get("username") or "",
            berkas=[{"nama": n, "ext": e} for n, e, _ in muatan],
            teks_admin=teks, tabel_admin=tabel)
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))

    # Nama simpan DIBANGKITKAN SERVER — nama asli hanya metadata, jadi tak ada
    # jalan bagi input user untuk mempengaruhi path di disk.
    d = pengetahuan.berkas_dir()
    d.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, (nama, ekst, data) in enumerate(muatan):
        simpan = f"{dok['id']}_{i}{ekst}"
        (d / simpan).write_bytes(data)
        meta.append({"nama": nama, "nama_simpan": simpan,
                     "ukuran": len(data), "ext": ekst})
    if meta:
        pengetahuan.update_dokumen(dok["id"], berkas=meta)

    pengetahuan_index.antre(dok["id"])
    return {"ok": True, "id": dok["id"], "status": "antre"}


@router.post("/pengetahuan/{dok_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
def pengetahuan_reindex(dok_id: str, _admin: dict = Depends(require_admin)):
    if not pengetahuan.get_dokumen(dok_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dokumen tidak ditemukan.")
    pengetahuan.set_status(dok_id, "antre",
                           {"langkah": "Menunggu giliran", "kini": 0, "total": 0, "persen": 0})
    pengetahuan_index.antre(dok_id)
    return {"ok": True, "id": dok_id, "status": "antre"}


@router.patch("/pengetahuan/{dok_id}")
def pengetahuan_update(dok_id: str, body: PengetahuanPatch,
                       _admin: dict = Depends(require_admin)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if not upd:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada perubahan.")
    try:
        d = pengetahuan.update_dokumen(dok_id, **upd)
    except KeyError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))
    except ValueError as err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(err))
    return {"ok": True, "dokumen": d}


@router.patch("/pengetahuan/{dok_id}/chunk/{cid}")
def pengetahuan_update_chunk(dok_id: str, cid: str, body: PengetahuanChunkPatch,
                             _admin: dict = Depends(require_admin)):
    try:
        c = pengetahuan.update_chunk(f"{dok_id}#{cid}", body.judul_id,
                                     body.kata_kunci, body.dicari)
    except KeyError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))
    return {"ok": True, "chunk": c}


@router.delete("/pengetahuan/{dok_id}")
def pengetahuan_delete(dok_id: str, _admin: dict = Depends(require_admin)):
    try:
        d = pengetahuan.delete_dokumen(dok_id)
    except KeyError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(err))
    return {"ok": True, "dokumen": d}


@router.post("/pengetahuan/cari")
def pengetahuan_cari(body: PengetahuanCari, _admin: dict = Depends(require_admin)):
    """Uji pencarian PERSIS seperti yang dilihat asisten — tanpa ini admin buta
    soal kualitas kurasi kata kuncinya."""
    hasil = pengetahuan.search(body.q, limit=8)
    return {"jumlah": len(hasil), "hasil": hasil}


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


# ── Config aplikasi mobile (versi APK + feature-flag, tanpa rebuild) ────────

class AppConfigRequest(BaseModel):
    # Keduanya opsional: panel boleh menyimpan versi saja atau config saja.
    version: dict | None = None
    config: dict | None = None


@router.get("/app-config")
def get_app_config(_admin: dict = Depends(require_admin)):
    """Isi yang sama dengan GET /api/app/meta, tapi untuk panel admin."""
    return app_config.load()


@router.put("/app-config")
def save_app_config(body: AppConfigRequest, _admin: dict = Depends(require_admin)):
    """Simpan versi APK terbaru + feature-flag. Berlaku saat aplikasi dibuka
    berikutnya — tak perlu rebuild/deploy APK."""
    return app_config.save(body.version, body.config)


# ── Tautan akun → pelanggan Accurate (untuk penawaran otomatis) ─────────────

class TautPelangganRequest(BaseModel):
    username: str
    customer_id: int | None = None     # None = lepas tautan
    customer_name: str | None = None
    customer_no: str | None = None


@router.get("/pelanggan-accurate/cari")
def cari_pelanggan_accurate(q: str = Query(..., min_length=1),
                            _admin: dict = Depends(require_admin)):
    """Cari pelanggan di Accurate untuk dipilih admin saat menautkan akun."""
    if not accurate.available():
        return {"configured": False, "customers": []}
    try:
        rows = accurate.search_customers(q, limit=20)
    except accurate.AccurateError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Accurate: {str(e)[:150]}")
    return {"configured": True, "customers": rows}


@router.get("/pelanggan-accurate")
def daftar_taut_pelanggan(_admin: dict = Depends(require_admin)):
    """Daftar akun + pelanggan Accurate yang tertaut."""
    try:
        users = customer_map.daftar()
    except customer_map.KolomBelumAda:
        return {"siap": False, "users": [],
                "error": ("Kolom tautan belum ada di database. Jalankan "
                          "migrations/024_users_accurate_customer.sql di Supabase.")}
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)[:200])
    return {"siap": True, "users": users}


@router.put("/pelanggan-accurate")
def taut_pelanggan(body: TautPelangganRequest, _admin: dict = Depends(require_admin)):
    """Tautkan (atau lepas) akun ke pelanggan Accurate. Dipakai penawaran
    otomatis saat order lunas — memakai customer_id, bukan nama."""
    ok, msg = customer_map.tautkan(body.username, body.customer_id,
                                   body.customer_name or "", body.customer_no or "")
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)
    return {"ok": True}
