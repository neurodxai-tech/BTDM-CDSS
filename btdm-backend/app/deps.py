"""
Shared FastAPI dependencies for authentication/authorization. Any router
needing "who is this request from" or "does this request have role X"
should depend on these rather than re-implementing header parsing —
that duplication is exactly how the earlier X-User-Role pattern drifted
across reports.py and studies.py with slightly different logic.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.services import auth as auth_service
from app.services.auth import UserRecord


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip() or None


_PASSWORD_CHANGE_ALLOWED = ("/api/v1/auth/",)


def get_current_user(request: Request, authorization: str | None = Header(default=None)) -> UserRecord:
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(401, "Missing bearer token — include 'Authorization: Bearer <token>'")
    user = auth_service.get_user_by_token(token)
    if not user:
        raise HTTPException(401, "Invalid or expired session")
    # Accounts created with a generated password can do nothing but change it.
    if user.must_change_password and not request.url.path.startswith(_PASSWORD_CHANGE_ALLOWED):
        raise HTTPException(403, "Password change required before using the system")
    return user


def require_role(*roles: str):
    """Usage: `user: UserRecord = Depends(require_role("admin"))` —
    raises 403 if the authenticated user's role isn't in `roles`, 401 if
    there's no valid session at all."""

    def _checker(request: Request, authorization: str | None = Header(default=None)) -> UserRecord:
        user = get_current_user(request, authorization)
        if user.role not in roles:
            raise HTTPException(403, f"Requires one of roles: {', '.join(roles)}")
        return user

    return _checker
