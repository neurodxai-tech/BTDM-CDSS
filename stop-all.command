#!/usr/bin/env bash
# Double-click to stop BTDM-CDSS. Your data is kept.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
docker compose down
read -p "BTDM-CDSS stopped. Press Enter to close."
