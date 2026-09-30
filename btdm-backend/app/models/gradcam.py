"""
Real Grad-CAM (Selvaraju et al.) computed via forward/backward hooks on the
classifier's last conv layer. This replaces the hand-placed Gaussian
"fake CAM" from the original static demo — this one reflects actual
gradient flow through your trained weights.

Also includes the "fused attention" combination (Grad-CAM + pixel
saliency + edge structure) that the training notebook itself explored
(BTDM_FINAL.ipynb, "Fused Grad-CAM" cell) — sharper and more
boundary-aware than raw Grad-CAM alone, which is what
weak_localization.py thresholds into a decision-ready region.

PERFORMANCE NOTE (fixed from an earlier version): computing Grad-CAM and
saliency used to be TWO separate forward+backward passes through the
whole hybrid CNN-Transformer network, and each `GradCAM(model)`
instantiation registered NEW hooks on the model's target layer without
ever removing them — since the model is cached globally for the
lifetime of the backend process (app/models/classifier.py's
get_classifier() cache), those hooks accumulated forever: every image
ever analyzed, in any study, added 2 more permanent hooks that kept
firing on every subsequent forward/backward pass. For a multi-image
upload (a full DICOM series can be dozens of slices) this meant each
image in the SAME batch was measurably slower than the last, and every
study analyzed afterward inherited that growing weight too. Fixed here
by (1) always removing hooks in a try/finally right after use, and
(2) computing Grad-CAM's layer gradients and the input-pixel saliency
map from a SINGLE forward+backward pass instead of two — autograd
populates gradients for the entire graph (both the hooked intermediate
layer and the leaf input tensor) from one backward() call, so doing it
twice was pure waste.
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F

from app.models.classifier import TumourClassifier


class GradCAM:
    def __init__(self, model: TumourClassifier):
        self.model = model
        self.target_layer = model.last_conv_layer()
        self._activations: torch.Tensor | None = None
        self._gradients: torch.Tensor | None = None
        self._forward_handle = self.target_layer.register_forward_hook(self._save_activations)
        self._backward_handle = self.target_layer.register_full_backward_hook(self._save_gradients)

    def _save_activations(self, module, inp, out):
        self._activations = out.detach()

    def _save_gradients(self, module, grad_in, grad_out):
        self._gradients = grad_out[0].detach()

    def remove(self) -> None:
        """MUST be called after use (see compute_fused_attention below,
        which does this in a try/finally) — otherwise these hooks stay
        registered on the shared cached model forever."""
        self._forward_handle.remove()
        self._backward_handle.remove()

    def _compute_cam(self, size: tuple[int, int]) -> torch.Tensor:
        assert self._activations is not None and self._gradients is not None, (
            "Hooks did not fire — check that target_layer is part of the "
            "forward graph for this model."
        )
        weights = self._gradients.mean(dim=(2, 3), keepdim=True)  # (1, C, 1, 1)
        cam = F.relu((weights * self._activations).sum(dim=1, keepdim=True))  # (1,1,h,w)
        cam = F.interpolate(cam, size=size, mode="bilinear", align_corners=False)
        cam = cam.squeeze()

        cam_min, cam_max = cam.min(), cam.max()
        if (cam_max - cam_min).item() > 1e-8:
            cam = (cam - cam_min) / (cam_max - cam_min)
        else:
            cam = torch.zeros_like(cam)
        return cam


def edge_features(image_2d: np.ndarray) -> np.ndarray:
    """np.gradient-based edge magnitude — matches the notebook's
    edge_features() exactly. Used as the "structure" term in the fusion."""
    gx, gy = np.gradient(image_2d)
    mag = np.sqrt(gx**2 + gy**2)
    mn, mx = mag.min(), mag.max()
    return (mag - mn) / (mx - mn + 1e-8)


def fused_attention(
    model: TumourClassifier,
    input_tensor: torch.Tensor,
    class_idx: int,
    image_2d_for_edges: np.ndarray,
) -> np.ndarray:
    """
    CAM-GATED fusion, computed from a SINGLE forward+backward pass (see
    module docstring for why this used to be two separate passes):

        fused = cam * (0.5 + 0.25*saliency + 0.25*edges)

    Grad-CAM remains the primary spatial signal (the only one of the
    three that's actually class-conditional); saliency and edges add
    sub-region detail rather than independent evidence — this also fixes
    an earlier failure mode where additive edges/saliency dragged up the
    WHOLE image's floor (edge structure exists almost everywhere in a
    brain MRI), producing a "region" covering most of the brain instead
    of a focal area.
    """
    input_tensor = input_tensor.clone().requires_grad_(True)
    gradcam = GradCAM(model)
    try:
        model.zero_grad()
        logits = model(input_tensor)
        score = logits[0, class_idx]
        score.backward()

        cam = gradcam._compute_cam(size=input_tensor.shape[-2:])

        assert input_tensor.grad is not None, "Input gradient missing — backward() did not reach the input tensor."
        saliency = input_tensor.grad.abs().max(dim=1)[0][0]
        sal_min, sal_max = saliency.min(), saliency.max()
        if (sal_max - sal_min).item() > 1e-8:
            saliency = (saliency - sal_min) / (sal_max - sal_min)
        else:
            saliency = torch.zeros_like(saliency)

        cam_np = cam.detach().cpu().numpy()
        sal_np = saliency.detach().cpu().numpy()
    finally:
        gradcam.remove()

    edges = edge_features(image_2d_for_edges)

    modulation = 0.5 + 0.25 * sal_np + 0.25 * edges  # in [0.5, 1.0]
    fused = cam_np * modulation

    mn, mx = fused.min(), fused.max()
    return (fused - mn) / (mx - mn + 1e-8)
