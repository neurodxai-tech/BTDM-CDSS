from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.access import require_owner
from app.deps import get_current_user, require_role
from app.schemas import ClinicalReport, ReportDraft, UpdateReportRequest
from app.services import audit, report_draft, storage
from app.services.auth import UserRecord

router = APIRouter(prefix="/api/v1/studies", tags=["reports"])


@router.get("/{study_id}/report", response_model=ClinicalReport)
async def get_report(study_id: str, user: UserRecord = Depends(get_current_user)):
    require_owner(study_id, user)
    report = storage.get_report(study_id)
    if not report:
        raise HTTPException(404, "No report available for this study yet")
    return report


@router.get("/{study_id}/report/draft", response_model=ReportDraft)
async def get_report_draft(
    study_id: str,
    user: UserRecord = Depends(require_role("radiologist")),
):
    """Returns SUGGESTED indication/impression text composed from the
    real analysis data — does not save or modify anything. The frontend
    populates the editable fields with this; the radiologist still has
    to review, edit, and PATCH .../report (and the impression still can't
    be blank at sign time) exactly as if they'd typed it from scratch."""
    require_owner(study_id, user)
    result = storage.get_analysis(study_id)
    if not result:
        raise HTTPException(404, "No analysis available for this study yet")
    return ReportDraft(
        clinical_indication=report_draft.generate_indication_draft(result),
        impression=report_draft.generate_impression_draft(result),
    )


@router.patch("/{study_id}/report", response_model=ClinicalReport)
async def update_report(
    study_id: str,
    body: UpdateReportRequest,
    user: UserRecord = Depends(require_role("radiologist")),
):
    """Edits the radiologist-owned sections (clinical indication,
    impression, recommendation) — findings/technique stay machine-
    generated. Only works while the report is still preliminary; once
    signed it's frozen (see storage.update_report)."""
    require_owner(study_id, user)
    updated = storage.update_report(
        study_id,
        clinical_indication=body.clinical_indication,
        impression=body.impression,
        recommendation=body.recommendation,
    )
    if not updated:
        raise HTTPException(404, "No editable (preliminary) report found for this study")
    audit.record("report.edit", user.id, study_id)
    return updated


@router.post("/{study_id}/report/sign", response_model=ClinicalReport)
async def sign_report(study_id: str, user: UserRecord = Depends(require_role("radiologist"))):
    # `signed_by` is the AUTHENTICATED user's name, not a client-supplied
    # field — a client could previously claim to be signing as anyone.
    require_owner(study_id, user)
    report = storage.get_report(study_id)
    if not report:
        raise HTTPException(404, "No report available to sign for this study")
    if report.status.value == "signed":
        raise HTTPException(409, "Report is already signed")
    if not report.impression.strip():
        raise HTTPException(
            400,
            "Cannot sign a report with no impression — add your clinical impression "
            "before signing (PATCH .../report). Signing the AI's findings verbatim "
            "without a radiologist's own conclusion isn't a real review.",
        )

    signed = storage.sign_report(study_id, user.name)
    if not signed:
        raise HTTPException(409, "Report could not be signed (missing or already signed)")
    audit.record("report.sign", user.id, study_id)
    return signed
