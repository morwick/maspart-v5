"""Router auth: login (JWT) + info user saat ini."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.config import get_settings
from ..core.ratelimit import client_ip, limit
from ..core.security import create_access_token
from ..deps import get_current_user
from ..schemas import LoginRequest, TokenResponse, UserOut
from ..services.auth import authenticate
from ..services import login_history, permissions, presence, session_policy
from ..services import supabase_client as sb

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(limit("login", 10, 60))],  # maks 10 percobaan / menit / IP
)
def login(body: LoginRequest, request: Request):
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah",
        )
    # IP asli klien (bukan IP Traefik) + perangkat pelapor. client_ip() mengambil
    # hop tepercaya dari KANAN, jadi XFF palsu dari klien tak bisa memalsukan ini.
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")

    # Catat login untuk monitoring: presence in-memory (sumber online/offline)
    # + DB best-effort (histori, bila kolom/tabel tersedia).
    presence.mark_login(user["username"], ip=ip, device=login_history.device_label(ua))
    sb.mark_login(user["username"])
    sb.log_activity(user["username"], "login")
    # Riwayat permanen utk deteksi akun dipakai ramai — diam bila tabel belum ada.
    login_history.record(user["username"], user.get("role", ""), ip, ua)

    # Akun dibatasi 1 perangkat → terbitkan sesi baru; sesi lama otomatis batal
    # (perangkat sebelumnya akan dapat 401 pada request berikutnya).
    sid = ""
    if session_policy.enabled(user["username"], user["role"]):
        sid = session_policy.start_session(user["username"])
    token = create_access_token(user["username"], user["role"], sid)
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
