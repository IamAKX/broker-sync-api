#!/bin/bash
# App Service startup command (Linux, native Python runtime).
# The native Python image does not ship the ODBC driver aioodbc/pyodbc need,
# so it's installed here before the app starts, on every container boot.
set -e

if ! command -v odbcinst >/dev/null 2>&1 || ! odbcinst -q -d | grep -q "ODBC Driver 18 for SQL Server"; then
  curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb
  dpkg -i packages-microsoft-prod.deb
  rm packages-microsoft-prod.deb
  apt-get update
  ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc-dev
fi

exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 2 \
  --bind 0.0.0.0:8000 \
  --timeout 120
