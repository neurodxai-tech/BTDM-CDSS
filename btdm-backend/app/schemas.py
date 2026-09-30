"""
Pydantic response/request models. These are written to match
frontend/lib/types.ts field-for-field (camelCase via `alias`) so the
Next.js frontend's lib/api.ts can point NEXT_PUBLIC_USE_MOCK_API=false at
this service with zero type changes on the client.
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Role(str, Enum):
    radiologist = "radiologist"
    referring_physician = "referring_physician"
    technologist = "technologist"
    admin = "admin"


class TumourClass(str, Enum):
    glioma = "glioma"
    meningioma = "meningioma"
    pituitary = "pituitary"
    notumor = "notumor"


class StudyStatus(str, Enum):
    uploaded = "uploaded"
    preprocessing = "preprocessing"
    analyzing = "analyzing"
    analyzed = "analyzed"
    report_pending_signoff = "report_pending_signoff"
    report_signed = "report_signed"
    failed = "failed"


class Study(CamelModel):
    id: str
    patient_alias: str
    modality: str = "MR"
    sequences: list[str]
    uploaded_at: datetime
    status: StudyStatus
    ohif_study_instance_uid: str
    image_ids: list[str] = []  # one or more images/slices belonging to this study
    skipped_file_count: int = 0
    skip_reasons: list[str] = []  # human-readable, one per skipped file (or a truncated summary)


class ClassProbabilities(CamelModel):
    glioma: float
    meningioma: float
    pituitary: float
    notumor: float


class SegmentationVolumes(CamelModel):
    enhancing_tumour_cm3: Optional[float] = None
    edema_cm3: Optional[float] = None
    necrotic_core_cm3: Optional[float] = None


class LesionMeasurement(CamelModel):
    """One detected region's real geometry — see
    app/models/weak_localization.py for exactly how these are computed
    and what they are NOT (a validated measurement)."""

    lesion_id: str  # "L1", "L2", ... ranked largest first
    area_cm2: float
    max_diameter_mm: float
    width_mm: float
    height_mm: float
    volume_cm3_estimate: Optional[float] = None
    bounding_box_normalized: list[float]  # [x0,y0,x1,y1] in [0,1]
    centroid_normalized: list[float]  # [x,y] in [0,1]
    location_description: str
    boundary_refined: bool = False


class WeakLocalization(CamelModel):
    """CAM-guided heuristic region estimate — NOT a trained/validated
    segmentation, NOT anatomically-validated measurement. See
    app/models/weak_localization.py for the method and its caveats.
    Populated as a fallback when no real segmentation model
    (SegmentationVolumes) is available, which is the current default
    since the training notebook never labeled pixel-level masks.

    `lesions` holds every detected region (multi-lesion support); the
    top-level area_cm2/bounding_box_normalized/centroid_normalized/
    location_description fields mirror lesions[0] (the largest) for
    backward compatibility with anything reading the single-region shape."""

    area_cm2: Optional[float] = None
    bounding_box_normalized: Optional[list[float]] = None  # [x0,y0,x1,y1] in [0,1]
    centroid_normalized: Optional[list[float]] = None  # [x,y] in [0,1]
    location_description: str
    is_diffuse: bool = False
    lesion_count: int = 0
    lesions: list[LesionMeasurement] = []
    method_note: str = (
        "Heuristic region(s) derived from fused model attention (Grad-CAM + "
        "saliency + edges), NOT a trained or validated segmentation, and NOT "
        "anatomically-validated measurement. For visual triage only — do not "
        "treat as measured tumour boundaries or clinical dimensions."
    )


class ModelInfo(CamelModel):
    name: str
    version: str
    validation_macro_f1: Optional[float] = None
    validation_note: str
    checkpoint_id: Optional[str] = None  # e.g. best_model_seed42
    checkpoint_sha256: Optional[str] = None  # exact file that produced this result


class PerImageResult(CamelModel):
    """One uploaded image/slice's own independent classification —
    every image in a multi-image study gets analyzed separately before
    being combined into the consensus below. Nothing here is inferred
    from the other images in the study."""

    image_id: str
    predicted_class: TumourClass
    probabilities: ClassProbabilities
    calibrated_confidence: float


class ConsensusInfo(CamelModel):
    """Majority-vote aggregation across all images in a multi-image
    study. Absent (null) for single-image studies — there's nothing to
    reach consensus over with one image, and the top-level
    AnalysisResult fields already ARE that single image's result."""

    num_images: int
    agreeing_images: int
    agreement_fraction: float
    reliability_score: float
    reliability_reason: str


