# Stock Data Backend — HLD & LLD

Design for a new FastAPI backend that will eventually replace/extend `broker-file-sync`'s
local file-based storage with a multi-tenant API. This document is a design spec only —
no implementation yet.

Reference sample data: `HistoricalDataEOD_02July2026.xls` — 215 stocks (rows) × 60
metric columns (`ScripName`, `Open`, `High`, `Low`, `Close`, `PMHL_High`, `PMHL_Low`,
`PMC`, `WOHLC_Open`, ...), one file per trading day. Column count and names are
expected to change over time.

---

## 1. Goals & Constraints

- FastAPI, production-grade structure (routers/services/repositories, DI, typed schemas).
- PostgreSQL (AWS RDS) as the database.
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
│   ┌──────────────────┐ ┌──────────────────┐  ┌─────────────────────┐ │
│   │     Routers      │ │     Services     │  │ Repositories (SQLA) │ │
│   │(auth, historic,  │→│ (business logic) │ →│   (query building,  │ │
│   │      data)       │ │                  │  │    schema-scoped)   │ │
│   └──────────────────┘ └──────────────────┘  └─────────────────────┘ │
│                                                                     │
│         ↑                                                           │
│   Middleware: JWT verification → user_id + tenant_id + role         │
│   Dependency: get_tenant_db() → session scoped to the               │
│                 caller's tenant schema (derived from JWT only)      │
└──────────────────────────┬──────────────────────────────────────────┘
                            │
                            ▼
       ┌──────────────────────────────────────────────────────┐
       │                  RDS PostgreSQL                       │
       │                                                      │
       │ ┌──────────────────┐                                     │
       │ │ public (central) │  Tenant, User, RefreshToken         │
       │ ├──────────────────┤                                     │
       │ │ sundar_dss       │  Stock, Metric, HistoricalStockValue │
       │ ├──────────────────┤                                     │
       │ │ ravi_dss         │  Stock, Metric, HistoricalStockValue │
       │ └──────────────────┘                                     │
       └──────────────────────────────────────────────────────┘
```

### 2.2 Multi-Tenancy Model: Schema-per-Tenant

One RDS PostgreSQL instance, one database, one schema per tenant. Each schema contains
an identical set of tables (`Stock`, `Metric`, `HistoricalStockValue`). A shared `public`
schema holds cross-tenant tables (`Tenant`, `User`, `RefreshToken`).

Rationale: cheaper than database-per-tenant (RDS bills per instance, not per database or
schema — this is the deciding factor: it keeps cost flat regardless of tenant count),
still gives real logical segregation (separate tables, separate namespaces), and keeps
connection pooling simple (one physical database, many schemas, one shared connection
pool rebound per request via `schema_translate_map`) versus juggling a distinct
connection pool per tenant.

Each signup provisions its own schema, named from the signer's first name (e.g.
`sundar_dss`; a second `Sundar` signup becomes `sundar1_dss`, `sundar2_dss`, ...).

One tenant per signup today; assigning an *existing* tenant's schema to an additional
email (multiple users sharing one tenant) is supported by the data model (`User.tenant_id`
FK — many `User` rows can point at one `Tenant`) but has no self-service endpoint yet;
it's a backend/admin operation for now (see §4).

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

### 2.4 Signup / Tenant Provisioning (self-service, synchronous, atomic)

1. `POST /auth/signup {name, email, password}`.
2. Validate inputs; check email not already registered (central `User` table).
3. Pick a candidate schema name: slugify the first word of `name`, append `_dss`
   (e.g. `sundar_dss`). Check the central `Tenant` table for existing names with this
   slug first (usually zero collisions) — `CREATE SCHEMA` itself remains the authority
   on uniqueness, so a physical name collision advances to the next numeric suffix
   (`sundar1_dss`, `sundar2_dss`, ...) regardless of what the pre-check found.
4. In **one transaction**: `CREATE SCHEMA [<name>]`, then create `Stock`/`Metric`/
   `HistoricalStockValue` in it via SQLAlchemy `metadata.create_all()` (no Alembic
   migration for tenant tables — see §3.5), then insert `Tenant` row (`schema_name` =
   the name from step 3), then insert `User` row (`role='owner'`).
5. If **any** part of step 4 fails, the entire transaction rolls back — unlike a
   cross-database operation, `CREATE SCHEMA` participates in a normal PostgreSQL
   transaction, so there's no partial state to clean up and no compensating action
   needed. Nothing partially provisioned is ever left behind.
6. On success, issue JWT + refresh token immediately — user is logged in without a
   separate login step.

This is synchronous because schema + table creation for three empty tables is
sub-second; no job queue is warranted at current scale (tens of tenants).

### 2.5 Data Flow — Historic Upload

```
Client (desktop app / script)
      │  computes some fields locally, reads others from broker export
      ▼
