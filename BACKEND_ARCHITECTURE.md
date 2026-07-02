# Stock Data Backend — HLD & LLD

Design for a new FastAPI backend that will eventually replace/extend `broker-file-sync`'s
local file-based storage with a multi-tenant API. This document is a design spec only —
no implementation yet.

Reference sample data: `Datascanner_20260627 (1).xls` — 217 stocks (rows) × 18 metric
columns (`ScripName`, `PMHL_High`, `PMHL_Low`, `PMC`, `WOHLC_Open`, ...), one file per
trading day. Column count and names are expected to change over time.

---

## 1. Goals & Constraints

- FastAPI, production-grade structure (routers/services/repositories, DI, typed schemas).
- Azure SQL as the database.
- Multi-tenant: each client ("tenant") gets logically segregated data.
- Users table for authentication.
- Daily stock data upload where the **set of metric columns varies day to day** —
  adding a column must never require a migration or break historical data; removing a
  column must not error, and the app should treat missing data as "not recorded" for that
  day (returned as `null`), not silently invent a value.
- Backdated (historical) data uploads must be supported, not just "today".
- Upload payload is **not** a raw Excel file — some fields are calculated client-side —
  so the API accepts structured JSON.
- Scope: HLD/LLD only. Implementation is a later phase.

---

## 2. High-Level Design (HLD)

### 2.1 Component Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FastAPI Application                         │
│                                                                     │
│   ┌──────────────┐   ┌──────────────────┐   ┌─────────────────────┐ │
│   │   Routers    │   │     Services     │   │ Repositories (SQLA) │ │
│   │ (auth, data) │ → │ (business logic) │ → │   (query building,  │ │
│   │              │   │                  │   │    schema-scoped)   │ │
│   └──────────────┘   └──────────────────┘   └─────────────────────┘ │
│                                                                     │
│         ↑                                                           │
│   Middleware: JWT verification → user_id + tenant_id + role         │
│   Dependency: get_tenant_db() → session scoped to the               │
│                 caller's tenant schema (derived from JWT only)      │
└──────────────────────────┬──────────────────────────────────────────┘
                            │
                            ▼
       ┌──────────────────────────────────────────────────────┐
       │                   Azure SQL Server                   │
       │                                                      │
       │ ┌──────────────────┐                                 │
       │ │ dbo (central)    │  Tenant, User, RefreshToken     │
       │ ├──────────────────┤                                 │
       │ │ tenant_acme_x7f2 │  Stock, Metric, DailyStockValue │
       │ ├──────────────────┤                                 │
       │ │ tenant_beta_q2k9 │  Stock, Metric, DailyStockValue │
       │ └──────────────────┘                                 │
       └──────────────────────────────────────────────────────┘
```

### 2.2 Multi-Tenancy Model: Schema-per-Tenant

One Azure SQL logical server, one database, one schema per tenant. Each schema contains
an identical set of tables (`Stock`, `Metric`, `DailyStockValue`). A shared `dbo` schema
holds cross-tenant tables (`Tenant`, `User`, `RefreshToken`).

Rationale: cheaper than database-per-tenant (Azure SQL bills per database), still gives
real logical segregation (separate tables, separate namespaces, independent per-tenant
migrations), and keeps connection pooling simple (one DB, many schemas) versus juggling
a connection pool per physical database.

### 2.3 Tenant Resolution (per request)

1. Client sends `Authorization: Bearer <JWT>`.
2. JWT payload (signed, server-issued) contains `sub` (user id), `tenant_id`,
   `schema_name`, `role`, `exp`.
3. `Depends(get_current_user)` verifies and decodes the token.
4. `Depends(get_tenant_db)` uses `schema_name` from the verified token to build a
   SQLAlchemy session whose table metadata is bound to that schema (via a
   `schema_translate_map` on the session, so the same ORM models work against any
   tenant's schema without per-tenant model classes).
5. **The tenant is never taken from a client-supplied header or query param** — only
   from the signed JWT. This prevents a client from spoofing another tenant's ID.

### 2.4 Signup / Tenant Provisioning (self-service, synchronous)

1. `POST /auth/signup {company_name, email, password}`.
2. Validate inputs; check email not already registered (central `User` table).
3. Generate a unique `schema_name` (slug of company name + short random suffix, e.g.
   `tenant_acme_x7f2`) to avoid collisions and avoid leaking company names verbatim into
   SQL identifiers.
4. In one transaction against `dbo`: insert `Tenant` row, insert `User` row
   (`role='owner'`).
5. Run the tenant-scoped Alembic migration chain programmatically against the new
   schema (`CREATE SCHEMA tenant_acme_x7f2;` then create `Stock`, `Metric`,
   `DailyStockValue` tables in it).
6. If schema creation or migration fails, roll back the `dbo` transaction (no orphaned
   tenant/user rows) and return a 500 with a clear error; nothing partially provisioned
   is left behind.
7. On success, issue JWT + refresh token immediately — user is logged in without a
   separate login step.

This is synchronous because table creation for three empty tables is sub-second; no job
queue is warranted at current scale (tens of tenants).

### 2.5 Data Flow — Daily Upload

```
Client (desktop app / script)
      │  computes some fields locally, reads others from broker export
      ▼
