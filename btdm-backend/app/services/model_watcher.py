"""
Watches the model-training output folder and imports new checkpoints.

Every MODEL_WATCH_INTERVAL seconds the worker calls FolderWatcher.scan():

  * finds *.pt / *.pth files anywhere under the folder (hidden files and
    folders are skipped) that match MODEL_WATCH_INCLUDE and not
    MODEL_WATCH_EXCLUDE
  * waits until a file has stopped changing, so a checkpoint that training
    is still writing is never imported half-finished
  * skips files already imported (same path, size and modification time,
    or the same SHA-256 under any name)
  * registers the rest as source="watch-folder"; the worker then validates
    each one, and model_registry.on_watch_model_validated() decides whether
    it becomes the active model

The folder is mounted read-only; nothing in it is ever modified or deleted.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import MODEL_WATCH_EXCLUDE, MODEL_WATCH_INCLUDE, MODEL_WATCH_LABEL, MODEL_WATCH_MIN_AGE
from app.services import events, model_registry

logger = logging.getLogger("btdm.services.model_watcher")

EXTENSIONS = {".pt", ".pth"}
MAX_FILE_BYTES = 1024 * 1024 * 1024
WATCHER_STATE_KEY = "btdm:worker:watcher"
# A file untouched for this long is treated as complete even on first sight
# (so existing checkpoints are imported on the first scan after start-up).
SETTLED_AFTER_S = 300


class FolderWatcher:
    def __init__(self, root: Path):
        self.root = root
        self._last_sig: dict[str, tuple[int, float]] = {}
        self._handled: set[tuple[str, int, float]] = set()
        self.last_scan: float | None = None

    def _candidates(self) -> list[Path]:
        out = []
        for p in self.root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in EXTENSIONS:
                continue
            rel = p.relative_to(self.root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            rel_s = rel.as_posix()
            if not any(fnmatch.fnmatch(rel_s, pat) for pat in MODEL_WATCH_INCLUDE):
                continue
            if any(fnmatch.fnmatch(rel_s, pat) for pat in MODEL_WATCH_EXCLUDE):
                continue
            out.append(p)
        return sorted(out, key=lambda p: p.stat().st_mtime)

    def scan(self) -> list[int]:
        """Returns ids of newly registered models."""
        self.last_scan = time.time()
        if not self.root.is_dir():
            self._publish(found=0, error=f"Folder not found: {self.root}")
            return []

        now = time.time()
        registered: list[int] = []
        files = self._candidates()
        for path in files:
            rel = path.relative_to(self.root).as_posix()
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            sig = (st.st_size, st.st_mtime)
            if (rel, *sig) in self._handled:
                continue

            age = now - st.st_mtime
            seen_unchanged = self._last_sig.get(rel) == sig
            self._last_sig[rel] = sig
            if age < MODEL_WATCH_MIN_AGE or not (seen_unchanged or age >= SETTLED_AFTER_S):
                continue  # still being written, or first sighting of a fresh file

            if st.st_size > MAX_FILE_BYTES:
                logger.warning("Skipping %s: larger than 1 GB", rel)
                self._handled.add((rel, *sig))
                continue

            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
            if model_registry.find_by_source(rel, st.st_size, mtime):
                self._handled.add((rel, *sig))
                continue

            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if model_registry.find_by_sha(sha):
                self._handled.add((rel, *sig))
                continue

            version = f"{path.stem}-{sha[:8]}"
            try:
                row = model_registry.register(
                    data, version,
                    notes=f"Auto-imported from training folder: {rel}",
                    created_by=None, source="watch-folder", source_path=rel, source_mtime=mtime,
                )
                registered.append(row.id)
                logger.info("Imported %s from the training folder as %s", rel, version)
            except ValueError as exc:
                logger.warning("Not importing %s: %s", rel, exc)
            self._handled.add((rel, *sig))

        self._publish(found=len(files))
        return registered

    def _publish(self, found: int, error: str | None = None) -> None:
        try:
            events.client().set(WATCHER_STATE_KEY, json.dumps({
                "folder": MODEL_WATCH_LABEL or str(self.root),
                "last_scan": datetime.now(timezone.utc).isoformat(),
                "checkpoint_files": found,
                "error": error,
            }), ex=600)
        except Exception:  # pragma: no cover
            logger.debug("Could not publish watcher state", exc_info=True)


def watcher_state() -> dict | None:
    raw = events.client().get(WATCHER_STATE_KEY)
    return json.loads(raw) if raw else None
