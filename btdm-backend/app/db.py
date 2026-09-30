"""
Relational schema (PostgreSQL in deployment).

What lives here: users, studies, image metadata, analysis jobs, analysis
results, reports, Grad-CAM asset pointers, the model registry and an
append-only audit log. What does NOT live here: image bytes, Grad-CAM
PNGs and checkpoint files (object storage, referenced by key), and
sessions / the job queue (Redis).

Analysis results and reports are stored as JSON documents validated by the
Pydantic schemas in app/schemas.py, so the REST contract with the frontend
is unchanged; the columns that are queried or constrained (status, class,
model version, sign-off) are real columns next to the document.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from app.config import DATABASE_URL

JSONDoc = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    password_salt: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))
    institution: Mapped[str] = mapped_column(String(200), default="—")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set for accounts created with a generated password (doctor registration
    # approval); the user must choose their own password at first login.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegistrationRequest(Base):
    """A doctor's self-registration, waiting for an administrator's decision.
    Approval creates the user account; the generated password is shown to
    the administrator once and never stored in plain text."""

    __tablename__ = "registration_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # also the reference number
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200), index=True)
    phone: Mapped[str] = mapped_column(String(40))
    nmc_number: Mapped[str] = mapped_column(String(40), index=True)  # Nepal Medical Council reg. no.
    specialty: Mapped[str] = mapped_column(String(120))
    requested_role: Mapped[str] = mapped_column(String(32))
    institution: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(100), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    object_key: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # pending -> validated | failed ; exactly one row has is_active = True
    status: Mapped[str] = mapped_column(String(20), default="pending")
    validation_detail: Mapped[dict | None] = mapped_column(JSONDoc, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    externally_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    # upload | bootstrap | watch-folder ; for watch-folder imports, the file's
    # path relative to the training folder and its modification time (used
    # to decide which checkpoint is "latest").
    source: Mapped[str] = mapped_column(String(20), default="upload")
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Study(Base):
    __tablename__ = "studies"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    patient_alias: Mapped[str] = mapped_column(String(200))
    modality: Mapped[str] = mapped_column(String(16), default="MR")
    sequences: Mapped[list] = mapped_column(JSONDoc, default=list)
    status: Mapped[str] = mapped_column(String(32), index=True)
    ohif_study_instance_uid: Mapped[str] = mapped_column(String(100))
    skipped_file_count: Mapped[int] = mapped_column(Integer, default=0)
    skip_reasons: Mapped[list] = mapped_column(JSONDoc, default=list)
    uploaded_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    images: Mapped[list["StudyImage"]] = relationship(
        back_populates="study", cascade="all, delete-orphan", order_by="StudyImage.position"
    )


class StudyImage(Base):
    __tablename__ = "study_images"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # dicom | nifti | standard
    object_key: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))

    study: Mapped[Study] = relationship(back_populates="images")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    worker: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Analysis(Base):
    """Latest analysis per study (re-running replaces it; every run is still
    traceable through analysis_jobs + audit_log)."""

    __tablename__ = "analyses"
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_version_id: Mapped[int | None] = mapped_column(ForeignKey("model_versions.id"), nullable=True)
    model_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predicted_class: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    below_confidence_gate: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[dict] = mapped_column(JSONDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GradcamAsset(Base):
    __tablename__ = "gradcam_assets"
    __table_args__ = (UniqueConstraint("study_id", "image_id", "mode"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    image_id: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str] = mapped_column(String(300))


class Report(Base):
    __tablename__ = "reports"
    study_id: Mapped[str] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # preliminary | signed
    signed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document: Mapped[dict] = mapped_column(JSONDoc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    """Append-only. Never updated or deleted by the application."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    target: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONDoc, nullable=True)


# ── Engine / sessions ──────────────────────────────────────────────────────
_engine_kwargs: dict = {"pool_pre_ping": True, "future": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_engine(DATABASE_URL, **_engine_kwargs)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_fk(dbapi_conn, _):  # pragma: no cover - dev/test only
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# Columns added after the first release: (table, column, SQL type, default).
# create_all() only creates missing TABLES, so existing databases get these
# columns added here. Replace with Alembic migrations as the schema grows.
_ADDED_COLUMNS = [
    ("users", "must_change_password", "BOOLEAN", "FALSE"),
    ("model_versions", "source", "VARCHAR(20)", "'upload'"),
    ("model_versions", "source_path", "VARCHAR(500)", None),
    ("model_versions", "source_mtime", "TIMESTAMP", None),
]


def init_db() -> None:
    """Creates missing tables, then adds any columns introduced since the
    database was first created."""
    from sqlalchemy import inspect, text

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type, default in _ADDED_COLUMNS:
            existing = {c["name"] for c in insp.get_columns(table)}
            if column not in existing:
                ddl = f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                if default is not None:
                    ddl += f" DEFAULT {default}"
                conn.execute(text(ddl))
