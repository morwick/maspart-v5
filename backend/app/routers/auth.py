"""Router auth: login (JWT) + info user saat ini."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..core.config import get_settings
from ..core.ratelimit import limit
from ..core.security import create_access_token
from ..deps import get_current_user
from ..schemas import LoginRequest, TokenResponse, UserOut
from ..services.auth import authenticate
from ..services import permissions
from ..services import supabase_client as sb

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(limit("login", 10, 60))],  # maks 10 percobaan / menit / IP
)
def login(body: LoginRequest):
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah",
        )
    # Catat login untuk monitoring (best-effort).
    sb.mark_login(user["username"])
    sb.log_activity(user["username"], "login")
    token = create_access_token(user["username"], user["role"])
    return TokenResponse(
        access_token=token,
        expires_in=get_settings().jwt_expire_minutes * 60,
        user=UserOut(**user),
    )


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    gudang = sb.get_user_gudang(user["username"]) if user.get("role") == "pembeli" else None
    return UserOut(username=user["username"], role=user["role"], gudang=gudang)


@router.get("/permissions")
def my_permissions(user: dict = Depends(get_current_user)):
    """Semua izin efektif user (menu + kolom + sub-tab harga) untuk gating frontend."""
    return permissions.all_effective(user["username"], user.get("role", "user"))
