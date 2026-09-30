from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.deps import require_role
from app.schemas import (
    ApproveRegistrationRequest,
    GeneratedCredentials,
    RegistrationCreate,
    RegistrationPublic,
    RegistrationSubmitted,
    RejectRegistrationRequest,
)
from app.services import events, registration
from app.services.auth import UserRecord

router = APIRouter(prefix="/api/v1/registrations", tags=["registrations"])

MAX_SUBMISSIONS_PER_HOUR = 20


def _public(r) -> RegistrationPublic:
    return RegistrationPublic(
        id=r.id, full_name=r.full_name, email=r.email, phone=r.phone, nmc_number=r.nmc_number,
        specialty=r.specialty, requested_role=r.requested_role, institution=r.institution,
        notes=r.notes or "", status=r.status, submitted_at=r.submitted_at,
        reviewed_at=r.reviewed_at, decision_note=r.decision_note, username=r.username,
    )


@router.post("", response_model=RegistrationSubmitted, status_code=201)
async def submit_registration(body: RegistrationCreate, request: Request):
    """Public: no login required. Creates a PENDING request only — no account
    exists until an administrator approves it."""
    if body.website:  # honeypot filled in -> automated submission
        raise HTTPException(400, "Submission rejected")
    ip = request.client.host if request.client else "unknown"
    key = f"btdm:registration-rate:{ip}"
    r = events.client()
    count = r.incr(key)
    if count == 1:
        r.expire(key, 3600)
    if count > MAX_SUBMISSIONS_PER_HOUR:
        raise HTTPException(429, "Too many registration attempts. Please try again later.")
    try:
        row = registration.submit(
            full_name=body.full_name, email=body.email, phone=body.phone, nmc_number=body.nmc_number,
            specialty=body.specialty, requested_role=body.requested_role,
            institution=body.institution, notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return RegistrationSubmitted(reference=row.id, status=row.status)


@router.get("", response_model=list[RegistrationPublic])
async def list_registrations(status: str | None = None, _admin: UserRecord = Depends(require_role("admin"))):
    return [_public(r) for r in registration.list_requests(status)]


@router.post("/{reg_id}/approve", response_model=GeneratedCredentials)
async def approve_registration(
    reg_id: str, body: ApproveRegistrationRequest, admin: UserRecord = Depends(require_role("admin"))
):
    try:
        row, username, password = registration.approve(reg_id, admin.id, body.role)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return GeneratedCredentials(registration=_public(row), username=username, temporary_password=password)


@router.post("/{reg_id}/reject", response_model=RegistrationPublic)
async def reject_registration(
    reg_id: str, body: RejectRegistrationRequest, admin: UserRecord = Depends(require_role("admin"))
):
    try:
        return _public(registration.reject(reg_id, admin.id, body.reason))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
