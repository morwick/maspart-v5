"""Router chat pesanan: pembeli ↔ gudang/cabang pengirim (per order)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..deps import get_current_user, require_branch, require_buyer
from ..services import chat, gudang, orders

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    body: str


def _role_in_order(o: dict, user: dict) -> str | None:
    """Peran user pada order: 'admin' | 'pembeli' | 'gudang' | None (bukan peserta)."""
    role = (user.get("role") or "").lower()
    uname = (user.get("username") or "").lower()
    if role == "admin":
        return "admin"
    if uname == (o.get("username") or "").lower():
        return "pembeli"
    branch = gudang.gudang_for_user(uname, role)
    if branch and gudang.gudang_label(branch) == (o.get("gudang") or ""):
        return "gudang"
    return None


def _order_or_403(code: str, user: dict) -> tuple[dict, str]:
    o = orders.order_owner_gudang(code)
    if not o:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pesanan tidak ditemukan.")
    r = _role_in_order(o, user)
    if not r:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tidak berhak mengakses chat pesanan ini.")
    return o, r


@router.get("/orders/{code}/chat")
def get_chat(code: str, user: dict = Depends(get_current_user)):
    o, role = _order_or_403(code, user)
    return {
        "role": role,
        "gudang": o.get("gudang"),
        "buyer": o.get("username"),
        "messages": chat.list_messages(code),
    }


@router.post("/orders/{code}/chat")
def post_chat(code: str, body: ChatRequest, user: dict = Depends(get_current_user)):
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pesan kosong.")
    _o, role = _order_or_403(code, user)
    if not chat.add_message(code, user["username"], role, text[:2000]):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal mengirim pesan.")
    return {"ok": True}


# ── Chat pra-pesanan: pembeli ↔ gudang (lepas dari order) ───────────
@router.get("/chat/buyer/threads")
def buyer_chat_threads(buyer: dict = Depends(require_buyer)):
    return {"threads": chat.buyer_threads(buyer["username"])}


@router.get("/chat/gudang/{key}")
def buyer_gudang_thread(key: str, buyer: dict = Depends(require_buyer)):
    key = key.strip().lower()
    if key not in gudang.branch_keys():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gudang tidak ditemukan.")
    return {"messages": chat.list_gudang_messages(key, buyer["username"])}


@router.post("/chat/gudang/{key}")
def buyer_gudang_send(key: str, body: ChatRequest, buyer: dict = Depends(require_buyer)):
    key = key.strip().lower()
    if key not in gudang.branch_keys():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gudang tidak ditemukan.")
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pesan kosong.")
    if not chat.add_gudang_message(key, buyer["username"], "pembeli", buyer["username"], text[:2000]):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal mengirim pesan.")
    return {"ok": True}


@router.get("/chat/branch/threads")
def branch_chat_threads(branch: dict = Depends(require_branch)):
    return {"threads": chat.branch_threads(branch["username"].strip().lower())}


@router.get("/chat/branch/{buyer_username}")
def branch_chat_thread(buyer_username: str, branch: dict = Depends(require_branch)):
    key = branch["username"].strip().lower()
    return {"messages": chat.list_gudang_messages(key, buyer_username.strip().lower())}


@router.post("/chat/branch/{buyer_username}")
def branch_chat_send(buyer_username: str, body: ChatRequest, branch: dict = Depends(require_branch)):
    key = branch["username"].strip().lower()
    text = (body.body or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pesan kosong.")
    if not chat.add_gudang_message(key, buyer_username.strip().lower(), "gudang", branch["username"], text[:2000]):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Gagal mengirim pesan.")
    return {"ok": True}
