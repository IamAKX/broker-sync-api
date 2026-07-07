# Broker Sync API

Multi-tenant FastAPI backend for daily stock/market data: each client ("tenant") logs
in, uploads a day's worth of stock metrics, and reads back snapshots or time series.
The set of metric columns is expected to change day to day — adding or dropping a
metric never requires a schema migration.

Built to replace/extend `broker-file-sync`'s local file-based storage with a real
multi-tenant API.

## Architecture

- **Multi-tenancy**: schema-per-tenant on a single RDS PostgreSQL database — one shared
  `public` schema for `Tenant`/`User`/`RefreshToken`, one schema per tenant for
  `Stock`/`Metric`/`HistoricalStockValue`, named from the signer's first name (e.g.
  `sundar_dss`; a collision becomes `sundar1_dss`). Tenant is resolved only from the
  signed JWT, never from client input. Tenant tables are created directly via
  SQLAlchemy at signup, in the same transaction as the schema creation — no migration
  chain, no ALTER TABLE, ever: new metric names become new catalog rows, not new
  columns.
- **Layering**: routers (thin, validation only) → services (business logic) →
  repositories (all SQL, bulk upsert via `INSERT ... ON CONFLICT`) — see
  [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md) for the full HLD/LLD.
- **Infra**: EC2 + RDS PostgreSQL, currently running the minimal dev-phase stack
  ($0/month for the first 12 months on the AWS free tier, then ~$22/month, flat
  regardless of tenant count) — see
  [`docs/AWS_ARCHITECTURE.md`](docs/AWS_ARCHITECTURE.md) for the full service
  inventory and the hardened production track to grow into later.

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md) | Application HLD/LLD: multi-tenancy model, data model, API surface, auth design |
| [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) | API contract: every endpoint with sample request/response payloads and error codes |
| [`docs/AWS_ARCHITECTURE.md`](docs/AWS_ARCHITECTURE.md) | Infrastructure design: dev-phase minimal stack vs. hardened production target, cost breakdowns |
| [`docs/AWS_DEPLOYMENT.md`](docs/AWS_DEPLOYMENT.md) | Config/connection reference and step-by-step deployment runbook with copy-pasteable `aws` CLI commands, from zero to a smoke-tested deployment |

## Quickstart (Local Development)

Requires Python 3.12+ and access to a dev PostgreSQL database (a local Docker
container works — see
[`docs/AWS_DEPLOYMENT.md §5`](docs/AWS_DEPLOYMENT.md#5-local-quickstart)).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD, JWT_SECRET

alembic -c alembic_central.ini upgrade head   # provisions public (Tenant/User/RefreshToken)
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for interactive API docs, or see
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) for the full contract with sample
requests/responses.

## Deploying

See [`docs/AWS_DEPLOYMENT.md`](docs/AWS_DEPLOYMENT.md) for the full runbook. Once AWS
resources exist and the `.env` file is configured on the instance:

```bash
rsync -avz --exclude ".venv" --exclude ".git" --exclude "__pycache__" . ec2-user@<ec2-public-ip>:/opt/brokersync
ssh ec2-user@<ec2-public-ip> "cd /opt/brokersync && source .venv/bin/activate && pip install -r requirements.txt && sudo systemctl restart brokersync"
```

## Project Layout

```
app/
├── main.py           # FastAPI app factory
├── core/             # settings, JWT/password security, structured logging
├── db/               # engines, sessions, schema-per-tenant session factory
├── models/           # SQLAlchemy models (central + per-tenant)
├── schemas/          # Pydantic request/response models
├── routers/          # auth, historic (/historic/* - HistoricalStockValue), data (/data/* - catalogs)
├── services/         # business logic, tenant provisioning (CREATE SCHEMA + create_all, atomic), upsert orchestration
├── repositories/      # all SQL, bulk upsert/query logic
└── exceptions.py      # domain exceptions + handlers
migrations/
└── central/          # Alembic chain for public only — tenant schemas have no migration chain
```
