from __future__ import annotations

import io
import logging
import zipfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.config import (
    MAX_FILE_BYTES,
    MAX_REQUEST_BYTES,
    MAX_ZIP_COMPRESSION_RATIO,
    MAX_ZIP_ENTRIES,
    MAX_ZIP_UNCOMPRESSED_BYTES,
)
from app.access import require_owner
from app.deps import get_current_user, require_role
from app.schemas import AnalysisResult, JobStatus, Study, StudyStatus, TriggerAnalysisResponse
from app.services import audit, events, storage
from app.services.deid import UidMapper, deidentify_dicom
from app.services.auth import UserRecord

logger = logging.getLogger("btdm.routers.studies")

router = APIRouter(prefix="/api/v1/studies", tags=["studies"])

# DICOM SOP Class / Modality values that never carry pixel data — real
# PACS exports of a full study routinely bundle these alongside actual
# images (localizer summaries, presentation states, structured reports),
# and none of them can be run through an image classifier.
_NON_IMAGE_MODALITIES = {"PR", "SR", "KO", "DOC", "AU", "SEG"}


def _infer_kind(filename: str, content_type: str | None, file_bytes: bytes) -> str:
    """
    Extension-based detection alone misses a LOT of real DICOM files:
    exports from scanners/PACS very commonly have no extension at all
    (or a plain numeric name like IMG00001), since DICOM doesn't
    require one. Falling back to that would silently misroute them to
    the PNG/JPG path, where PIL can't decode raw DICOM binary and the
    whole analysis fails with no clear error.

    Real DICOM files (after the "Part 10" preamble) have the 4-byte
    magic string "DICM" at byte offset 128 — check that directly,
    regardless of filename, before falling back to extension checks.
    """
    if len(file_bytes) >= 132 and file_bytes[128:132] == b"DICM":
        return "dicom"

    lower = filename.lower()
    if lower.endswith(".dcm"):
        return "dicom"
    if lower.endswith(".nii") or lower.endswith(".nii.gz"):
        return "nifti"
    return "standard"


def _is_zip(filename: str, file_bytes: bytes) -> bool:
    if filename.lower().endswith(".zip"):
        return True
    return len(file_bytes) >= 4 and file_bytes[:4] == b"PK\x03\x04"


def _validate_dicom_has_pixels(file_bytes: bytes) -> tuple[bool, str | None]:
    """
    Returns (is_valid_image, skip_reason). A real full-study PACS/scanner
    export ALWAYS mixes non-image DICOM objects in with the actual
    images — Presentation States, Structured Reports, key-object notes —
    none of which have pixel data to classify. Silently trying to run
    these through the model previously crashed the ENTIRE study's
    analysis (see run_analysis's per-image error handling fix) rather
    than just skipping the individual bad file; this check catches most
    of them at upload time instead, with a clear reason surfaced to the
    user.
    """
    try:
        import pydicom

        # NOTE: stop_before_pixels=True must NOT be used here — that flag
        # means pydicom stops parsing BEFORE it reaches the PixelData
        # element, so "PixelData" in ds would be False for every real
        # image too, not just the ones that actually lack pixel data.
        # This reads the raw PixelData bytes (fast — no decompression
        # happens until .pixel_array is actually accessed later), which
        # is what correctly distinguishes real images from PR/SR objects.
        ds = pydicom.dcmread(io.BytesIO(file_bytes), force=True)
    except Exception as exc:
        return False, f"could not parse as DICOM ({exc})"

    modality = getattr(ds, "Modality", None)
    if modality in _NON_IMAGE_MODALITIES:
        return False, f"no image data — Modality '{modality}' (e.g. presentation state / structured report, not a scan)"

    if "PixelData" not in ds and "FloatPixelData" not in ds and "DoubleFloatPixelData" not in ds:
        return False, "no pixel data in this DICOM object"

    return True, None


def _validate_standard_image(file_bytes: bytes) -> tuple[bool, str | None]:
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(file_bytes))
        img.verify()
        return True, None
    except Exception as exc:
        return False, f"could not decode as an image ({exc})"