POST /historic/daily-upload  { trade_date, rows: [{ symbol, metrics: {...} }] }
      │
      ▼
HistoricalService
  1. Resolve tenant schema from JWT (handled by dependency)
  2. For each row: upsert Stock by symbol (create if unseen)
  3. For each metric key across all rows: upsert into Metric catalog
     (auto-register if name not seen before for this tenant)
  4. Upsert HistoricalStockValue per (trade_date, stock_id, metric_id):
     insert if absent, overwrite value if present — metrics NOT included
     in this payload for this date are left untouched (no deletion)
      │
      ▼
RDS PostgreSQL — this tenant's own schema
```

**Why a new metric name never needs a schema change**: `Metric` is a catalog table —
each distinct metric name (`PMHL_High`, `VAH`, a brand-new column that shows up in
tomorrow's payload, ...) becomes a new *row* here, auto-registered the first time it's
seen, not a new SQL column on `HistoricalStockValue`. Values live in
`HistoricalStockValue`'s generic `value_number`/`value_text` columns, keyed by
`metric_id`. This entity-attribute-value (EAV) shape is *why* the upload endpoint can
accept an arbitrarily different set of metric keys on any given day with zero DDL —
see §3.2 for the table shapes and §3.2's scenario table for exactly what happens in
each case.

### 2.6 Data Flow — Reads

- `GET /historic/snapshot?date=YYYY-MM-DD` (date optional, defaults to latest) → full
  wide-pivoted grid for that day: all stocks × all metrics recorded that day.
- `GET /historic/timeseries?symbol=&metric=&from=&to=` → single metric, single stock,
  across a date range — for charting/backtesting.
- `GET /historic/latest` → alias for snapshot with no date (most recent `trade_date`
  present in `HistoricalStockValue`).
- Missing `(date, stock, metric)` combinations are returned as `null`, never a
  fabricated `0` — a metric that wasn't recorded that day is not the same as a metric
  whose value was zero.

---

## 3. Low-Level Design (LLD)

### 3.1 Central Schema (`public`) — Tables

```sql
Tenant
  id            UUID          PK, default gen_random_uuid()
  name          VARCHAR(200)  NOT NULL   -- the signer's name, e.g. "Sundar"
  schema_name   VARCHAR(128)  NOT NULL UNIQUE   -- e.g. "sundar_dss"
  created_at    TIMESTAMP     NOT NULL default now()

User
  id             UUID         PK, default gen_random_uuid()
  tenant_id      UUID         NOT NULL FK -> Tenant.id
  email          VARCHAR(320) NOT NULL UNIQUE
  password_hash  VARCHAR(255) NOT NULL
  role           VARCHAR(20)  NOT NULL   -- 'owner' | 'member'
  created_at     TIMESTAMP    NOT NULL default now()

RefreshToken
  id            UUID         PK, default gen_random_uuid()
  user_id       UUID         NOT NULL FK -> User.id
  token_hash    VARCHAR(255) NOT NULL
  expires_at    TIMESTAMP    NOT NULL
  revoked_at    TIMESTAMP    NULL