class AnalysisResult(CamelModel):
    study_id: str
    predicted_class: TumourClass  # consensus class if multi-image, else the single image's class
    probabilities: ClassProbabilities  # mean across images if multi-image
    calibrated_confidence: float  # mean confidence-in-consensus-class if multi-image
    predictive_entropy: float
    below_confidence_gate: bool
    segmentation: SegmentationVolumes
    weak_localization: Optional[WeakLocalization] = None
    per_image_results: Optional[list[PerImageResult]] = None  # populated only for multi-image studies
    consensus: Optional[ConsensusInfo] = None  # populated only for multi-image studies
    primary_image_id: Optional[str] = None  # which image's Grad-CAM/weak-localization is shown above
    model: ModelInfo
    inferred_at: datetime


class ReportStatus(str, Enum):
    preliminary = "preliminary"
    signed = "signed"


class ClinicalReport(CamelModel):
    """Structured MR report — standard radiology sections rather than one
    opaque text blob. `findings` is AI-generated (from the analysis
    pipeline) and read-only; `clinical_indication`, `impression`, and
    `recommendation` are the radiologist's own words, editable via
    PATCH .../report until signed, then frozen. `summary` is kept as a
    derived, backward-compatible concatenation of the sections below —
    nothing new reads it, but nothing that already reads it breaks."""

    study_id: str
    status: ReportStatus
    clinical_indication: str = ""
    technique: str = ""
    findings: str = ""
    impression: str = ""
    recommendation: str = ""
    summary: str
    signed_by: Optional[str] = None
    signed_at: Optional[datetime] = None


class UpdateReportRequest(CamelModel):
    clinical_indication: Optional[str] = None
    impression: Optional[str] = None
    recommendation: Optional[str] = None


class ReportDraft(CamelModel):
    """Suggested draft text only — never auto-saved to the report. The
    radiologist must explicitly accept/edit and then PATCH .../report to
    actually apply it, same as if they'd typed it themselves."""

    clinical_indication: str
    impression: str


class SignReportRequest(CamelModel):
    signed_by: str = Field(..., description="Name/ID of the co-signing radiologist")


class TriggerAnalysisResponse(CamelModel):
    job_id: str


class ModelRegistryEntry(CamelModel):
    id: int
    version: str
    sha256: str
    checkpoint_id: Optional[str] = None
    size_bytes: int
    status: str  # pending | validated | failed
    validation_detail: Optional[dict] = None
    is_active: bool
    externally_validated: bool
    notes: str = ""
    source: str = "upload"  # upload | bootstrap | watch-folder
    source_path: Optional[str] = None
    source_mtime: Optional[datetime] = None
    created_at: datetime
    activated_at: Optional[datetime] = None


class ModelRegistryState(CamelModel):
    locked: bool
    expected_checkpoint_id: str
    worker_online: bool
    worker_loaded_model: Optional[dict] = None
    auto_activate: bool = False
    training_folder: Optional[dict] = None  # {folder, last_scan, checkpoint_files, error}
    models: list[ModelRegistryEntry]


class JobStatus(CamelModel):
    job_id: str
    status: str  # queued | running | done | failed
    error: Optional[str] = None
    duration_ms: Optional[int] = None


class UserPublic(CamelModel):
    """Never includes password_hash/salt — this is what the API returns
    for any user-facing listing/lookup."""

    id: str
    name: str
    username: str
    role: Role
    institution: str
    active: bool
    must_change_password: bool = False


class LoginRequest(CamelModel):
    username: str
    password: str


class LoginResponse(CamelModel):
    token: str
    user: UserPublic


class CreateUserRequest(CamelModel):
    name: str
    username: str
    password: str
    role: Role
    institution: str = "—"
    # The admin panel sends true: an admin-chosen password is temporary and
    # the user must replace it at first login.
    require_password_change: bool = False


# ── Doctor registration ────────────────────────────────────────────────────
class RegistrationCreate(CamelModel):
    full_name: str
    email: str
    phone: str
    nmc_number: str
    specialty: str
    requested_role: str = "radiologist"  # radiologist | referring_physician
    institution: str
    notes: str = ""
    website: str = ""  # honeypot: humans never see or fill this field


class RegistrationSubmitted(CamelModel):
    reference: str
    status: str


class RegistrationPublic(CamelModel):
    id: str
    full_name: str
    email: str
    phone: str
    nmc_number: str
    specialty: str
    requested_role: str
    institution: str
    notes: str
    status: str
    submitted_at: datetime
    reviewed_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    username: Optional[str] = None


class ApproveRegistrationRequest(CamelModel):
    role: Optional[str] = None  # override the requested role


class RejectRegistrationRequest(CamelModel):
    reason: str = ""


class GeneratedCredentials(CamelModel):
    """Returned ONCE, to the approving administrator only."""
    registration: RegistrationPublic
    username: str
    temporary_password: str


class ChangePasswordRequest(CamelModel):
    current_password: str
    new_password: str
