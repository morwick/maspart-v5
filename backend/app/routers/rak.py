"""
Router Rak & Kartu Stok — di mana barangnya, bukan berapa banyak.

MELIHAT terbuka untuk semua staf internal (siapa pun yang mengambil barang butuh
tahu raknya); MENULIS dipagari `users.gudang_kelola` lewat `rak.boleh_tulis`.
PEMBELI ditolak 403 di SEMUA endpoint — susunan rak gudang adalah data internal
dan tak pernah ada gunanya bagi pembeli.

⚠️ Jebakan URL: Part Number bisa ber-suffix varian dengan garis miring
('WG9525160004/2'). Uvicorn men-decode %2F menjadi '/' SEBELUM route dicocokkan,
jadi PN semacam itu tak akan pernah cocok dengan segmen `{pn}` biasa (404 yang
membingungkan). Tiap endpoint per-part karena itu punya ALIAS dengan PN di
posisi TERAKHIR (`{pn:path}`) — bentuk lama tetap dilayani, klien dengan PN
ber-'/' memakai alias.
"""
from __future__ import annotations

import secrets

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile, status)
from pydantic import BaseModel, Field

from ..deps import get_current_user
from ..services import gudang_config, part_index, rak
from ..services import supabase_client as sb

router = APIRouter(prefix="/api/rak", tags=["rak"])

# Foto kartu stok = FOTO, bukan dokumen: versi _IMG_MIME tanpa pdf (admin.py:489).
# PDF sengaja ditolak — UI menampilkannya sebagai thumbnail + lightbox.
_IMG_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_MAX_FOTO_BYTES = 10 * 1024 * 1024    # 10 MB, samakan dengan bukti transfer (orders.py)
_MAX_IMPORT_BYTES = 10 * 1024 * 1024  # file rak nyata hanya beberapa ratus KB


def require_internal(user: dict = Depends(get_current_user)) -> dict:
    """Staf internal saja. Pembeli ditolak di PINTU — bukan disaring per-field,
    supaya tak ada satu pun jalur yang lupa menyaring."""
    if (user.get("role") or "").lower() == "pembeli":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Data rak gudang hanya untuk staf internal.")
    return user


class RakBody(BaseModel):
    rak: str = Field(max_length=120)
    catatan: str | None = Field(None, max_length=500)


def _gate(user: dict, gudang: str) -> str:
    """Pastikan user boleh MENULIS gudang ini; kembalikan label yang sudah dirapikan."""
    g = (gudang or "").strip()
    if not g:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Gudang kosong.")
    if not rak.boleh_tulis(user, g):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Akun ini tidak mengelola gudang '{g}'. Minta admin menambahkannya "
            "di Manajemen User → Gudang Kelola.",
        )
    return g


def _labels_dikenal() -> set[str]:
    """Semua label gudang yang sah: indeks Accurate ∪ config admin. Dipakai
    menolak label karangan — tanpa ini, salah ketik gudang membuat baris rak
    yang tak akan pernah tampil di mana pun."""
    try:
        return set(part_index.gudang_names()) | set(gudang_config.coords_map())
    except Exception:
        return set()


# ── Baca ─────────────────────────────────────────────────────────────
@router.get("/part/{pn}")
@router.get("/part-of/{pn:path}")
def rak_part(pn: str, _user: dict = Depends(require_internal)):
    """Rak part ini di SEMUA gudang → {label: {rak, catatan, foto_url, ...}}."""
    pn = (pn or "").strip()
    if not pn:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Part Number kosong.")
    return {"part_number": pn.upper(), "rak": rak.get_for_pn(pn)}


@router.get("/gudang/{label}")
def rak_gudang(label: str, q: str = Query("", max_length=80),
               limit: int = Query(300, ge=1, le=2000),
               _user: dict = Depends(require_internal)):
    """Lookup TERBALIK: isi satu gudang (opsional cari PN/rak/catatan)."""
    items = rak.get_for_gudang(label, q, limit)
    return {"gudang": (label or "").strip(), "jumlah": len(items), "items": items}


