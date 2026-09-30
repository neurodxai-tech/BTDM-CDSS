"""
Inference worker — run with:  python -m app.worker

The web API never runs the model. It queues jobs in Redis; this process
keeps the model loaded (warm) in memory and processes jobs one at a time:

  analyze         run the study through the pipeline, store results in
                  PostgreSQL, Grad-CAM PNGs in object storage, and publish
                  live status events for the browser
  validate_model  strict-load a newly registered checkpoint in a SEPARATE
                  model instance, check its fingerprint, run a smoke test,
                  record validated/failed

If MODEL_WATCH_DIR is set, it also scans the training output folder every
MODEL_WATCH_INTERVAL seconds and imports new checkpoints (see
services/model_watcher.py). Imported checkpoints that pass validation are
activated automatically only when MODEL_LOCKED=false.

Between jobs it checks which model the registry says is active. If that has
changed (possible only when MODEL_LOCKED=false), it loads and validates the
new checkpoint first and then swaps it in with a single reference
assignment; if anything fails, the old model keeps serving. A model is never
changed in the middle of a job.

Several workers can run side by side (e.g. one per GPU); each pulls from the
same queue.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import time
from datetime import datetime, timezone

from app import db
from app.config import MODEL_CACHE_DIR, MODEL_WATCH_DIR, MODEL_WATCH_INTERVAL
from app.schemas import StudyStatus
from app.services import events, model_registry, storage
from app.services.model_watcher import FolderWatcher
from app.services.objectstore import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("btdm.worker")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    def __init__(self):
        import torch

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.loaded: dict | None = None  # meta of the model currently serving
        self._stop = False
        self.watcher = FolderWatcher(MODEL_WATCH_DIR) if MODEL_WATCH_DIR else None
        self._next_scan = 0.0

    # ── model management ──────────────────────────────────────────────
    def _fetch_checkpoint(self, row) -> "os.PathLike":
        import hashlib

        path = MODEL_CACHE_DIR / f"{row.sha256}.pt"
        if not path.exists():
            store().download_to(row.object_key, path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != row.sha256:
            path.unlink(missing_ok=True)
            raise ValueError("Checkpoint checksum mismatch after download")
        return path

    def _load_and_validate(self, row):
        from app.models.classifier import load_checkpoint_strict, validate_model

        path = self._fetch_checkpoint(row)
        model = load_checkpoint_strict(path, self.device)
        detail = validate_model(model, self.device)
        detail.update(device=self.device, validated_at=datetime.now(timezone.utc).isoformat(), worker=WORKER_ID)
        return model, detail

    def sync_active_model(self) -> None:
        from app.models.classifier import set_active_classifier

        row = model_registry.get_active()
        if row is None:
            if self.loaded is not None:
                logger.warning("Registry has no active model; keeping %s loaded", self.loaded["version"])
            return
        if self.loaded and self.loaded["sha256"] == row.sha256:
            return
        if row.status == "failed":
            logger.error("Active model %s failed validation earlier — not loading it", row.version)
            return
        logger.info("Loading model %s (%s…)", row.version, row.sha256[:12])
        try:
            model, detail = self._load_and_validate(row)
        except Exception as exc:
            logger.exception("Model %s failed to load/validate; keeping the previous model", row.version)
            model_registry.mark(row.id, "failed", {"error": str(exc), "worker": WORKER_ID})
            return
        if row.status != "validated":
            model_registry.mark(row.id, "validated", detail)
        meta = {
            "id": row.id, "version": row.version, "sha256": row.sha256,
            "checkpoint_id": row.checkpoint_id, "externally_validated": row.externally_validated,
        }
        set_active_classifier(model, meta)  # atomic swap
        self.loaded = meta
        events.client().set(events.ACTIVE_MODEL_KEY, json.dumps({**meta, "device": self.device, "worker": WORKER_ID}))
        logger.info("Now serving %s on %s", row.version, self.device)

    # ── jobs ──────────────────────────────────────────────────────────
    def handle(self, job: dict) -> None:
        kind = job.get("type")
        if kind == "analyze":
            self.analyze(job["job_id"], job["study_id"])
        elif kind == "validate_model":
            self.validate(job["model_id"])
        else:
            logger.warning("Unknown job type %r", kind)

    def validate(self, model_id: int) -> None:
        row = model_registry.get(model_id)
        if row is None:
            return
        try:
            _, detail = self._load_and_validate(row)
            model_registry.mark(model_id, "validated", detail)
            logger.info("Model %s validated", row.version)
        except Exception as exc:
            model_registry.mark(model_id, "failed", {"error": str(exc), "worker": WORKER_ID})
            logger.warning("Model %s failed validation: %s", row.version, exc)
            return
        model_registry.on_watch_model_validated(model_id)  # no-op unless it came from the training folder

    def scan_training_folder(self, force: bool = False) -> None:
        if self.watcher is None or (not force and time.monotonic() < self._next_scan):
            return
        self._next_scan = time.monotonic() + MODEL_WATCH_INTERVAL
        try:
            self.watcher.scan()
        except Exception:
            logger.exception("Training-folder scan failed")

    def analyze(self, job_id: str, study_id: str) -> None:
        from app.services import inference, report_builder

        started = time.perf_counter()
        storage.update_job(job_id, status="running", started_at=datetime.now(timezone.utc), worker=WORKER_ID)
        events.publish_study(study_id, type="status", status="running", jobId=job_id)
        try:
            if self.loaded is None:
                raise RuntimeError("No validated active model is loaded on the inference worker")
            images = storage.get_images(study_id)
            if not images:
                raise RuntimeError("No image data stored for this study")
            events.publish_study(study_id, type="progress", message=f"Analysing {len(images)} image(s)", jobId=job_id)

            result = inference.run_analysis(study_id, images)
            variants = inference._gradcam_cache.pop(study_id, {})
            storage.save_gradcam_assets(study_id, variants)
            storage.save_analysis(result, job_id=job_id, model_version_id=self.loaded["id"],
                                  model_sha256=self.loaded["sha256"])
            study = storage.get_study(study_id)
            if study is not None:
                storage.save_report(report_builder.build_report(result, study))
            storage.set_status(study_id, StudyStatus.report_pending_signoff)

            ms = int((time.perf_counter() - started) * 1000)
            storage.update_job(job_id, status="done", finished_at=datetime.now(timezone.utc), duration_ms=ms)
            events.publish_study(study_id, type="status", status="done", jobId=job_id, durationMs=ms)
            logger.info("Study %s analysed in %d ms", study_id, ms)
        except Exception as exc:
            logger.exception("Analysis failed for study %s", study_id)
            ms = int((time.perf_counter() - started) * 1000)
            storage.update_job(job_id, status="failed", error=str(exc)[:2000],
                               finished_at=datetime.now(timezone.utc), duration_ms=ms)
            storage.set_status(study_id, StudyStatus.failed)
            events.publish_study(study_id, type="status", status="failed", jobId=job_id, error=str(exc)[:500])

    # ── main loop ─────────────────────────────────────────────────────
    def recover_interrupted_jobs(self) -> None:
        """Jobs this host left 'running' when it stopped are marked failed so
        they don't show as analysing forever; the user can re-run them."""
        from sqlalchemy import select

        host = socket.gethostname()
        with db.session_scope() as s:
            for job in s.scalars(select(db.AnalysisJob).where(db.AnalysisJob.status == "running")).all():
                if job.worker and job.worker.split(":")[0] == host:
                    job.status = "failed"
                    job.error = "Worker restarted while this job was running"
                    study = s.get(db.Study, job.study_id)
                    if study:
                        study.status = StudyStatus.failed.value

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_stop", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_stop", True))
        db.init_db()
        model_registry.bootstrap_from_weights_dir()
        self.recover_interrupted_jobs()
        if self.watcher:
            logger.info("Watching training folder %s every %.0f s", MODEL_WATCH_DIR, MODEL_WATCH_INTERVAL)
            self.scan_training_folder(force=True)
        self.sync_active_model()
        r = events.client()
        logger.info("Worker %s ready (device=%s)", WORKER_ID, self.device)
        while not self._stop:
            r.set(events.HEARTBEAT_KEY, WORKER_ID, ex=30)
            item = r.blpop(events.JOB_QUEUE, timeout=5)
            if item:
                try:
                    self.handle(json.loads(item[1]))
                except Exception:
                    logger.exception("Unhandled error processing job %s", item[1])
            self.scan_training_folder()
            self.sync_active_model()  # between jobs only
        logger.info("Worker stopping")


if __name__ == "__main__":
    Worker().run()
