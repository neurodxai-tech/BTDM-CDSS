# Changes

## 1.2.1 — admin command line

- `python -m app.manage` (run with `docker compose exec api …`): list accounts, reset a password (new 8-character one-time password), disable/enable, delete test accounts (optionally with their studies after typed confirmation), list/delete/purge registration requests. All actions are audited.
- Deleting an account also deletes the registration record that created it; the API supports deleting an account together with its studies when confirmed with the user ID.
- `POST /api/v1/users/{id}/reset-password` for administrators.

## 1.2.0 — password policy, final release

- One server-enforced password policy (`app/services/password_policy.py`): chosen passwords 8–64 characters with a letter and a number, not containing the user ID, not a common password; the same rules are shown and pre-checked in every form (`GET /api/v1/auth/password-policy`).
- One-time passwords for approved doctors and admin-created accounts are 8 characters (upper, lower, digit; no look-alikes) and must be replaced at first login. User IDs are 7–8 characters (`rad-###`, `ref-###`, `tech-###`); manually chosen IDs are 3–32 of a-z 0-9 . _ -.
- `recover-env.sh` restores a lost `.env` from `~/btdm-env-backup` or from the running containers; `start-all.command` runs it automatically.
- Existing accounts and passwords are unaffected.

## 1.1.0 — isolated doctor workspaces, hardened

- Workspace isolation verified end to end: every doctor sees only their own patients, images, analyses and reports; all other users, including administrators, get 404 on every study endpoint. Only the model is shared.
- Sessions end after 30 minutes of inactivity (sliding, server-enforced; `SESSION_IDLE_MINUTES`) and after 12 hours at most; the browser also signs out on its own after 30 idle minutes and in all tabs when one tab signs out.
- A user who owns patient data cannot be deleted, only disabled; their workspace stays intact and inaccessible to everyone else.
- Admin-generated passwords use the browser's cryptographic random generator.

## 1.0.0 — final deployment release

Consolidates 0.1–0.5 into one clean project.
- Login lockout: 10 failed attempts per username (30 per address) within 15 minutes returns 429 for 15 minutes.
- `PUBLIC_APP_URL`, `PUBLIC_API_URL` and `BIND_ADDRESS` in `.env` for serving a network behind an HTTPS proxy.
- Double-clickable `start-all.command` / `stop-all.command` (Colima + Docker).
- `.dockerignore` files; removed obsolete local-run scripts, duplicate READMEs and TESTING.md; one complete README (install, operation, backup/restore, upgrading, security).

## 0.5.0 — separate worklist per doctor

- Every study belongs to the user who uploaded it. Each doctor's **My worklist** shows only their own studies, and every study endpoint (view, analyse, results, Grad-CAM, live status, report, sign, delete) returns 404 for anyone else's study.
- Referring physicians can now upload, analyse and delete their own studies; signing reports remains radiologist-only.
- All users' analyses still run on the single shared worker and model (the pinned Seed 42 checkpoint); every result records its SHA-256.

## 0.4.0 — doctor registration with admin approval

- Public **Request an account** form (`/register`, linked from the login page): name, email, phone, NMC registration number, specialty, institution, requested role. Duplicate pending requests and bot submissions (honeypot field) are rejected.
- **Admin → Doctor registrations**: review pending requests, approve (optionally changing the role) or reject with a reason.
- On approval the account is created automatically: username by role (`rad-###`, `ref-###`, `tech-###`, next free number) and a random 12-character password shown to the admin **once** (only a hash is stored).
- The new doctor must change that password at first login; until then every other API call returns 403.
- Admins no longer see the Worklist tab and land on the Admin panel after login.
- Existing databases are upgraded automatically (new table + `users.must_change_password`); existing accounts are unaffected.

## 0.3.2

- Radiologists (doctors) can now upload studies, as well as technologists (frontend permission matrix and server-side check).

## 0.3.1

- Removed the MinIO service (its image is no longer available from Docker Hub). Images, Grad-CAM files and checkpoints now live in the shared Docker volume `btdm_objectstore`; S3-compatible storage remains available by setting `S3_ENDPOINT`.

