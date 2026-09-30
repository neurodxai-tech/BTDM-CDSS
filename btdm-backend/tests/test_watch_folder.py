"""
Training-folder watcher: import, stability wait, de-duplication, lock policy,
auto-activation of the newest checkpoint, and manual rollback.

    PYTHONPATH=. python tests/test_watch_folder.py
"""
import io
import json
import os
import sys
import tempfile
import time
import types
import zipfile
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp())
WATCH = TMP / "Modeltrained"
(WATCH / "runs" / "2026-09").mkdir(parents=True)
(TMP / "weights").mkdir()  # deliberately empty: the model must come from the training folder

os.environ.update(
    DATABASE_URL=f"sqlite:///{TMP / 'test.db'}",
    OBJECT_STORE_DIR=str(TMP / "objects"),
    MODEL_CACHE_DIR=str(TMP / "cache"),
    WEIGHTS_DIR=str(TMP / "weights"),
    ADMIN_BOOTSTRAP_PASSWORD="Long-test-pass2026",
    MODEL_WATCH_DIR=str(WATCH),
    MODEL_WATCH_LABEL="/path/to/Modeltrained",
    MODEL_WATCH_MIN_AGE="0",
    MODEL_WATCH_INTERVAL="0",
    MODEL_LOCKED="true",
    MODEL_AUTO_ACTIVATE="true",
)

torch_stub = mock.MagicMock()
torch_stub.cuda.is_available.return_value = False
for name in ["torch", "torch.nn", "torch.nn.functional", "torchvision", "torchvision.models", "cv2",
             "scipy", "scipy.ndimage", "scipy.spatial", "nibabel"]:
    sys.modules[name] = torch_stub
clf = types.ModuleType("app.models.classifier")
clf._meta = {}
clf.set_active_classifier = lambda model, meta: clf._meta.update(meta)
clf.active_meta = lambda: dict(clf._meta)
sys.modules["app.models.classifier"] = clf

import fakeredis  # noqa: E402

SERVER = fakeredis.FakeServer()
from app.services import events  # noqa: E402

events.client.cache_clear()
events.client = lambda: fakeredis.FakeRedis(server=SERVER, decode_responses=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import model_registry  # noqa: E402
from app.worker import Worker  # noqa: E402


def ckpt(name: str, payload: bytes = b"w") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}/data.pkl", payload)
    return buf.getvalue()


def write(rel: str, data: bytes, age_s: float) -> Path:
    p = WATCH / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def make_worker() -> Worker:
    w = Worker()

    def load_and_validate(row):
        path = w._fetch_checkpoint(row)
        if b"broken" in path.read_bytes():
            raise ValueError("Unexpected key(s) in state_dict: optimizer.state")
        return object(), {"param_count": 25057731, "smoke_test": "passed"}

    w._load_and_validate = load_and_validate
    return w


def cycle(w: Worker) -> None:
    """One worker loop iteration: scan, process every queued job, sync model."""
    w.scan_training_folder(force=True)
    r = events.client()
    while (item := r.lpop(events.JOB_QUEUE)) is not None:
        w.handle(json.loads(item))
    w.sync_active_model()


def active_version() -> str | None:
    a = model_registry.get_active()
    return a.version if a else None


