"""
Preprocessing pipeline — matches BTDM_FINAL.ipynb's `skull_strip_crop()`
and `preprocess_image()` exactly (an earlier version of this file guessed
at the manuscript prose's "adaptive 95th-percentile threshold" and got
the actual algorithm wrong — see corrections below, code is ground truth):

  1. Greyscale conversion (ITU-R BT.601 weights if RGB source)
  2. Skull-strip + bbox crop: threshold = 95th percentile of NON-ZERO
     pixel intensities; foreground mask = pixels > 0.10 * threshold
     (NOT pixels >= the percentile itself — the percentile sets an upper
     reference point, and the actual mask threshold is 10% of that value,
     a much more permissive/lower cutoff). 10% bounding-box padding.
  3. Resize to 224x224 bilinear, range-preserving (rescale to uint8 for
     PIL's resize, then rescale back to the original min/max range)
  4. Per-image z-score computed on NON-ZERO pixels only:
     mu, sigma = nonzero_pixels.mean(), nonzero_pixels.std()
     then applied to the WHOLE image (including zero/background pixels,
     which become negative values, not masked out)
  5. Training-only augmentation (rotation, flip, brightness/contrast,
     scale, p=0.50 each) — not applied at inference time.

N4ITK bias correction and MNI152 affine registration remain explicit
no-op stubs — the notebook implements them as optional and does not
apply them in the reported benchmark either.
"""
from __future__ import annotations

import io
import logging

import numpy as np
import torch
from PIL import Image

logger = logging.getLogger("btdm.services.preprocessing")

TARGET_SIZE = 224
Z_SCORE_EPSILON = 1e-8
SKULL_STRIP_PERCENTILE = 95
SKULL_STRIP_THRESHOLD_FRACTION = 0.10  # mask = img > (percentile_value * this fraction)
SKULL_STRIP_PADDING_FRACTION = 0.10



def load_standard_image(file_bytes: bytes) -> np.ndarray:
    """PNG/JPG/TIFF path — research-mode input only (no voxel spacing, no
    windowing metadata). Returns a grayscale float32 array in [0, 1]
    (0-1 here purely for image decoding; z-score normalization in
    `zscore_normalize` is the normalization that actually feeds the model,
    per §3.4 step 4)."""
    img = Image.open(io.BytesIO(file_bytes)).convert("L")
    return np.asarray(img, dtype=np.float32) / 255.0



