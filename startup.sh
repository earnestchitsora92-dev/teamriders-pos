#!/usr/bin/env bash
# Azure App Service startup command for Team Riders POS.
set -euo pipefail

# Persistent storage on App Service lives under /home (survives restarts and deployments).
export MX_DATA_DIR="${MX_DATA_DIR:-/home/data}"
export MX_DB_PATH="${MX_DB_PATH:-/home/data/teamriders.db}"
# WAL is not reliable on the SMB-backed /home share, so use rollback journal there.
export MX_SQLITE_JOURNAL="${MX_SQLITE_JOURNAL:-delete}"
export PYTHONUNBUFFERED=1
mkdir -p "$MX_DATA_DIR"

cd /home/site/wwwroot
if [ -d /home/site/wwwroot/antenv ]; then
  # Virtualenv created by the Oryx build during zip deployment.
  # shellcheck disable=SC1091
  source /home/site/wwwroot/antenv/bin/activate
fi

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips="*"
