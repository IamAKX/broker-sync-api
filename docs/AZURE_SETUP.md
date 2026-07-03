# Configuration & Connection Reference

Reference doc — not a step-by-step guide. See [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md)
for the ordered runbook that provisions the resources this doc configures.

Scope: the **dev-phase stack** from
[AZURE_ARCHITECTURE.md §0](AZURE_ARCHITECTURE.md#0-dev-phase-architecture-minimal-cost) —
Azure SQL **Basic** tier + App Service **B1 Basic**, secrets in plain env
vars/Application Settings (no Key Vault/Managed Identity yet). The app is
**schema-per-tenant** (see
[`BACKEND_ARCHITECTURE.md` §2.2](BACKEND_ARCHITECTURE.md#22-multi-tenancy-model-schema-per-tenant)):
one Azure SQL database total, with a shared `dbo` schema plus one schema per
signed-up tenant.

---

## 1. Required Configuration Values

All settings are read by [`app/core/config.py`](../app/core/config.py) via
`pydantic-settings`. Same variable names are used locally (`.env`) and in Azure
(App Service Application Settings) — only *where* they're set differs.

| Variable | Example | Description |
|---|---|---|
| `ENVIRONMENT` | `development` / `production` | Toggles debug behavior (e.g. SQL echo, docs exposure) |
| `SQL_SERVER` | `brokersync-dev.database.windows.net` | Azure SQL logical server hostname |
| `SQL_DATABASE` | `brokersync` | Database name — holds `dbo` and every tenant schema |
| `SQL_USER` | `brokersync_admin` | SQL login (Basic tier auth — no Azure AD/Managed Identity yet) |
| `SQL_PASSWORD` | `<secret>` | SQL login password |
| `SQL_DRIVER` | `ODBC Driver 18 for SQL Server` | Must match the driver installed on the host — see §3 |
| `JWT_SECRET` | `<random 32+ byte string>` | HS256 signing secret for access tokens |
| `JWT_ACCESS_EXPIRY_MINUTES` | `30` | Access token lifetime, per `BACKEND_ARCHITECTURE.md` §3.4 |
| `JWT_REFRESH_EXPIRY_DAYS` | `7` | Refresh token lifetime |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed origins |

## 2. Where Each Value Lives

| Location | Used for | Notes |
|---|---|---|
| `.env` (gitignored, copy from `.env.example`) | Local development | Loaded automatically by `pydantic-settings`; never committed |
| App Service **Application Settings** | Deployed app | Set via `az webapp config appsettings set` (see deployment doc §5) — encrypted at rest by Azure, injected as env vars into the container at runtime |

No Key Vault / Managed Identity in this phase (§0 of `AZURE_ARCHITECTURE.md` defers
that until real tenant data or a compliance need exists) — Application Settings is the
sole secret store for now.

## 3. SQL Connection Details

### Driver requirement (important gotcha)

The app uses `aioodbc`/`pyodbc`, which require the **Microsoft ODBC Driver 18 for SQL
Server** to be installed on whatever host runs the app. This driver is **not**
preinstalled on:
- macOS/most local dev machines — install via Homebrew (see quickstart below).
- App Service's native Linux Python runtime — installed at container startup by
  [`startup.sh`](../startup.sh), which pulls it from Microsoft's apt repo before
  launching Gunicorn. This is the one non-obvious requirement of pairing pyodbc with
  App Service's native runtime (as opposed to a custom Docker image with the driver
  baked in).

Local install (macOS):
```bash
brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release
brew update
HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18 mssql-tools18
```

### Connection string format

Built at runtime in `app/core/config.py` from the individual `SQL_*` variables above,
using the async ODBC connection format SQLAlchemy expects:

```
mssql+aioodbc://<SQL_USER>:<SQL_PASSWORD>@<SQL_SERVER>:1433/<SQL_DATABASE>?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
```

`Encrypt=yes` is mandatory — Azure SQL rejects unencrypted connections. There's only
one database (and therefore one connection string) — per-tenant isolation happens via
`schema_translate_map` at the session level (`app/db/tenant_session.py`), not via a
different connection target.

### Firewall rule (required — Basic tier has no VNet/Private Endpoint)

Azure SQL Basic tier keeps its **public endpoint**; access is gated by **server-level
firewall rules**, not network isolation. Two rules are needed:

1. **Your local dev machine's IP** — so you can connect from your laptop.
2. **"Allow Azure services and resources to access this server"** — a built-in toggle
   that lets App Service (which doesn't have a static outbound IP on the Basic App
   Service plan) reach the SQL server. This is broader than pinning exact outbound
   IPs, but is the standard approach for App Service ↔ Azure SQL without VNet
   integration (which Premium v3 would be required for — explicitly deferred in §0).

Both are created via `az sql server firewall-rule create` — exact commands in
[AZURE_DEPLOYMENT.md §3](AZURE_DEPLOYMENT.md).

## 4. Migrations: Central Only

There is **one** Alembic chain, for `dbo`:

| Config | Targets | When it runs |
|---|---|---|
| `alembic_central.ini` | `dbo` schema (`Tenant`, `User`, `RefreshToken`) | Manually, once per deploy — see deployment doc §7 |

**Tenant schemas have no migration chain.** Their tables (`Stock`, `Metric`,
`DailyStockValue`) are created once, directly via SQLAlchemy's `metadata.create_all()`
(schema-bound via `schema_translate_map`), when a tenant schema is provisioned at
signup — in the same transaction as the `Tenant`/`User` inserts
(`app/services/provisioning_service.py`, `app/services/auth_service.py`). There's
nothing to version: the table *shape* is fixed; the varying part (metric names) is row
data in the `Metric` catalog table, not a column — see `BACKEND_ARCHITECTURE.md`
§2.5/§3.2 for why.

## 5. Local Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in SQL_* and JWT_SECRET
alembic -c alembic_central.ini upgrade head
uvicorn app.main:app --reload
```

Then open `http://localhost:8000/docs`.