def load_dicom(file_bytes: bytes) -> tuple[np.ndarray, dict]:
    """Real DICOM parsing + VOI LUT windowing + rescale slope/intercept.
    Returns (pixel_array_float32_0_1, metadata_dict).

    Handles MULTI-FRAME DICOM: modern scanner exports very commonly store
    an entire series (e.g. 28 axial slices) as ONE file with a 3D pixel
    array (frames, rows, cols) — this is standard, valid DICOM (see the
    NumberOfFrames tag), not a malformed file. An earlier version of this
    function assumed every DICOM was single-slice 2D and crashed on
    `.shape` unpacking for any multi-frame file — on a real hospital
    export mixing single- and multi-frame series, that silently excluded
    roughly HALF the real images. Fixed the same way multi-slice NIfTI
    is already handled: take the middle frame as the representative
    2D slice for this single-image-per-analysis pipeline."""
    import pydicom
    from pydicom.pixel_data_handlers.util import apply_voi_lut

    ds = pydicom.dcmread(io.BytesIO(file_bytes))
    arr = ds.pixel_array.astype(np.float32)

    num_frames = 1
    if arr.ndim == 3:
        num_frames = arr.shape[0]
        arr = arr[num_frames // 2]  # middle frame, same convention as load_nifti's middle slice
    elif arr.ndim > 3:
        # Shouldn't occur for standard MR (e.g. an unexpected 4D layout)
        # — rather than guess which axis is "frames", fail clearly.
        raise ValueError(f"Unsupported DICOM pixel array shape {ds.pixel_array.shape} — expected 2D or 3D (multi-frame)")

    slope = float(getattr(ds, "RescaleSlope", 1.0))
    intercept = float(getattr(ds, "RescaleIntercept", 0.0))
    arr = arr * slope + intercept

    try:
        arr = apply_voi_lut(arr, ds)
    except Exception as exc:  # pragma: no cover - defensive, not all DICOMs carry VOI LUT
        logger.warning("VOI LUT application failed (%s); using raw rescaled values", exc)

    arr_min, arr_max = arr.min(), arr.max()
    if arr_max - arr_min > 1e-6:
        arr = (arr - arr_min) / (arr_max - arr_min)
    else:
        arr = np.zeros_like(arr)

    metadata = {
        "patient_id_present": hasattr(ds, "PatientID"),
        "modality": getattr(ds, "Modality", None),
        "rows": getattr(ds, "Rows", None),
        "columns": getattr(ds, "Columns", None),
        "pixel_spacing": [float(x) for x in getattr(ds, "PixelSpacing", [1.0, 1.0])],
        "slice_thickness": float(getattr(ds, "SliceThickness", 1.0)),
        "num_frames": num_frames,
    }
    return arr, metadata


def load_nifti(file_bytes: bytes) -> tuple[np.ndarray, dict]:
    """Real NIfTI parsing via nibabel. Returns the full 3D volume
    (normalized) plus voxel spacing from the affine — caller selects which
    slice(s) to run 2D inference on, or adapts to a volumetric model."""
    import nibabel as nib
    import tempfile
    import os

    # nibabel needs a filesystem path for .nii.gz; write to a temp file.
    with tempfile.NamedTemporaryFile(suffix=".nii.gz", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        img = nib.load(tmp_path)
        data = img.get_fdata().astype(np.float32)
        spacing = img.header.get_zooms()[:3]
    finally:
        os.unlink(tmp_path)

    data_min, data_max = data.min(), data.max()
    if data_max - data_min > 1e-6:
        data = (data - data_min) / (data_max - data_min)

    metadata = {"voxel_spacing_mm": [float(s) for s in spacing], "shape": list(data.shape)}
    return data, metadata


def skull_strip_and_crop(
    image_2d: np.ndarray,
    percentile: float = SKULL_STRIP_PERCENTILE,
    threshold_fraction: float = SKULL_STRIP_THRESHOLD_FRACTION,
    padding_fraction: float = SKULL_STRIP_PADDING_FRACTION,
) -> np.ndarray:
    """
    Matches BTDM_FINAL.ipynb's `skull_strip_crop()` exactly:
      1. thresh = 95th percentile of NON-ZERO pixel values
      2. mask = image > thresh * 0.10  (a permissive low cutoff, not the
         percentile value itself — this catches essentially all brain
         tissue while excluding true background/skull margin)
      3. bounding box of the mask, padded by 10% of the box's height/width

    An earlier version of this function thresholded directly at the raw
    percentile value (`image >= percentile(image, 95)`), which is a much
    more restrictive mask than the notebook's actual `> 0.10 * percentile`
    — that mismatch would have silently cropped away real brain tissue
    relative to what the model was trained on.
    """
    nonzero = image_2d[image_2d > 0]
    if nonzero.size < 10:
        return image_2d

    threshold = float(np.percentile(nonzero, percentile))
    mask = image_2d > (threshold * threshold_fraction)

    if not mask.any():
        return image_2d

    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    r0, r1 = np.where(rows)[0][[0, -1]]
    c0, c1 = np.where(cols)[0][[0, -1]]

    h, w = image_2d.shape
    pad_r = max(1, int(padding_fraction * (r1 - r0)))
    pad_c = max(1, int(padding_fraction * (c1 - c0)))

    r0 = max(0, r0 - pad_r)
    r1 = min(h, r1 + pad_r + 1)
    c0 = max(0, c0 - pad_c)
    c1 = min(w, c1 + pad_c + 1)

    cropped = image_2d[r0:r1, c0:c1]
    return cropped if cropped.size > 0 else image_2d


def zscore_normalize(image_2d: np.ndarray, epsilon: float = Z_SCORE_EPSILON) -> np.ndarray:
    """
    Matches BTDM_FINAL.ipynb's step 4 exactly: mu/sigma are computed from
    NON-ZERO pixels only (background/cropped-out pixels don't skew the
    brain-tissue intensity statistics), but the resulting normalization
    is then applied to the WHOLE image — background zero pixels become
    `-mu / (sigma + eps)`, they are not masked out afterward.

    An earlier version of this function computed mu/sigma over ALL
    pixels including background zeros, which biases the statistics
    whenever the crop still contains a non-trivial zero-background
    margin — a silent train/inference mismatch.
    """
    nonzero = image_2d[image_2d != 0]
    if nonzero.size > 0:
        mu, sigma = float(nonzero.mean()), float(nonzero.std())
    else:
        mu, sigma = float(image_2d.mean()), float(image_2d.std())
    return (image_2d - mu) / (sigma + epsilon)


def n4_bias_correction(volume: np.ndarray) -> np.ndarray:
    """TODO: N4ITK bias correction (SimpleITK). The notebook implements
    this as an optional stage (see its `_sitk_ok` flag) and does not
    apply it in the reported benchmark — this stub intentionally does
    nothing, matching that scope."""
    return volume


def register_to_mni152(volume: np.ndarray) -> np.ndarray:
    """TODO: MNI152 affine registration (ANTs/SimpleITK). Same status as
    n4_bias_correction above — optional, not used in the reported
    benchmark, intentionally a no-op stub here."""
    return volume


def to_model_input(image_2d: np.ndarray, in_channels: int = 1) -> torch.Tensor:
    """
    Resize to TARGET_SIZE (bilinear, range-preserving — matches the
    notebook's step 3: rescale to uint8 for PIL's resize, then rescale
    back to the original min/max range before z-scoring) + z-score
    (step 4). Default in_channels=1, matching the model's single-channel
    input (§3.5.1 / notebook's `PatchEmbed`/`HybridExtractor`, both
    in_ch=1).
    """
    mn, mx = image_2d.min(), image_2d.max()
    value_range = mx - mn + 1e-8

    scaled_u8 = ((image_2d - mn) / value_range * 255).astype(np.uint8)
    resized_u8 = Image.fromarray(scaled_u8).resize((TARGET_SIZE, TARGET_SIZE), Image.BILINEAR)
    resized = np.asarray(resized_u8, dtype=np.float32) * (value_range / 255.0) + mn

    normalized = zscore_normalize(resized)
    tensor = torch.from_numpy(normalized).float().unsqueeze(0)  # (1, H, W)
    if in_channels > 1:
        tensor = tensor.repeat(in_channels, 1, 1)
    return tensor.unsqueeze(0)  # (1, C, H, W)