# ── Tulis ────────────────────────────────────────────────────────────
@router.put("/part/{pn}/{gudang}")
@router.put("/gudang/{gudang}/part/{pn:path}")
def simpan_rak(pn: str, gudang: str, body: RakBody,
               user: dict = Depends(require_internal)):
    g = _gate(user, gudang)
    dikenal = _labels_dikenal()
    # Set kosong = indeks & config belum siap (cold start) → jangan menolak;
    # menghalangi input hanya karena indeks belum hangat lebih merugikan.
    if dikenal and g not in dikenal:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gudang '{g}' tidak dikenal.")
    ok, msg = rak.upsert(pn, g, body.rak, body.catatan or "", user.get("username", ""))
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, msg)
    return {"ok": True, "part_number": (pn or "").strip().upper(), "gudang": g,
            "rak": rak.get_for_pn(pn).get(g, {})}


@router.delete("/part/{pn}/{gudang}")
@router.delete("/gudang/{gudang}/part/{pn:path}")
def hapus_rak(pn: str, gudang: str, user: dict = Depends(require_internal)):
    g = _gate(user, gudang)
    ok, msg = rak.hapus(pn, g)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, msg)
    return {"ok": True}


# ── Foto kartu stok ──────────────────────────────────────────────────
@router.post("/part/{pn}/{gudang}/foto")
@router.post("/foto/{gudang}/part/{pn:path}")
async def unggah_foto(pn: str, gudang: str, file: UploadFile = File(...),
                      user: dict = Depends(require_internal)):
    """Ganti foto kartu stok (hanya yang TERBARU disimpan).

    Nama file dibuat SERVER (token acak): nama asli dari HP/scanner bisa
    mengandung path traversal atau menimpa objek milik pasangan lain."""
    g = _gate(user, gudang)
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if ext not in _IMG_MIME:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Foto harus jpg/jpeg/png/webp.")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    if len(data) > _MAX_FOTO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Ukuran file maksimal 10 MB.")

    pk = rak.pn_key(pn) or "PN"
    g_aman = "".join(ch for ch in g if ch.isalnum() or ch in "-_") or "gudang"
    path = f"rak-kartu/{g_aman}/{pk}-{secrets.token_hex(6)}.{ext}"
    ok, msg = sb.upload_storage_object(path, data, _IMG_MIME[ext], bucket=sb.PHOTO_BUCKET)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upload gagal: {msg}")
    url = sb.photo_public_url(path)
    ok2, msg2 = rak.set_foto(pn, g, url, path, user.get("username", ""))
    if not ok2:
        # Baris rak belum ada / DB menolak → objek yang baru diunggah jangan
        # ditinggal yatim di bucket.
        sb.delete_storage_object(sb.PHOTO_BUCKET, path)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg2)
    return {"ok": True, "foto_url": url}


@router.delete("/part/{pn}/{gudang}/foto")
@router.delete("/foto/{gudang}/part/{pn:path}")
def hapus_foto(pn: str, gudang: str, user: dict = Depends(require_internal)):
    g = _gate(user, gudang)
    ok, msg = rak.hapus_foto(pn, g, user.get("username", ""))
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)
    return {"ok": True}


# ── Impor massal (Excel/CSV) ─────────────────────────────────────────
@router.post("/import")
async def impor_rak(gudang: str = Form(...), file: UploadFile = File(...),
                    user: dict = Depends(require_internal)):
    """Unggah daftar rak satu gudang. Kolom: Part Number | Rak | Catatan.

    Satu gudang per unggahan — gerbang tulis berlaku per gudang, jadi file
    campur-gudang akan membuat pagar itu mustahil ditegakkan per baris."""
    g = _gate(user, gudang)
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xls", ".xlsm", ".csv")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File harus Excel (.xlsx/.xls/.xlsm) atau .csv.")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    if len(data) > _MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Ukuran file maksimal 10 MB.")
    try:
        valid, dilewati = rak.parse_import(data, file.filename or "")
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File tidak terbaca: {e}")

    tersimpan = 0
    for row in valid:
        ok, msg = rak.upsert(row["pn"], g, row["rak"], row.get("catatan", ""),
                             user.get("username", ""))
        if ok:
            tersimpan += 1
        else:
            dilewati.append({"pn": row["pn"], "alasan": msg})
    return {"ok": True, "gudang": g, "tersimpan": tersimpan,
            "dilewati": dilewati[:200], "jumlah_dilewati": len(dilewati)}
