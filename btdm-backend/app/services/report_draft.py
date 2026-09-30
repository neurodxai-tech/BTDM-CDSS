"""
Generates EDITABLE DRAFT text for the radiologist's Clinical Indication
and Impression sections, using standard radiological reporting
conventions — hedged language ("favors", "highly consistent with",
"indeterminate between") whose STRENGTH is tied to the model's actual
calibrated confidence, not a fixed phrase regardless of how certain the
call really was. Every sentence traces to a specific computed value;
nothing here is invented to sound more authoritative than the underlying
number supports.

SCOPE HONESTY (unchanged from the original version): this only drafts
from fields the model genuinely computes (predicted class, calibrated
confidence, predictive entropy, class probability distribution, lesion
location/size/count, multi-image consensus). It deliberately does NOT
fabricate margin/enhancement/oedema/WHO-grade language — this classifier
does not assess texture, contrast enhancement pattern, oedema extent, or
histopathological/molecular grade, so writing those into a draft would
be inventing findings that were never actually measured.
"""
from __future__ import annotations

import math

from app.schemas import AnalysisResult, TumourClass

_CLASS_LABELS = {
    "glioma": "glioma",
    "meningioma": "meningioma",
    "pituitary": "pituitary adenoma",
    "notumor": "no tumour",
}

_SUGGESTED_RECOMMENDATIONS = {
    "glioma": "Neurosurgical and neuro-oncology consultation recommended; consider advanced MR (perfusion/spectroscopy) for further characterization.",
    "meningioma": "Neurosurgical consultation recommended for surgical planning discussion; interval follow-up imaging if conservative management is elected.",
    "pituitary": "Endocrinology referral (hormonal work-up) and dedicated pituitary-protocol MRI recommended if surgical planning is being considered.",
    "notumor": "No follow-up imaging indicated on the basis of this study alone; correlate with clinical presentation.",
}

# ln(4) — the maximum possible Shannon entropy for a 4-class softmax
# distribution (all classes equally likely). Normalizing raw entropy by
# this gives a 0-1 scale that's actually meaningful regardless of how
# many classes exist, rather than reporting a raw nat value with no
# reference point.
_MAX_ENTROPY_4CLASS = math.log(4)


def _hedge_phrase(confidence: float) -> str:
    """Standard radiological hedging language, strength tied to the
    actual calibrated confidence — this is the difference between a
    report that SOUNDS authoritative and one that IS defensible. A 51%
    call and a 99% call are both "the top class", but they do not
    deserve the same sentence."""
    if confidence >= 0.95:
        return "findings are highly consistent with"
    if confidence >= 0.85:
        return "findings are consistent with"
    if confidence >= 0.70:
        return "findings are suggestive of, favoring"
    return "findings are indeterminate, with a marginal favoring toward"


def _entropy_descriptor(entropy: float) -> str:
    normalized = entropy / _MAX_ENTROPY_4CLASS
    if normalized < 0.15:
        return "low"
    if normalized < 0.40:
        return "moderate"
    return "elevated"


def generate_indication_draft(result: AnalysisResult) -> str:
    """
    Clinical indication is fundamentally NOT something imaging can
    generate — it's the reason the study was ORDERED (referring
    clinician's history), and the model only ever sees pixels, never the
    patient's chart. This drafts standard indication phrasing around
    what was found, clearly marked as a placeholder for the actual
    clinical history.
    """
    label = _CLASS_LABELS[result.predicted_class.value]
    if result.predicted_class == TumourClass.notumor:
        return (
            "[SUGGESTED — replace with actual referring history] "
            "MRI brain requested for evaluation of clinical symptoms as indicated by the referring "
            "provider; correlate with presenting complaint and prior imaging if available."
        )
    return (
        "[SUGGESTED — replace with actual referring history] "
        f"MRI brain requested for further characterization of imaging findings suggestive of {label}; "
        "correlate with presenting complaint, relevant history, and prior imaging if available."
    )


