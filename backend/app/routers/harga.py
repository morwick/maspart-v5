"""Router Harga: List Harga (harga.xlsx), kurs, Cari & Batch harga SIMS."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel

from ..deps import get_current_user, require_admin
from ..services import harga

router = APIRouter(prefix="/api/harga", tags=["harga"])
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_BATCH = 300


# ── List Harga ───────────────────────────────────────────────────────
@router.get("/list")
def list_harga(
    q: str = Query(""),
    sort: str = Query("pn", description="pn | name | harga_asc | harga_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    _user: dict = Depends(get_current_user),
):
    df = harga.list_harga(q, sort)
    total_filtered = len(df)
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    disp = harga.display_frame(df.iloc[start : start + page_size])
    return {
        "total": harga.total_count(),
        "total_filtered": total_filtered,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "rows": disp.to_dict(orient="records"),
    }


@router.get("/list/export")
def list_export(
    q: str = Query(""),
    sort: str = Query("pn"),
    _user: dict = Depends(get_current_user),
):
    df = harga.list_harga(q, sort)
    data = harga.to_excel_bytes(harga.display_frame(df))
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="harga_sparepart.xlsx"'},
    )


# ── Kurs ─────────────────────────────────────────────────────────────
@router.get("/rate")
def rate(force: bool = Query(False), _user: dict = Depends(get_current_user)):
    value, err = harga.get_rate(force=force)
    return {"rate": value, "error": err}


# ── Cari Harga (SIMS) ────────────────────────────────────────────────
@router.get("/cari")
def cari(
    pn: str = Query(..., min_length=1),
    refresh: bool = Query(False),
    _user: dict = Depends(get_current_user),
):
    return harga.cari_harga(pn.strip().upper(), force_refresh=refresh)


# ── Batch Cari Harga (SIMS) ──────────────────────────────────────────
class BatchHargaRequest(BaseModel):
    text: str = ""
    part_numbers: list[str] | None = None


class BatchExportRequest(BaseModel):
    rate: float = 0.0
    rows: list[dict] = []


def _collect_pns(body: BatchHargaRequest) -> list[str]:
    raw = body.part_numbers or [
        ln.strip() for ln in (body.text or "").splitlines() if ln.strip()
    ]
    seen, out = set(), []
    for pn in raw:
        u = pn.strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(pn.strip())
    return out


@router.post("/batch")
def batch(body: BatchHargaRequest, _user: dict = Depends(get_current_user)):
    pns = _collect_pns(body)
    if not pns:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada Part Number.")
    if len(pns) > _MAX_BATCH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Maksimum {_MAX_BATCH} PN per batch."
        )
    return harga.batch_harga(pns)


@router.post("/batch/export")
def batch_export(body: BatchExportRequest, _user: dict = Depends(get_current_user)):
    data = harga.batch_to_excel(body.rate, body.rows)
    return Response(
        content=data,
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="batch_harga.xlsx"'},
    )


@router.post("/refresh")
def refresh_list(_admin: dict = Depends(require_admin)):
    harga.refresh()
    return {"ok": True, "total": harga.total_count()}
