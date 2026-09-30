"""
Model registry: which checkpoints exist, which passed validation, and which
one is active. Checkpoint files live in object storage under
models/<sha256>.pt; this module only handles records and never imports
torch (validation runs in the worker).

Lifecycle:  register -> pending --(worker validates)--> validated | failed
            validated --(activate, only when MODEL_LOCKED=false)--> active

Every analysis stores the active model's id and SHA-256, so each result
can be traced to the exact file that produced it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update

from app import db
from app.config import (
    CLASSIFIER_WEIGHTS_PATH,
    EXPECTED_CHECKPOINT_ID,
    MODEL_AUTO_ACTIVATE,
    MODEL_EXTERNALLY_VALIDATED,
    MODEL_LOCKED,
    MODEL_VERSION,
)
from app.services import audit, events
from app.services.objectstore import store

logger = logging.getLogger("btdm.services.model_registry")


class ModelLockedError(PermissionError):
    pass


def _checkpoint_id(data: bytes) -> Optional[str]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            return zf.namelist()[0].split("/", 1)[0]
    except (zipfile.BadZipFile, IndexError):
        return None


def _looks_like_checkpoint(data: bytes) -> bool:
    """torch.save() zip archive (PyTorch >= 1.6) or the legacy pickle format."""
    return _checkpoint_id(data) is not None or data[:1] == b"\x80"


def find_by_sha(sha: str) -> Optional[db.ModelVersion]:
    with db.session_scope() as s:
        return s.scalar(select(db.ModelVersion).where(db.ModelVersion.sha256 == sha))


def find_by_source(source_path: str, size: int, mtime: datetime) -> Optional[db.ModelVersion]:
    """An already-imported training-folder file with this path, size and
    modification time (lets the watcher skip re-hashing unchanged files)."""
    with db.session_scope() as s:
        rows = s.scalars(select(db.ModelVersion).where(
            db.ModelVersion.source_path == source_path, db.ModelVersion.size_bytes == size)).all()
    for r in rows:
        if r.source_mtime and abs(_aware(r.source_mtime).timestamp() - mtime.timestamp()) < 1.0:
            return r
    return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def register(
    data: bytes,
    version: str,
    notes: str = "",
    created_by: str | None = None,
    source: str = "upload",
    source_path: str | None = None,
    source_mtime: datetime | None = None,
) -> db.ModelVersion:
    if not _looks_like_checkpoint(data):
        raise ValueError("Not a PyTorch checkpoint (expected a file written by torch.save())")
    sha = hashlib.sha256(data).hexdigest()
    with db.session_scope() as s:
        if s.scalar(select(db.ModelVersion).where(db.ModelVersion.sha256 == sha)):
            raise ValueError("This exact checkpoint is already registered")
        if s.scalar(select(db.ModelVersion).where(db.ModelVersion.version == version)):
            raise ValueError(f"Version '{version}' already exists")
    key = f"models/{sha}.pt"
    store().put(key, data)
    with db.session_scope() as s:
        row = db.ModelVersion(
            version=version, sha256=sha, checkpoint_id=_checkpoint_id(data), object_key=key,
            size_bytes=len(data), status="pending", notes=notes, created_by=created_by,
            source=source, source_path=source_path, source_mtime=source_mtime,
        )
        s.add(row)
        s.flush()
        model_id = row.id
    events.enqueue({"type": "validate_model", "model_id": model_id})
    audit.record("model.register", created_by, str(model_id), version=version, sha256=sha)
    return get(model_id)


def bootstrap_from_weights_dir() -> None:
    """First start: if nothing is active yet and weights/classifier.pt
    exists, register it and make it the active (pinned) model. This is the
    one activation allowed while locked — it establishes the pin."""
    with db.session_scope() as s:
        if s.scalar(select(db.ModelVersion).where(db.ModelVersion.is_active.is_(True))):
            return
    path: Path = CLASSIFIER_WEIGHTS_PATH
    if not path.exists():
        logger.warning("No active model and no %s — analyses will fail until a model is registered.", path)
        return
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    with db.session_scope() as s:
        row = s.scalar(select(db.ModelVersion).where(db.ModelVersion.sha256 == sha))
    if row is None:
        key = f"models/{sha}.pt"
        store().put(key, data)
        with db.session_scope() as s:
            row = db.ModelVersion(
                version=MODEL_VERSION, sha256=sha, checkpoint_id=_checkpoint_id(data), object_key=key,
                size_bytes=len(data), status="pending", notes="Bootstrapped from weights/classifier.pt",
                externally_validated=MODEL_EXTERNALLY_VALIDATED, source="bootstrap",
            )
            s.add(row)
    with db.session_scope() as s:
        s.execute(update(db.ModelVersion).where(db.ModelVersion.sha256 == sha).values(
            is_active=True, activated_at=datetime.now(timezone.utc)))
    audit.record("model.bootstrap", None, sha, version=MODEL_VERSION)
    logger.info("Pinned %s (sha256 %s…) as the active model", MODEL_VERSION, sha[:12])


def activate(model_id: int, user_id: str) -> db.ModelVersion:
    if MODEL_LOCKED:
        raise ModelLockedError(
            "The active model is locked (MODEL_LOCKED=true). Changing it requires an operator "
            "to unlock the deployment and restart — deliberately not possible from the UI."
        )
    return _set_active(model_id, user_id, "model.activate")


def _set_active(model_id: int, actor: str | None, action: str) -> db.ModelVersion:
    with db.session_scope() as s:
        row = s.get(db.ModelVersion, model_id)
        if row is None:
            raise LookupError("Model not found")
        if row.status != "validated":
            raise ValueError(f"Only validated models can be activated (this one is '{row.status}')")
        previous = s.scalar(select(db.ModelVersion).where(db.ModelVersion.is_active.is_(True)))
        s.execute(update(db.ModelVersion).values(is_active=False))
        row.is_active = True
        row.activated_at = datetime.now(timezone.utc)
        prev_version = previous.version if previous else None
    audit.record(action, actor, str(model_id), previous=prev_version)
    return get(model_id)


def on_watch_model_validated(model_id: int) -> Optional[int]:
    """Auto-activation policy for checkpoints imported from the training
    folder, applied once, right after a model passes validation:

      * nothing active yet  -> pin it if it is the manuscript checkpoint
                               (EXPECTED_CHECKPOINT_ID); when unlocked, pin
                               it if it is the newest validated import
      * something active    -> only when unlocked: switch if this is the
                               newest validated import (by file time)

    Because it runs only on a NEW validation, an administrator's manual
    rollback stays in place until a newer file appears in the folder.
    Returns the activated model id, or None."""
    if not MODEL_AUTO_ACTIVATE:
        return None
    row = get(model_id)
    if row is None or row.status != "validated" or row.source != "watch-folder":
        return None
    with db.session_scope() as s:
        newest = s.scalars(
            select(db.ModelVersion)
            .where(db.ModelVersion.source == "watch-folder", db.ModelVersion.status == "validated")
            .order_by(db.ModelVersion.source_mtime.desc(), db.ModelVersion.id.desc())
        ).first()
    is_newest = newest is not None and newest.id == row.id
    active = get_active()

    if active is None:
        if row.checkpoint_id == EXPECTED_CHECKPOINT_ID or (not MODEL_LOCKED and is_newest):
            _set_active(row.id, None, "model.auto_pin")
            logger.info("Auto-pinned %s from the training folder", row.version)
            return row.id
        return None
    if MODEL_LOCKED:
        logger.info("New checkpoint %s validated but not activated: MODEL_LOCKED=true", row.version)
        return None
    if is_newest and active.id != row.id:
        _set_active(row.id, None, "model.auto_activate")
        logger.info("Auto-activated newest training checkpoint %s (was %s)", row.version, active.version)
        return row.id
    return None


def get(model_id: int) -> Optional[db.ModelVersion]:
    with db.session_scope() as s:
        return s.get(db.ModelVersion, model_id)


def get_active() -> Optional[db.ModelVersion]:
    with db.session_scope() as s:
        return s.scalar(select(db.ModelVersion).where(db.ModelVersion.is_active.is_(True)))


def list_all() -> list[db.ModelVersion]:
    with db.session_scope() as s:
        return list(s.scalars(select(db.ModelVersion).order_by(db.ModelVersion.created_at.desc())).all())


def mark(model_id: int, status: str, detail: dict) -> None:
    with db.session_scope() as s:
        row = s.get(db.ModelVersion, model_id)
        if row:
            row.status = status
            row.validation_detail = detail


def worker_state() -> tuple[bool, Optional[dict]]:
    r = events.client()
    online = bool(r.exists(events.HEARTBEAT_KEY))
    raw = r.get(events.ACTIVE_MODEL_KEY)
    return online, (json.loads(raw) if raw else None)
