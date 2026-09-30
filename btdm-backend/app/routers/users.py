from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.deps import require_role
from app.schemas import CreateUserRequest, UserPublic
from app.services import audit
from app.services import auth as auth_service
from app.services.auth import UserRecord

router = APIRouter(prefix="/api/v1/users", tags=["users"])


def _to_public(user: UserRecord) -> UserPublic:
    return UserPublic(
        id=user.id, name=user.name, username=user.username,
        role=user.role, institution=user.institution, active=user.active,
        must_change_password=user.must_change_password,
    )


@router.post("", response_model=UserPublic, status_code=201)
async def create_user(body: CreateUserRequest, admin: UserRecord = Depends(require_role("admin"))):
    try:
        user = auth_service.create_user(
            name=body.name, username=body.username, password=body.password,
            role=body.role.value, institution=body.institution,
            must_change_password=body.require_password_change,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    audit.record("user.create", admin.id, user.id, role=user.role)
    return _to_public(user)


@router.get("", response_model=list[UserPublic])
async def list_users(_admin: UserRecord = Depends(require_role("admin"))):
    return [_to_public(u) for u in auth_service.list_users()]


@router.patch("/{user_id}/active", response_model=UserPublic)
async def set_active(user_id: str, active: bool, _admin: UserRecord = Depends(require_role("admin"))):
    if not auth_service.set_active(user_id, active):
        raise HTTPException(404, "User not found")
    user = auth_service.get_user(user_id)
    assert user is not None
    return _to_public(user)


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: str, admin: UserRecord = Depends(require_role("admin"))):
    """New one-time password, shown once; the user must change it at next login."""
    if user_id == admin.id:
        raise HTTPException(400, "Use 'Change password' for your own account")
    try:
        username, password = auth_service.reset_password(user_id)
    except LookupError:
        raise HTTPException(404, "User not found")
    audit.record("user.password_reset", admin.id, user_id)
    return {"username": username, "password": password}


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    delete_studies: bool = False,
    confirm: str = "",
    admin: UserRecord = Depends(require_role("admin")),
):
    """Deletes an account. If it owns patient studies the request is refused
    unless delete_studies=true AND confirm equals the account's user ID; then
    the studies (records, images, results, reports) are erased permanently
    first. Intended for test accounts — for real doctors, disable instead."""
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete your own account while logged in as it")
    from app.services import storage

    target = auth_service.get_user(user_id)
    if target is None:
        raise HTTPException(404, "User not found")
    n = storage.count_studies(user_id)
    if n and not delete_studies:
        raise HTTPException(
            409,
            f"This user owns {n} patient stud{'y' if n == 1 else 'ies'}. Disable the account instead "
            "(their workspace stays intact and inaccessible to everyone else), or delete the account "
            "together with its studies — that cannot be undone.",
        )
    if n:
        if confirm != target.username:
            raise HTTPException(400, f"To delete {n} studies permanently, confirm with the user ID '{target.username}'")
        for sid in storage.study_ids_of(user_id):
            storage.delete_study(sid)
        audit.record("user.studies_erased", admin.id, user_id, count=n)
    if not auth_service.delete_user(user_id):
        raise HTTPException(404, "User not found")
    audit.record("user.delete", admin.id, user_id)
    return None
