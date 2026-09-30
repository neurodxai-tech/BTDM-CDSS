"""
Lesion segmentation model. Ships as a compact 2D U-Net baseline so this
scaffold runs end-to-end on a single slice/image today. Per the
architecture spec (§3.2), a real deployment should use a 3D model
(nnU-Net / Swin-UNETR) over registered multi-sequence volumes — swap this
class for that when you have a volumetric checkpoint; the rest of the
pipeline (services/inference.py) only depends on the
`predict_mask(tensor) -> tensor` contract, not this specific architecture.

Drop your trained checkpoint at CLASSIFIER_WEIGHTS_PATH's sibling,
SEGMENTATION_WEIGHTS_PATH (default ./weights/segmentation.pt), saved as a
plain state_dict. Output convention (matching BraTS-style labeling):
  0 = background, 1 = necrotic core, 2 = edema, 3 = enhancing tumour
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from app.config import SEGMENTATION_WEIGHTS_PATH

logger = logging.getLogger("btdm.models.segmentation")

SEG_CLASSES = ["background", "necrotic_core", "edema", "enhancing_tumour"]


def _double_conv(in_ch: int, out_ch: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, 3, padding=1),
        nn.BatchNorm2d(out_ch),
        nn.ReLU(inplace=True),
        nn.Conv2d(out_ch, out_ch, 3, padding=1),
        nn.BatchNorm2d(out_ch),
        nn.ReLU(inplace=True),
    )


class UNet2D(nn.Module):
    def __init__(self, in_channels: int = 1, num_classes: int = len(SEG_CLASSES), base: int = 32):
        super().__init__()
        self.enc1 = _double_conv(in_channels, base)
        self.enc2 = _double_conv(base, base * 2)
        self.enc3 = _double_conv(base * 2, base * 4)
        self.pool = nn.MaxPool2d(2)

        self.bottleneck = _double_conv(base * 4, base * 8)

        self.up3 = nn.ConvTranspose2d(base * 8, base * 4, 2, stride=2)
        self.dec3 = _double_conv(base * 8, base * 4)
        self.up2 = nn.ConvTranspose2d(base * 4, base * 2, 2, stride=2)
        self.dec2 = _double_conv(base * 4, base * 2)
        self.up1 = nn.ConvTranspose2d(base * 2, base, 2, stride=2)
        self.dec1 = _double_conv(base * 2, base)

        self.out_conv = nn.Conv2d(base, num_classes, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        e1 = self.enc1(x)
        e2 = self.enc2(self.pool(e1))
        e3 = self.enc3(self.pool(e2))
        b = self.bottleneck(self.pool(e3))

        d3 = self.dec3(torch.cat([self.up3(b), e3], dim=1))
        d2 = self.dec2(torch.cat([self.up2(d3), e2], dim=1))
        d1 = self.dec1(torch.cat([self.up1(d2), e1], dim=1))

        return self.out_conv(d1)  # (B, num_classes, H, W) — raw logits


_seg_instance: Optional[UNet2D] = None
_seg_weights_loaded: bool = False


def get_segmentation_model(device: str = "cpu") -> tuple[UNet2D, bool]:
    global _seg_instance, _seg_weights_loaded
    if _seg_instance is not None:
        return _seg_instance, _seg_weights_loaded

    model = UNet2D()
    loaded = _try_load_weights(model, SEGMENTATION_WEIGHTS_PATH)
    model.to(device)
    model.eval()

    _seg_instance = model
    _seg_weights_loaded = loaded
    return model, loaded


def _try_load_weights(model: nn.Module, path: Path) -> bool:
    if not path.exists():
        logger.warning(
            "No segmentation weights found at %s — segmentation output will "
            "be suppressed (no lesion volumes reported) until a checkpoint "
            "is provided.",
            path,
        )
        return False

    state_dict = torch.load(path, map_location="cpu", weights_only=True)
    result = model.load_state_dict(state_dict, strict=False)
    if result.missing_keys or result.unexpected_keys:
        logger.error(
            "Segmentation checkpoint at %s does not match UNet2D architecture. "
            "Missing: %s Unexpected: %s",
            path,
            result.missing_keys,
            result.unexpected_keys,
        )
    logger.info("Loaded segmentation weights from %s", path)
    return True


def voxel_volume_cm3(mask: torch.Tensor, class_idx: int, voxel_spacing_mm: tuple[float, float, float]) -> float:
    """Voxel count x voxel volume, converted mm3 -> cm3. For a single 2D
    slice, pass slice thickness as the third spacing dimension."""
    count = int((mask == class_idx).sum().item())
    voxel_mm3 = voxel_spacing_mm[0] * voxel_spacing_mm[1] * voxel_spacing_mm[2]
    return round(count * voxel_mm3 / 1000.0, 2)