def _expand_zip(filename: str, file_bytes: bytes, depth: int = 0) -> list[tuple[str, bytes]]:
    """
    Expands one .zip archive into its contents (recursing through
    subdirectories — real PACS exports commonly organize files as
    SERIES_FOLDER/IMAGE_FILE), skipping directory entries and obvious
    junk (__MACOSX, .DS_Store).

    Guarded against zip bombs: entry count, total declared uncompressed
    size and per-entry compression ratio are checked from the central
    directory BEFORE anything is decompressed, and nested zips are
    expanded at most one level deep.
    """
    out: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_ZIP_ENTRIES:
                raise HTTPException(413, f"Archive has {len(infos)} files; limit is {MAX_ZIP_ENTRIES}")
            total = sum(i.file_size for i in infos)
            if total > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise HTTPException(413, f"Archive expands to {total / 1e6:.0f} MB; limit is {MAX_ZIP_UNCOMPRESSED_BYTES / 1e6:.0f} MB")
            for info in infos:
                name = info.filename
                base = name.rsplit("/", 1)[-1]
                if base.startswith(".") or "__MACOSX" in name:
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    raise HTTPException(413, f"Archive entry {base} exceeds the per-file limit")
                if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
                    raise HTTPException(413, "Archive rejected: suspicious compression ratio")
                try:
                    data = zf.read(info)
                except Exception as exc:
                    logger.warning("Skipping unreadable zip entry %s: %s", base, exc)
                    continue
                if _is_zip(base, data):
                    if depth >= 1:
                        logger.warning("Skipping nested archive %s (max depth 1)", base)
                        continue
                    out.extend(_expand_zip(base, data, depth + 1))
                else:
                    out.append((name, data))
    except zipfile.BadZipFile:
        logger.warning("File %s looked like a zip but failed to open as one", filename)
    return out


@router.post("", response_model=Study, status_code=201)
async def upload_study(
    files: list[UploadFile] = File(..., description="One or more images/slices, or a .zip archive of a full study"),
    patient_alias: str = Form(..., description="De-identified display alias — never real MRN/name"),
    sequences: str = Form("T1", description="Comma-separated sequence labels, e.g. 'T1c,T2,FLAIR'"),
    user: UserRecord = Depends(require_role("technologist", "radiologist", "referring_physician")),  # matches frontend RBAC "upload_study"
):
    if not files:
        raise HTTPException(400, "No files uploaded")

    # Step 1: read everything, expanding any .zip archives into their
    # contents (a real PACS/scanner export is very commonly a single zip
    # containing a SERIES_FOLDER/IMAGE_FILE tree).
    raw_candidates: list[tuple[str, bytes]] = []
    request_bytes = 0
    for f in files:
        if f.size is not None and f.size > MAX_FILE_BYTES:
            raise HTTPException(413, f"{f.filename} exceeds the {MAX_FILE_BYTES / 1e6:.0f} MB per-file limit")
        file_bytes = await f.read()
        request_bytes += len(file_bytes)
        if len(file_bytes) > MAX_FILE_BYTES or request_bytes > MAX_REQUEST_BYTES:
            raise HTTPException(413, f"Upload exceeds the {MAX_REQUEST_BYTES / 1e6:.0f} MB request limit")
        if not file_bytes:
            continue
        name = f.filename or "upload"
        if _is_zip(name, file_bytes):
            expanded = _expand_zip(name, file_bytes)
            logger.info("Expanded zip %s into %d candidate files", name, len(expanded))
            raw_candidates.extend(expanded)
        else:
            raw_candidates.append((name, file_bytes))

    if not raw_candidates:
        raise HTTPException(400, "No files found (all uploads were empty, or the zip contained nothing usable)")

    # Step 2: classify + validate each candidate. A real full-study export
    # ALWAYS mixes non-image DICOM objects (presentation states,
    # structured reports, key-object notes) in with the actual scans —
    # these are filtered out HERE, with a clear reason, rather than being
    # allowed through to crash the whole study's analysis later.
    loaded: list[tuple[bytes, str]] = []
    skip_reasons: list[str] = []
    uid_mapper = UidMapper()
    for name, file_bytes in raw_candidates:
        kind = _infer_kind(name, None, file_bytes)
        name = name.rsplit("/", 1)[-1]  # PACS folder names often contain patient names

        if kind == "dicom":
            valid, reason = _validate_dicom_has_pixels(file_bytes)
            if valid:
                try:
                    file_bytes = deidentify_dicom(file_bytes, uid_mapper)
                except Exception as exc:
                    valid, reason = False, f"could not be de-identified ({exc})"

        elif kind == "standard":
            valid, reason = _validate_standard_image(file_bytes)
        else:  # nifti — trust extension, header-only validation is more involved
            valid, reason = True, None

        if valid:
            loaded.append((file_bytes, kind))
        else:
            skip_reasons.append(f"{name}: {reason}")
            logger.info("Skipping %s: %s", name, reason)

    if not loaded:
        raise HTTPException(
            400,
            f"None of the {len(raw_candidates)} file(s) were usable images. "
            f"First few reasons: {'; '.join(skip_reasons[:5])}",
        )

    if skip_reasons:
        logger.info(
            "Study upload: %d of %d files accepted, %d skipped (non-image objects)",
            len(loaded), len(raw_candidates), len(skip_reasons),
        )

    study = storage.create_study(
        patient_alias=patient_alias,
        sequences=[s.strip() for s in sequences.split(",") if s.strip()],
        files=loaded,
        skip_reasons=skip_reasons,
        uploaded_by=user.id,
    )
    audit.record("study.upload", user.id, study.id, images=len(loaded), skipped=len(skip_reasons))
    return study


@router.get("", response_model=list[Study])
async def list_studies(user: UserRecord = Depends(get_current_user)):
    """The caller's own worklist only."""
    return storage.list_studies(owner_id=user.id)


