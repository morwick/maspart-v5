"""Router pembeli: etalase belanja (katalog produk) + lokasi gudang."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..deps import require_buyer
from ..services import buyer_catalog, gudang
from ..services import supabase_client as sb

router = APIRouter(prefix="/api/buyer", tags=["buyer"])


@router.get("/home")
def home(buyer: dict = Depends(require_buyer)):
    """Data beranda etalase: lokasi, kategori, terlaris, unggulan."""
    key = sb.get_user_gudang(buyer["username"])
    return buyer_catalog.home_payload(buyer["username"], key)


@router.get("/catalog")
def catalog(
    q: str = Query("", description="Kata kunci: PN atau nama part (sinonim ikut)"),
    kategori: str = Query("", description="Key kategori etalase (mis. 'rem')"),
    sort: str = Query("relevan", description="relevan|harga_asc|harga_desc|nama|stok"),
    ready: bool = Query(False, description="Hanya yang READY di lokasi pembeli"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    buyer: dict = Depends(require_buyer),
):
    """Katalog produk siap-jual, stok di-scope ke lokasi pembeli."""
    key = sb.get_user_gudang(buyer["username"])
    return buyer_catalog.catalog_page(
        buyer["username"], key, q=q, kategori=kategori, sort=sort,
        ready_only=ready, page=page, page_size=page_size,
    )


class SetLocationRequest(BaseModel):
    key: str


@router.get("/locations")
def locations(_buyer: dict = Depends(require_buyer)):
    """Daftar gudang yang bisa dipilih pembeli."""
    return {"locations": gudang.list_locations()}


@router.get("/location")
def my_location(buyer: dict = Depends(require_buyer)):
    """Lokasi gudang yang sedang dipilih pembeli (key + label)."""
    key = sb.get_user_gudang(buyer["username"])
    loc = gudang.location(key)
    return {"key": key, "label": gudang.gudang_label(loc["label"]) if loc else None}


@router.post("/location")
def set_location(body: SetLocationRequest, buyer: dict = Depends(require_buyer)):
    key = (body.key or "").strip().lower()
    loc = gudang.location(key)
    if not loc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Lokasi gudang tidak valid.")
    ok, msg = sb.set_user_gudang(buyer["username"], key)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal simpan lokasi: {msg}")
    sb.log_activity(buyer["username"], "pilih_gudang", target=key)
    return {"ok": True, "key": key, "label": gudang.gudang_label(loc["label"])}
