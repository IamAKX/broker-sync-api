# API Contract

Request/response reference for every endpoint, with sample payloads. For *why* the API
is shaped this way (multi-tenancy model, EAV metric storage, auth design), see
[`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md). This doc is the *what* — exact
fields, types, and status codes — kept in sync with the Pydantic schemas in
[`app/schemas/`](../app/schemas) and the routers in [`app/routers/`](../app/routers).

Interactive, always-current docs are also available at `/docs` (Swagger UI) and
`/redoc` on a running instance, generated from the same schemas.

---

## Conventions

- **Base URL**: `http://localhost:8000` locally; `https://<webapp-name>.azurewebsites.net`
  when deployed (see [`AZURE_DEPLOYMENT.md`](AZURE_DEPLOYMENT.md)).
- **Content type**: `application/json` for all request and response bodies.
- **Auth**: `Authorization: Bearer <access_token>` header on every endpoint except
  `/auth/signup` and `/auth/login`. The token is issued by signup/login/refresh.
- **Tenant scoping**: which tenant's schema a request reads/writes is derived **only**
  from the verified JWT (`schema_name` claim) — never from a header, query param, or
  request body. There is no way to specify a different tenant than the one the token
  was issued for.
- **Dates**: `YYYY-MM-DD` (ISO 8601 date, no time component) for `trade_date` and date
  range query params.
- **Error shape**: every error response is `{"detail": "<human-readable message>", "code": "<machine-readable code>"}`. See [Error Codes](#error-codes) below.

---

## Auth

### `POST /auth/signup`

Creates a new tenant (its own schema, provisioned atomically) and an owner user, then
logs them in immediately.

**Request body**
```json
{
  "name": "Sundar",
  "email": "sundar@example.com",
  "password": "Str0ngPassw0rd!"
}
```

| Field | Type | Constraints |
|---|---|---|
| `name` | string | 1–200 chars. Drives the tenant's schema name (e.g. `sundar_dss`; a repeat becomes `sundar1_dss`) |
| `email` | string | Valid email, must not already be registered |
| `password` | string | 8–128 chars |

**Response `201 Created`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "3n8fK...opaque-random-token...9dQ",
  "token_type": "bearer"
}
```

**Errors**: `409 duplicate_email`, `422` (validation), `500 schema_provisioning_failed`.

### `POST /auth/login`

**Request body**
```json
{
  "email": "sundar@example.com",
  "password": "Str0ngPassw0rd!"
}
```

**Response `200 OK`** — same shape as signup's response.

**Errors**: `401 invalid_credentials`.

### `POST /auth/refresh`

Exchanges a valid, unexpired, unrevoked refresh token for a new access token +
refresh token pair. The old refresh token is revoked as part of this call (rotation).

**Request body**
```json
{
  "refresh_token": "3n8fK...opaque-random-token...9dQ"
}
```

**Response `200 OK`** — same shape as signup's response (new tokens).

**Errors**: `401 invalid_credentials` (expired, revoked, or unknown token).

### `POST /auth/logout`

Revokes a refresh token. Idempotent — logging out an already-revoked or unknown token
still returns `204`.

**Request body**
```json
{
  "refresh_token": "3n8fK...opaque-random-token...9dQ"
}
```

**Response**: `204 No Content` (empty body).

---

## Data

All `/data/*` endpoints require `Authorization: Bearer <access_token>` and operate on
the caller's own tenant schema.

### `POST /data/daily-upload`

Upserts one trading day's rows. New stocks and new metric names are auto-registered.
Metrics **not present** in a re-upload for an existing `(trade_date, stock)` are left
untouched — this endpoint never deletes.

**Request body**
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
    },
    {
      "symbol": "TCS",
      "metrics": {
        "PMC": 3890.25
      }
    }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `trade_date` | date | Cannot be in the future. Can be any past date — backdated uploads use the same code path as "today" |
| `rows[].symbol` | string | 1–50 chars. Upserted into the `Stock` catalog if unseen |
| `rows[].display_name` | string \| null | Optional |
| `rows[].metrics` | object | Arbitrary key → number or string. Keys not seen before are auto-registered in the `Metric` catalog — **no schema change, no migration, ever** |

**Response `200 OK`**
```json
{
  "trade_date": "2026-06-27",
  "stocks_upserted": 2,
  "metrics_registered": 4,
  "values_upserted": 5
}
```

**Errors**: `422 invalid_trade_date` (future date), `422` (validation), `401` (missing/invalid token).

### `GET /data/snapshot?date=YYYY-MM-DD`

Wide-pivoted grid for one date: all stocks × all metrics recorded that day. `date` is
optional — omitting it returns the most recent `trade_date` present in the data
(equivalent to `/data/latest`).

**Response `200 OK`**
```json
{
  "trade_date": "2026-06-27",
  "stocks": [
    {
      "symbol": "RADICO",
      "display_name": "Radico Khaitan Limited",
      "metrics": {
        "PMHL_High": 3679.0,
        "PMHL_Low": 3302.0,
        "PMC": 3554.9,
        "VAH": 3554.9
      }
    },
    {
      "symbol": "TCS",
      "display_name": null,
      "metrics": {
        "PMC": 3890.25,
        "PMHL_High": null
      }
    }
  ]
}
```

`"PMHL_High": null` for TCS means that metric **was not recorded** for TCS on this
date — distinct from an actual recorded value of `0`. A metric key only appears in a
stock's `metrics` object if it was recorded for *some* stock on that date; per-stock,
a missing metric is returned as `null` rather than omitted, so clients can render a
consistent column set.

### `GET /data/latest`

Alias for `/data/snapshot` with no `date` — same response shape, using the most recent
`trade_date` in the tenant's data.

### `GET /data/timeseries?symbol=&metric=&from=&to=`

Single metric, single stock, across a date range — for charting/backtesting.

| Query param | Type | Required |
|---|---|---|
| `symbol` | string | yes |
| `metric` | string | yes |
| `from` | date | no — omit for no lower bound |
| `to` | date | no — omit for no upper bound |

**Example**: `GET /data/timeseries?symbol=RADICO&metric=PMC&from=2026-06-01&to=2026-06-27`

**Response `200 OK`**
```json
{
  "symbol": "RADICO",
  "metric": "PMC",
  "points": [
    { "trade_date": "2026-06-01", "value": 3510.0 },
    { "trade_date": "2026-06-02", "value": 3522.4 },
    { "trade_date": "2026-06-27", "value": 3554.9 }
  ]
}
```

An unknown `symbol` or `metric` returns `200` with an empty `points` array, not a 404
— querying for data that doesn't exist yet is a normal, expected case for this API.

### `GET /data/metrics`

Lists every metric registered for the caller's tenant (auto-registered by past uploads
via `/data/daily-upload` — there's no separate "create a metric" endpoint).

**Response `200 OK`**
```json
[
  { "name": "PMC", "data_type": "number", "is_active": true },
  { "name": "PMHL_High", "data_type": "number", "is_active": true },
  { "name": "Notes", "data_type": "text", "is_active": true }
]
```

`data_type` is inferred at upload time from the JSON value's type (`number` vs.
`text`) the first time that metric name is seen.

### `GET /data/stocks`

Lists every stock registered for the caller's tenant.

**Response `200 OK`**
```json
[
  { "symbol": "RADICO", "display_name": "Radico Khaitan Limited", "is_active": true },
  { "symbol": "TCS", "display_name": null, "is_active": true }
]
```

---

## Error Codes

Every non-2xx response follows `{"detail": "...", "code": "..."}`. Codes map 1:1 to
the domain exceptions in [`app/exceptions.py`](../app/exceptions.py):

| HTTP status | `code` | When |
|---|---|---|
| 401 | `invalid_credentials` | Wrong email/password on login; missing, invalid, or expired bearer token; expired/revoked/unknown refresh token |
| 404 | `tenant_not_found` | A user's tenant row is missing (data integrity issue, not a normal client error) |
| 409 | `duplicate_email` | Signup with an email that's already registered |
| 422 | `invalid_trade_date` | `trade_date` in `/data/daily-upload` is in the future |
| 422 | — | Pydantic request validation errors (missing/malformed fields) — standard FastAPI shape, not the `{"detail","code"}` shape above |
| 500 | `schema_provisioning_failed` | Tenant schema/table creation failed during signup (the whole signup transaction rolls back — no partial tenant is left behind) |

**Example error response** (`401`):
```json
{
  "detail": "Invalid email or password",
  "code": "invalid_credentials"
}
```
