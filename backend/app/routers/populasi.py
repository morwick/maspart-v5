"""Router Populasi Unit: data terfilter + paginasi, opsi filter, export Excel."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..deps import get_current_user, require_admin
from ..services import populasi

router = APIRouter(prefix="/api/populasi", tags=["populasi"])

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_filters(filters: str) -> dict:
    if not filters:
        return {}
    try:
        data = json.loads(filters)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@router.get("")
def get_data(
    q: str = Query("", description="Kata kunci (semua kolom)"),
    filters: str = Query("", description='JSON {kolom: nilai}, mis. {"MODEL":"HOWO"}'),
    sort: str = Query("", description="Nama kolom untuk diurutkan"),
    dir: str = Query("asc", description="asc | desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    _user: dict = Depends(get_current_user),
):
    df = populasi.query(q, _parse_filters(filters))
    df = populasi.sort_df(df, sort, dir)
    total_filtered = len(df)
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    page_df = df.iloc[start : start + page_size].fillna("")
    return {
        "columns": populasi.columns(),
        "filter_options": populasi.filter_options(),
        "total": populasi.total_count(),
        "total_filtered": total_filtered,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "rows": page_df.astype(str).to_dict(orient="records"),
    }


@router.get("/export")
def export(
    q: str = Query(""),
    filters: str = Query(""),
    _user: dict = Depends(get_current_user),
):
    df = populasi.query(q, _parse_filters(filters))
    data = populasi.to_excel_bytes(df.fillna(""))
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="populasi_unit.xlsx"'},
    )


@router.post("/refresh")
def refresh(_admin: dict = Depends(require_admin)):
    populasi.refresh()
    return {"ok": True, "total": populasi.total_count()}
