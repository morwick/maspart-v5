"""Router cabang: pesanan masuk untuk gudang ini + proses status (notifikasi)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..deps import require_branch
from ..services import orders

router = APIRouter(prefix="/api/branch", tags=["branch"])

# Status yang dianggap "perlu diproses" cabang (untuk badge notifikasi).
_ACTIVE = ("menunggu_pembayaran", "menunggu_verifikasi", "diproses", "dikirim")
# Status yang boleh diubah oleh cabang.
_BRANCH_STATUSES = ("diproses", "dikirim", "selesai", "batal")


class StatusRequest(BaseModel):
    status: str
    tracking_no: str | None = None


@router.get("/orders")
def branch_orders(branch: dict = Depends(require_branch)):
    return {
        "branch": branch["branch_label"],
        "orders": orders.list_orders(gudang=branch["branch_label"]),
    }


@router.get("/orders/count")
def branch_orders_count(branch: dict = Depends(require_branch)):
    """Jumlah pesanan yang masih perlu diproses cabang (untuk badge notifikasi)."""
    ords = orders.list_orders(gudang=branch["branch_label"])
    count = sum(1 for o in ords if o.get("status") in _ACTIVE)
    return {"count": count, "branch": branch["branch_label"]}


@router.get("/orders/{code}")
def branch_order_detail(code: str, branch: dict = Depends(require_branch)):
    o = orders.get_order(code, gudang=branch["branch_label"])
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pesanan tidak ditemukan untuk cabang ini.")
    return o


@router.get("/sales")
def branch_sales(branch: dict = Depends(require_branch)):
    """Rekap penjualan khusus cabang ini (omzet, per bulan, status, part terlaris)."""
    return orders.sales_recap(gudang=branch["branch_label"])


@router.put("/orders/{code}/status")
def branch_set_status(code: str, body: StatusRequest, branch: dict = Depends(require_branch)):
    if body.status not in _BRANCH_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Status tidak valid untuk cabang.")
    tracking = (body.tracking_no or "").strip() or None
    if not orders.set_status_branch(code, branch["branch_label"], body.status, tracking_no=tracking):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Gagal ubah status / bukan order cabang ini.")
    return {"ok": True}