POST /data/daily-upload  { trade_date, rows: [{ symbol, metrics: {...} }] }
      │
      ▼
DataUploadService
  1. Resolve tenant schema from JWT (handled by dependency)
  2. For each row: upsert Stock by symbol (create if unseen)
  3. For each metric key across all rows: upsert into Metric catalog
     (auto-register if name not seen before for this tenant)
  4. Upsert DailyStockValue per (trade_date, stock_id, metric_id):
     insert if absent, overwrite value if present — metrics NOT included
     in this payload for this date are left untouched (no deletion)
      │
      ▼
Azure SQL — tenant schema tables
```

### 2.6 Data Flow — Reads

- `GET /data/snapshot?date=YYYY-MM-DD` (date optional, defaults to latest) → full
  wide-pivoted grid for that day: all stocks × all metrics recorded that day.
- `GET /data/timeseries?symbol=&metric=&from=&to=` → single metric, single stock, across
  a date range — for charting/backtesting.
- `GET /data/latest` → alias for snapshot with no date (most recent `trade_date`
  present in `DailyStockValue`).
- Missing `(date, stock, metric)` combinations are returned as `null`, never a
  fabricated `0` — a metric that wasn't recorded that day is not the same as a metric
  whose value was zero.

---

## 3. Low-Level Design (LLD)

### 3.1 Central Schema (`dbo`) — Tables

```sql
Tenant
  id            UNIQUEIDENTIFIER  PK, default newid()
  name          NVARCHAR(200)     NOT NULL
  schema_name   NVARCHAR(128)     NOT NULL UNIQUE
  created_at    DATETIME2         NOT NULL default sysutcdatetime()

User
  id             UNIQUEIDENTIFIER PK, default newid()
  tenant_id      UNIQUEIDENTIFIER NOT NULL FK -> Tenant.id
  email          NVARCHAR(320)    NOT NULL UNIQUE
  password_hash  NVARCHAR(255)    NOT NULL
  role           NVARCHAR(20)     NOT NULL   -- 'owner' | 'member'
  created_at     DATETIME2        NOT NULL default sysutcdatetime()

RefreshToken
  id            UNIQUEIDENTIFIER PK, default newid()
  user_id       UNIQUEIDENTIFIER NOT NULL FK -> User.id
  token_hash    NVARCHAR(255)    NOT NULL
  expires_at    DATETIME2        NOT NULL
  revoked_at    DATETIME2        NULL
```

### 3.2 Per-Tenant Schema — Tables

```sql
Stock
  id            INT IDENTITY PK
  symbol        NVARCHAR(50)   NOT NULL UNIQUE   -- e.g. "RADICO"
  display_name  NVARCHAR(200)  NULL              -- e.g. "Radico Khaitan Limited"
  is_active     BIT            NOT NULL default 1

