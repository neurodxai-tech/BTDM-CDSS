"""Append-only audit trail (who did what, when). Failures to write the audit
row are logged, never swallowed silently, but also never break the request."""
from __future__ import annotations

import logging

from app.db import AuditLog, session_scope

logger = logging.getLogger("btdm.audit")


def record(action: str, user_id: str | None = None, target: str | None = None, **detail) -> None:
    try:
        with session_scope() as s:
            s.add(AuditLog(action=action, user_id=user_id, target=target, detail=detail or None))
    except Exception:  # pragma: no cover
        logger.exception("Failed to write audit record %s %s", action, target)
