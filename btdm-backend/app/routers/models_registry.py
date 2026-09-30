from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.config import EXPECTED_CHECKPOINT_ID, MODEL_AUTO_ACTIVATE, MODEL_LOCKED
from app.deps import get_current_user, require_role
from app.schemas import ModelRegistryEntry, ModelRegistryState
from app.services import model_registry
from app.services.model_watcher import watcher_state
from app.services.auth import UserRecord

router = APIRouter(prefix="/api/v1/models", tags=["models"])

MAX_CHECKPOINT_BYTES = 500 * 1024 * 1024


def _entry(row) -> ModelRegistryEntry:
    return ModelRegistryEntry(
        id=row.id, version=row.version, sha256=row.sha256, checkpoint_id=row.checkpoint_id,
        size_bytes=row.size_bytes, status=row.status, validation_detail=row.validation_detail,
        is_active=row.is_active, externally_validated=row.externally_validated, notes=row.notes,
        source=row.source, source_path=row.source_path, source_mtime=row.source_mtime,
        created_at=row.created_at, activated_at=row.activated_at,
    )


@router.get("", response_model=ModelRegistryState)
async def list_models(_user: UserRecord = Depends(get_current_user)):
    online, loaded = model_registry.worker_state()
    return ModelRegistryState(
        locked=MODEL_LOCKED,
        expected_checkpoint_id=EXPECTED_CHECKPOINT_ID,
        worker_online=online,
        worker_loaded_model=loaded,
        auto_activate=MODEL_AUTO_ACTIVATE,
        training_folder=watcher_state(),
        models=[_entry(r) for r in model_registry.list_all()],
    )


@router.post("", response_model=ModelRegistryEntry, status_code=201)
async def register_model(
    file: UploadFile = File(..., description="torch.save(model.state_dict()) checkpoint"),
    version: str = Form(..., min_length=1, max_length=100),
    notes: str = Form(""),
    user: UserRecord = Depends(require_role("admin")),
):
    """Uploads and registers a checkpoint. The worker validates it in the
    background (strict key match, parameter fingerprint, smoke test). This is
    allowed while locked — only activation is blocked."""
    if file.size is not None and file.size > MAX_CHECKPOINT_BYTES:
        raise HTTPException(413, "Checkpoint too large")
    data = await file.read()
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise HTTPException(413, "Checkpoint too large")
    try:
        row = model_registry.register(data, version.strip(), notes, user.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return _entry(row)


@router.post("/{model_id}/activate", response_model=ModelRegistryEntry)
async def activate_model(model_id: int, user: UserRecord = Depends(require_role("admin"))):
    try:
        row = model_registry.activate(model_id, user.id)
    except model_registry.ModelLockedError as exc:
        raise HTTPException(423, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return _entry(row)
