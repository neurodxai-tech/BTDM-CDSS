"""
Per-doctor worklists: every study belongs to the user who uploaded it, and
only that user can see or act on it. Anything else gets 404 (not 403), so
one doctor cannot even learn that another doctor's study id exists.

All users share the same inference worker and therefore the same active
model (the pinned Seed 42 checkpoint); ownership only scopes the data.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.services import storage
from app.services.auth import UserRecord


def require_owner(study_id: str, user: UserRecord) -> None:
    owner = storage.study_owner(study_id)
    if owner is storage._MISSING or owner is None or owner != user.id:
        raise HTTPException(404, "Study not found")
