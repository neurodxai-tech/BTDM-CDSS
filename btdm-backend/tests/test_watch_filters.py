"""Include/exclude filters of the training-folder watcher, on the real
Modeltrained layout.   PYTHONPATH=. python tests/test_watch_filters.py"""
import tempfile
from pathlib import Path

from app.services import model_watcher

root = Path(tempfile.mkdtemp())
layout = [
    "Modeltables/results.csv",
    "Modelweights/model_config.json",
    "Modelweights/deployment_BTDM-CDSS/BTDM-CDSS_model_seed42.pth",
    "Modelweights/deployment_BTDM-CDSS/model_card.json",
    *[f"Modelweights/main_5seeds/best_model_seed{s}.pth" for s in (42, 123, 456, 789, 101112)],
    *[f"Modelweights/cross_validation/fold{i}_full.pth" for i in range(10)],
    "Modelweights/ablations/A2_ce_only.pth",
    "Modelweights/baselines/resnet50.pth",
    "Modelweights/.ipynb_checkpoints/x.pth",
]
for rel in layout:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def found(include, exclude=()):
    model_watcher.MODEL_WATCH_INCLUDE, model_watcher.MODEL_WATCH_EXCLUDE = list(include), list(exclude)
    w = model_watcher.FolderWatcher(root)
    return sorted(p.relative_to(root).as_posix() for p in w._candidates())


everything = found(["*"])
assert len(everything) == 18 and not any(".ipynb" in f for f in everything), everything
recommended = found(["Modelweights/deployment_BTDM-CDSS/*.pth", "Modelweights/main_5seeds/*.pth"])
assert len(recommended) == 6 and all("cross_validation" not in f and "ablations" not in f for f in recommended)
no_cv = found(["*"], ["Modelweights/cross_validation/*"])
assert len(no_cv) == 8
print("watch-folder filters OK:", len(everything), "all /", len(recommended), "recommended /", len(no_cv), "without CV")
