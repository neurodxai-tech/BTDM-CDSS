"""
Persistence layer: PostgreSQL for records, object storage for bytes.

Same function names the routers have always used, plus job and Grad-CAM asset helpers for the worker.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, select

from app import db
from app.schemas import AnalysisResult, ClinicalReport, ReportStatus, Study, StudyStatus
from app.services.objectstore import store


def _to_schema(row: db.Study) -> Study:
    return Study(
        id=row.id,
        patient_alias=row.patient_alias,
        modality=row.modality,
        sequences=list(row.sequences or []),
        uploaded_at=row.uploaded_at,
        status=StudyStatus(row.status),
        ohif_study_instance_uid=row.ohif_study_instance_uid,
        image_ids=[img.id for img in row.images],
        skipped_file_count=row.skipped_file_count,
        skip_reasons=list(row.skip_reasons or []),
    )


# ── Studies & images ───────────────────────────────────────────────────────
def create_study(
    patient_alias: str,
    sequences: list[str],
    files: list[tuple[bytes, str]],  # (file_bytes, kind)
    skip_reasons: list[str] | None = None,
    uploaded_by: str | None = None,
) -> Study:
    study_id = f"study-{uuid.uuid4().hex[:12]}"
    skip_reasons = skip_reasons or []
    objects = store()
    try:
        with db.session_scope() as s:
            row = db.Study(
                id=study_id,
                patient_alias=patient_alias,
                modality="MR",
                sequences=sequences,
                status=StudyStatus.uploaded.value,
                ohif_study_instance_uid=f"1.2.826.0.1.3680043.8.1055.1.{uuid.uuid4().int % (10**20)}",
                skipped_file_count=len(skip_reasons),
                skip_reasons=skip_reasons[:20],
                uploaded_by=uploaded_by,
            )
            for pos, (file_bytes, kind) in enumerate(files):
                image_id = f"img-{uuid.uuid4().hex[:12]}"
                key = f"studies/{study_id}/{image_id}"
                objects.put(key, file_bytes)
                row.images.append(
                    db.StudyImage(
                        id=image_id, position=pos, kind=kind, object_key=key,
                        size_bytes=len(file_bytes), sha256=hashlib.sha256(file_bytes).hexdigest(),
                    )
                )
            s.add(row)
            s.flush()
            return _to_schema(row)
    except Exception:
        objects.delete_prefix(f"studies/{study_id}/")
        raise


def list_studies(limit: int = 500, owner_id: str | None = None) -> list[Study]:
    """Newest first. With owner_id, only that user's studies (their worklist)."""
    with db.session_scope() as s:
        q = select(db.Study)
        if owner_id is not None:
            q = q.where(db.Study.uploaded_by == owner_id)
        rows = s.scalars(q.order_by(db.Study.uploaded_at.desc()).limit(limit)).all()
        return [_to_schema(r) for r in rows]


def count_studies(owner_id: str) -> int:
    from sqlalchemy import func

    with db.session_scope() as s:
        return s.scalar(select(func.count()).select_from(db.Study).where(db.Study.uploaded_by == owner_id)) or 0


def study_ids_of(owner_id: str) -> list[str]:
    with db.session_scope() as s:
        return list(s.scalars(select(db.Study.id).where(db.Study.uploaded_by == owner_id)).all())


_MISSING = object()


def study_owner(study_id: str):
    """The uploading user's id, None if unknown, or _MISSING if no such study."""
    with db.session_scope() as s:
        row = s.get(db.Study, study_id)
        return _MISSING if row is None else row.uploaded_by


def get_study(study_id: str) -> Optional[Study]:
    with db.session_scope() as s:
        row = s.get(db.Study, study_id)
        return _to_schema(row) if row else None


def get_images(study_id: str) -> list[tuple[str, bytes, str]]:
    """[(image_id, file_bytes, kind), ...] in upload order."""
    with db.session_scope() as s:
        rows = s.scalars(
            select(db.StudyImage).where(db.StudyImage.study_id == study_id).order_by(db.StudyImage.position)
        ).all()
        refs = [(r.id, r.object_key, r.kind) for r in rows]
    objects = store()
    out = []
    for image_id, key, kind in refs:
        data = objects.get(key)
        if data is not None:
            out.append((image_id, data, kind))
    return out


def set_status(study_id: str, status: StudyStatus) -> None:
    with db.session_scope() as s:
        row = s.get(db.Study, study_id)
        if row:
            row.status = status.value


def delete_study(study_id: str) -> bool:
    with db.session_scope() as s:
        row = s.get(db.Study, study_id)
        if not row:
            return False
        s.delete(row)
    objects = store()
    objects.delete_prefix(f"studies/{study_id}/")
    objects.delete_prefix(f"gradcam/{study_id}/")
    return True


# ── Jobs ───────────────────────────────────────────────────────────────────
def create_job(study_id: str, requested_by: str | None) -> str:
    job_id = f"job-{uuid.uuid4().hex[:16]}"
    with db.session_scope() as s:
        s.add(db.AnalysisJob(id=job_id, study_id=study_id, status="queued", requested_by=requested_by))
        row = s.get(db.Study, study_id)
        if row:
            row.status = StudyStatus.analyzing.value
    return job_id


