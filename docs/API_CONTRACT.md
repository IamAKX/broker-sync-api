# API Contract

Request/response reference for every endpoint, with sample **success and failure**
payloads. For *why* the API is shaped this way (multi-tenancy model, EAV metric
storage, auth design), see [`BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md). This
doc is the *what* — exact fields, types, and status codes — kept in sync with the
Pydantic schemas in [`app/schemas/`](../app/schemas) and the routers in
[`app/routers/`](../app/routers).

Interactive, always-current docs are also available at `/docs` (Swagger UI) and
`/redoc` on a running instance, generated from the same schemas.

---

## Conventions

- **Base URL**: `http://localhost:8000` locally; `http://<ec2-public-ip>:8000`
  when deployed (see [`AWS_DEPLOYMENT.md`](AWS_DEPLOYMENT.md)).
- **Content type**: `application/json` for all request and response bodies.
- **Auth**: `Authorization: Bearer <access_token>` header on every `/historic/*`,
  `/data/*`, and `/holidays/*` endpoint, and none of the `/auth/*` endpoints. The token
  is issued by signup/login/refresh.
- **Tenant scoping**: which tenant's schema a request reads/writes is derived **only**
  from the verified JWT (`schema_name` claim) — never from a header, query param, or
  request body. There is no way to specify a different tenant than the one the token
  was issued for.
- **Dates**: `YYYY-MM-DD` (ISO 8601 date, no time component) for `trade_date` and date
  range query params.
- **Error shape**: every domain error response is
  `{"detail": "<human-readable message>", "code": "<machine-readable code>"}`. See
  [Error Codes](#error-codes) below for the full list and which endpoints can return
  which codes.

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
  "phone_number": "+91 98765 43210",
  "password": "Str0ngPassw0rd!"
}
```

| Field | Type | Constraints |
|---|---|---|
| `name` | string | 1–200 chars. Stored as the user's display name and drives the tenant's schema name (e.g. `sundar_dss`; a repeat becomes `sundar1_dss`) |
| `email` | string | Valid email, must not already be registered |
| `phone_number` | string | 7–20 chars, digits with optional leading `+`, spaces, dashes, parens |
| `password` | string | 8–128 chars |

**Success response `201 Created`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "3n8fK...opaque-random-token...9dQ",
  "token_type": "bearer"
}
```

**Failure response `409 Conflict`** — email already registered
```json
{
  "detail": "Email already registered",
  "code": "duplicate_email"
}
```

**Failure response `422 Unprocessable Entity`** — request validation (e.g. password too short)
```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "password"],
      "msg": "String should have at least 8 characters",
      "input": "short"
    }
  ]
}
```

Other possible failure: `500 schema_provisioning_failed` if tenant schema/table
creation fails (rare — the whole signup transaction rolls back, no partial tenant is
left behind).

### `POST /auth/login`

**Request body**
```json
{
  "email": "sundar@example.com",
  "password": "Str0ngPassw0rd!"
}
```

**Success response `200 OK`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "9pQmZ...opaque-random-token...4vX",
  "token_type": "bearer"
}
```

**Failure response `401 Unauthorized`** — wrong email or password
```json
{
  "detail": "Invalid email or password",
  "code": "invalid_credentials"
}
```

### `POST /auth/refresh`

Exchanges a valid, unexpired, unrevoked refresh token for a new access token +
refresh token pair. The old refresh token is revoked as part of this call (rotation).

**Request body**
```json
{
  "refresh_token": "3n8fK...opaque-random-token...9dQ"
}
```

**Success response `200 OK`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "7hLwR...new-opaque-random-token...2sT",
  "token_type": "bearer"
}
```

**Failure response `401 Unauthorized`** — expired, revoked, or unknown token
```json
{
  "detail": "Invalid or expired refresh token",
  "code": "invalid_credentials"
}
```

### `POST /auth/logout`

Revokes a refresh token. Idempotent — logging out an already-revoked or unknown token
still returns `204` (there is nothing meaningful to fail on for this endpoint, so no
failure response shape is documented beyond standard request validation).

