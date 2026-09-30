"""
Orchestrates a full analysis run for one study, which may contain ONE OR
MORE uploaded images/slices. Each image is classified independently
(nothing about one image's analysis depends on any other image in the
study), then combined into a majority-vote consensus when there's more
than one.

Critical behavior: if no trained weights are present, this does NOT
fabricate a confident-looking prediction. It runs the untrained network
(so the code path is exercised end-to-end for development) but marks
`model.validationNote` accordingly and forces belowConfidenceGate=True,
so the frontend's low-confidence banner fires honestly instead of
displaying a plausible-looking but meaningless result.
"""
from __future__ import annotations

import io
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from scipy import ndimage

from app.config import CLASSES, CONFIDENCE_GATE, MODEL_NAME, MODEL_VERSION, MODEL_EXTERNALLY_VALIDATED
from app.models.calibration import TemperatureScaler, predictive_entropy
from app.models.classifier import get_classifier
from app.models.gradcam import fused_attention
from app.models.segmentation import get_segmentation_model, voxel_volume_cm3
from app.models.weak_localization import detect_lesions
from app.schemas import (
    AnalysisResult,
    ClassProbabilities,
    ConsensusInfo,
    LesionMeasurement,
    ModelInfo,
    PerImageResult,
    SegmentationVolumes,
    TumourClass,
    WeakLocalization,
)
from app.services import preprocessing

logger = logging.getLogger("btdm.services.inference")

# study_id -> {image_id: {mode: PNG bytes}}, served by GET /studies/{id}/gradcam
# (?mode=plain|heatmap|region|both, defaults to 'both' for backward compat)
_gradcam_cache: dict[str, dict[str, dict[str, bytes]]] = {}


@dataclass
class _SingleImageAnalysis:
    image_id: str
    predicted_class: str
    probabilities: dict[str, float]
    calibrated_confidence: float
    entropy: float
    weak_loc: WeakLocalization | None
    seg_volumes: SegmentationVolumes
    gradcam_variants: dict[str, bytes]  # keys: 'plain', 'heatmap', 'region', 'both'
    clf_weights_loaded: bool


