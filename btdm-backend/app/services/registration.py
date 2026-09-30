"""
Doctor self-registration with administrator approval.

  submit()   public — stores a pending request, returns its reference number
  approve()  admin  — creates the account with a generated username and
                      password; the password is returned ONCE for the admin
                      to hand over and is never stored in plain text. The
                      doctor must change it at first login.
  reject()   admin  — records the decision and an optional reason

Usernames follow the existing convention: rad-001, rad-002 … for
radiologists, ref-001 … for referring physicians.
"""
from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select

from app import db
from app.services import password_policy
from app.services import audit
from app.services import auth as auth_service

DOCTOR_ROLES = {"radiologist", "referring_physician"}
USERNAME_PREFIX = {"radiologist": "rad", "referring_physician": "ref", "technologist": "tech"}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
def generate_password() -> str:
    """One-time password, GENERATED_PASSWORD_LENGTH characters (default 8)."""
    return password_policy.generate()


def next_username(role: str) -> str:
    prefix = USERNAME_PREFIX.get(role, "user")
    pattern = re.compile(rf"^{prefix}-(\d+)$")
    highest = 0
    for u in auth_service.list_users():
        m = pattern.match(u.username)
        if m:
            highest = max(highest, int(m.group(1)))
    n = highest + 1
    while auth_service.username_exists(f"{prefix}-{n:03d}"):
        n += 1
    return f"{prefix}-{n:03d}"


def _clean(value: str, field: str, max_len: int, required: bool = True) -> str:
    v = " ".join((value or "").split())
    if required and not v:
        raise ValueError(f"{field} is required")
    if len(v) > max_len:
        raise ValueError(f"{field} is too long")
    return v


def submit(
    full_name: str, email: str, phone: str, nmc_number: str, specialty: str,
    requested_role: str, institution: str, notes: str = "",
) -> db.RegistrationRequest:
    full_name = _clean(full_name, "Full name", 200)
    email = _clean(email, "Email", 200).lower()
    phone = _clean(phone, "Phone", 40)
    nmc_number = _clean(nmc_number, "NMC registration number", 40).upper()
    specialty = _clean(specialty, "Specialty", 120)
    institution = _clean(institution, "Institution", 200)
    notes = (notes or "").strip()[:2000]
    if not _EMAIL_RE.match(email):
        raise ValueError("Please enter a valid email address")
    if requested_role not in DOCTOR_ROLES:
        raise ValueError("Requested role must be radiologist or referring physician")

    with db.session_scope() as s:
        dup = s.scalar(select(db.RegistrationRequest).where(
            db.RegistrationRequest.status.in_(["pending", "approved"]),
            or_(db.RegistrationRequest.email == email, db.RegistrationRequest.nmc_number == nmc_number),
        ))
        if dup is not None:
            raise ValueError(
                "A registration with this email or NMC number is already "
                + ("pending review" if dup.status == "pending" else "approved")
                + f" (reference {dup.id})."
            )
        row = db.RegistrationRequest(
            id=f"REG-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}",
            full_name=full_name, email=email, phone=phone, nmc_number=nmc_number,
            specialty=specialty, requested_role=requested_role, institution=institution,
            notes=notes, status="pending",
        )
        s.add(row)
        s.flush()
        ref = row.id
    audit.record("registration.submit", None, ref, role=requested_role)
    return get(ref)


def get(reg_id: str) -> Optional[db.RegistrationRequest]:
    with db.session_scope() as s:
        return s.get(db.RegistrationRequest, reg_id)


def list_requests(status: str | None = None) -> list[db.RegistrationRequest]:
    with db.session_scope() as s:
        q = select(db.RegistrationRequest).order_by(db.RegistrationRequest.submitted_at.desc())
        if status:
            q = q.where(db.RegistrationRequest.status == status)
        return list(s.scalars(q).all())


def approve(reg_id: str, admin_id: str, role: str | None = None) -> tuple[db.RegistrationRequest, str, str]:
    """Returns (request, username, generated_password)."""
    req = get(reg_id)
    if req is None:
        raise LookupError("Registration not found")
    if req.status != "pending":
        raise ValueError(f"This registration was already {req.status}")
    role = role or req.requested_role
    if role not in DOCTOR_ROLES:
        raise ValueError("Role must be radiologist or referring physician")

    username = next_username(role)
    password = generate_password()
    user = auth_service.create_user(
        name=req.full_name, username=username, password=password, role=role,
        institution=req.institution, must_change_password=True,
    )
    with db.session_scope() as s:
        row = s.get(db.RegistrationRequest, reg_id)
        row.status = "approved"
        row.reviewed_by = admin_id
        row.reviewed_at = datetime.now(timezone.utc)
        row.user_id = user.id
        row.username = username
        row.requested_role = role
    audit.record("registration.approve", admin_id, reg_id, new_user_id=user.id, username=username, role=role)
    return get(reg_id), username, password


def reject(reg_id: str, admin_id: str, reason: str = "") -> db.RegistrationRequest:
    req = get(reg_id)
    if req is None:
        raise LookupError("Registration not found")
    if req.status != "pending":
        raise ValueError(f"This registration was already {req.status}")
    with db.session_scope() as s:
        row = s.get(db.RegistrationRequest, reg_id)
        row.status = "rejected"
        row.reviewed_by = admin_id
        row.reviewed_at = datetime.now(timezone.utc)
        row.decision_note = (reason or "").strip()[:1000] or None
    audit.record("registration.reject", admin_id, reg_id)
    return get(reg_id)