```

### 3.2 Per-Tenant Schema — Tables

Created via SQLAlchemy `metadata.create_all()` directly against the tenant's own
schema at signup (§2.4 step 4) — no Alembic migration chain for these tables (§3.5).

```sql
Stock
  id            SERIAL        PK
  symbol        VARCHAR(50)   NOT NULL UNIQUE   -- e.g. "RADICO"
  display_name  VARCHAR(200)  NULL              -- e.g. "Radico Khaitan Limited"
  is_active     BOOLEAN       NOT NULL default true

Metric
  id            SERIAL        PK
  name          VARCHAR(100)  NOT NULL UNIQUE   -- e.g. "PMHL_High", "VAH"
  data_type     VARCHAR(10)   NOT NULL          -- 'number' | 'text'
  is_active     BOOLEAN       NOT NULL default true
  created_at    TIMESTAMP     NOT NULL default now()

HistoricalStockValue
  trade_date    DATE          NOT NULL
  stock_id      INT           NOT NULL FK -> Stock.id
  metric_id     INT           NOT NULL FK -> Metric.id
  value_number  DECIMAL(18,4) NULL
  value_text    VARCHAR(200)  NULL
  updated_at    TIMESTAMP     NOT NULL default now()
  PRIMARY KEY (trade_date, stock_id, metric_id)

-- Supporting indexes:
CREATE INDEX ix_hsv_stock_metric_date ON HistoricalStockValue (stock_id, metric_id, trade_date);
  -- serves timeseries reads: WHERE stock_id = ? AND metric_id = ? ORDER BY trade_date
CREATE INDEX ix_hsv_date ON HistoricalStockValue (trade_date);
  -- serves snapshot reads: WHERE trade_date = ?
