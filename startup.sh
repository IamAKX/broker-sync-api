#!/bin/bash
# EC2 systemd service ExecStart command (see docs/AWS_DEPLOYMENT.md).
# No ODBC driver install needed — asyncpg/psycopg are pure-Python/C extension
# PostgreSQL drivers with no external system package dependency.
set -e

exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 2 \
  --bind 0.0.0.0:8000 \
  --timeout 120
