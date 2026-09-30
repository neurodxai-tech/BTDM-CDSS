"""
Central config. Every path a trained-weights file needs to satisfy is
defined here — this is the single source of truth for "where do my
weights go" (see /weights/README.md for the exact file format expected).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WEIGHTS_DIR = Path(os.getenv("WEIGHTS_DIR", BASE_DIR / "weights"))

CLASSIFIER_WEIGHTS_PATH = WEIGHTS_DIR / "classifier.pt"
SEGMENTATION_WEIGHTS_PATH = WEIGHTS_DIR / "segmentation.pt"
CALIBRATION_PATH = WEIGHTS_DIR / "calibration.json"

CLASSES = ["glioma", "meningioma", "pituitary", "notumor"]
NUM_CLASSES = len(CLASSES)

# Confidence gate: below this, a study is flagged for mandatory specialist
# review regardless of predicted class (spec §3.4 / ANIAS Sec.3.10.5).
CONFIDENCE_GATE = float(os.getenv("CONFIDENCE_GATE", "0.70"))

# Model metadata surfaced in API responses / model card. Update VERSION and
# VALIDATED whenever you promote a newly-validated checkpoint — nothing
# should silently start claiming "validated" just because a file exists.
MODEL_NAME = "btdm-classify-seg-ensemble"
MODEL_VERSION = os.getenv("MODEL_VERSION", "v0.0.0-no-weights-loaded")
MODEL_EXTERNALLY_VALIDATED = os.getenv("MODEL_EXTERNALLY_VALIDATED", "false").lower() == "true"

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# ── Checkpoint identity ────────────────────────────────────────────────────
# torch.save() writes a zip whose top-level folder is the stem of the file
# name the checkpoint was ORIGINALLY saved under (e.g. "best_model_seed42"),
# and that name survives renaming the file to classifier.pt. /health reports
# it so nobody can mistake which seed is actually being served. The
# manuscript's frozen artifact is the Seed 42 checkpoint.
EXPECTED_CHECKPOINT_ID = os.getenv("EXPECTED_CHECKPOINT_ID", "best_model_seed42")


def read_checkpoint_id(path: Path = CLASSIFIER_WEIGHTS_PATH) -> str | None:
    import zipfile

    if not path.exists():
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            first = zf.namelist()[0]
        return first.split("/", 1)[0]
    except (zipfile.BadZipFile, IndexError, OSError):
        return None


# ── Upload limits (bytes) ──────────────────────────────────────────────────
# A single MR study export is typically tens to a few hundred MB. These caps
# stop one request (or one "zip bomb") from exhausting server memory.
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(200 * 1024 * 1024)))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(500 * 1024 * 1024)))
MAX_ZIP_UNCOMPRESSED_BYTES = int(os.getenv("MAX_ZIP_UNCOMPRESSED_BYTES", str(1024 * 1024 * 1024)))
MAX_ZIP_ENTRIES = int(os.getenv("MAX_ZIP_ENTRIES", "5000"))
MAX_ZIP_COMPRESSION_RATIO = float(os.getenv("MAX_ZIP_COMPRESSION_RATIO", "100"))

# ── Persistence & queue ────────────────────────────────────────────────────
# PostgreSQL in deployment (docker-compose sets this). The SQLite default
# exists only so the test-suite and a quick local run work without Docker.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'btdm-dev.db'}")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Object storage: S3-compatible (MinIO) when S3_ENDPOINT is set, otherwise a
# local directory (development only).
S3_ENDPOINT = os.getenv("S3_ENDPOINT")  # e.g. http://minio:9000
S3_BUCKET = os.getenv("S3_BUCKET", "btdm")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
OBJECT_STORE_DIR = Path(os.getenv("OBJECT_STORE_DIR", BASE_DIR / "object-store"))

# Where the worker keeps downloaded checkpoints (by SHA-256).
MODEL_CACHE_DIR = Path(os.getenv("MODEL_CACHE_DIR", BASE_DIR / "model-cache"))

SESSION_TTL_HOURS = float(os.getenv("SESSION_TTL_HOURS", "12"))  # absolute maximum
# Sessions also end after this much inactivity (shared workstations: the next
# person at the computer must not land in the previous doctor's workspace).
SESSION_IDLE_MINUTES = float(os.getenv("SESSION_IDLE_MINUTES", "30"))

# ── Model version lock ─────────────────────────────────────────────────────
# While true (the default, and REQUIRED during Part 2 external validation)
# the active model cannot be changed through the API: new checkpoints can be
# uploaded and validated, but not activated. Only an operator changing this
# setting and restarting the services can unlock it.
MODEL_LOCKED = os.getenv("MODEL_LOCKED", "true").lower() == "true"

# Architectural fingerprint every candidate checkpoint must match.
EXPECTED_PARAM_COUNT = int(os.getenv("EXPECTED_PARAM_COUNT", "25057731"))

# ── Training-folder watcher ────────────────────────────────────────────────
# Folder (inside the worker container) that is scanned for *.pt / *.pth files
# produced by training. New or changed files are imported into the registry
# and validated automatically. docker-compose mounts the host folder named by
# MODEL_TRAINING_DIR here, read-only.
_watch = os.getenv("MODEL_WATCH_DIR", "").strip()
MODEL_WATCH_DIR = Path(_watch) if _watch else None
MODEL_WATCH_LABEL = os.getenv("MODEL_WATCH_LABEL", _watch)  # host path, for display only
MODEL_WATCH_INTERVAL = float(os.getenv("MODEL_WATCH_INTERVAL", "30"))
# A file must be unchanged for this long before import (training may still be writing it).
MODEL_WATCH_MIN_AGE = float(os.getenv("MODEL_WATCH_MIN_AGE", "20"))
# Which files under the folder to import: comma-separated glob patterns on the
# path relative to the folder (e.g. "Modelweights/main_5seeds/*.pth").
# Default: everything. EXCLUDE wins over INCLUDE.
MODEL_WATCH_INCLUDE = [p.strip() for p in os.getenv("MODEL_WATCH_INCLUDE", "*").split(",") if p.strip()]
MODEL_WATCH_EXCLUDE = [p.strip() for p in os.getenv("MODEL_WATCH_EXCLUDE", "").split(",") if p.strip()]
# Switch to the newest validated checkpoint from the training folder as soon
# as it passes validation. Has NO effect while MODEL_LOCKED=true, except that
# with no active model at all, the manuscript checkpoint (EXPECTED_CHECKPOINT_ID)
# found in the folder is pinned.
MODEL_AUTO_ACTIVATE = os.getenv("MODEL_AUTO_ACTIVATE", "true").lower() == "true"
