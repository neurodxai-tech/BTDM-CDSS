#!/usr/bin/env bash
# Double-click to start BTDM-CDSS (macOS, Colima + Docker).
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -f .env ]; then
  echo "No .env file found — trying to restore it..."
  ./recover-env.sh || { echo "Could not restore .env. See README section 7."; read -p "Press Enter to close"; exit 1; }
fi
if command -v colima >/dev/null && ! colima status >/dev/null 2>&1; then
  echo "Starting the Docker engine (Colima)..."
  colima start --cpu 4 --memory 8 --disk 60
fi
echo "Starting BTDM-CDSS..."
docker compose up -d
echo "Waiting for the API..."
for i in $(seq 1 60); do curl -sf http://localhost:8000/health >/dev/null && break; sleep 2; done
curl -s http://localhost:8000/health; echo
open http://localhost:3000
read -p "BTDM-CDSS is running. Press Enter to close this window (the app keeps running)."
