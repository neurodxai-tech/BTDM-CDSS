"""
Classification model — rewritten to be a STRUCTURAL, KEY-FOR-KEY MATCH of
the actual training notebook (BTDM_FINAL.ipynb: GradRevFn, GRL,
PatchEmbed, TransEnc, HybridExtractor, BrainTumorModel classes).

An earlier version of this file used renamed modules
(cnn_branch/transformer_branch/classifier_head/domain_discriminator/
projection_head) that were architecturally equivalent but had DIFFERENT
state_dict KEYS than the real checkpoint. Loading a real checkpoint
against that version silently failed 100% of keys (all "unexpected", all
"missing") while still reporting weights_loaded=True — the worst kind of
bug, since it looked like it worked. This version's module names
(`extractor.cnn`, `extractor.pe`, `extractor.tr`, `domain_clf`,
`proj_head`, `classifier`) are copied directly from the notebook so a
checkpoint saved via `torch.save(model.state_dict(), ...)` there loads
with zero missing/unexpected keys here.

THIS IS THE FILE THAT LOADS YOUR TRAINED WEIGHTS.
Drop your checkpoint at CLASSIFIER_WEIGHTS_PATH (default
./weights/classifier.pt) as a plain state_dict.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models

from app.config import CLASSIFIER_WEIGHTS_PATH, NUM_CLASSES

logger = logging.getLogger("btdm.models.classifier")

# Architecture constants — match the notebook's Step 1 config cell exactly.
TRANSFORMER_DIM = 512
TRANSFORMER_HEADS = 8
TRANSFORMER_LAYERS = 6
TRANSFORMER_MLP_DIM = 2048
PATCH_SIZE = 16
IMG_SIZE = 224
NUM_SOURCES = 3  # Figshare, SARTAJ, Br35H
LAMBDA_ADV = 0.30  # lambda1, Eq. 8
LAMBDA_CON = 0.50  # lambda2, Eq. 12
JOINT_FEATURE_DIM = 1792  # 1280 (CNN) + 512 (Transformer)


# ── Gradient Reversal Layer (Eq. 5-6) — verbatim from the notebook ────────
class GradRevFn(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x, lam):
        ctx.lam = lam
        return x.view_as(x)

    @staticmethod
    def backward(ctx, g):
        return -ctx.lam * g, None


class GRL(nn.Module):
    def __init__(self, lam: float = LAMBDA_ADV):
        super().__init__()
        self.lam = lam

    def forward(self, x):
        return GradRevFn.apply(x, self.lam)


# ── Patch Embedding + learnable position embeddings — verbatim ───────────
class PatchEmbed(nn.Module):
    def __init__(self, img: int = 224, patch: int = 16, in_ch: int = 1, dim: int = 512):
        super().__init__()
        self.proj = nn.Conv2d(in_ch, dim, kernel_size=patch, stride=patch)
        n = (img // patch) ** 2  # 196 tokens
        self.pos = nn.Parameter(torch.zeros(1, n, dim))
        nn.init.trunc_normal_(self.pos, std=0.02)

    def forward(self, x):
        return self.proj(x).flatten(2).transpose(1, 2) + self.pos


# ── Transformer encoder, pre-LN, GELU — verbatim ──────────────────────────
class TransEnc(nn.Module):
    def __init__(self, d: int = 512, h: int = 8, L: int = 6, mlp: int = 2048, drop: float = 0.10):
        super().__init__()
        layer = nn.TransformerEncoderLayer(
            d_model=d, nhead=h, dim_feedforward=mlp,
            dropout=drop, activation="gelu",
            batch_first=True, norm_first=True,
        )
        self.enc = nn.TransformerEncoder(layer, num_layers=L)
        self.norm = nn.LayerNorm(d)

    def forward(self, x):
        return self.norm(self.enc(x)).mean(1)  # GAP -> (B, D)


# ── Hybrid extractor (Eq. 1-3) — verbatim ─────────────────────────────────
class HybridExtractor(nn.Module):
    def __init__(self, pretrained_imagenet: bool = True):
        super().__init__()
        weights = models.EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained_imagenet else None
        cnn = models.efficientnet_b0(weights=weights)
        orig = cnn.features[0][0]
        nc = nn.Conv2d(1, orig.out_channels, orig.kernel_size, orig.stride, orig.padding, bias=False)
        if pretrained_imagenet:
            with torch.no_grad():
                nc.weight.data = orig.weight.data.mean(dim=1, keepdim=True)
        cnn.features[0][0] = nc
        cnn.classifier = nn.Identity()
        cnn.avgpool = nn.AdaptiveAvgPool2d(1)
        self.cnn = cnn
        self.pe = PatchEmbed(IMG_SIZE, PATCH_SIZE, 1, TRANSFORMER_DIM)
        self.tr = TransEnc(TRANSFORMER_DIM, TRANSFORMER_HEADS, TRANSFORMER_LAYERS, TRANSFORMER_MLP_DIM)
        self.out_dim = JOINT_FEATURE_DIM

    def forward(self, x):
        return torch.cat([self.cnn(x).flatten(1), self.tr(self.pe(x))], 1)  # (B, 1792) — Eq. 3

    def last_conv_layer(self) -> nn.Module:
        """Grad-CAM target: the LAST raw nn.Conv2d found by iterating all
        submodules of the CNN — matches the notebook's gradcam() exactly
        (`for m in mdl.extractor.cnn.modules(): if isinstance(m, nn.Conv2d): last_conv=m`)."""
        last_conv = None
        for m in self.cnn.modules():
            if isinstance(m, nn.Conv2d):
                last_conv = m
        assert last_conv is not None, "No Conv2d found in CNN backbone"
        return last_conv


# ── Full model (Eq. 4, 8, 9, 12) — verbatim structure, renamed class ─────
class TumourClassifier(nn.Module):
    """Renamed from the notebook's `BrainTumorModel` for clarity in this
    codebase, but every submodule attribute name below is identical to
    the notebook so state_dict keys match exactly."""

    def __init__(
        self,
        num_classes: int = NUM_CLASSES,
        num_sources: int = NUM_SOURCES,
        la: float = LAMBDA_ADV,
        lc: float = LAMBDA_CON,
        pretrained_imagenet: bool = True,
    ):
        super().__init__()
        self.la = la
        self.lc = lc
        self.extractor = HybridExtractor(pretrained_imagenet=pretrained_imagenet)
        d = JOINT_FEATURE_DIM
        self.domain_clf = nn.Sequential(
            GRL(la), nn.Linear(d, 256), nn.ReLU(), nn.Dropout(0.50), nn.Linear(256, num_sources)
        )
        self.proj_head = nn.Sequential(
            nn.Linear(d, 512), nn.ReLU(), nn.Dropout(0.10), nn.Linear(512, 128)
        )
        self.classifier = nn.Sequential(
            nn.Linear(d, 256), nn.ReLU(), nn.Dropout(0.50), nn.Linear(256, num_classes)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Inference forward — classification logits only (the shape the
        rest of this backend, and Grad-CAM, expect). The notebook's own
        forward() returns a 3-tuple (logits, domain_logits, proj) — see
        forward_full() below for that exact behavior if ever needed."""
        f = self.extractor(x)
        return self.classifier(f)

    def forward_full(self, x: torch.Tensor):
        """Matches BrainTumorModel.forward() in the notebook exactly:
        returns (logits, domain_logits, normalized_projection)."""
        f = self.extractor(x)
        return self.classifier(f), self.domain_clf(f), F.normalize(self.proj_head(f), dim=1)

    def count_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)

    def last_conv_layer(self) -> nn.Module:
        return self.extractor.last_conv_layer()


