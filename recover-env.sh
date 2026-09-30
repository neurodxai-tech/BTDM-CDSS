#!/usr/bin/env bash
# Restores .env if it was lost. Uses, in order:
#   1. ~/btdm-env-backup   (made during setup)
#   2. the settings of the still-running containers
# Never prints the passwords.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then echo ".env already exists — nothing to do."; exit 0; fi
if [ -f "$HOME/btdm-env-backup" ]; then
  cp "$HOME/btdm-env-backup" .env && chmod 600 .env
  echo "Restored .env from ~/btdm-env-backup"; exit 0
fi
command -v docker >/dev/null || { echo "No backup and Docker is not available."; exit 1; }
python3 - <<'PY'
import os, subprocess, sys
def env(c):
    r = subprocess.run(["docker", "inspect", c, "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
                       capture_output=True, text=True)
    if r.returncode: sys.exit(f"Container {c} not found — cannot recover automatically.")
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
pg, api, wk = env("btdm-cdss-postgres-1"), env("btdm-cdss-api-1"), env("btdm-cdss-worker-1")
vals = {"POSTGRES_PASSWORD": pg["POSTGRES_PASSWORD"], "ADMIN_BOOTSTRAP_PASSWORD": api["ADMIN_BOOTSTRAP_PASSWORD"],
        "MODEL_TRAINING_DIR": '"%s"' % wk.get("MODEL_WATCH_LABEL", ""), "MODEL_LOCKED": api.get("MODEL_LOCKED", "true")}
out = [f"{k}={vals[k]}" if (k := l.split("=", 1)[0]) in vals and not l.startswith("#") else l
       for l in open(".env.example").read().splitlines()]
open(".env", "w").write("\n".join(out) + "\n"); os.chmod(".env", 0o600)
print("Recovered .env from the running containers.")
PY
cp .env "$HOME/btdm-env-backup"; chmod 600 "$HOME/btdm-env-backup"
echo "Also saved a copy to ~/btdm-env-backup"