Metric
  id            INT IDENTITY PK
  name          NVARCHAR(100)  NOT NULL UNIQUE   -- e.g. "PMHL_High", "VAH"
  data_type     NVARCHAR(10)   NOT NULL          -- 'number' | 'text'
  is_active     BIT            NOT NULL default 1
  created_at    DATETIME2      NOT NULL default sysutcdatetime()

DailyStockValue
  trade_date    DATE           NOT NULL
  stock_id      INT            NOT NULL FK -> Stock.id
  metric_id     INT            NOT NULL FK -> Metric.id
  value_number  DECIMAL(18,4)  NULL
  value_text    NVARCHAR(200)  NULL
  updated_at    DATETIME2      NOT NULL default sysutcdatetime()
  PRIMARY KEY (trade_date, stock_id, metric_id)

-- Supporting indexes:
CREATE INDEX ix_dsv_stock_metric_date ON DailyStockValue (stock_id, metric_id, trade_date);
  -- serves timeseries reads: WHERE stock_id = ? AND metric_id = ? ORDER BY trade_date
CREATE INDEX ix_dsv_date ON DailyStockValue (trade_date);
  -- serves snapshot reads: WHERE trade_date = ?
```

Two value columns (`value_number` / `value_text`) rather than one generic text column:
numeric metrics stay natively sortable/aggregable in SQL; `Metric.data_type` tells the
write path which column to populate, and `is_active` lets a metric be retired from the
catalog without deleting its historical rows.

**Why this satisfies the "variable columns" requirement:**

| Scenario | What happens |
|---|---|
| New metric column appears in tomorrow's upload | Auto-inserted into `Metric`, values inserted into `DailyStockValue`. No DDL. |
| A metric column is absent from tomorrow's upload | No new `DailyStockValue` rows for `(tomorrow, *, that_metric)`. Past dates for that metric are untouched. Reads for tomorrow return `null` for that metric. |
| Backdated upload for a date 3 weeks ago | Same upsert path, just with an older `trade_date`. No special-casing needed — "backdated" and "today" are the same code path. |
| Re-upload correcting today's data | Upsert per `(trade_date, stock, metric)` — overwrites only the metrics present in the new payload; other metrics for that date already stored are left alone. |

### 3.3 API Surface

**Auth**
| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Create tenant + owner user, provision schema, return JWT + refresh token |
| POST | `/auth/login` | Verify credentials, return JWT + refresh token |
| POST | `/auth/refresh` | Exchange valid refresh token for new access token |
| POST | `/auth/logout` | Revoke refresh token |

**Data**
| Method | Path | Description |
|---|---|---|
| POST | `/data/daily-upload` | Upsert one trading day's rows (see payload below) |
| GET | `/data/snapshot?date=` | Wide grid for one date (defaults to latest) |
| GET | `/data/latest` | Alias for snapshot with no date |
| GET | `/data/timeseries?symbol=&metric=&from=&to=` | Single metric/stock across a date range |
| GET | `/data/metrics` | List the tenant's registered metrics (name, data_type, is_active) |
| GET | `/data/stocks` | List the tenant's registered stocks |

**Upload request body:**
```json
{
  "trade_date": "2026-06-27",
  "rows": [
    {
      "symbol": "RADICO",
      "display_name": "Radico Khaitan Limited",
      "metrics": {
        "PMHL_High": 3679.0,
        "PMHL_Low": 3302.0,
        "PMC": 3554.9,
        "VAH": 3554.9
      }
    }
  ]
}
```

**Snapshot response:**
```json
{
  "trade_date": "2026-06-27",
  "stocks": [
    {
      "symbol": "RADICO",
      "display_name": "Radico Khaitan Limited",
      "metrics": { "PMHL_High": 3679.0, "PMHL_Low": 3302.0, "VAH": null }
    }
  ]
}
```
`VAH: null` here means VAH was not recorded for RADICO on this date — distinct from an
actual recorded value of `0`.

### 3.4 Auth & Security Details

- Passwords hashed with `bcrypt` via `passlib`.
- Access JWT: `HS256`, ~30 min expiry, secret from Azure Key Vault (or env var locally).
  Payload: `{sub, tenant_id, schema_name, role, exp}`.
- Refresh token: opaque random token, ~7 day expiry, stored **hashed** in
  `RefreshToken`, revocable (logout / password change invalidates it).
- `Depends(get_current_user)` — verifies JWT, loads user context.
- `Depends(get_tenant_db)` — builds a schema-scoped SQLAlchemy session from
  `schema_name` in the verified JWT.
- `require_role("owner")` — dependency for future admin-only endpoints (metric
  management, user management within a tenant).
- All cross-tenant table access happens only through `dbo`-scoped sessions used
  strictly by `auth` routes; all business-data access happens only through
  tenant-scoped sessions — the two session types are never mixed in one request.

### 3.5 Tech Stack

- **FastAPI** + **Pydantic v2** — routers thin, validation in schemas.
- **SQLAlchemy 2.0** (async) with `pyodbc`/`aioodbc` driver for Azure SQL.
- **Alembic** — two independent migration chains: one for `dbo` (central tables), one
  "tenant template" chain replayed against each new schema at signup time.
- **pydantic-settings** — config via env vars (connection string, JWT secret, CORS).
- **passlib[bcrypt]** — password hashing.
- **pytest** + **httpx.AsyncClient** — API tests; a disposable test tenant schema
  created/torn down per test session.
- **structlog** (or stdlib logging + JSON formatter) — every log line carries
  `tenant_id` and `request_id`.

### 3.6 Project Layout

```
backend/
├── main.py                      # FastAPI app factory, startup/shutdown
├── core/
│   ├── config.py                # Settings (pydantic-settings)
│   ├── security.py              # JWT encode/decode, password hashing
│   └── logging.py
├── db/
│   ├── central_session.py       # engine/session for dbo schema
│   ├── tenant_session.py        # per-request schema-scoped session factory
│   └── base.py                  # declarative base(s)
├── models/
│   ├── central.py                # Tenant, User, RefreshToken
│   └── tenant.py                 # Stock, Metric, DailyStockValue
├── schemas/                      # Pydantic request/response models
│   ├── auth.py
│   └── data.py
├── routers/
│   ├── auth.py
│   └── data.py
├── services/
│   ├── auth_service.py
│   ├── provisioning_service.py  # create schema + run migrations for new tenant
│   └── data_service.py          # upsert logic, pivot logic for snapshot reads
├── repositories/
│   ├── stock_repo.py
│   ├── metric_repo.py
│   └── daily_value_repo.py
├── migrations/
│   ├── central/                 # Alembic env for dbo
│   └── tenant/                  # Alembic env replayed per new tenant schema
└── tests/
```

### 3.7 Error Handling

Centralized exception handlers map domain exceptions to consistent JSON
(`{"detail": ..., "code": ...}`):

- `TenantNotFoundError` → 404
- `DuplicateEmailError` → 409
- `InvalidCredentialsError` → 401
- `SchemaProvisioningError` → 500 (signup transaction rolled back)
- `InvalidTradeDateError` → 422
- Anything else → standard FastAPI `HTTPException` / validation errors (422)

---

## 4. Open Items For Later Phases

These are explicitly out of scope for this design pass, called out so they're not
forgotten:

- Wiring the existing PySide6 desktop app (`services/master_generator.py`,
  `screens/login.py`, `screens/signup.py`) to call this API instead of / in addition to
  local file merge — noted as a future extension, not part of this design.
- Rate limiting, audit logging, and per-tenant usage metering.
- Elastic pool / multi-server sharding if tenant count grows well beyond current
  expectations.
- Metric type validation strictness (e.g. rejecting a text value for a metric already
  typed `number`) — current design accepts it into `value_text` as a fallback; a
  stricter policy can be layered in later without a schema change.

---

← Design spec for `broker-file-sync` backend extension. See [architecture.md](docs/architecture.md) for the existing desktop app's architecture.