def generate_impression_draft(result: AnalysisResult) -> str:
    """Standard structured impression: numbered findings, hedged
    diagnostic language scaled to actual confidence, quantitative
    uncertainty (entropy) alongside the point estimate, differential
    from the model's own probability distribution, lesion measurements,
    and multi-image consensus — every line traceable to a real computed
    value."""
    label = _CLASS_LABELS[result.predicted_class.value]
    pct = round(result.calibrated_confidence * 100, 1)
    entropy_desc = _entropy_descriptor(result.predictive_entropy)
    lines: list[str] = []

    if result.predicted_class == TumourClass.notumor:
        lines.append(
            f"1. No definite intracranial mass lesion identified. Calibrated confidence in a normal "
            f"study: {pct}% (predictive entropy {result.predictive_entropy:.2f}, {entropy_desc} classification uncertainty)."
        )
        lines.append(
            "2. Normal study with respect to the four classes this model distinguishes "
            "(glioma / meningioma / pituitary adenoma / no tumour); does not exclude pathology outside this model's scope."
        )
    else:
        hedge = _hedge_phrase(result.calibrated_confidence)
        lines.append(
            f"1. Imaging {hedge} {label} (calibrated confidence {pct}%; "
            f"predictive entropy {result.predictive_entropy:.2f}, {entropy_desc} classification uncertainty)."
        )

        probs = result.probabilities
        others = sorted(
            (
                (cls, getattr(probs, cls))
                for cls in ("glioma", "meningioma", "pituitary", "notumor")
                if cls != result.predicted_class.value
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        differential = ", ".join(f"{_CLASS_LABELS[c]} ({p*100:.1f}%)" for c, p in others if p > 0.01)
        if differential:
            lines.append(f"2. Differential diagnosis per model output, in descending likelihood: {differential}.")

        if result.weak_localization and result.weak_localization.lesions:
            lesion_lines = []
            for lesion in result.weak_localization.lesions:
                vol = f", estimated volume ~{lesion.volume_cm3_estimate} cm³" if lesion.volume_cm3_estimate is not None else ""
                lesion_lines.append(
                    f"{lesion.lesion_id}: {lesion.location_description}, maximum diameter "
                    f"{lesion.max_diameter_mm} mm, cross-sectional area {lesion.area_cm2} cm²{vol} "
                    f"({'boundary refined to image contrast via GrabCut' if lesion.boundary_refined else 'attention-derived boundary, contrast-unrefined'})"
                )
            lines.append(f"{len(lines) + 1}. Region(s) of interest — {'; '.join(lesion_lines)}.")
        elif result.weak_localization and result.weak_localization.is_diffuse:
            lines.append(
                f"{len(lines) + 1}. Model attention is diffusely distributed rather than focal; no "
                "discrete lesion boundary meets the reporting threshold for this study."
            )

    if result.consensus:
        c = result.consensus
        lines.append(
            f"{len(lines) + 1}. Multi-image concordance: {c.agreeing_images}/{c.num_images} images "
            f"({c.agreement_fraction*100:.0f}%) concur on this classification; composite reliability "
            f"score {c.reliability_score*100:.0f}%."
        )

    if result.below_confidence_gate:
        lines.append(
            f"{len(lines) + 1}. RESULT BELOW THE PRE-SPECIFIED CONFIDENCE GATE (70%) — this classification "
            "alone does not meet the threshold for a stand-alone determination; independent specialist "
            "review is required regardless of the impression above."
        )

    lines.append(f"{len(lines) + 1}. Recommendation: {_SUGGESTED_RECOMMENDATIONS[result.predicted_class.value]}")

    lines.append(
        "\nNote: This impression was AI-drafted from imaging classification analysis only. Heuristic "
        "lesion measurements are derived from model attention (Grad-CAM/saliency), not a trained or "
        "validated segmentation instrument, and this system does not assess margins, contrast "
        "enhancement pattern, oedema, or WHO tumour grade — grading requires histopathological/"
        "molecular assessment, not imaging classification alone. Every line above must be reviewed, "
        "edited, and confirmed against independent radiological interpretation before finalizing."
    )

    return "\n".join(lines)
