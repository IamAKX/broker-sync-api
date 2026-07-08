#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ec2-user/broker-sync-api}"
BRANCH="${BRANCH:-main}"

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
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "[deploy] Restarting service..."
sudo systemctl daemon-reload
sudo systemctl restart brokersync

sleep 3
sudo systemctl status brokersync --no-pager

echo "[deploy] Health check..."
curl -fsS http://127.0.0.1:8000/health || true

echo "[deploy] Streaming live logs..."
sudo journalctl -u brokersync -f
