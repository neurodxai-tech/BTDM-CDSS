"""
Weakly-supervised boundary refinement via CAM-seeded GrabCut.

This answers a genuinely different question than raw Grad-CAM/fused
attention. Grad-CAM answers "where did the model look" — coarse, blurry,
computed at ~7x7 resolution then upsampled, so it can never trace a real
edge. This module instead uses the fused attention map purely as a SEED
(a hint of roughly where the foreground/background are), then hands the
actual boundary-finding job to GrabCut — a classical graph-cut algorithm
that segments based on real pixel intensity/color statistics. On a brain
MRI, that means it can snap to real contrast-enhancing tumour margins
(T1c) or real hyperintense signal boundaries (T2/FLAIR), which the coarse
attention map cannot represent regardless of how good the classifier is.

This is NOT a trained/validated segmentation model — no pixel-level
lesion masks exist to train one, and this module doesn't change that.
It IS a real, literature-established technique (CAM/attention-seeded
GrabCut or CRF for weakly-supervised segmentation — e.g. Khoreva et al.,
"Simple Does It," CVPR 2017, uses the same seed-then-graph-cut pattern
from bounding boxes rather than CAMs). Whether it's an improvement over
the raw attention region for THIS use case is an empirical question you
should sanity-check by comparing the two on real ANIAS images with known
ground truth, not something to assume from the technique's pedigree alone.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger("btdm.models.segmentation_refine")

# Percentile thresholds on the fused attention map used to seed GrabCut's
# four-level mask. Tune these against real validation data if the refined
# boundary consistently over- or under-shoots relative to expert-drawn
# ground truth once you have it.
SURE_FOREGROUND_PERCENTILE = 97.0
PROBABLE_FOREGROUND_PERCENTILE = 85.0
PROBABLE_BACKGROUND_PERCENTILE = 40.0
GRABCUT_ITERATIONS = 5


def refine_boundary_grabcut(
    image_2d: np.ndarray,
    fused_map: np.ndarray,
    candidate_mask: np.ndarray,
) -> np.ndarray | None:
    """
    image_2d: (H, W) float array, the actual MRI slice (any range).
    fused_map: (H, W) float array in [0, 1], from gradcam.fused_attention().
    candidate_mask: (H, W) bool — the region already found by
        weak_localization.detect_lesions() for ONE lesion. Used to bound
        where GrabCut is even allowed to consider "probable foreground",
        so it refines that specific lesion's boundary rather than
        potentially latching onto a brighter, unrelated structure
        elsewhere in the slice.

    Returns a refined (H, W) boolean mask, or None if GrabCut couldn't
    find a confident foreground seed (falls back to the caller using the
    original candidate_mask unrefined).
    """
    h, w = image_2d.shape
    img_min, img_max = image_2d.min(), image_2d.max()
    if img_max - img_min < 1e-8:
        return None
    img_u8 = ((image_2d - img_min) / (img_max - img_min) * 255).astype(np.uint8)
    img_bgr = cv2.cvtColor(np.stack([img_u8] * 3, axis=-1), cv2.COLOR_RGB2BGR)

    # Restrict consideration to a padded bounding box around the candidate
    # region — prevents GrabCut from wandering off to a brighter unrelated
    # region elsewhere in the slice just because it has higher raw attention.
    rows = np.any(candidate_mask, axis=1)
    cols = np.any(candidate_mask, axis=0)
    r0, r1 = np.where(rows)[0][[0, -1]]
    c0, c1 = np.where(cols)[0][[0, -1]]
    pad_r = max(4, int(0.25 * (r1 - r0)))
    pad_c = max(4, int(0.25 * (c1 - c0)))
    r0, r1 = max(0, r0 - pad_r), min(h, r1 + pad_r + 1)
    c0, c1 = max(0, c0 - pad_c), min(w, c1 + pad_c + 1)

    local_fused = fused_map[r0:r1, c0:c1]
    local_candidate = candidate_mask[r0:r1, c0:c1]

    mask = np.full(local_fused.shape, cv2.GC_BGD, dtype=np.uint8)
    mask[local_fused >= np.percentile(local_fused, PROBABLE_BACKGROUND_PERCENTILE)] = cv2.GC_PR_BGD
    # Probable/sure foreground restricted to inside the original candidate
    # region — this is what keeps GrabCut anchored to THIS lesion.
    prob_fg_threshold = np.percentile(local_fused, PROBABLE_FOREGROUND_PERCENTILE)
    mask[(local_fused >= prob_fg_threshold) & local_candidate] = cv2.GC_PR_FGD
    sure_fg_threshold = np.percentile(local_fused, SURE_FOREGROUND_PERCENTILE)
    mask[(local_fused >= sure_fg_threshold) & local_candidate] = cv2.GC_FGD

    if not (mask == cv2.GC_FGD).any():
        logger.info("GrabCut: no confident foreground seed found, skipping refinement.")
        return None

    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    local_img = img_bgr[r0:r1, c0:c1]

    try:
        cv2.grabCut(local_img, mask, None, bgd_model, fgd_model, GRABCUT_ITERATIONS, cv2.GC_INIT_WITH_MASK)
    except cv2.error as exc:
        logger.warning("GrabCut failed (%s) — falling back to unrefined attention region.", exc)
        return None

    local_refined = np.isin(mask, [cv2.GC_FGD, cv2.GC_PR_FGD])
    if not local_refined.any():
        return None

    refined = np.zeros((h, w), dtype=bool)
    refined[r0:r1, c0:c1] = local_refined
    return refined
