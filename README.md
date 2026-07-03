# Broker Sync API

Multi-tenant FastAPI backend for daily stock/market data: each client ("tenant") logs
in, uploads a day's worth of stock metrics, and reads back snapshots or time series.
The set of metric columns is expected to change day to day — adding or dropping a
metric never requires a schema migration.

Built to replace/extend `broker-file-sync`'s local file-based storage with a real
multi-tenant API.

## Architecture

- **Multi-tenancy**: schema-per-tenant on a single Azure SQL database — one shared
  `dbo` schema for `Tenant`/`User`/`RefreshToken`, one schema per tenant for
  `Stock`/`Metric`/`DailyStockValue`, named from the signer's first name (e.g.
  `sundar_dss`; a collision becomes `sundar1_dss`). Tenant is resolved only from the
  signed JWT, never from client input. Tenant tables are created directly via
  SQLAlchemy at signup, in the same transaction as the schema creation — no migration
  chain, no ALTER TABLE, ever: new metric names become new catalog rows, not new
  columns.
- **Layering**: routers (thin, validation only) → services (business logic) →
  repositories (all SQL, bulk upsert via SQL `MERGE`) — see
  [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md) for the full HLD/LLD.
- **Infra**: Azure App Service + Azure SQL, currently running the minimal dev-phase
  stack (~$17/month, flat regardless of tenant count) — see
  [`docs/AZURE_ARCHITECTURE.md`](docs/AZURE_ARCHITECTURE.md) for the full service
  inventory and the hardened production track to grow into later.

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/BACKEND_ARCHITECTURE.md`](docs/BACKEND_ARCHITECTURE.md) | Application HLD/LLD: multi-tenancy model, data model, API surface, auth design |
| [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) | API contract: every endpoint with sample request/response payloads and error codes |
| [`docs/AZURE_ARCHITECTURE.md`](docs/AZURE_ARCHITECTURE.md) | Infrastructure design: dev-phase minimal stack vs. hardened production target, cost breakdowns |
| [`docs/AZURE_SETUP.md`](docs/AZURE_SETUP.md) | Config/connection reference: required env vars, SQL connection string format, ODBC driver setup, firewall rules |
| [`docs/AZURE_DEPLOYMENT.md`](docs/AZURE_DEPLOYMENT.md) | Step-by-step deployment runbook with copy-pasteable `az` CLI commands, from zero to a smoke-tested deployment |

## Quickstart (Local Development)

Requires Python 3.12+ and the Microsoft ODBC Driver 18 for SQL Server (see
[`docs/AZURE_SETUP.md §3`](docs/AZURE_SETUP.md#3-sql-connection-details) for the
install command) and access to a dev Azure SQL database.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD, JWT_SECRET

alembic -c alembic_central.ini upgrade head   # provisions dbo (Tenant/User/RefreshToken)
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for interactive API docs, or see
[`docs/API_CONTRACT.md`](docs/API_CONTRACT.md) for the full contract with sample
requests/responses.

## Running Tests

```bash
pytest
```

Tests sign up disposable tenants (each getting its own real tenant schema) against the
dev database and drop those schemas in teardown — no mocking of the database layer.

## Deploying

See [`docs/AZURE_DEPLOYMENT.md`](docs/AZURE_DEPLOYMENT.md) for the full runbook. Once
Azure resources exist and Application Settings are configured:

```bash
zip -r deploy.zip . -x ".venv/*" ".git/*" "__pycache__/*" "*.pyc" "docs/*" ".env"
az webapp deploy --name <webapp-name> --resource-group <rg-name> --src-path deploy.zip --type zip
```

## Project Layout

```
app/
├── main.py           # FastAPI app factory
├── core/             # settings, JWT/password security, structured logging
├── db/               # engines, sessions, schema-per-tenant session factory
├── models/           # SQLAlchemy models (central + per-tenant)
├── schemas/          # Pydantic request/response models
├── routers/          # auth, data
├── services/         # business logic, tenant provisioning (CREATE SCHEMA + create_all, atomic), upsert orchestration
├── repositories/      # all SQL, bulk upsert/query logic
└── exceptions.py      # domain exceptions + handlers
migrations/
└── central/          # Alembic chain for dbo only — tenant schemas have no migration chain
tests/
```