def main():
    with TestClient(app) as c:
        tok = c.post("/api/v1/auth/login", json={"username": "admin", "password": "Long-test-pass2026"}).json()["token"]
        A = {"Authorization": f"Bearer {tok}"}
        assert active_version() is None, "no weights/classifier.pt, so nothing active yet"

        # Existing training outputs (settled: last modified hours ago)
        write("best_model_seed42.pth", ckpt("best_model_seed42", b"s42"), age_s=7200)
        write("best_model_seed456.pth", ckpt("best_model_seed456", b"s456"), age_s=3600)  # newer than seed42
        write("runs/2026-09/epoch_12_full.pt", ckpt("epoch_12_full", b"broken"), age_s=3000)
        write("notes.txt", b"not a model", age_s=3000)
        write(".ipynb_checkpoints/best_model_seed42-checkpoint.pth", ckpt("x", b"hidden"), age_s=3000)
        (WATCH / "copy_of_seed42.pth").write_bytes((WATCH / "best_model_seed42.pth").read_bytes())
        os.utime(WATCH / "copy_of_seed42.pth", (time.time() - 5000,) * 2)

        w = make_worker()
        cycle(w)
        models = {m.source_path: m for m in model_registry.list_all()}
        assert set(models) == {"best_model_seed42.pth", "best_model_seed456.pth", "runs/2026-09/epoch_12_full.pt"}, set(models)
        assert models["runs/2026-09/epoch_12_full.pt"].status == "failed"
        print("import: .pt/.pth found recursively; hidden, non-model and duplicate files skipped OK")

        # Locked + nothing active: the manuscript checkpoint is pinned, even though seed456 is newer
        assert active_version().startswith("best_model_seed42-"), active_version()
        assert w.loaded["checkpoint_id"] == "best_model_seed42"
        print("locked: Seed 42 auto-pinned (newer Seed 456 not used) OK")

        # A fresh file that training is still writing is not imported until it stops changing
        p = write("best_model_seed123.pth", ckpt("best_model_seed123", b"s123"), age_s=0)
        w.scan_training_folder(force=True)
        assert model_registry.find_by_sha(__import__("hashlib").sha256(p.read_bytes()).hexdigest()) is None
        cycle(w)  # second scan: unchanged -> imported, validated
        s123 = next(m for m in model_registry.list_all() if m.source_path == "best_model_seed123.pth")
        assert s123.status == "validated" and not s123.is_active
        assert active_version().startswith("best_model_seed42-"), "locked: still Seed 42"
        print("locked: new checkpoint imported + validated after it stopped changing, NOT activated OK")

        n_before = len(model_registry.list_all())
        cycle(w)
        cycle(w)
        assert len(model_registry.list_all()) == n_before, "unchanged files are not re-imported"
        print("re-scan: no duplicates OK")

        with mock.patch.object(model_registry, "MODEL_LOCKED", False):
            # Unlocked: a newer training output becomes active automatically
            write("best_model_seed789.pth", ckpt("best_model_seed789", b"s789"), age_s=0)
            cycle(w)
            cycle(w)
            assert active_version().startswith("best_model_seed789-"), active_version()
            assert w.loaded["version"].startswith("best_model_seed789-")
            print("unlocked: newest checkpoint auto-activated and hot-swapped OK")

            # An OLDER file validating later doesn't displace the newest one
            write("old_rerun.pth", ckpt("old_rerun", b"old"), age_s=90000)
            cycle(w)
            assert active_version().startswith("best_model_seed789-")
            print("unlocked: older file does not replace newer active model OK")

            # Admin rollback sticks until a newer file appears
            s42_id = next(m.id for m in model_registry.list_all() if m.source_path == "best_model_seed42.pth")
            assert c.post(f"/api/v1/models/{s42_id}/activate", headers=A).status_code == 200
            cycle(w)
            assert active_version().startswith("best_model_seed42-"), "manual rollback kept"
            # Retraining overwrites the same file name -> new content, imported as a new version
            write("best_model_seed789.pth", ckpt("best_model_seed789", b"s789-retrained"), age_s=0)
            cycle(w)
            cycle(w)
            assert active_version().startswith("best_model_seed789-") and w.loaded["sha256"] != s123.sha256
            print("unlocked: manual rollback respected; overwritten file re-imported and activated OK")

        state = c.get("/api/v1/models", headers=A).json()
        assert state["trainingFolder"]["checkpoint_files"] == 7, state["trainingFolder"]
        assert state["autoActivate"] is True and state["locked"] is True
        assert any(m["source"] == "watch-folder" and m["sourcePath"] for m in state["models"])
        print("API exposes training-folder status and file provenance OK")
    print("\nALL WATCH-FOLDER TESTS PASSED")


if __name__ == "__main__":
    main()