## 0.3.0 — automatic import from the training folder

- The worker watches `MODEL_TRAINING_DIR` (mounted read-only) and imports new or changed `.pt`/`.pth` checkpoints after they stop changing; duplicates, hidden folders (e.g. `.ipynb_checkpoints`) and non-checkpoint files are skipped.
- Imported checkpoints are validated like uploads; wrapped (`model_state_dict`) and DataParallel checkpoints are accepted.
- Locked (default): Seed 42 is pinned when found; newer files are imported but not used. Unlocked: the newest validated file is activated automatically; manual rollbacks hold until a newer file appears.
- Registry shows each model's source file and save time, plus live training-folder status.

## 0.2.0 — persistent database, job queue, live status, model registry

Architecture
- PostgreSQL for users, studies, image metadata, jobs, results, reports, model registry and an append-only audit log.
- MinIO (S3 API) for image bytes, Grad-CAM PNGs and checkpoint files.
- Redis for the job queue, live status events and login sessions (hashed tokens, 12 h expiry).
- Separate inference worker (`python -m app.worker`) that keeps the model loaded; the API image no longer contains PyTorch.
- Server-Sent Events push queued → running → done/failed to the browser; automatic polling fallback. Reopening a study that is still analysing re-attaches to the live stream.

Model registry
- Upload checkpoints in Admin → Model registry; the worker validates each (strict key match, 25,057,731 parameters, fixed-input smoke test, SHA-256 check after download).
- `MODEL_LOCKED=true` by default: activation refused (HTTP 423) so the Seed 42 checkpoint stays frozen through Part 2.
- When unlocked, validated models hot-swap between jobs; a failed load keeps the previous model serving.
- Every result stores the model version and checkpoint SHA-256.

Behaviour
- Duplicate analysis requests reuse the running job; signed studies cannot be re-analysed; re-signing returns 409.
- New study status `failed` with the error shown on the study page.
- Worker restarts mark its interrupted jobs as failed instead of leaving them "analysing" forever.
- `/health` reports database, Redis, object store and worker status plus the active checkpoint.

## 0.1.1 — release clean-up

Model / manuscript consistency
- Model card now shows the manuscript's Seed 42 results (Table 3, Table 5) and pre-specified ANIAS criteria, including primary macro F1 ≥ 0.85 and IRC Protocol No. 520260158.
- Removed stale Seed 456 numbers, the old 24,957,379 parameter reference, and the internal "manuscript-guess" registry entry.
- Training-data note reworded: no-tumour = Br35H only is a source–class confound, not a safeguard.
- `/health` now reports the checkpoint's internal name (`checkpoint_id`); backend logs and the UI warn if it isn't `best_model_seed42`.

Security
- All study, result, Grad-CAM and report endpoints now require login; upload requires the technologist role and analysis requires technologist or radiologist (matching the frontend permission matrix).
- Frontend sends the session token on every call; Grad-CAM images are fetched with the token.
- No default admin password: the backend refuses to start without ADMIN_BOOTSTRAP_PASSWORD (≥ 12 characters).
- Constant-time password hash comparison.
- Docker ports bound to 127.0.0.1.

Uploads
- Per-file (200 MB), per-request (500 MB) and zip limits (1 GB expanded, 5,000 entries, compression ratio ≤ 100, nesting depth 1).
- DICOM headers de-identified at upload before storage (identifiers/dates blanked, UIDs remapped consistently, private tags removed). Pixel data unchanged.
- Folder paths stripped from skip messages (PACS folder names can contain patient names).

Packaging
- Flattened layout so docker-compose.yml, start-all.command and TESTING.md paths work from the repo root.
- Weights no longer baked into the Docker image or stored in git; mounted read-only at runtime.
- Removed node_modules, .next, Finder duplicates, __MACOSX, .DS_Store, __pycache__, logs, .env.local and the duplicate top-level classifier.py. Added .gitignore and README.

Bug fix
- `skull_strip_and_crop` now uses its `padding_fraction` argument (value unchanged at 0.10, so outputs are identical).