@router.get("/{study_id}", response_model=Study)
async def get_study(study_id: str, user: UserRecord = Depends(get_current_user)):
    require_owner(study_id, user)
    study = storage.get_study(study_id)
    if not study:
        raise HTTPException(404, "Study not found")
    return study


@router.post("/{study_id}/analyze", response_model=TriggerAnalysisResponse, status_code=202)
async def analyze_study(study_id: str, user: UserRecord = Depends(require_role("technologist", "radiologist", "referring_physician"))):
    require_owner(study_id, user)
    study = storage.get_study(study_id)
    if not study:
        raise HTTPException(404, "Study not found")
    if study.status == StudyStatus.report_signed:
        raise HTTPException(409, "This study's report is signed; re-analysis would overwrite a signed record")
    existing = storage.active_job(study_id)
    if existing:
        return TriggerAnalysisResponse(job_id=existing.id)  # already queued/running — don't duplicate work
    if not study.image_ids:
        raise HTTPException(400, "No image data stored for this study")

    job_id = storage.create_job(study_id, user.id)
    events.enqueue({"type": "analyze", "job_id": job_id, "study_id": study_id})
    events.publish_study(study_id, type="status", status="queued", jobId=job_id)
    audit.record("study.analyze", user.id, study_id, job_id=job_id)
    return TriggerAnalysisResponse(job_id=job_id)


@router.get("/{study_id}/job", response_model=JobStatus)
async def get_job(study_id: str, user: UserRecord = Depends(get_current_user)):
    require_owner(study_id, user)
    job = storage.latest_job(study_id)
    if not job:
        raise HTTPException(404, "No analysis has been requested for this study")
    return JobStatus(job_id=job.id, status=job.status, error=job.error, duration_ms=job.duration_ms)


@router.get("/{study_id}/events")
async def study_events(study_id: str, user: UserRecord = Depends(get_current_user)):
    """Server-Sent Events: pushes {type:"status", status:"queued|running|
    done|failed"} the moment the worker changes state, so the browser does
    not have to poll. Sends the current state first, a heartbeat every 15 s,
    and closes after a terminal state. The frontend reads this with fetch()
    (not EventSource) so it can send the Authorization header."""
    import asyncio
    import json

    import redis.asyncio as aioredis

    from app.config import REDIS_URL

    require_owner(study_id, user)

    async def stream():
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(events.study_channel(study_id))
        try:
            # Subscribe first, then read current state: no event can slip between.
            job = storage.latest_job(study_id)
            current = job.status if job else "none"
            yield f"data: {json.dumps({'type': 'status', 'status': current, 'jobId': job.id if job else None, 'error': job.error if job else None})}\n\n"
            if current in ("done", "failed", "none"):
                return
            idle = 0.0
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    idle = 0.0
                    yield f"data: {msg['data']}\n\n"
                    if json.loads(msg["data"]).get("status") in ("done", "failed"):
                        return
                else:
                    idle += 1.0
                    if idle >= 15:
                        idle = 0.0
                        yield ": heartbeat\n\n"
                    await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
            await r.aclose()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{study_id}/results", response_model=AnalysisResult)
async def get_results(study_id: str, user: UserRecord = Depends(get_current_user)):
    require_owner(study_id, user)
    result = storage.get_analysis(study_id)
    if not result:
        raise HTTPException(404, "No analysis available for this study yet")
    return result


@router.get("/{study_id}/gradcam")
async def get_gradcam(study_id: str, image_id: str | None = None, mode: str = "both", user: UserRecord = Depends(get_current_user)):
    require_owner(study_id, user)
    """Defaults to the primary/consensus image if image_id is omitted —
    pass image_id explicitly to view a specific slice's own attention map
    (see the per-image results in GET .../results).

    mode: 'plain' | 'heatmap' | 'region' | 'both' (default) — all 4 are
    pre-generated at analysis time from the same underlying data, so
    switching modes is just picking which cached PNG to serve, no
    re-computation."""
    if mode not in ("plain", "heatmap", "region", "both"):
        raise HTTPException(400, "mode must be one of: plain, heatmap, region, both")

    target_image_id = image_id
    if not target_image_id:
        result = storage.get_analysis(study_id)
        target_image_id = result.primary_image_id if result else None

    png = storage.get_gradcam_png(study_id, target_image_id, mode)
    if not png:
        raise HTTPException(404, "No Grad-CAM available (requires loaded weights + a completed analysis)")
    return Response(content=png, media_type="image/png")


@router.delete("/{study_id}", status_code=204)
async def delete_study(
    study_id: str,
    user: UserRecord = Depends(require_role("technologist", "radiologist", "referring_physician")),
):
    require_owner(study_id, user)
    existed = storage.delete_study(study_id)
    if not existed:
        raise HTTPException(404, "Study not found")
    audit.record("study.delete", user.id, study_id)
    return None
