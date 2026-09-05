#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ec2-user/broker-sync-api}"
BRANCH="${BRANCH:-main}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

if [[ ! -d "$REPO_DIR" ]]; then
  echo "[deploy] Repository directory not found: $REPO_DIR"
  exit 1
fi

echo "[deploy] Updating source code from git..."
cd "$REPO_DIR"
git fetch --all --prune
git checkout "$BRANCH"
git pull origin "$BRANCH"

echo "[deploy] Rebuilding virtualenv..."
rm -rf .venv
"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Applies any new migrations/central/versions/*.py the code being deployed
# depends on. Missing this step is exactly what caused a "Sync Now"/
# "Internal Server Error" outage on 2026-09-05: 6290fc2 shipped new EodBar
# columns (migration 0007) and this script deployed that code straight from
# git pull with no migration step at all, so the running app queried
# columns (or_high, call_strike_highest_oi, ...) that didn't exist yet on
# the RDS schema (still at 0006) — every /inception/bars call (the Local
# Data Sync "Sync Now"/"Full Resync" buttons) 500'd with asyncpg's
# UndefinedColumnError until this was run by hand. Runs before the service
# is stopped below so a bad migration aborts the deploy (set -euo pipefail)
# without taking down the currently-running (old-schema-compatible) service.
echo "[deploy] Running database migrations..."
alembic -c alembic_central.ini upgrade head

echo "[deploy] Stopping existing service if running..."
if sudo systemctl is-active --quiet brokersync; then
  sudo systemctl stop brokersync
fi

if pgrep -f "uvicorn|gunicorn" >/dev/null 2>&1; then
  echo "[deploy] Stopping stray app processes..."
  pkill -f "uvicorn|gunicorn" || true
fi

echo "[deploy] Restarting service..."
sudo systemctl daemon-reload
sudo systemctl restart brokersync

sleep 3
sudo systemctl status brokersync --no-pager

echo "[deploy] Health check..."
curl -fsS http://127.0.0.1:8000/health || true

echo "[deploy] Streaming live logs..."
sudo journalctl -u brokersync -f
