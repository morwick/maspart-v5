"""Router Stok: daftar stok LIVE seluruh barang dari ERP Accurate.

Kegagalan sesi/koneksi Accurate dikembalikan sebagai status (bukan HTTP error)
agar frontend bisa menampilkan pesan seadanya — pola sama dengan endpoint
``/api/parts/accurate-stock``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..deps import get_current_user, require_admin
from ..services import accurate, stok

router = APIRouter(prefix="/api/stok", tags=["stok"])
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _empty(payload: dict) -> dict:
    return {
        "total": 0,
        "total_filtered": 0,
        "page": 1,
        "page_size": 0,
        "total_pages": 1,
        "rows": [],
        **payload,
    }


@router.get("/list")
def list_stok(
    q: str = Query(""),
    sort: str = Query("pn", description="pn | name | stok_asc | stok_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    _user: dict = Depends(get_current_user),
):
    if not accurate.available():
        return _empty({"configured": False, "reason": "no_session"})
    try:
        rows = stok.stock_rows(q, sort)
        total = stok.total_count()
    except accurate.AccurateSessionExpired:
        return _empty({"configured": True, "session_expired": True})
    except accurate.AccurateError:
        return _empty({"configured": True, "error": True})

    total_filtered = len(rows)
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    disp = stok.display_rows(rows[start : start + page_size])
    return {
        "configured": True,
        "total": total,
        "total_filtered": total_filtered,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "rows": disp,
    }


@router.get("/list/export")
def list_export(
    q: str = Query(""),
    sort: str = Query("pn"),
    _user: dict = Depends(get_current_user),
):
    rows = stok.stock_rows(q, sort) if accurate.available() else []
    data = stok.to_excel_bytes(rows)
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="stok_accurate.xlsx"'},
    )


@router.post("/refresh")
def refresh_list(_admin: dict = Depends(require_admin)):
    accurate.refresh(force=True)
    return {"ok": True, "total": stok.total_count()}
