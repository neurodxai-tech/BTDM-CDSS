"""
CAM-guided multi-lesion weak localization — NOT a trained/validated
segmentation, and NOT anatomically-validated measurement.

IMPORTANT LIMITATION, stated plainly rather than buried: this detects
regions of elevated MODEL ATTENTION, not lesions per se. If the model's
attention doesn't concentrate on a real lesion (a false negative in the
classifier's own reasoning), this will not find it either — it cannot
detect anything the classifier itself didn't attend to. "Detect every
visible lesion with high anatomical accuracy" is a claim that requires a
trained, pixel-annotated segmentation model; none exists for this
project (the training notebook never labeled lesion masks). This module
is the honest, best-effort alternative: multiple discrete high-attention
regions, each measured with real geometry (diameter, dimensions, area)
computed correctly from the actual mask — but derived from
classification attention, not a validated measurement instrument.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.spatial import ConvexHull, QhullError

THRESHOLD_PERCENTILE_START = 80.0
THRESHOLD_PERCENTILE_MAX = 98.0
THRESHOLD_PERCENTILE_STEP = 3.0
MAX_FOCAL_AREA_FRACTION = 0.12  # a focal lesion shouldn't cover >12% of the slice
MIN_REGION_FRACTION_OF_IMAGE = 0.002  # discard tiny noise components
MAX_LESIONS = 5  # cap on how many distinct regions we'll report


@dataclass
class LesionMeasurement:
    lesion_id: str  # "L1", "L2", ... ranked by area, largest first
    area_cm2: float
    max_diameter_mm: float
    width_mm: float
    height_mm: float
    volume_cm3_estimate: float | None  # only set if slice thickness is known — see caveat below
    bounding_box_normalized: tuple[float, float, float, float]
    centroid_normalized: tuple[float, float]
    location_description: str
    boundary_refined: bool  # True if GrabCut successfully snapped this boundary to real
                             # image contrast; False if measurements are from the raw
                             # thresholded attention region (refinement unavailable/failed)
    mask: np.ndarray


@dataclass
class MultiLesionResult:
    lesions: list[LesionMeasurement] = field(default_factory=list)
    is_diffuse: bool = False
    diffuse_message: str | None = None


def _largest_components(binary: np.ndarray, max_count: int) -> list[tuple[np.ndarray, int]]:
    """Returns up to max_count (mask, size) tuples, largest first."""
    labeled, num_features = ndimage.label(binary)
    if num_features == 0:
        return []
    sizes = ndimage.sum(binary, labeled, range(1, num_features + 1))
    order = np.argsort(sizes)[::-1]
    out = []
    for idx in order[:max_count]:
        label = idx + 1
        out.append((labeled == label, int(sizes[idx])))
    return out


def _max_diameter_mm(mask: np.ndarray, spacing_row_mm: float, spacing_col_mm: float) -> float:
    """Real max diameter (Feret diameter): the longest straight-line
    distance between any two points on the region's boundary, computed
    in real-world mm using anisotropic pixel spacing (not just pixel
    count) — this is what "maximum diameter" means clinically, not a
    bounding-box diagonal."""
    ys, xs = np.where(mask)
    if len(xs) < 2:
        return 0.0
    points = np.column_stack([xs, ys]).astype(np.float64)

    if len(points) >= 4:
        try:
            hull = ConvexHull(points)
            points = points[hull.vertices]
        except QhullError:
            pass  # degenerate (collinear) region — fall back to all points

    max_dist = 0.0
    n = len(points)
    for i in range(n):
        dx = (points[i + 1 :, 0] - points[i, 0]) * spacing_col_mm
        dy = (points[i + 1 :, 1] - points[i, 1]) * spacing_row_mm
        if len(dx) == 0:
            continue
        dists = np.hypot(dx, dy)
        local_max = dists.max()
        if local_max > max_dist:
            max_dist = local_max
    return round(float(max_dist), 1)


def _describe_location(x: float, y: float) -> str:
    horizontal = "left" if x < 0.45 else "right" if x > 0.55 else "central"
    vertical = "anterior" if y < 0.45 else "posterior" if y > 0.55 else "mid"
    if horizontal == "central" and vertical == "mid":
        return "centrally located"
    if horizontal == "central":
        return f"{vertical} midline region"
    if vertical == "mid":
        return f"{horizontal} hemisphere, mid-slice"
    return f"{vertical} {horizontal} region"


def detect_lesions(
    fused_map: np.ndarray,
    pixel_spacing_mm: tuple[float, float] = (1.0, 1.0),  # (row, col)
    slice_thickness_mm: float | None = None,
    max_lesions: int = MAX_LESIONS,
    image_2d: np.ndarray | None = None,
    refine_boundary: bool = True,
) -> MultiLesionResult:
    """
    fused_map: (H, W) float array in [0, 1], from gradcam.fused_attention().
    pixel_spacing_mm: (row_spacing, col_spacing) in mm.
    slice_thickness_mm: if known (DICOM), enables a crude single-slice
        volume ESTIMATE (area x thickness) — explicitly an approximation
        assuming the lesion's cross-section is representative through the
        slice, not a real 3D measurement. None for PNG/JPG input (no
        spacing metadata) — volume is correctly omitted, not guessed.

    ADAPTIVE THRESHOLD: starts tight (80th percentile) and escalates (to
    98th) until the LARGEST region clears the focal-size constraint —
    same reasoning as the single-lesion version this replaces: accepting
    a loose threshold produces a "region" covering most of the brain,
    not a real finding. Once thresholded, ALL qualifying components
    (not just the largest) are kept as separate lesions, up to
    max_lesions.
    """
    h, w = fused_map.shape
    image_area = h * w
    spacing_row, spacing_col = pixel_spacing_mm

    percentile = THRESHOLD_PERCENTILE_START
    components: list[tuple[np.ndarray, int]] = []

    while percentile <= THRESHOLD_PERCENTILE_MAX:
        threshold = np.percentile(fused_map, percentile)
        binary = fused_map >= threshold
        components = _largest_components(binary, max_lesions)
        components = [(m, s) for m, s in components if s >= MIN_REGION_FRACTION_OF_IMAGE * image_area]

        if not components:
            return MultiLesionResult(lesions=[], is_diffuse=False, diffuse_message="No focal attention region above the noise floor.")

        largest_size = components[0][1]
        if largest_size <= MAX_FOCAL_AREA_FRACTION * image_area:
            break
        percentile += THRESHOLD_PERCENTILE_STEP
    else:
        return MultiLesionResult(
            lesions=[],
            is_diffuse=True,
            diffuse_message=(
                "Model attention is spread broadly across the slice, not concentrated in "
                "one or more focal regions — no lesion boundaries are reported. This can "
                "happen with diffuse/infiltrative patterns, or when the model's evidence "
                "for this class isn't spatially concentrated."
            ),
        )

    lesions: list[LesionMeasurement] = []
    for i, (mask, _size) in enumerate(components):
        mask = ndimage.binary_fill_holes(mask)
        was_refined = False

        if refine_boundary and image_2d is not None:
            try:
                from app.models.segmentation_refine import refine_boundary_grabcut

                refined = refine_boundary_grabcut(image_2d, fused_map, mask)
                if refined is not None and refined.sum() > 0:
                    mask = ndimage.binary_fill_holes(refined)
                    was_refined = True
            except Exception:
                # GrabCut refinement is a best-effort enhancement — if it
                # errors for any reason, fall back to the unrefined
                # attention-thresholded mask rather than failing the
                # whole analysis over a boundary-refinement step.
                pass

        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        r0, r1 = np.where(rows)[0][[0, -1]]
        c0, c1 = np.where(cols)[0][[0, -1]]

        area_px = int(mask.sum())
        area_mm2 = area_px * spacing_row * spacing_col
        area_cm2 = round(area_mm2 / 100.0, 2)

        width_mm = round((c1 - c0) * spacing_col, 1)
        height_mm = round((r1 - r0) * spacing_row, 1)
        max_diameter = _max_diameter_mm(mask, spacing_row, spacing_col)

        volume_estimate = None
        if slice_thickness_mm is not None:
            volume_estimate = round(area_mm2 * slice_thickness_mm / 1000.0, 2)

        centroid_r, centroid_c = ndimage.center_of_mass(mask)
        centroid_x, centroid_y = centroid_c / w, centroid_r / h

        lesions.append(
            LesionMeasurement(
                lesion_id=f"L{i + 1}",
                area_cm2=area_cm2,
                max_diameter_mm=max_diameter,
                width_mm=width_mm,
                height_mm=height_mm,
                volume_cm3_estimate=volume_estimate,
                bounding_box_normalized=(c0 / w, r0 / h, c1 / w, r1 / h),
                centroid_normalized=(centroid_x, centroid_y),
                location_description=_describe_location(centroid_x, centroid_y),
                boundary_refined=was_refined,
                mask=mask,
            )
        )

    return MultiLesionResult(lesions=lesions, is_diffuse=False, diffuse_message=None)
