"""
Builds the structured MR report from an AnalysisResult + the Study it
belongs to. Spec §4 calls for full DICOM SR / FHIR DiagnosticReport
generation — this is the structured-text piece; FHIR resource
construction is the next build step (interop service) and should
consume this same data.

Standard radiology report sections:
  - Clinical indication : why the study was ordered (radiologist fills in)
  - Technique            : modality/sequences (auto, from the Study)
  - Findings             : AI-assisted findings (auto, from AnalysisResult) — read-only
  - Impression           : the radiologist's own conclusion — REQUIRED before signing
  - Recommendation       : follow-up/next-steps (radiologist, optional)
"""
from __future__ import annotations

from app.schemas import AnalysisResult, ClinicalReport, ReportStatus, Study

_CLASS_LABELS = {
    "glioma": "Glioma",
    "meningioma": "Meningioma",
    "pituitary": "Pituitary",
    "notumor": "No tumour",
}


def _build_findings(result: AnalysisResult) -> str:
    label = _CLASS_LABELS[result.predicted_class.value]
    pct = round(result.calibrated_confidence * 100, 1)
    parts = [f"AI-assisted classification: {label} (calibrated confidence {pct}%)."]

    if result.consensus:
        c = result.consensus
        parts.append(
            f"Multi-image consensus across {c.num_images} images: {c.agreeing_images} agreed "
            f"({c.agreement_fraction*100:.0f}%), reliability score {c.reliability_score*100:.0f}%."
        )

    if result.below_confidence_gate:
        parts.append("Confidence is BELOW the review gate — flagged for mandatory specialist review.")

    wl = result.weak_localization
    if wl and wl.lesions:
        for lesion in wl.lesions:
            vol = f", ~{lesion.volume_cm3_estimate} cm³ (est.)" if lesion.volume_cm3_estimate is not None else ""
            method = (
                "boundary refined to image contrast via GrabCut"
                if lesion.boundary_refined
                else "boundary is the raw attention region (contrast refinement unavailable)"
            )
            parts.append(
                f"{lesion.lesion_id}: {lesion.location_description}, max diameter {lesion.max_diameter_mm} mm, "
                f"area {lesion.area_cm2} cm²{vol} ({method}; heuristic, not a validated measurement)."
            )
    elif wl and wl.is_diffuse:
        parts.append("Model attention was diffuse — no focal region(s) could be delineated.")

    seg = result.segmentation
    if seg.enhancing_tumour_cm3 is not None:
        parts.append(
            f"Enhancing tumour volume {seg.enhancing_tumour_cm3} cm³, "
            f"oedema {seg.edema_cm3} cm³, necrotic core {seg.necrotic_core_cm3} cm³."
        )

    return " ".join(parts)


def _build_technique(study: Study) -> str:
    seq = ", ".join(study.sequences) if study.sequences else "unspecified sequence"
    n = len(study.image_ids)
    plural = "s" if n != 1 else ""
    return f"{study.modality} brain, {seq} ({n} image{plural} submitted)."


def _compose_summary(report_fields: dict) -> str:
    """Backward-compatible flat summary — derived, not authoritative."""
    parts = []
    if report_fields["clinical_indication"]:
        parts.append(f"Indication: {report_fields['clinical_indication']}")
    parts.append(f"Technique: {report_fields['technique']}")
    parts.append(f"Findings: {report_fields['findings']}")
    if report_fields["impression"]:
        parts.append(f"Impression: {report_fields['impression']}")
    if report_fields["recommendation"]:
        parts.append(f"Recommendation: {report_fields['recommendation']}")
    if not report_fields["impression"]:
        parts.append("Correlate clinically; not a final diagnosis until reviewed and co-signed by a radiologist.")
    return " ".join(parts)


def build_report(result: AnalysisResult, study: Study) -> ClinicalReport:
    findings = _build_findings(result)
    technique = _build_technique(study)

    fields = {
        "clinical_indication": "",
        "technique": technique,
        "findings": findings,
        "impression": "",
        "recommendation": "",
    }

    return ClinicalReport(
        study_id=result.study_id,
        status=ReportStatus.preliminary,
        clinical_indication="",
        technique=technique,
        findings=findings,
        impression="",
        recommendation="",
        summary=_compose_summary(fields),
        signed_by=None,
        signed_at=None,
    )
