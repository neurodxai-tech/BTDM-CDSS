from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.config import CORS_ORIGINS, EXPECTED_CHECKPOINT_ID, MODEL_LOCKED
from app.routers import auth, models_registry, registrations, reports, studies, users
from app.services import events, model_registry
from app.services.auth import bootstrap_admin
from app.services.objectstore import store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("btdm.startup")

app = FastAPI(
    title="BTDM-CDSS API",
    description="Brain Tumour Clinical Decision Support System — research build, not a cleared medical device.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(studies.router)
app.include_router(reports.router)
app.include_router(models_registry.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(registrations.router)


@app.on_event("startup")
async def on_startup():
    db.init_db()
    store()
    bootstrap_admin()
    model_registry.bootstrap_from_weights_dir()
    active = model_registry.get_active()
    if active and active.checkpoint_id != EXPECTED_CHECKPOINT_ID:
        logger.warning(
            "Active checkpoint is '%s' but the manuscript's frozen artifact is '%s'.",
            active.checkpoint_id, EXPECTED_CHECKPOINT_ID,
        )


@app.get("/health")
async def health():
    """Liveness plus the facts an operator needs at a glance: are the
    database, Redis, object store and worker up, and which exact model is
    serving."""
    checks = {}
    try:
        with db.engine.connect() as c:
            c.exec_driver_sql("SELECT 1")
        checks["database"] = True
    except Exception:
        checks["database"] = False
    try:
        checks["redis"] = bool(events.client().ping())
    except Exception:
        checks["redis"] = False
    try:
        checks["object_store"] = store().ping()
    except Exception:
        checks["object_store"] = False

    active = model_registry.get_active() if checks["database"] else None
    worker_online, worker_model = model_registry.worker_state() if checks["redis"] else (False, None)
    checks["worker"] = worker_online
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "checks": checks,
        "model_locked": MODEL_LOCKED,
        "model_version": active.version if active else None,
        "checkpoint_id": active.checkpoint_id if active else None,
        "checkpoint_sha256": active.sha256 if active else None,
        "model_status": active.status if active else None,
        "worker_loaded_sha256": worker_model.get("sha256") if worker_model else None,
        "expected_checkpoint_id": EXPECTED_CHECKPOINT_ID,
        "checkpoint_matches_manuscript": bool(active and active.checkpoint_id == EXPECTED_CHECKPOINT_ID),
    }