**Request body**
```json
{
  "refresh_token": "3n8fK...opaque-random-token...9dQ"
}
```

**Success response**: `204 No Content` (empty body).

### `GET /auth/me`

Requires `Authorization: Bearer <access_token>`. Returns the caller's profile.

**Success response `200 OK`**
```json
{
  "name": "Sundar",
  "email": "sundar@example.com",
  "phone_number": "+91 98765 43210",
  "role": "owner",
  "created_at": "2026-07-03T10:15:00",
  "last_login_at": "2026-07-17T09:02:11"
}
```

`last_login_at` is the timestamp of the *previous* login, not the one currently in
progress — it stays fixed for the whole session. `null` on a user's first session
(nothing to show yet).

### `PATCH /auth/me`

Requires `Authorization: Bearer <access_token>`. Updates name/email/phone number.
Since those fields are also JWT claims, this returns a fresh access token — the
caller must replace its stored token with the one in the response, or the JWT will
keep showing the old values until the next login/refresh.

**Request body**
```json
{
  "name": "Sundar P.",
  "email": "sundar@example.com",
  "phone_number": "+91 98765 43210"
}
```

Same field constraints as signup's `name`/`email`/`phone_number`.

**Success response `200 OK`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Failure response `409 Conflict`** — email already taken by another user
```json
{
  "detail": "Email already registered",
  "code": "duplicate_email"
}
```

### `POST /auth/change-password`

Requires `Authorization: Bearer <access_token>`. Verifies `current_password` before
applying `new_password`.

**Request body**
```json
{
  "current_password": "Str0ngPassw0rd!",
  "new_password": "EvenStr0nger!"
}
```

**Success response**: `204 No Content` (empty body).

**Failure response `401 Unauthorized`** — current password is wrong
```json
{
  "detail": "Current password is incorrect",
  "code": "invalid_credentials"
}
```

---

## Historic

All `/historic/*` endpoints require `Authorization: Bearer <access_token>` and operate
on the caller's own tenant schema, reading/writing the `HistoricalStockValue` table.
Every endpoint below can additionally return:

**Failure response `401 Unauthorized`** — missing, malformed, or expired bearer token
```json
{
  "detail": "Missing bearer token",
  "code": "invalid_credentials"
}
```

### `POST /historic/daily-upload`

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

**Success response `200 OK`**
```json
{
  "trade_date": "2026-06-27",
  "stocks_upserted": 2,
  "metrics_registered": 4,
  "values_upserted": 5
}
```

**Failure response `422 Unprocessable Entity`** — `trade_date` in the future
```json
{
  "detail": "trade_date cannot be in the future",
  "code": "invalid_trade_date"
}
```

**Failure response `422 Unprocessable Entity`** — `trade_date` is a registered market
holiday (see [Holidays](#holidays) below) — the whole upload is rejected, nothing is
saved
```json
{
  "detail": "2026-06-27 is a market holiday — upload rejected",
  "code": "trade_date_is_holiday"
}
```

### `GET /historic/snapshot?date=YYYY-MM-DD`

Wide-pivoted grid for one date: all stocks × all metrics recorded that day. `date` is
optional — omitting it returns the most recent `trade_date` present in the data
(equivalent to `/historic/latest`).

**Success response `200 OK`**
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

**Success response `200 OK`** — no data recorded for that date yet
```json
{
  "trade_date": "2026-06-27",
  "stocks": []
}
```

**Failure response `422 Unprocessable Entity`** — malformed `date` query param
```json
{
  "detail": [
    {
      "type": "date_from_datetime_parsing",
      "loc": ["query", "date"],
      "msg": "Input should be a valid date or datetime, input is too short",
      "input": "not-a-date"
    }
  ]
}
```

### `GET /historic/latest`

Alias for `/historic/snapshot` with no `date` — returns the most recent `trade_date`
present in the tenant's data.

**Success response `200 OK`**
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
    }
  ]
}
```

**Success response `200 OK`** — no data uploaded yet for this tenant (not an error)
```json
{
  "trade_date": "2026-07-15",
  "stocks": []
}
```
`trade_date` here is today's date (no upload exists to derive a "latest" from yet).

### `GET /historic/timeseries?symbol=&metric=&from=&to=`

Single metric, single stock, across a date range — for charting/backtesting.

| Query param | Type | Required |
|---|---|---|
| `symbol` | string | yes |
| `metric` | string | yes |
| `from` | date | no — omit for no lower bound |
| `to` | date | no — omit for no upper bound |

**Example**: `GET /historic/timeseries?symbol=RADICO&metric=PMC&from=2026-06-01&to=2026-06-27`

**Success response `200 OK`**
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

**Success response `200 OK`** — unknown `symbol` or `metric`
```json
{
  "symbol": "NOTREAL",
  "metric": "PMC",
  "points": []
}
```
An unknown `symbol` or `metric` returns `200` with an empty `points` array, not a 404
— querying for data that doesn't exist yet is a normal, expected case for this API.

**Failure response `422 Unprocessable Entity`** — missing required query param
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["query", "metric"],
      "msg": "Field required"
    }
  ]
}
```

