"""Router Repair Kit Transmisi: daftar model + export Excel per/semua transmisi."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..deps import get_current_user
from ..services import repairkit

router = APIRouter(prefix="/api/repairkit", tags=["repairkit"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/transmisi")
def list_transmisi(_user: dict = Depends(get_current_user)):
    return {"available": repairkit.available(), "models": repairkit.list_models()}


@router.get("/transmisi/export")
def export_transmisi(
    model: str = Query("", description="Model/PN/unit transmisi; kosong = semua model"),
    _user: dict = Depends(get_current_user),
):
    data = repairkit.to_excel_bytes(model or None)
    fname = f"repairkit_transmisi_{model}".strip("_").replace(" ", "_") or "repairkit_transmisi"
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'},
    )
