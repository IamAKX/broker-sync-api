#!/bin/bash
# EC2 systemd service ExecStart command (see docs/AWS_DEPLOYMENT.md).
# No ODBC driver install needed — asyncpg/psycopg are pure-Python/C extension
# PostgreSQL drivers with no external system package dependency.
#
# Sizing (db.t3.small app box, 2 vCPU / 2 GB): 3 async uvicorn workers to
# absorb concurrent heavy requests (/lmv-snapshot/range, /inception/bars)
# without the pool queueing past the client timeout. --timeout 120 so a
# legitimately slow request isn't mistaken for a hung worker; --max-requests
# recycles a worker periodically to cap memory creep from large response
# buffers.
set -e

exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 3 \
  --bind 0.0.0.0:8000 \
  --timeout 120 \
  --graceful-timeout 30 \
  --max-requests 2000 \
  --max-requests-jitter 200
