"""Router pembeli: daftar lokasi gudang + pilih lokasi sebelum belanja."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..deps import require_buyer
from ..services import gudang
from ..services import supabase_client as sb

router = APIRouter(prefix="/api/buyer", tags=["buyer"])


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