_classifier_instance: Optional[TumourClassifier] = None
_weights_loaded: bool = False


def get_classifier(device: str = "cpu") -> tuple[TumourClassifier, bool]:
    """
    Returns (model, weights_loaded). Cached after first call.
    weights_loaded=False means the model is running with ImageNet-only /
    random init — callers MUST treat predictions as non-clinical
    placeholder output in that case (services/inference.py checks this
    flag and marks every result accordingly).
    """
    global _classifier_instance, _weights_loaded
    if _classifier_instance is not None:
        return _classifier_instance, _weights_loaded

    has_checkpoint = CLASSIFIER_WEIGHTS_PATH.exists()
    model = TumourClassifier(pretrained_imagenet=not has_checkpoint)
    weights_loaded = _try_load_weights(model, CLASSIFIER_WEIGHTS_PATH)
    model.to(device)
    model.eval()

    _classifier_instance = model
    _weights_loaded = weights_loaded
    return model, weights_loaded


def _try_load_weights(model: nn.Module, path: Path) -> bool:
    if not path.exists():
        logger.warning(
            "No classifier weights found at %s — serving with ImageNet-only "
            "init on the CNN branch and random init elsewhere. Predictions "
            "are placeholder only. Drop your trained state_dict at this "
            "path to activate real inference.",
            path,
        )
        return False

    state_dict = torch.load(path, map_location="cpu", weights_only=True)
    result = model.load_state_dict(state_dict, strict=False)

    if result.missing_keys or result.unexpected_keys:
        logger.error(
            "Classifier checkpoint at %s does not fully match the model "
            "architecture.\nMissing keys: %s\nUnexpected keys: %s\n"
            "Loading proceeded with strict=False but this is very likely "
            "wrong — verify TumourClassifier's architecture (this file) "
            "matches your training script's module names and dimensions.",
            path,
            result.missing_keys,
            result.unexpected_keys,
        )
    else:
        logger.info("Checkpoint keys matched exactly — zero missing, zero unexpected.")

    logger.info("Loaded classifier weights from %s", path)
    return True


