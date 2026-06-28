"""Router Stok Opname: draft + history per user."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile, status

from ..deps import get_current_user
from ..services import opname

router = APIRouter(prefix="/api/opname", tags=["opname"])


@router.get("/draft")
def get_draft(user: dict = Depends(get_current_user)):
    return {"draft": opname.load_draft(user["username"])}


@router.post("/draft/from-upload")
async def draft_from_upload(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xls", ".xlsm", ".csv")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File harus Excel/CSV.")
    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File kosong.")
    try:
        parsed = opname.parse_upload(file.filename or "", data)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Gagal baca file: {e}")
    if not parsed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tidak ada part number terbaca.")
    session = opname.build_session(parsed, user["username"], file.filename or "")
    ok, err = opname.save_draft(user["username"], session)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal simpan draft: {err}")
    return {"session": session}


@router.put("/draft")
def save_draft(session: dict = Body(...), user: dict = Depends(get_current_user)):
    if not isinstance(session, dict) or not session.get("session_id"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sesi tidak valid.")
    ok, err = opname.save_draft(user["username"], session)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal simpan: {err}")
    return {"ok": True, "updated_at": session.get("updated_at")}


@router.delete("/draft")
def delete_draft(user: dict = Depends(get_current_user)):
    opname.delete_draft(user["username"])
    return {"ok": True}


@router.get("/history")
def history(user: dict = Depends(get_current_user)):
    return {"history": opname.load_history(user["username"])}


@router.post("/finalize")
def finalize(session: dict = Body(...), user: dict = Depends(get_current_user)):
    if not isinstance(session, dict) or not session.get("session_id"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Sesi tidak valid.")
    ok, err = opname.finalize(user["username"], session)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal finalisasi: {err}")
    return {"ok": True}


@router.delete("/history/{session_id}")
def delete_history(session_id: str, user: dict = Depends(get_current_user)):
    opname.delete_history_entry(user["username"], session_id)
    return {"ok": True}
