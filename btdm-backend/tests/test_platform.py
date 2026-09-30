"""
Integration tests for the persistence / queue / registry layer.

Runs without PostgreSQL, Redis, MinIO or PyTorch: SQLite + fakeredis + the
local object store stand in, and the ML modules are stubbed. The code under
test (routers, storage, auth, registry, worker job handling) is the real code.

    cd btdm-backend
    pip install -r requirements-api.txt fakeredis
    PYTHONPATH=. python tests/test_platform.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp())
WEIGHTS = TMP / "weights"
WEIGHTS.mkdir()


def fake_checkpoint(name: str, payload: bytes = b"x") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}/data.pkl", payload)
        z.writestr(f"{name}/version", b"3")
    return buf.getvalue()


(WEIGHTS / "classifier.pt").write_bytes(fake_checkpoint("best_model_seed42"))
os.environ.update(
    DATABASE_URL=f"sqlite:///{TMP / 'test.db'}",
    OBJECT_STORE_DIR=str(TMP / "objects"),
    MODEL_CACHE_DIR=str(TMP / "cache"),
    WEIGHTS_DIR=str(WEIGHTS),
    ADMIN_BOOTSTRAP_PASSWORD="Long-test-pass2026",
    MODEL_VERSION="v1.0.0-seed42",
)

# ── stub ML stack ──────────────────────────────────────────────────────────
torch_stub = mock.MagicMock()
torch_stub.cuda.is_available.return_value = False
for name in ["torch", "torch.nn", "torch.nn.functional", "torchvision", "torchvision.models", "cv2",
             "scipy", "scipy.ndimage", "scipy.spatial", "nibabel"]:
    sys.modules[name] = torch_stub
classifier_stub = types.ModuleType("app.models.classifier")
classifier_stub._meta = {}
classifier_stub.set_active_classifier = lambda model, meta: classifier_stub._meta.update(meta)
classifier_stub.active_meta = lambda: dict(classifier_stub._meta)
sys.modules["app.models.classifier"] = classifier_stub

# ── fake Redis shared by sync + async clients ──────────────────────────────
import fakeredis
import fakeredis.aioredis
import redis.asyncio

SERVER = fakeredis.FakeServer()
from app.services import events  # noqa: E402

events.client.cache_clear()
events.client = lambda: fakeredis.FakeRedis(server=SERVER, decode_responses=True)
redis.asyncio.from_url = lambda *a, **k: fakeredis.aioredis.FakeRedis(server=SERVER, decode_responses=True)

from app.schemas import AnalysisResult, ClassProbabilities, ModelInfo, SegmentationVolumes, TumourClass  # noqa: E402


def fake_run_analysis(study_id, images):
    time.sleep(0.3)  # long enough for the SSE test to observe "running"
    inference_stub._gradcam_cache[study_id] = {images[0][0]: {"both": b"\x89PNG-both", "plain": b"\x89PNG-plain"}}
    return AnalysisResult(
        study_id=study_id, predicted_class=TumourClass("glioma"),
        probabilities=ClassProbabilities(glioma=0.9, meningioma=0.05, pituitary=0.03, notumor=0.02),
        calibrated_confidence=0.9, predictive_entropy=0.4, below_confidence_gate=False,
        segmentation=SegmentationVolumes(), primary_image_id=images[0][0],
        model=ModelInfo(name="btdm", version=classifier_stub._meta.get("version", "?"), validation_note="test",
                        checkpoint_id=classifier_stub._meta.get("checkpoint_id"),
                        checkpoint_sha256=classifier_stub._meta.get("sha256")),
        inferred_at=datetime.now(timezone.utc),
    )


inference_stub = types.ModuleType("app.services.inference")
inference_stub._gradcam_cache = {}
inference_stub.run_analysis = fake_run_analysis
sys.modules["app.services.inference"] = inference_stub

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import model_registry  # noqa: E402
from app.worker import Worker  # noqa: E402


def make_worker() -> Worker:
    w = Worker()

    def load_and_validate(row):
        w._fetch_checkpoint(row)  # real download + checksum verification
        if b"broken" in (TMP / "cache" / f"{row.sha256}.pt").read_bytes():
            raise ValueError("Missing keys: extractor.cnn.features.0.0.weight")
        return object(), {"param_count": 25057731, "smoke_test": "passed"}

    w._load_and_validate = load_and_validate
    return w


def drain(worker: Worker) -> int:
    r = events.client()
    n = 0
    while (item := r.lpop(events.JOB_QUEUE)) is not None:
        worker.handle(json.loads(item))
        n += 1
    worker.sync_active_model()
    return n


def main():
    with TestClient(app) as c:
        # ── startup / health ──────────────────────────────────────────
        h = c.get("/health").json()
        assert h["checks"]["database"] and h["checks"]["redis"] and h["checks"]["object_store"], h
        assert h["checkpoint_id"] == "best_model_seed42" and h["checkpoint_matches_manuscript"], h
        assert h["model_locked"] is True and not h["checks"]["worker"]
        print("startup + health OK (weights bootstrapped and pinned)")

        # ── auth & sessions in Redis ──────────────────────────────────
        tok = c.post("/api/v1/auth/login", json={"username": "admin", "password": "Long-test-pass2026"}).json()["token"]
        A = {"Authorization": f"Bearer {tok}"}
        for u, role in [("tech", "technologist"), ("rad", "radiologist")]:
            r = c.post("/api/v1/users", headers=A, json={"name": u, "username": u, "password": "Mri2026test", "role": role})
            assert r.status_code == 201, r.text
        login = lambda u: {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"username": u, "password": "Mri2026test"}).json()["token"]}
        T, R = login("tech"), login("rad")
        keys = [k for k in events.client().keys("btdm:session:*")]
        assert keys and all(len(k) == len("btdm:session:") + 64 for k in keys), "tokens stored hashed"
        assert all(0 < events.client().ttl(k) <= 12 * 3600 for k in keys), "sessions expire"
        print("sessions hashed in Redis with TTL OK")

        for _ in range(10):
            assert c.post("/api/v1/auth/login", json={"username": "rad", "password": "wrong-password"}).status_code == 401
        assert c.post("/api/v1/auth/login", json={"username": "rad", "password": "Mri2026test"}).status_code == 429
        events.client().delete("btdm:login-fail:user:rad")
        assert c.post("/api/v1/auth/login", json={"username": "rad", "password": "Mri2026test"}).status_code == 200
        print("login brute-force lockout (429 after 10 failures) OK")

        # ── upload → object store + DB ────────────────────────────────
        png = io.BytesIO()
        from PIL import Image
        Image.new("L", (64, 64), 128).save(png, format="PNG")
        r = c.post("/api/v1/studies", headers=R, files={"files": ("slice.png", png.getvalue(), "image/png")},
                   data={"patient_alias": "ANIAS-001"})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        assert list((TMP / "objects" / "studies" / sid).iterdir()), "image bytes in object store"
        print("upload persisted OK")

        # ── upload security: auth, de-identification, limits ──────────
        for path in ["/api/v1/studies", f"/api/v1/studies/{sid}", f"/api/v1/studies/{sid}/results",
                     f"/api/v1/studies/{sid}/gradcam", f"/api/v1/studies/{sid}/report", f"/api/v1/studies/{sid}/events"]:
            assert c.get(path).status_code == 401, path
        r2 = c.post("/api/v1/studies", headers=R, files={"files": ("a.png", png.getvalue())}, data={"patient_alias": "x"})
        assert r2.status_code == 201, "radiologists (doctors) can upload"
        c.delete(f"/api/v1/studies/{r2.json()['id']}", headers=R)
        ref_h = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={"username": "admin", "password": "Long-test-pass2026"}).json()["token"]}
        assert c.post("/api/v1/studies", headers=ref_h, files={"files": ("a.png", png.getvalue())},
                      data={"patient_alias": "x"}).status_code == 403, "admin can't upload"
        import numpy as np
        import pydicom
        from pydicom.dataset import Dataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian, generate_uid
        fm = FileMetaDataset(); fm.TransferSyntaxUID = ExplicitVRLittleEndian
        fm.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"; fm.MediaStorageSOPInstanceUID = generate_uid()
        ds = Dataset(); ds.file_meta = fm; ds.preamble = b"\0" * 128
        ds.is_little_endian, ds.is_implicit_VR = True, False
        ds.PatientName, ds.PatientID, ds.PatientBirthDate = "DOE^JOHN", "MRN12345", "19800101"
        ds.Modality, ds.SOPClassUID, ds.SOPInstanceUID = "MR", fm.MediaStorageSOPClassUID, fm.MediaStorageSOPInstanceUID
        ds.Rows = ds.Columns = 8; ds.BitsAllocated = 16; ds.BitsStored = 12; ds.HighBit = 11
        ds.PixelRepresentation = 0; ds.SamplesPerPixel = 1; ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelData = np.arange(64, dtype=np.uint16).tobytes()
        raw = io.BytesIO(); ds.save_as(raw, write_like_original=False)
        r = c.post("/api/v1/studies", headers=T, files={"files": ("IM0001", raw.getvalue())}, data={"patient_alias": "P2"})
        assert r.status_code == 201, r.text
        stored = next((TMP / "objects" / "studies" / r.json()["id"]).iterdir()).read_bytes()
        assert b"DOE^JOHN" not in stored and b"MRN12345" not in stored
        assert np.array_equal(pydicom.dcmread(io.BytesIO(stored)).pixel_array, ds.pixel_array)
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("x.bin", b"\0" * 20_000_000)
        assert c.post("/api/v1/studies", headers=T, files={"files": ("x.zip", bomb.getvalue())},
                      data={"patient_alias": "P"}).status_code == 413
        c.delete(f"/api/v1/studies/{r.json()['id']}", headers=T)
        print("auth + DICOM de-identification + zip-bomb guard OK")

        # ── analyze → queue; worker processes; SSE live stream ────────
        r = c.post(f"/api/v1/studies/{sid}/analyze", headers=R)
        assert r.status_code == 202
        job_id = r.json()["jobId"]
        assert c.post(f"/api/v1/studies/{sid}/analyze", headers=R).json()["jobId"] == job_id, "no duplicate jobs"
        assert c.get(f"/api/v1/studies/{sid}", headers=R).json()["status"] == "analyzing"

        worker = make_worker()
        worker.sync_active_model()
        assert worker.loaded and worker.loaded["checkpoint_id"] == "best_model_seed42"

        received = []

        def listen():
            with c.stream("GET", f"/api/v1/studies/{sid}/events", headers=R) as s:
                for line in s.iter_lines():
                    if line.startswith("data: "):
                        received.append(json.loads(line[6:]))

        t = threading.Thread(target=listen)
        t.start()
        time.sleep(0.5)
        drain(worker)
        t.join(timeout=10)
        statuses = [e.get("status") for e in received if e.get("type") == "status"]
        assert statuses[0] == "queued" and "running" in statuses and statuses[-1] == "done", statuses
        print("SSE live events OK:", statuses)

        res = c.get(f"/api/v1/studies/{sid}/results", headers=R).json()
        assert res["predictedClass"] == "glioma" and res["model"]["checkpointId"] == "best_model_seed42"
        with db.session_scope() as s:
            row = s.get(db.Analysis, sid)
            assert row.model_sha256 == h["checkpoint_sha256"] and row.model_version_id is not None
        assert c.get(f"/api/v1/studies/{sid}/gradcam?mode=plain", headers=R).content == b"\x89PNG-plain"
        assert c.get(f"/api/v1/studies/{sid}/job", headers=R).json()["status"] == "done"
        assert c.get(f"/api/v1/studies/{sid}", headers=R).json()["status"] == "report_pending_signoff"
        print("results + Grad-CAM + model traceability OK")

        # ── report sign-off and immutability ──────────────────────────
        assert c.patch(f"/api/v1/studies/{sid}/report", headers=R, json={"impression": "Findings c/w glioma."}).status_code == 200
        assert c.post(f"/api/v1/studies/{sid}/report/sign", headers=R).status_code == 200
        assert c.post(f"/api/v1/studies/{sid}/report/sign", headers=R).status_code == 409
        assert c.post(f"/api/v1/studies/{sid}/analyze", headers=R).status_code == 409, "signed studies not re-run"
        print("sign-off frozen OK")

        # ── model registry + lock ─────────────────────────────────────
        r = c.post("/api/v1/models", headers=A, files={"file": ("m.pt", fake_checkpoint("best_model_seed123", b"new"))},
                   data={"version": "v1.1.0-seed123"})
        assert r.status_code == 201 and r.json()["status"] == "pending", r.text
        new_id = r.json()["id"]
        bad = c.post("/api/v1/models", headers=A, files={"file": ("b.pt", fake_checkpoint("broken", b"broken"))},
                     data={"version": "v9-broken"}).json()["id"]
        assert c.post("/api/v1/models", headers=T, files={"file": ("m.pt", b"x")}, data={"version": "x"}).status_code == 403
        assert c.post("/api/v1/models", headers=A, files={"file": ("m.pt", b"not a zip")}, data={"version": "y"}).status_code == 409
        drain(worker)
        state = c.get("/api/v1/models", headers=R).json()
        by_id = {m["id"]: m for m in state["models"]}
        assert by_id[new_id]["status"] == "validated" and by_id[bad]["status"] == "failed", by_id
        assert state["locked"] and state["workerOnline"] is False  # no heartbeat in tests
        r = c.post(f"/api/v1/models/{new_id}/activate", headers=A)
        assert r.status_code == 423, r.text
        print("registry validation + lock (423) OK")

        with mock.patch.object(model_registry, "MODEL_LOCKED", False):
            assert c.post(f"/api/v1/models/{bad}/activate", headers=A).status_code == 409, "failed model can't activate"
            assert c.post(f"/api/v1/models/{new_id}/activate", headers=A).status_code == 200
        worker.sync_active_model()
        assert worker.loaded["version"] == "v1.1.0-seed123", "hot-swapped between jobs"
        h2 = c.get("/health").json()
        assert h2["checkpoint_id"] == "best_model_seed123" and not h2["checkpoint_matches_manuscript"]
        print("unlocked activation + hot swap OK")

        # ── session revocation ────────────────────────────────────────
        tech_id = next(u["id"] for u in c.get("/api/v1/users", headers=A).json() if u["username"] == "tech")
        c.patch(f"/api/v1/users/{tech_id}/active?active=false", headers=A)
        assert c.get("/api/v1/studies", headers=T).status_code == 401
        c.post("/api/v1/auth/logout", headers=R)
        assert c.get("/api/v1/studies", headers=R).status_code == 401
        print("session revocation OK")

        # ── audit trail ───────────────────────────────────────────────
        with db.session_scope() as s:
            actions = [a.action for a in s.query(db.AuditLog).all()]
        for needed in ["auth.login", "study.upload", "study.analyze", "report.sign", "model.register", "model.activate"]:
            assert needed in actions, needed
        print("audit trail OK")

    # ── persistence across a restart ──────────────────────────────────
    db.engine.dispose()
    with TestClient(app) as c2:
        tok = c2.post("/api/v1/auth/login", json={"username": "rad", "password": "Mri2026test"}).json()["token"]
        studies = c2.get("/api/v1/studies", headers={"Authorization": f"Bearer {tok}"}).json()
        assert studies and studies[0]["status"] == "report_signed"
    print("data survives restart OK")
    print("\nALL PLATFORM TESTS PASSED")


if __name__ == "__main__":
    main()