def reset_cache() -> None:
    """Used by tests / hot-reload after dropping in a new checkpoint."""
    global _classifier_instance, _weights_loaded
    _classifier_instance = None
    _weights_loaded = False


# ── Registry-driven loading (used by the worker) ───────────────────────────
# The worker, not the web API, owns the model. It loads a checkpoint with
# strict key matching, validates it, and only then installs it here with
# set_active_classifier(); inference.run_analysis() then picks it up via
# get_classifier(). Installing is a single reference swap between jobs, so
# no request ever sees a half-loaded model.
_active_meta: dict = {}


def load_checkpoint_strict(path: Path, device: str = "cpu") -> TumourClassifier:
    model = TumourClassifier(pretrained_imagenet=False)
    obj = torch.load(path, map_location="cpu", weights_only=True)
    # Training scripts often save {"model_state_dict": ..., "optimizer": ..., "epoch": ...}
    # rather than the bare state_dict; accept the common wrappers.
    if isinstance(obj, dict):
        for key in ("model_state_dict", "state_dict", "model"):
            if isinstance(obj.get(key), dict):
                obj = obj[key]
                break
    if not isinstance(obj, dict):
        raise ValueError("Checkpoint does not contain a state_dict")
    # Strip the "module." prefix added by nn.DataParallel / DDP.
    if obj and all(k.startswith("module.") for k in obj):
        obj = {k[len("module."):]: v for k, v in obj.items()}
    model.load_state_dict(obj, strict=True)  # any missing/unexpected key raises
    model.to(device)
    model.eval()
    return model


def validate_model(model: TumourClassifier, device: str = "cpu") -> dict:
    """Architectural fingerprint + deterministic smoke test. Raises
    ValueError on failure; returns details to store on the registry row."""
    from app.config import EXPECTED_PARAM_COUNT

    n_params = model.count_params()
    if n_params != EXPECTED_PARAM_COUNT:
        raise ValueError(f"Parameter count {n_params:,} != expected {EXPECTED_PARAM_COUNT:,}")
    gen = torch.Generator().manual_seed(0)
    x = torch.randn(2, 1, IMG_SIZE, IMG_SIZE, generator=gen).to(device)
    with torch.no_grad():
        logits = model(x)
    if tuple(logits.shape) != (2, NUM_CLASSES):
        raise ValueError(f"Unexpected output shape {tuple(logits.shape)}")
    if not torch.isfinite(logits).all():
        raise ValueError("Non-finite logits on the smoke-test input")
    # Rounded logits on a fixed input: a reproducibility fingerprint that
    # lets you confirm two servers are running numerically the same model.
    fingerprint = [round(float(v), 4) for v in logits[0].cpu()]
    return {"param_count": n_params, "smoke_test": "passed", "logit_fingerprint": fingerprint}


def set_active_classifier(model: TumourClassifier, meta: dict) -> None:
    global _classifier_instance, _weights_loaded, _active_meta
    _active_meta = dict(meta)
    _weights_loaded = True
    _classifier_instance = model


def clear_active_classifier() -> None:
    global _classifier_instance, _weights_loaded, _active_meta
    _classifier_instance = None
    _weights_loaded = False
    _active_meta = {}


def active_meta() -> dict:
    return dict(_active_meta)