def run_analysis(
    study_id: str,
    images: list[tuple[str, bytes, str]],  # [(image_id, file_bytes, kind), ...]
    voxel_spacing_mm: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> AnalysisResult:
    if not images:
        raise ValueError("run_analysis called with no images")

    # Each image is analyzed independently and in isolation — a single
    # bad file (unsupported compression, corrupt data, or anything that
    # slipped past upload-time validation) must not take down the WHOLE
    # study's analysis. This used to be a plain list comprehension with
    # no exception handling, so any one image failing crashed the entire
    # background task, silently leaving the study stuck with no report
    # and no indication of which file or why.
    per_image: list[_SingleImageAnalysis] = []
    failures: list[str] = []
    for image_id, file_bytes, kind in images:
        try:
            per_image.append(_analyze_one_image(image_id, file_bytes, kind, voxel_spacing_mm))
        except Exception as exc:
            logger.exception("Analysis failed for image %s in study %s — skipping it, continuing with the rest.", image_id, study_id)
            failures.append(f"{image_id}: {exc}")

    if not per_image:
        raise RuntimeError(
            f"All {len(images)} image(s) in this study failed to analyze. "
            f"First failure: {failures[0] if failures else 'unknown'}"
        )

    if failures:
        logger.warning(
            "Study %s: %d of %d images analyzed successfully, %d failed and were excluded from consensus.",
            study_id, len(per_image), len(images), len(failures),
        )

    _gradcam_cache[study_id] = {a.image_id: a.gradcam_variants for a in per_image if a.gradcam_variants}

    if len(per_image) == 1:
        return _build_single_image_result(study_id, per_image[0])
    return _build_consensus_result(study_id, per_image)


def _analyze_one_image(
    image_id: str, file_bytes: bytes, kind: str, voxel_spacing_mm: tuple[float, float, float]
) -> _SingleImageAnalysis:
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # 1. Load + preprocess -------------------------------------------------
    if kind == "dicom":
        image_2d, meta = preprocessing.load_dicom(file_bytes)
        spacing = (*meta.get("pixel_spacing", [1.0, 1.0]), meta.get("slice_thickness", 1.0))
    elif kind == "nifti":
        volume, meta = preprocessing.load_nifti(file_bytes)
        image_2d = volume[:, :, volume.shape[2] // 2]
        spacing = tuple(meta.get("voxel_spacing_mm", [1.0, 1.0, 1.0]))
    else:
        image_2d = preprocessing.load_standard_image(file_bytes)
        spacing = voxel_spacing_mm

    image_2d = preprocessing.skull_strip_and_crop(image_2d)
    input_tensor = preprocessing.to_model_input(image_2d, in_channels=1).to(device)

    # 2. Classification -----------------------------------------------------
    classifier, clf_weights_loaded = get_classifier(device)
    calibrator = TemperatureScaler.load()

    with torch.no_grad():
        logits = classifier(input_tensor)
    calibrated_logits = calibrator.apply(logits)
    probs = F.softmax(calibrated_logits, dim=1).squeeze(0)

    pred_idx = int(torch.argmax(probs).item())
    predicted_class = CLASSES[pred_idx]
    calibrated_confidence = float(probs[pred_idx].item())
    entropy = predictive_entropy(probs)
    prob_dict = {cls: float(probs[i].item()) for i, cls in enumerate(CLASSES)}

    # 3. Fused attention + CAM-guided multi-lesion detection ----------------
    weak_loc: WeakLocalization | None = None
    gradcam_variants: dict[str, bytes] = {}
    if clf_weights_loaded and predicted_class != "notumor":
        try:
            edges_source = input_tensor[0, 0].detach().cpu().numpy()
            fused = fused_attention(classifier, input_tensor, pred_idx, edges_source)
            slice_thickness = spacing[2] if kind == "dicom" and len(spacing) > 2 else None

            # BUG FIX: `image_2d` here is post-skull-strip-crop but
            # PRE-resize — its shape is whatever the crop produced (e.g.
            # 180x165), NOT the 224x224 that `fused` is always computed
            # at (fused_attention operates on `input_tensor`, which IS
            # resized). Passing the un-resized image_2d into
            # detect_lesions/GrabCut fed it two arrays of DIFFERENT
            # shapes — GrabCut's internal row/col indexing (built from
            # the 224-sized candidate mask) would then run off the edge
            # of the smaller image_2d array, corrupting the refined
            # mask's shape. That mask then failed to broadcast against
            # the 224x224 render canvas later, silently caught as
            # "Skipping boundary draw (bad mask)" — every single time,
            # not intermittently, since the shape mismatch was
            # unconditional. Resizing image_2d to match `fused` here
            # fixes the root cause instead of continuing to just catch
            # the resulting failure downstream.
            #
            # This also means the mm/cm measurements need the SAME
            # rescaling applied to pixel spacing — the crop→224 resize
            # changes how many real-world mm each of the 224 pixels
            # represents, so using the original (pre-resize) DICOM
            # pixel spacing directly would understate or overstate every
            # lesion's reported size by the crop/224 ratio.
            crop_h, crop_w = image_2d.shape
            scale_row = crop_h / float(fused.shape[0])
            scale_col = crop_w / float(fused.shape[1])
            effective_spacing = (spacing[0] * scale_row, spacing[1] * scale_col)

            img_u8 = (np.clip(image_2d, 0, 1) * 255).astype(np.uint8)
            image_2d_for_lesions = (
                np.asarray(Image.fromarray(img_u8).resize((fused.shape[1], fused.shape[0]), Image.BILINEAR), dtype=np.float32) / 255.0
            )

            multi = detect_lesions(
                fused,
                pixel_spacing_mm=effective_spacing,
                slice_thickness_mm=slice_thickness,
                image_2d=image_2d_for_lesions,
            )

            lesion_schemas = [
                LesionMeasurement(
                    lesion_id=l.lesion_id,
                    area_cm2=l.area_cm2,
                    max_diameter_mm=l.max_diameter_mm,
                    width_mm=l.width_mm,
                    height_mm=l.height_mm,
                    volume_cm3_estimate=l.volume_cm3_estimate,
                    bounding_box_normalized=list(l.bounding_box_normalized),
                    centroid_normalized=list(l.centroid_normalized),
                    location_description=l.location_description,
                    boundary_refined=l.boundary_refined,
                )
                for l in multi.lesions
            ]
            primary_lesion = multi.lesions[0] if multi.lesions else None
            weak_loc = WeakLocalization(
                area_cm2=primary_lesion.area_cm2 if primary_lesion else None,
                bounding_box_normalized=list(primary_lesion.bounding_box_normalized) if primary_lesion else None,
                centroid_normalized=list(primary_lesion.centroid_normalized) if primary_lesion else None,
                location_description=(
                    primary_lesion.location_description if primary_lesion else (multi.diffuse_message or "No focal attention region above the noise floor.")
                ),
                is_diffuse=multi.is_diffuse,
                lesion_count=len(multi.lesions),
                lesions=lesion_schemas,
            )
            # Generate all 4 display modes up front — cheap (just
            # different compositing of the SAME already-computed fused
            # map and lesion masks, no extra model inference), and lets
            # the frontend toggle instantly between them with no extra
            # backend round-trip per click.
            for mode in ("plain", "heatmap", "region", "both"):
                try:
                    gradcam_variants[mode] = _render_variant(image_2d, fused, multi.lesions, mode)
                except Exception:
                    logger.exception("Render mode '%s' failed for image %s", mode, image_id)
        except Exception:
            logger.exception("Fused attention / lesion detection failed for image %s", image_id)
            # Belt-and-suspenders: if weak_loc above already succeeded
            # (real lesion measurements exist) but the image render
            # itself failed for some other reason, still show SOMETHING
            # rather than leaving the panel with numbers but no image.
            if not gradcam_variants:
                try:
                    plain = _plain_image_to_png(image_2d)
                    gradcam_variants = {"plain": plain, "heatmap": plain, "region": plain, "both": plain}
                except Exception:
                    logger.exception("Plain image fallback also failed for image %s", image_id)
    elif clf_weights_loaded and predicted_class == "notumor":
        # No lesion to attend to, but the radiologist should still be able
        # to SEE the actual analyzed image rather than an empty "not
        # available" panel — a plain grayscale view (no color, no
        # heatmap, nothing implying a finding that isn't there) confirms
        # what was actually processed, without fabricating attention over
        # a normal scan. All 4 modes serve the same plain image since
        # there's no attention data to show regardless of which is picked.
        try:
            plain = _plain_image_to_png(image_2d)
            gradcam_variants = {"plain": plain, "heatmap": plain, "region": plain, "both": plain}
        except Exception:
            logger.exception("Plain image render failed for image %s", image_id)

    # 4. Real segmentation model (empty by default — see weak_loc above) ----
    seg_volumes = SegmentationVolumes()
    if predicted_class != "notumor":
        seg_model, seg_weights_loaded = get_segmentation_model(device)
        if seg_weights_loaded:
            seg_input = preprocessing.to_model_input(image_2d, in_channels=1).to(device)
            with torch.no_grad():
                seg_logits = seg_model(seg_input)
            seg_mask = torch.argmax(seg_logits, dim=1).squeeze(0)
            seg_volumes = SegmentationVolumes(
                necrotic_core_cm3=voxel_volume_cm3(seg_mask, 1, spacing),
                edema_cm3=voxel_volume_cm3(seg_mask, 2, spacing),
                enhancing_tumour_cm3=voxel_volume_cm3(seg_mask, 3, spacing),
            )

    return _SingleImageAnalysis(
        image_id=image_id,
        predicted_class=predicted_class,
        probabilities=prob_dict,
        calibrated_confidence=calibrated_confidence,
        entropy=entropy,
        weak_loc=weak_loc,
        seg_volumes=seg_volumes,
        gradcam_variants=gradcam_variants,
        clf_weights_loaded=clf_weights_loaded,
    )


def _model_info(clf_weights_loaded: bool) -> ModelInfo:
    from app.models.classifier import active_meta

    meta = active_meta()
    validated = meta.get("externally_validated", MODEL_EXTERNALLY_VALIDATED)
    validation_note = (
        "Externally validated — cleared for the validation scope on file."
        if validated
        else "Not externally validated. Research use only."
    )
    if not clf_weights_loaded:
        validation_note = (
            "NO TRAINED WEIGHTS LOADED — this is placeholder output from an "
            "untrained network, not a real prediction."
        )
    return ModelInfo(
        name=MODEL_NAME,
        version=meta.get("version", MODEL_VERSION),
        validation_macro_f1=None,
        validation_note=validation_note,
        checkpoint_id=meta.get("checkpoint_id"),
        checkpoint_sha256=meta.get("sha256"),
    )


def _build_single_image_result(study_id: str, a: _SingleImageAnalysis) -> AnalysisResult:
    below_gate = a.calibrated_confidence < CONFIDENCE_GATE or not a.clf_weights_loaded
    return AnalysisResult(
        study_id=study_id,
        predicted_class=TumourClass(a.predicted_class),
        probabilities=ClassProbabilities(**a.probabilities),
        calibrated_confidence=a.calibrated_confidence,
        predictive_entropy=a.entropy,
        below_confidence_gate=below_gate,
        segmentation=a.seg_volumes,
        weak_localization=a.weak_loc,
        per_image_results=None,
        consensus=None,
        primary_image_id=a.image_id,
        model=_model_info(a.clf_weights_loaded),
        inferred_at=datetime.now(timezone.utc),
    )


def _build_consensus_result(study_id: str, per_image: list[_SingleImageAnalysis]) -> AnalysisResult:
    """Majority vote across all images. Ties are broken by summed
    confidence-in-that-class across the tied classes. The 'primary' image
    (whose Grad-CAM/weak-localization/segmentation represent the study at
    the top level) is the agreeing image with the highest confidence in
    the consensus class — i.e. the clearest example of the winning vote,
    not just image #1."""
    class_votes = Counter(a.predicted_class for a in per_image)
    top_count = max(class_votes.values())
    tied_classes = [c for c, n in class_votes.items() if n == top_count]

    if len(tied_classes) == 1:
        consensus_class = tied_classes[0]
    else:
        # Break ties by total confidence mass behind each tied class.
        confidence_sum = {c: 0.0 for c in tied_classes}
        for a in per_image:
            if a.predicted_class in confidence_sum:
                confidence_sum[a.predicted_class] += a.calibrated_confidence
        consensus_class = max(tied_classes, key=lambda c: confidence_sum[c])

    agreeing = [a for a in per_image if a.predicted_class == consensus_class]
    agreement_fraction = len(agreeing) / len(per_image)

    mean_confidence = sum(a.calibrated_confidence for a in agreeing) / len(agreeing)
    mean_probs = {
        cls: sum(a.probabilities[cls] for a in per_image) / len(per_image) for cls in CLASSES
    }
    mean_entropy = sum(a.entropy for a in per_image) / len(per_image)

    any_weights_loaded = any(a.clf_weights_loaded for a in per_image)

    # Reliability: combines HOW MANY images agree with HOW CONFIDENT the
    # agreeing images are — full agreement at low confidence, or partial
    # agreement at high confidence, are both less reliable than both being
    # high, so this multiplies rather than averages the two signals.
    reliability_score = round(agreement_fraction * mean_confidence, 4)
    if agreement_fraction == 1.0:
        reliability_reason = f"All {len(per_image)} images agree on {consensus_class}."
    else:
        reliability_reason = (
            f"{len(agreeing)} of {len(per_image)} images agree on {consensus_class} "
            f"({agreement_fraction*100:.0f}% agreement) — the remaining image(s) predicted "
            f"a different class. Disagreement across slices/views lowers reliability "
            f"regardless of any single image's confidence."
        )

    primary = max(agreeing, key=lambda a: a.calibrated_confidence)

    below_gate = (
        mean_confidence < CONFIDENCE_GATE
        or not any_weights_loaded
        or agreement_fraction < 0.6  # majority alone isn't enough if it's a bare majority
    )

    per_image_results = [
        PerImageResult(
            image_id=a.image_id,
            predicted_class=TumourClass(a.predicted_class),
            probabilities=ClassProbabilities(**a.probabilities),
            calibrated_confidence=a.calibrated_confidence,
        )
        for a in per_image
    ]

    return AnalysisResult(
        study_id=study_id,
        predicted_class=TumourClass(consensus_class),
        probabilities=ClassProbabilities(**mean_probs),
        calibrated_confidence=mean_confidence,
        predictive_entropy=mean_entropy,
        below_confidence_gate=below_gate,
        segmentation=primary.seg_volumes,
        weak_localization=primary.weak_loc,
        per_image_results=per_image_results,
        consensus=ConsensusInfo(
            num_images=len(per_image),
            agreeing_images=len(agreeing),
            agreement_fraction=round(agreement_fraction, 4),
            reliability_score=reliability_score,
            reliability_reason=reliability_reason,
        ),
        primary_image_id=primary.image_id,
        model=_model_info(any_weights_loaded),
        inferred_at=datetime.now(timezone.utc),
    )


def _plain_image_to_png(image_2d: np.ndarray) -> bytes:
    """Plain grayscale render of the analyzed image — no color, no
    heatmap, no boundary. Used for the 'no tumour' prediction, where
    showing a colored attention overlay would be misleading (implying a
    finding that isn't there); this instead just confirms to the
    radiologist what image was actually processed."""
    arr = (image_2d * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def get_gradcam_png(study_id: str, image_id: str | None = None, mode: str = "both") -> bytes | None:
    per_study = _gradcam_cache.get(study_id)
    if not per_study:
        return None
    if image_id:
        variants = per_study.get(image_id)
        if not variants:
            return None
        return variants.get(mode) or variants.get("both")
    # Default: first available image's requested mode (callers needing
    # the primary image should pass its id explicitly — the router looks
    # it up from the stored AnalysisResult.primary_image_id).
    first_variants = next(iter(per_study.values()), None)
    if not first_variants:
        return None
    return first_variants.get(mode) or first_variants.get("both")


def clear_gradcam(study_id: str) -> None:
    _gradcam_cache.pop(study_id, None)


def _render_variant(image_2d: np.ndarray, fused: np.ndarray, lesions: list, mode: str) -> bytes:
    """
    Four independent display modes over the SAME underlying data — no
    re-computation of attention or lesions between them, just different
    compositing, so generating all 4 up front is cheap:

      'plain'   — the analyzed image alone, no overlay at all.
      'heatmap' — image blended with the jet-colormap attention map,
                  NO lesion boundary/label drawn on top.
      'region'  — the image alone (no color tint) with each lesion's
                  white boundary + label drawn directly on it — the
                  boundary/measurement without the attention color
                  underneath it.
      'both'    — heatmap blend AND boundary/labels together (the
                  original combined behavior this replaced).

    RESILIENCE (unchanged from the version this replaces): each lesion's
    boundary/label drawing is isolated in its own try/except so one bad
    lesion mask can't blank the whole image.
    """
    from PIL import ImageDraw

    h, w = fused.shape
    orig = Image.fromarray((image_2d * 255).astype(np.uint8)).convert("L").resize((w, h))
    orig_arr = np.asarray(orig, dtype=np.float32)

    if mode == "plain":
        return _plain_image_to_png(image_2d)

    if mode == "heatmap":
        jet = _jet_colormap(fused)
        blended = (0.5 * orig_arr[..., None] + 0.5 * jet).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(blended, mode="RGB").save(buf, format="PNG")
        return buf.getvalue()

    # 'region' and 'both' both need boundary + label drawing; they only
    # differ in whether the base is color-tinted first.
    if mode == "region":
        base = np.stack([orig_arr] * 3, axis=-1).astype(np.uint8)
    else:  # 'both'
        jet = _jet_colormap(fused)
        base = (0.5 * orig_arr[..., None] + 0.5 * jet).astype(np.uint8)

    for lesion in lesions:
        try:
            mask = lesion.mask
            if mask is None or not mask.any():
                continue
            boundary = mask ^ ndimage.binary_erosion(mask, iterations=2)
            base[boundary] = np.array([255, 255, 255], dtype=np.uint8)
        except Exception:
            logger.warning("Skipping boundary draw for lesion %s (bad mask)", getattr(lesion, "lesion_id", "?"))

    img = Image.fromarray(base, mode="RGB")
    draw = ImageDraw.Draw(img)
    for lesion in lesions:
        try:
            mask = lesion.mask
            if mask is None or not mask.any():
                continue
            rows = np.any(mask, axis=1)
            cols = np.any(mask, axis=0)
            row_indices = np.where(rows)[0]
            col_indices = np.where(cols)[0]
            if row_indices.size == 0 or col_indices.size == 0:
                continue
            r0, c0 = row_indices[0], col_indices[0]
            draw.text((max(int(c0), 2), max(int(r0) - 12, 2)), lesion.lesion_id, fill=(255, 255, 255))
        except Exception:
            logger.warning("Skipping label draw for lesion %s", getattr(lesion, "lesion_id", "?"))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jet_colormap(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0, 1)
    r = np.clip(1.5 - np.abs(4 * v - 3), 0, 1)
    g = np.clip(1.5 - np.abs(4 * v - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * v - 1), 0, 1)
    return (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)
