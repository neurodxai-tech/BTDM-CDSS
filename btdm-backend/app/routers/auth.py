from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.deps import get_current_user, _extract_bearer
from app.schemas import ChangePasswordRequest, LoginRequest, LoginResponse, UserPublic
from app.services import audit, events
from app.services import auth as auth_service
from app.services.auth import UserRecord

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _to_public(user: UserRecord) -> UserPublic:
    return UserPublic(
        id=user.id, name=user.name, username=user.username,
        role=user.role, institution=user.institution, active=user.active,
        must_change_password=user.must_change_password,
    )


# Brute-force protection: failed logins are counted in Redis per username
# and per client address over a sliding 15-minute window.
_FAIL_WINDOW_S = 15 * 60
_MAX_FAILS_PER_USER = 10
_MAX_FAILS_PER_IP = 30


def _fail_keys(username: str, request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    return f"btdm:login-fail:user:{username}", f"btdm:login-fail:ip:{ip}"


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    username = body.username.strip().lower()[:100]
    user_key, ip_key = _fail_keys(username, request)
    r = events.client()
    if int(r.get(user_key) or 0) >= _MAX_FAILS_PER_USER or int(r.get(ip_key) or 0) >= _MAX_FAILS_PER_IP:
        audit.record("auth.login_blocked", None, username)
        raise HTTPException(429, "Too many failed login attempts. Try again in 15 minutes or ask an administrator.")

    token = auth_service.authenticate(body.username, body.password)
    if not token:
        for key in (user_key, ip_key):
            if r.incr(key) == 1:
                r.expire(key, _FAIL_WINDOW_S)
        audit.record("auth.login_failed", None, username)
        raise HTTPException(401, "Invalid username or password")
    r.delete(user_key)
    user = auth_service.get_user_by_token(token)
    assert user is not None  # token was just issued for this user
    audit.record("auth.login", user.id)
    return LoginResponse(token=token, user=_to_public(user))


@router.get("/password-policy")
async def password_policy_rules():
    """Public: lets the forms show and pre-check the same rules the server enforces."""
    from app.services import password_policy

    return password_policy.describe()


@router.post("/logout", status_code=204)
async def logout(authorization: str | None = Header(default=None)):
    token = _extract_bearer(authorization)
    if token:
        auth_service.logout(token)
    return None


@router.get("/me", response_model=UserPublic)
async def me(user: UserRecord = Depends(get_current_user)):
    return _to_public(user)


@router.post("/change-password", response_model=UserPublic)
async def change_password(body: ChangePasswordRequest, user: UserRecord = Depends(get_current_user)):
    try:
        auth_service.change_password(user.id, body.current_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit.record("auth.password_changed", user.id)
    return _to_public(auth_service.get_user(user.id))