### `GET /historic/availability?from=YYYY-MM-DD&to=YYYY-MM-DD`

For every date in `[from, to]`, tells whether **any** metric/stock data was uploaded
for that `trade_date` — one `true`/`false` per calendar day, useful for a date-range
picker that greys out days with no data.

| Query param | Type | Required |
|---|---|---|
| `from` | date | yes |
| `to` | date | yes — must be on or after `from`, and within 366 days of `from` |

**Example**: `GET /historic/availability?from=2026-06-25&to=2026-06-28`

**Success response `200 OK`**
```json
{
  "date_from": "2026-06-25",
  "date_to": "2026-06-28",
  "dates": [
    { "trade_date": "2026-06-25", "has_data": false },
    { "trade_date": "2026-06-26", "has_data": true },
    { "trade_date": "2026-06-27", "has_data": true },
    { "trade_date": "2026-06-28", "has_data": false }
  ]
}
```
`dates` always contains one entry per calendar day in the range, in order, regardless
of whether any data exists — an empty tenant returns `has_data: false` for every day
rather than an empty list.

**Failure response `422 Unprocessable Entity`** — `from` is after `to`, or range exceeds 366 days
```json
{
  "detail": "date_from must be on or before date_to",
  "code": "invalid_date_range"
}
```

**Failure response `422 Unprocessable Entity`** — missing required query param
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["query", "to"],
      "msg": "Field required"
    }
  ]
}
```

---

## Data

All `/data/*` endpoints require `Authorization: Bearer <access_token>` and operate on
the caller's own tenant schema, listing the `Stock`/`Metric` catalog tables (not the
`HistoricalStockValue` value table — see [Historic](#historic) above for that). Every
endpoint below can additionally return the same `401 invalid_credentials` failure
shown above for a missing/malformed/expired bearer token.

### `GET /data/metrics`

Lists every metric registered for the caller's tenant (auto-registered by past uploads
via `/historic/daily-upload` — there's no separate "create a metric" endpoint).

**Success response `200 OK`**
```json
[
  { "name": "PMC", "data_type": "number", "is_active": true },
  { "name": "PMHL_High", "data_type": "number", "is_active": true },
  { "name": "Notes", "data_type": "text", "is_active": true }
]
```

`data_type` is inferred at upload time from the JSON value's type (`number` vs.
`text`) the first time that metric name is seen.

**Success response `200 OK`** — no metrics registered yet
```json
[]
```

### `GET /data/stocks`

Lists every stock registered for the caller's tenant.

**Success response `200 OK`**
```json
[
  { "symbol": "RADICO", "display_name": "Radico Khaitan Limited", "is_active": true },
  { "symbol": "TCS", "display_name": null, "is_active": true }
]
```

**Success response `200 OK`** — no stocks registered yet
```json
[]
```

---

## Holidays

All `/holidays` endpoints require `Authorization: Bearer <access_token>` and operate on
the caller's own tenant schema. Maintained manually (Edit → Market Holidays in the
client) — there is no automatic feed. `/historic/daily-upload` rejects any `trade_date`
that matches a row here (see above).

### `GET /holidays?year=YYYY`

Lists holidays for the caller's tenant, ordered by date. `year` is optional — omitting
it returns every holiday on record, across all years.

**Success response `200 OK`**
```json
[
  { "id": 1, "holiday_date": "2026-01-26", "name": "Republic Day" },
  { "id": 2, "holiday_date": "2026-08-15", "name": "Independence Day" }
]
```

### `POST /holidays`

**Request body**
```json
{ "holiday_date": "2026-01-26", "name": "Republic Day" }
```

| Field | Type | Notes |
|---|---|---|
| `holiday_date` | date | Must be unique across the tenant's holiday list |
| `name` | string | 1–200 chars |

**Success response `201 Created`**
```json
{ "id": 1, "holiday_date": "2026-01-26", "name": "Republic Day" }
```

**Failure response `409 Conflict`** — a holiday already exists on that date
```json
{
  "detail": "A holiday already exists on 2026-01-26",
  "code": "duplicate_holiday_date"
}
```

### `PATCH /holidays/{holiday_id}`

Full replace of `holiday_date` and `name` — same request/response/failure shape as
`POST /holidays` above, plus:

**Failure response `404 Not Found`** — no holiday with that id for this tenant
```json
{
  "detail": "Holiday 999 not found",
  "code": "holiday_not_found"
}
```

### `DELETE /holidays/{holiday_id}`

**Success response `204 No Content`** — empty body

**Failure response `404 Not Found`** — same shape as `PATCH` above

---

## Error Codes

Every domain error response follows `{"detail": "...", "code": "..."}`. Codes map 1:1
to the domain exceptions in [`app/exceptions.py`](../app/exceptions.py). Request
validation errors (missing/malformed fields) use FastAPI's standard `{"detail": [...]}`
shape instead — shown inline above wherever an endpoint can trigger one.

| HTTP status | `code` | Returned by | When |
|---|---|---|---|
| 401 | `invalid_credentials` | `/auth/login`, `/auth/refresh`, `/auth/change-password`, all `/historic/*`, all `/data/*` | Wrong email/password; missing/invalid/expired bearer token; expired/revoked/unknown refresh token; wrong current password |
| 404 | `tenant_not_found` | `/auth/login`, `/auth/refresh`, `/auth/me` (PATCH) | A user's tenant row is missing (data integrity issue, not a normal client error) |
| 404 | `user_not_found` | `/auth/me` (GET, PATCH), `/auth/change-password` | The user behind a valid token no longer exists (data integrity issue, not a normal client error) |
| 409 | `duplicate_email` | `/auth/signup`, `/auth/me` (PATCH) | Signup or profile update with an email that's already registered |
| 422 | `invalid_trade_date` | `/historic/daily-upload` | `trade_date` is in the future |
| 422 | `trade_date_is_holiday` | `/historic/daily-upload` | `trade_date` matches a row in the tenant's holiday list |
| 422 | `invalid_date_range` | `/historic/availability` | `from` is after `to`, or the range exceeds 366 days |
| 404 | `holiday_not_found` | `/holidays/{id}` (PATCH, DELETE) | No holiday with that id for this tenant |
| 409 | `duplicate_holiday_date` | `/holidays` (POST), `/holidays/{id}` (PATCH) | Another holiday already exists on the given date |
| 500 | `schema_provisioning_failed` | `/auth/signup` | Tenant schema/table creation failed during signup — the whole signup transaction rolls back, no partial tenant is left behind |

**Example domain error response** (`401` from a missing bearer token on any `/historic/*` or `/data/*` call):
```json
{
  "detail": "Missing bearer token",
  "code": "invalid_credentials"
}
```

**Example validation error response** (`422`, any endpoint, FastAPI's standard shape —
note there is no `"code"` field on these):
```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "email"],
      "msg": "value is not a valid email address",
      "input": "not-an-email"
    }
  ]
}
```
