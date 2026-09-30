"""
Credential auth: salted PBKDF2-SHA256 password hashes in PostgreSQL,
opaque bearer tokens as Redis sessions that expire after SESSION_TTL_HOURS.
Only a SHA-256 of each token is stored, so a Redis dump can't be replayed.

Remaining gaps before production: no login rate limiting / lockout, no
password complexity rules beyond length, no SSO/OIDC.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select

from app import db
from app.config import SESSION_IDLE_MINUTES, SESSION_TTL_HOURS
from app.services import events, password_policy

logger = logging.getLogger("btdm.services.auth")

VALID_ROLES = {"radiologist", "referring_physician", "technologist", "admin"}
_SESSION_PREFIX = "btdm:session:"
_USER_SESSIONS_PREFIX = "btdm:user-sessions:"


@dataclass
class UserRecord:
    id: str
    name: str
    username: str
    role: str
    institution: str
    active: bool
    created_at: datetime
    must_change_password: bool = False


def _record(row: db.User) -> UserRecord:
    return UserRecord(
        id=row.id, name=row.name, username=row.username, role=row.role,
        institution=row.institution, active=row.active, created_at=row.created_at,
        must_change_password=bool(row.must_change_password),
    )


def _normalize(username: str) -> str:
    return username.strip().lower()


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def _token_key(token: str) -> str:
    return _SESSION_PREFIX + hashlib.sha256(token.encode()).hexdigest()


def create_user(
    name: str, username: str, password: str, role: str, institution: str = "—",
    must_change_password: bool = False,
) -> UserRecord:
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}' — must be one of {sorted(VALID_ROLES)}")
    norm = _normalize(username)
    password_policy.validate_username(norm)
    password_policy.validate(password, norm)
    with db.session_scope() as s:
        if s.scalar(select(db.User).where(db.User.username == norm)):
            raise ValueError(f"Username '{norm}' already exists")
        salt = secrets.token_hex(16)
        row = db.User(
            id=f"user-{secrets.token_hex(6)}", name=name, username=norm,
            password_hash=_hash_password(password, salt), password_salt=salt,
            role=role, institution=institution, active=True,
            must_change_password=must_change_password,
        )
        s.add(row)
        s.flush()
        return _record(row)


def authenticate(username: str, password: str) -> Optional[str]:
    """Session token on success; None on any failure (deliberately not
    revealing whether the username exists)."""
    with db.session_scope() as s:
        row = s.scalar(select(db.User).where(db.User.username == _normalize(username)))
        if row is None:
            _hash_password(password, "timing-equaliser")
            return None
        if not row.active or not hmac.compare_digest(_hash_password(password, row.password_salt), row.password_hash):
            return None
        user_id = row.id
    token = secrets.token_urlsafe(32)
    ttl = int(SESSION_TTL_HOURS * 3600)
    r = events.client()
    key = _token_key(token)
    # value = "<user id>|<issued-at epoch>"; the key's own TTL is the idle
    # timeout and is renewed on every authenticated request (sliding).
    r.set(key, f"{user_id}|{int(time.time())}", ex=_idle_seconds())
    r.sadd(_USER_SESSIONS_PREFIX + user_id, key)
    r.expire(_USER_SESSIONS_PREFIX + user_id, ttl)
    return token


def _idle_seconds() -> int:
    return max(60, int(SESSION_IDLE_MINUTES * 60))


def get_user_by_token(token: str) -> Optional[UserRecord]:
    r = events.client()
    key = _token_key(token)
    value = r.get(key)
    if not value:
        return None  # never issued, logged out, or idle for too long
    user_id, _, issued = value.partition("|")
    if issued and time.time() - int(issued) > SESSION_TTL_HOURS * 3600:
        r.delete(key)  # absolute lifetime reached
        return None
    user = get_user(user_id)
    if user is None or not user.active:
        return None
    r.expire(key, _idle_seconds())  # sliding idle timeout
    return user


def change_password(user_id: str, current_password: str, new_password: str) -> None:
    """Raises ValueError with a user-facing message on failure."""
    if new_password == current_password:
        raise ValueError("New password must be different from the current one")
    with db.session_scope() as s:
        row = s.get(db.User, user_id)
        if row is None or not row.active:
            raise ValueError("Account not found")
        if not hmac.compare_digest(_hash_password(current_password, row.password_salt), row.password_hash):
            raise ValueError("Current password is incorrect")
        password_policy.validate(new_password, row.username)
        salt = secrets.token_hex(16)
        row.password_salt = salt
        row.password_hash = _hash_password(new_password, salt)
        row.must_change_password = False


def username_exists(username: str) -> bool:
    with db.session_scope() as s:
        return s.scalar(select(db.User).where(db.User.username == _normalize(username))) is not None


def logout(token: str) -> None:
    events.client().delete(_token_key(token))


def list_users() -> list[UserRecord]:
    with db.session_scope() as s:
        return [_record(r) for r in s.scalars(select(db.User).order_by(db.User.created_at)).all()]


def get_user(user_id: str) -> Optional[UserRecord]:
    with db.session_scope() as s:
        row = s.get(db.User, user_id)
        return _record(row) if row else None


def _revoke_all_sessions_for(user_id: str) -> None:
    r = events.client()
    set_key = _USER_SESSIONS_PREFIX + user_id
    keys = list(r.smembers(set_key))
    if keys:
        r.delete(*keys)
    r.delete(set_key)


def set_active(user_id: str, active: bool) -> bool:
    with db.session_scope() as s:
        row = s.get(db.User, user_id)
        if not row:
            return False
        row.active = active
    if not active:
        _revoke_all_sessions_for(user_id)
    return True


def reset_password(user_id: str) -> tuple[str, str]:
    """Admin reset: new one-time password (policy length, default 8), must be
    changed at next login; every open session of the user is ended.
    Returns (username, password)."""
    password = password_policy.generate()
    with db.session_scope() as s:
        row = s.get(db.User, user_id)
        if row is None:
            raise LookupError("User not found")
        salt = secrets.token_hex(16)
        row.password_salt = salt
        row.password_hash = _hash_password(password, salt)
        row.must_change_password = True
        username = row.username
    _revoke_all_sessions_for(user_id)
    return username, password


def delete_user(user_id: str) -> bool:
    with db.session_scope() as s:
        row = s.get(db.User, user_id)
        if not row:
            return False
        s.delete(row)
        # The approved registration that created this account holds the
        # doctor's personal details; remove it with the account.
        s.execute(delete(db.RegistrationRequest).where(db.RegistrationRequest.user_id == user_id))
    _revoke_all_sessions_for(user_id)
    return True


def bootstrap_admin() -> None:
    """Creates the first admin account if the users table has no admin."""
    with db.session_scope() as s:
        if s.scalar(select(db.User).where(db.User.role == "admin")):
            return
    password = os.getenv("ADMIN_BOOTSTRAP_PASSWORD") or ""
    if len(password) < 12 or password == "changeme123":
        raise RuntimeError(
            "Set ADMIN_BOOTSTRAP_PASSWORD (at least 12 characters, not 'changeme123') "
            "before first start — no default admin password is shipped."
        )
    try:
        create_user(name="Default Admin", username="admin", password=password, role="admin")
    except ValueError as exc:
        raise RuntimeError(f"ADMIN_BOOTSTRAP_PASSWORD is not acceptable. {exc}") from exc
    logger.info("Bootstrap admin account created (username: admin).")
