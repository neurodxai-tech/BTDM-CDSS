"""
Post-hoc calibration (Guo et al., 2017 temperature scaling) so the
confidence number shown to clinicians reflects actual empirical accuracy
rather than raw (typically overconfident) softmax output.

Fit `temperature` ONCE on a held-out validation set after training:

    calib = TemperatureScaler()
    calib.fit(val_logits, val_labels)   # val_logits: (N, num_classes) tensor
    calib.save(CALIBRATION_PATH)

Drop the resulting calibration.json next to your weights; if absent, T
defaults to 1.0 (i.e. no calibration adjustment — raw softmax, which the
API marks as such in the model card).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

from app.config import CALIBRATION_PATH

logger = logging.getLogger("btdm.models.calibration")


class TemperatureScaler:
    def __init__(self, temperature: float = 1.0):
        self.temperature = temperature

    def apply(self, logits: torch.Tensor) -> torch.Tensor:
        return logits / self.temperature

    def fit(self, logits: torch.Tensor, labels: torch.Tensor, lr: float = 0.01, max_iter: int = 50) -> float:
        """Optimizes a single scalar temperature via LBFGS on validation NLL."""
        temp = nn.Parameter(torch.ones(1) * 1.5)
        optimizer = torch.optim.LBFGS([temp], lr=lr, max_iter=max_iter)
        nll = nn.CrossEntropyLoss()

        def closure():
            optimizer.zero_grad()
            loss = nll(logits / temp, labels)
            loss.backward()
            return loss

        optimizer.step(closure)
        self.temperature = float(temp.item())
        return self.temperature

    def save(self, path: Path = CALIBRATION_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"temperature": self.temperature}))

    @classmethod
    def load(cls, path: Path = CALIBRATION_PATH) -> "TemperatureScaler":
        if not path.exists():
            logger.warning(
                "No calibration file at %s — using T=1.0 (uncalibrated raw "
                "softmax). Fit TemperatureScaler on a validation set to "
                "activate calibrated confidence.",
                path,
            )
            return cls(temperature=1.0)
        data = json.loads(path.read_text())
        return cls(temperature=float(data["temperature"]))


def predictive_entropy(probs: torch.Tensor) -> float:
    """Shannon entropy of the class distribution, normalized to [0, 1] by
    dividing by log(num_classes). 0 = maximally confident, 1 = uniform/
    maximally uncertain."""
    eps = 1e-12
    entropy = -(probs * torch.log(probs + eps)).sum()
    max_entropy = torch.log(torch.tensor(float(probs.numel())))
    return float((entropy / max_entropy).item())