```

Two value columns (`value_number` / `value_text`) rather than one generic text column:
numeric metrics stay natively sortable/aggregable in SQL; `Metric.data_type` tells the
write path which column to populate, and `is_active` lets a metric be retired from the
catalog without deleting its historical rows.

**Why this satisfies the "variable columns" requirement:**

| Scenario | What happens |
|---|---|
| New metric column appears in tomorrow's upload | Auto-inserted into `Metric`, values inserted into `HistoricalStockValue`. No DDL. |
| A metric column is absent from tomorrow's upload | No new `HistoricalStockValue` rows for `(tomorrow, *, that_metric)`. Past dates for that metric are untouched. Reads for tomorrow return `null` for that metric. |
| Backdated upload for a date 3 weeks ago | Same upsert path, just with an older `trade_date`. No special-casing needed — "backdated" and "today" are the same code path. |
| Re-upload correcting today's data | Upsert per `(trade_date, stock, metric)` — overwrites only the metrics present in the new payload; other metrics for that date already stored are left alone. |

### 3.3 API Surface

**Auth**
| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Create tenant (own schema) + owner user, return JWT + refresh token |
| POST | `/auth/login` | Verify credentials, return JWT + refresh token |
| POST | `/auth/refresh` | Exchange valid refresh token for new access token |
| POST | `/auth/logout` | Revoke refresh token |

**Historic** (reads/writes `HistoricalStockValue`)
| Method | Path | Description |
|---|---|---|
| POST | `/historic/daily-upload` | Upsert one trading day's rows (see payload below) |
| GET | `/historic/snapshot?date=` | Wide grid for one date (defaults to latest) |
| GET | `/historic/latest` | Alias for snapshot with no date |
| GET | `/historic/timeseries?symbol=&metric=&from=&to=` | Single metric/stock across a date range |
| DELETE | `/historic/{trade_date}` | Delete every value recorded for one date, all stocks/metrics |

**Data** (lists the `Stock`/`Metric` catalogs)
| Method | Path | Description |
|---|---|---|
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
- Access JWT: `HS256`, ~30 min expiry, secret from an env var (`.env` locally, Secrets Manager in production).
  Payload: `{sub, tenant_id, schema_name, role, exp}`.
- Refresh token: opaque random token, ~7 day expiry, stored **hashed** in
  `RefreshToken`, revocable (logout / password change invalidates it).
- `Depends(get_current_user)` — verifies JWT, loads user context.
- `Depends(get_tenant_db)` — builds a schema-scoped SQLAlchemy session from
  `schema_name` in the verified JWT.
- `require_role("owner")` — dependency for future admin-only endpoints (metric
  management, user management within a tenant).
- All cross-tenant table access happens only through `public`-scoped sessions used
  strictly by `auth` routes; all business-data access happens only through
  tenant-scoped sessions — the two session types are never mixed in one request.

### 3.5 Tech Stack

- **FastAPI** + **Pydantic v2** — routers thin, validation in schemas.
- **SQLAlchemy 2.0** (async) with `asyncpg`/`psycopg` driver for PostgreSQL.
- **Alembic** — **one** migration chain, for `public` (central tables: `Tenant`, `User`,
  `RefreshToken`). Tenant schemas have **no migration chain**: their three tables
  (`Stock`/`Metric`/`HistoricalStockValue`) are created once, at signup, via
  SQLAlchemy's `metadata.create_all()` directly against the ORM models (schema-bound
  via `schema_translate_map`) — there's nothing to version because the table *shape*
  never changes; only metric *rows* change (§2.5).
- **pydantic-settings** — config via env vars (connection string, JWT secret, CORS).
- **passlib[bcrypt]** — password hashing.
- **structlog** (or stdlib logging + JSON formatter) — every log line carries
  `tenant_id` and `request_id`.

### 3.6 Project Layout

```
app/
├── main.py                      # FastAPI app factory, startup/shutdown
├── core/
│   ├── config.py                # Settings (pydantic-settings)
│   ├── security.py              # JWT encode/decode, password hashing
│   ├── deps.py                  # get_current_user, require_role
│   └── logging.py
├── db/
│   ├── central_session.py       # engine/session for public schema
│   ├── tenant_session.py        # per-request schema-scoped session factory
│   ├── deps.py                  # get_central_db, get_tenant_db
│   └── base.py                  # declarative base(s)
├── models/
│   ├── central.py                # Tenant, User, RefreshToken
│   └── tenant.py                 # Stock, Metric, HistoricalStockValue
├── schemas/                      # Pydantic request/response models
│   ├── auth.py
│   ├── historic.py                # upload/snapshot/timeseries schemas
│   └── data.py                    # metric/stock catalog list schemas
├── routers/
│   ├── auth.py
│   ├── historic.py                # /historic/* — reads/writes HistoricalStockValue
│   └── data.py                    # /data/* — lists Stock/Metric catalogs
├── services/
│   ├── auth_service.py
│   ├── provisioning_service.py  # CREATE SCHEMA + create_all() for new tenant, atomic
│   └── historical_service.py    # upsert logic, pivot logic for snapshot reads
├── repositories/
│   ├── stock_repo.py
│   ├── metric_repo.py
│   └── historical_value_repo.py
└── exceptions.py
migrations/
└── central/                     # Alembic chain for public only
```

### 3.7 Error Handling

Centralized exception handlers map domain exceptions to consistent JSON
(`{"detail": ..., "code": ...}`):

- `TenantNotFoundError` → 404
- `DuplicateEmailError` → 409
- `InvalidCredentialsError` → 401
- `SchemaProvisioningError` → 500 (signup transaction rolled back — schema creation
  participates in the same transaction as the Tenant/User inserts, see §2.4)
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
- **Admin endpoint to attach an additional email to an existing tenant** — the data
  model already supports many `User` rows pointing at one `Tenant`/schema (§2.2), but
  no self-service or admin API exists yet to do this; today it would be a manual DB
  operation.
- Metric type validation strictness (e.g. rejecting a text value for a metric already
  typed `number`) — current design accepts it into `value_text` as a fallback; a
  stricter policy can be layered in later without a schema change.

---

← Design spec for `broker-file-sync` backend extension. See [architecture.md](docs/architecture.md) for the existing desktop app's architecture.