def active_job(study_id: str) -> Optional[db.AnalysisJob]:
    with db.session_scope() as s:
        return s.scalars(
            select(db.AnalysisJob)
            .where(db.AnalysisJob.study_id == study_id, db.AnalysisJob.status.in_(["queued", "running"]))
            .order_by(db.AnalysisJob.created_at.desc())
        ).first()


def latest_job(study_id: str) -> Optional[db.AnalysisJob]:
    with db.session_scope() as s:
        return s.scalars(
            select(db.AnalysisJob).where(db.AnalysisJob.study_id == study_id).order_by(db.AnalysisJob.created_at.desc())
        ).first()


def update_job(job_id: str, **fields) -> None:
    with db.session_scope() as s:
        job = s.get(db.AnalysisJob, job_id)
        if job:
            for k, v in fields.items():
                setattr(job, k, v)


# ── Analyses & Grad-CAM ────────────────────────────────────────────────────
def save_analysis(
    result: AnalysisResult,
    job_id: str | None = None,
    model_version_id: int | None = None,
    model_sha256: str | None = None,
) -> None:
    doc = result.model_dump(mode="json")
    with db.session_scope() as s:
        row = s.get(db.Analysis, result.study_id)
        if row is None:
            row = db.Analysis(study_id=result.study_id)
            s.add(row)
        row.job_id = job_id
        row.model_version_id = model_version_id
        row.model_sha256 = model_sha256
        row.predicted_class = result.predicted_class.value
        row.confidence = result.calibrated_confidence
        row.below_confidence_gate = result.below_confidence_gate
        row.result = doc
        row.created_at = datetime.now(timezone.utc)


def get_analysis(study_id: str) -> Optional[AnalysisResult]:
    with db.session_scope() as s:
        row = s.get(db.Analysis, study_id)
        return AnalysisResult.model_validate(row.result) if row else None


def save_gradcam_assets(study_id: str, variants: dict[str, dict[str, bytes]]) -> None:
    """variants: {image_id: {mode: png_bytes}}. Replaces any previous set."""
    objects = store()
    objects.delete_prefix(f"gradcam/{study_id}/")
    with db.session_scope() as s:
        s.execute(delete(db.GradcamAsset).where(db.GradcamAsset.study_id == study_id))
        for image_id, modes in variants.items():
            for mode, png in modes.items():
                key = f"gradcam/{study_id}/{image_id}/{mode}.png"
                objects.put(key, png, "image/png")
                s.add(db.GradcamAsset(study_id=study_id, image_id=image_id, mode=mode, object_key=key))


def get_gradcam_png(study_id: str, image_id: str | None, mode: str) -> Optional[bytes]:
    with db.session_scope() as s:
        q = select(db.GradcamAsset).where(db.GradcamAsset.study_id == study_id).order_by(db.GradcamAsset.id)
        if image_id:
            q = q.where(db.GradcamAsset.image_id == image_id)
        assets = s.scalars(q).all()
        if not assets:
            return None
        chosen = image_id or assets[0].image_id
        by_mode = {a.mode: a.object_key for a in assets if a.image_id == chosen}
        key = by_mode.get(mode) or by_mode.get("both")
    return store().get(key) if key else None


# ── Reports ────────────────────────────────────────────────────────────────
def save_report(report: ClinicalReport) -> None:
    with db.session_scope() as s:
        row = s.get(db.Report, report.study_id)
        if row is None:
            row = db.Report(study_id=report.study_id)
            s.add(row)
        row.status = report.status.value
        row.signed_by = report.signed_by
        row.signed_at = report.signed_at
        row.document = report.model_dump(mode="json")


def get_report(study_id: str) -> Optional[ClinicalReport]:
    with db.session_scope() as s:
        row = s.get(db.Report, study_id)
        return ClinicalReport.model_validate(row.document) if row else None


def update_report(
    study_id: str,
    clinical_indication: Optional[str] = None,
    impression: Optional[str] = None,
    recommendation: Optional[str] = None,
) -> Optional[ClinicalReport]:
    """Radiologist-editable fields only. Returns None if there is no report
    or it is already signed (signed reports are frozen)."""
    existing = get_report(study_id)
    if existing is None or existing.status != ReportStatus.preliminary:
        return None

    updates: dict = {}
    if clinical_indication is not None:
        updates["clinical_indication"] = clinical_indication
    if impression is not None:
        updates["impression"] = impression
    if recommendation is not None:
        updates["recommendation"] = recommendation
    updated = existing.model_copy(update=updates)

    parts = []
    if updated.clinical_indication:
        parts.append(f"Indication: {updated.clinical_indication}")
    parts.append(f"Technique: {updated.technique}")
    parts.append(f"Findings: {updated.findings}")
    if updated.impression:
        parts.append(f"Impression: {updated.impression}")
    if updated.recommendation:
        parts.append(f"Recommendation: {updated.recommendation}")
    if not updated.impression:
        parts.append("Correlate clinically; not a final diagnosis until reviewed and co-signed by a radiologist.")
    updated = updated.model_copy(update={"summary": " ".join(parts)})
    save_report(updated)
    return updated


def sign_report(study_id: str, signed_by: str) -> Optional[ClinicalReport]:
    existing = get_report(study_id)
    if existing is None or existing.status == ReportStatus.signed:
        return None
    signed = existing.model_copy(
        update={"status": ReportStatus.signed, "signed_by": signed_by, "signed_at": datetime.now(timezone.utc)}
    )
    save_report(signed)
    set_status(study_id, StudyStatus.report_signed)
    return signed
