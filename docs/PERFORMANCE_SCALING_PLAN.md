# Performance & Scaling Plan — Read Timeouts, Historic-Data Latency, Query Cost

**Status:** proposal / in progress
**Last updated:** 2026-09-10
**Owners:** backend + infra
**Scope:** `broker-sync-api` (FastAPI + RDS PostgreSQL), `broker-file-sync` (desktop client), AWS infra (account `631069968633`, `ap-south-1`)

---

## 1. Goal

Eliminate the recurring **`Read timed out`** failures and make the data APIs — especially the
historic / large-payload ones (`/lmv-snapshot/range`, `/historic/range`, Inception) — return with
**near-zero latency**, while **reducing per-query cost** on the database.

### Success criteria

| # | Criterion | How it's measured |
|---|---|---|
| G1 | Zero `Read timed out` on any endpoint during market hours for 2 consecutive weeks | client `error.log`, backend structured logs |
| G2 | `/lmv-snapshot/range?days=60` p95 < **300 ms** (currently ~7 s uncached, or times out) | Performance Insights / API timing logs |
| G3 | Settings / theme / small endpoints p95 < **150 ms** even while a heavy query runs | API timing logs |
| G4 | DB `FreeableMemory` never drops below **200 MB**; `SwapUsage` trends to ~0 | CloudWatch |
| G5 | DB CPU and I/O per `/lmv-snapshot/range` call reduced **≥ 10×** (fewer rows scanned) | `EXPLAIN (ANALYZE, BUFFERS)` before/after |
| G6 | Connection pool never the bottleneck — no `QueuePool limit ... timed out` | backend logs |

---

## 2. Root cause (established from CloudWatch + query analysis)

The failures are **not** a network, application-code, or CPU problem.

1. **The database instance is memory-starved.** `db.t3.micro` = 1 GB RAM. `FreeableMemory` sits at
   ~40–55 MB even at idle and the instance runs with ~35–110 MB in **swap**. During the incident
   window CPU was **4–5 %** with a **full credit balance** — CPU was never the constraint.

2. **One query class tips it over.** `GET /lmv-snapshot/range` reads the per-tenant
   `LmvDailySnapshot` table, which is **EAV** (`trade_date, stock_id, metric_id, value_number,
   value_text`). Serving 60 days = **~200 stocks × ~78 metrics × 60 days ≈ 900 000 rows**, two
   joins (`Stock`, `Metric`), then a pivot in Python. ~7 s per call, **no caching**, fired on
   **every strategy toggle / N-Day refresh / formula-stats open**.

3. **When that query runs, the instance swap-thrashes and *every* query stalls** — including
   trivial ones like `SELECT theme`. That is why unrelated endpoints (`/auth/me/theme`,
   `/settings/*`, `/opening-range/snapshot`) all time out together in bursts while `/health`
   (no DB) stays instant.

4. **Connection pool amplified it.** Two engines (central + tenant), each `pool_size=5 +
   max_overflow=5`, `pool_timeout=30 s`. A burst of ~7 s queries held every connection; the
   next request waited on the pool for up to 30 s and the client's 15 s read timeout fired first.
   *(Mitigated — see §3.)*

5. **The AWS account is on the Free Plan**, which forbids any RDS class except `db.t3.micro`.
   Resizing the DB is impossible until the account is upgraded to a Paid Plan.

### Data footprint (for sizing)

| Table | Rows | Size | Schema | Growth |
|---|---|---|---|---|
| `EodBar` (central) | ~1.06 M | 250 MB | wide | ~200 rows/trading day + backfill |
| `LmvDailySnapshot` (per tenant `hari_dss`) | ~607 k | 102 MB | **EAV** | ~15.6 k rows/day/tenant |
| `HistoricalStockValue` | ~7 k | 22 MB | EAV | low |
| **Whole DB** | | **429 MB** | | moderate |

---

## 3. Shipped — the read-timeout fix (2026-09-10)

**All deployed and load-tested. Result: 0 timeouts under a 12-concurrent
stress test (~3× realistic peak); `/lmv-snapshot/range` server-side median
**1 ms** on a cache hit; no worker OOM/kills; small endpoints stay
70–230 ms under stress.** Pre/post numbers: `docs/perf-baseline/`.

### Infrastructure

| Change | Effect |
|---|---|
| **RDS `db.t3.micro` → `db.t3.small`** (1 → 2 GB), `--apply-immediately` | FreeableMemory ~35 MB → ~910 MB, SwapUsage ~42 MB → **0** — swap-thrash gone |
| **Custom parameter group `brokersync-pg16`** — `work_mem` 4 → 16 MB, `log_min_duration_statement` = 1 s, `track_io_timing` on (`statement_timeout` deliberately left 0 here so alembic migrations aren't killed — the API engines set 30 s per-connection) | big sorts stop disk-spilling; slow queries logged |
| **Performance Insights** on (7-day), **backup retention 1 → 7 days** | diagnostics + safety |
| **EC2 `t3.micro` → `t3.small`** (1 → 2 GB), stop/modify/start, Elastic IP unchanged | workers stop OOM-ing under concurrent heavy requests |
| gunicorn **2 → 3 workers**, `--timeout 120`, `--max-requests 2000 --max-requests-jitter 200` (`startup.sh` + the systemd unit) | absorb concurrency; a slow request isn't mistaken for a hung worker; buffer creep is recycled away |
| 6 CloudWatch alarms (`brokersync-db-*`: FreeableMemory, SwapUsage, CPU, connections, ReadLatency, FreeStorage) — state-only, no SNS | visibility |
| AZ move (planned I7) — **not done**: `modify-db-instance` can't change a Single-AZ instance's AZ in place; needs snapshot-restore. Minor cost item only, deferred. | — |

### Backend code (`broker-sync-api`)

| Commit | Change |
|---|---|
| `e4713a8` | **Connection-pool hardening** — `statement_timeout=30 s`, `idle_in_transaction_session_timeout=60 s`, `pool_timeout 30→10 s`, `pool_recycle=1800 s`, `application_name` per engine, shared `app/db/engine_config.py` |
| `2881da5` | **In-process response cache** (`app/core/cache.py`, `TTLCache` + tag-group invalidation) on `/lmv-snapshot/range`,`/snapshot`,`/latest`, `/historic/range`,`/snapshot`,`/latest`, `/inception/bars`, `/settings/{key}` — invalidated on the matching upload/delete/PUT. `*_payload()` service variants build a plain dict off the event loop. |
| `6b5d54b` | **`LmvDailySnapshotWide`** pre-pivoted read model — one JSONB row per (trade_date, stock), written in the same txn as the EAV rows; `get_snapshot_range_payload` reads it once `wide_table_ready()`, else falls back to the EAV pivot. `scripts/backfill_lmv_wide.py` (idempotent `INSERT … jsonb_object_agg … ON CONFLICT` per schema) ran for all tenants. Payload 10 MB → 1–3 MB, pivot ~75× cheaper. |
| `bcca82d` | **Single-flight** `get_or_set` — a burst of identical cache misses runs the producer once, the rest await it (kills the toggle-storm thundering herd). |
| `97ca080` | **`cached_response()`** caches an `_Encoded` (orjson bytes + gzip bytes, built once off-thread) — a cache hit just sends the right bytes by `Accept-Encoding`; no per-request re-serialize/re-gzip (that was ~1.7–4 s on the event loop even when the value was cached). |

### Client (`broker-file-sync` `574d3d6`)

- `/lmv-snapshot/range` fetched **once per window** (was once per distinct N-day window)
- N-Day refresh: 15 s warm timeout + exponential backoff + serve last-good; "↻ N-Day Data" bypasses backoff
- `get_range()` `timeout` override
- (bundled) conditional-formatting `THIS` no longer hard-blocks — issue #37

### Still open (lower priority, none blocking the timeout fix)

- **`ElastiCache` (Redis/Valkey)** — the in-process cache is per-worker, so each of the 3 workers warms independently and a write invalidates only the serving worker. A shared cache makes hits consistent and cross-worker. **~$12/mo**, worth it if the per-worker warm-up latency is noticeable.
- **`/inception/bars` has no authentication** (`get_central_db` only, no `get_current_user`) — EodBar data is publicly readable. Separate security fix.
- Historic-table wide model (C2 for `HistoricalStockValue`), EAV partitioning (D2), server-side scheduled jobs (C5), incremental range fetch (CL2), non-blocking client startup (CL1).

---

## 4. Infrastructure changes

> Prices are **ap-south-1, on-demand, PostgreSQL**, verified via the AWS Pricing API (Sept 2026).
> Add **18 % GST** if billed through AWS India. `730 h/month`.

### 4.0 Prerequisite (mandatory, $0)

| Change | Why | Cost |
|---|---|---|
| **Upgrade AWS account: Free Plan → Paid Plan** | Free Plan blocks every RDS class except `db.t3.micro`, and auto-closes the account ~6 months after creation (≈ 2027-01-06). Nothing else in this section is possible until this is done. | $0 (pay-as-you-go begins; existing signup credits still offset early usage) |

### 4.1 Mandatory — fixes the read timeouts

| # | Change | Detail | Δ Cost / mo |
|---|---|---|---|
| I1 | **Resize RDS `db.t3.micro` → `db.t3.small`** | 1 GB → **2 GB RAM**. `shared_buffers` ~189 MB → ~380 MB, OS page-cache headroom for the 429 MB DB, room for sort/hash workspace. `--apply-immediately`, ~3–5 min reboot, **no data loss**, same endpoint DNS. | **+$19.71** ($18.98 → $38.69) |
| I2 | **Custom DB parameter group** (currently on `default.postgres16`) | `work_mem` 4 MB → **32 MB** (the range query's sort/hash currently disk-spills every call); `statement_timeout` = `30000` (server-side backstop); `idle_in_transaction_session_timeout` = `60000`; `log_min_duration_statement` = `1000` (log every query > 1 s); `effective_cache_size` ≈ `1500MB`; `track_io_timing` = `on`. Static params (`shared_buffers` stays class-default) apply on the I1 reboot. | $0 |
| I3 | **Enable Performance Insights** (7-day retention) | Confirms the fix and shows the exact query + wait type on any recurrence. | $0 (free tier) |
| I4 | **RDS backup retention 1 → 7 days** | 429 MB DB, well within the free backup allowance (100 % of allocated storage). | ~$0 |
| I5 | **6 CloudWatch alarms → SNS/email** | `FreeableMemory < 200 MB`, `SwapUsage > 50 MB`, `CPUUtilization > 80 % (5 min)`, `DatabaseConnections > 30`, `DBLoad > 2`, API ALB/target 5xx (once behind an LB). | ~$0.60 ($0.10/alarm) |

**Mandatory subtotal: ≈ +$20 / mo.**

### 4.2 Recommended — cheap, do with the same maintenance window

| # | Change | Detail | Δ Cost / mo |
|---|---|---|---|
| I6 | **Make RDS private** (`PubliclyAccessible: false`) | DB is currently reachable from the public internet, gated only by a password + a list of *hardcoded home ISP IPs* (which rotate and will silently break access). Restrict the security group to the app's SG only. | **−$3.65** (drops one public IPv4) |
| I7 | **Move RDS to `ap-south-1a`** (same AZ as EC2) | Done in the I1 reboot via `--availability-zone ap-south-1a`. Removes cross-AZ EC2↔DB data-transfer charge; marginally lower latency. No resilience loss (Single-AZ has none either way). | **−~$1** |
| I8 | **Security-group cleanup** | Remove `SSH :22` open to `0.0.0.0/0` and `API :8000` open to `0.0.0.0/0` on `brokersync-ec2-sg-v2`; use SSM Session Manager for shell. Delete stale SGs (`brokersync-ec2-sg`). | $0 |
| I9 | **Delete the pre-resize safety snapshot** once the resize is verified | `brokersync-dev-db-pre-t3small-<ts>` | ~$0 |

**Recommended subtotal: ≈ −$5 / mo** (net saving).

### 4.3 Optional — only if load stays high after code changes, or when scaling past one app server

| # | Change | Detail | Δ Cost / mo |
|---|---|---|---|
| I10 | **RDS `db.t3.small` → `db.t3.medium`** | 4 GB RAM. Only needed if `EodBar` / snapshot history grows enough to erode the 2 GB margin (est. 12–18 months) **and** the wide-table + cache (§6) didn't land. | +$38.69 more |
| I11 | **ElastiCache (Valkey) `cache.t4g.micro`, 1 node** | Only needed when the API runs on **more than one instance** (in-process cache no longer shared). Until then §6's in-process cache is free and sufficient. | +$11.68 |
| I12 | **RDS Multi-AZ** | Automatic failover, zero-downtime minor patching. Resilience, **not** a latency fix. | +~$41 (doubles instance + storage) |
| I13 | **Read replica** | Route formula-stats / Inception reads off the primary. Only worth it at much higher analytical load. | +$38.69 (another `t3.small`) |
| I14 | **EC2 `t3.micro` → `t3.small`** | The app box is also 1 GB with no swap. Not the current bottleneck; revisit if gunicorn workers + jobs cause OOM. | +$20.51 |

### 4.4 Explicitly **not** recommended now

NAT Gateway (+$41), ALB (+$24), ECS Fargate migration, Aurora Serverless v2 (+$130–200), WAF,
VPC interface endpoints. All valuable for a larger production system; **none required** to fix
read timeouts or historic-data latency. Revisit when there are more tenants / an SLA.

### 4.5 Infra cost summary

| Line item | Now (Free Plan) | After mandatory + recommended |
|---|---|---|
| EC2 `t3.micro` | $0\* | $8.18 |
| EBS 30 GB gp3 | $0\* | $2.77 |
| **RDS `db.t3.small`** | — | **$38.69** |
| RDS storage 20 GB gp3 | $0\* | $2.53 |
| RDS backups (7-day) | — | ~$0 |
| Public IPv4 × 1 (EC2 only; RDS made private) | $0\* | $3.65 |
| Data transfer out (< 100 GB free) | $0 | ~$0–5 |
| Performance Insights | — | $0 |
| CloudWatch alarms (~6) | — | $0.60 |
| **Subtotal (USD, pre-tax)** | **~$0** | **≈ $58–64 / mo** |
| + 18 % GST (if AWS India) | | ≈ +$11 |
| **Total** | | **≈ $70–75 / mo (~₹6,000–6,300)** |

\* Currently $0 because the account is on the Free Plan. These become billable on the Paid Plan;
signup free-tier credits offset the first 1–3 months.

**If `db.t3.medium` is chosen instead of `t3.small`:** add ~$39 → **≈ $110–115 / mo (~₹9,500)**.

---

## 5. Database changes (schema, indexes, queries)

### D1 — Wide, pre-pivoted snapshot read model  *(the single biggest win)*

**Problem:** EAV means "give me a rectangle of days × stocks × metrics" reads ~900 k rows and
pivots in Python.

**Change:** add a derived read table, written at ingest time:

```sql
CREATE TABLE "LmvDailySnapshotWide" (
    trade_date  date    NOT NULL,
    stock_id    integer NOT NULL REFERENCES "Stock"(id),
    metrics     jsonb   NOT NULL,          -- {"High": 1810.5, "AvgRate": ..., ...}
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (trade_date, stock_id)
);
CREATE INDEX ix_ldsw_date ON "LmvDailySnapshotWide" (trade_date);
```

- Populated in `upsert_lmv_snapshot` (§6 C2): pivot the incoming rows **once, on write**, and
  `INSERT ... ON CONFLICT (trade_date, stock_id) DO UPDATE`.
- `get_snapshot_range` becomes:
  `SELECT trade_date, stock_id, metrics FROM "LmvDailySnapshotWide" WHERE trade_date IN (:dates)`
  → **~12 000 rows for 60 days, no join, no Python pivot**.
- One-off **backfill migration** to populate history from the existing EAV table.
- The EAV `LmvDailySnapshot` stays as the source of truth / for point lookups; the wide table is
  a rebuildable projection.
- Same treatment for `HistoricalStockValue` if formula-stats hits it hard.

**Effect:** rows scanned per call ↓ ~75×, CPU + I/O ↓ ~10–20×, latency ~7 s → tens of ms,
payload smaller. Directly satisfies G2 and G5, and makes the DB instance size much less critical.

### D2 — Partition the EAV tables by month

```sql
-- LmvDailySnapshot, HistoricalStockValue → PARTITION BY RANGE (trade_date), monthly children
```

- Range queries touch only the relevant partitions instead of the whole table/index.
- Enables cheap retention (`DETACH` + archive old partitions to S3 later).
- Do **after** D1; lower priority.

### D3 — Index / query review

- Confirm `LmvDailySnapshot` `ix_lds_date (trade_date)` is used for `WHERE trade_date IN (...)`
  (`EXPLAIN` — it should be a bitmap index scan, not a seq scan).
- `fetch_recent_trade_dates` (`SELECT DISTINCT trade_date ... ORDER BY trade_date DESC LIMIT n`)
  → back with an index-only scan; consider a tiny `TradeDate` dimension table updated at ingest
  so "most recent N trading days" is a 1-row-per-day lookup, not a `DISTINCT` over 600 k rows.
- Add `EXPLAIN (ANALYZE, BUFFERS)` snapshots for the top 5 endpoints to
  `docs/query-baselines/` before and after D1.

### D4 — Parameter tuning

Covered in I2. Key one: `work_mem` 4 MB → 32 MB removes the disk-spill on the current range query
even before D1 lands.

---

## 6. Code changes — backend (`broker-sync-api`)

### C1 — Response cache for historic / large endpoints  *(do first, $0, no infra)*

- **In-process TTL cache** (e.g. `cachetools.TTLCache`, or a tiny hand-rolled dict with
  timestamps) in a new `app/core/cache.py`.
- Cache these responses keyed by **`(schema_name, cache-relevant params)`**:
  | Endpoint | Key | TTL / invalidation |
  |---|---|---|
  | `GET /lmv-snapshot/range` | `(schema, sorted(trade_dates))` — resolve `days` → dates first (cheap `fetch_recent_trade_dates`), then cache on the date set | invalidate on `POST /lmv-snapshot/daily-upload` for that schema; also 15 min TTL safety net |
  | `GET /historic/range` | `(schema, date_from, date_to)` | same idea, invalidate on historic upload |
  | `GET /opening-range/snapshot` | `(schema, date)` | 60 s TTL |
  | `GET /settings/*`, `GET /auth/me/theme`, `/settings/custom_strategy_categories` | `(schema, user_id, key)` | invalidate on the matching `PUT`; 5 min TTL |
- Cache **after** auth/tenant resolution so it's never cross-tenant.
- Wrap values so a cache entry is immutable (return a copy / pre-serialized bytes).
- **Effect:** a strategy-toggle storm hits the dict, not Postgres. Repeat `/lmv-snapshot/range`
  calls become sub-millisecond. Satisfies G2, G3, G6.
- **Migration path:** when the API moves to >1 instance, swap the backend for ElastiCache (I11)
  behind the same `cache.py` interface — no call-site changes.

### C2 — Write the wide table on ingest

- In `lmv_snapshot_service.upsert_lmv_snapshot`: after the EAV upsert, build the per-(date, stock)
  `metrics` dict and upsert into `LmvDailySnapshotWide` in the **same transaction**.
- `get_snapshot_range` reads from the wide table (see D1). Keep a feature flag / fallback to the
  EAV path for the first release.
- Alembic: `0009_lmv_daily_snapshot_wide` (table + index) and a data-backfill step (batched,
  `INSERT ... SELECT` grouped by `(trade_date, stock_id)` with `jsonb_object_agg`).

### C3 — Split the connection pool by workload

- Keep the OLTP pool small and fast (settings, strategies, live sync).
- Give the **heavy analytical queries their own small pool** (`pool_size=2, max_overflow=2`,
  longer `statement_timeout`) so a slow `/lmv-snapshot/range` / Inception scan can **never**
  consume the connections that `/auth/me/theme` needs.
- Cheapest form: a third `create_async_engine` in `engine_config.py`
  (`brokersync-analytics`), used by the range / formula-stats / Inception services.
- When a read replica exists (I13), point this engine at the reader endpoint — no call-site
  changes.

### C4 — Payload size

- Add `GZipMiddleware` (FastAPI) — the range/historic JSON compresses ~8–10×, cutting transfer
  time and cost for the desktop client.
- `get_snapshot_range`: only return metrics the client actually uses if a `metrics=` filter is
  supplied (optional query param); default stays "all" for compatibility.
- Consider `ORJSONResponse` for these endpoints (already a dependency) — faster serialization
  of the large arrays.

### C5 — Move scheduled jobs server-side

- The EOD archive / opening-range capture / availability check currently run **inside the
  desktop client** (`broker-file-sync/services/scheduled_jobs.py`) — the day isn't archived if
  nobody has LMV open at 20:45.
- Target: **EventBridge Scheduler → a small Lambda** (or an ECS scheduled task) that pulls the
  data and writes the snapshot. The client can still push; the server owns the schedule.
- Medium priority — doesn't affect read timeouts, but removes a correctness risk and lets the
  wide table (C2) always be current.

### C6 — Keep the pool-hardening (done) and document it

`app/db/engine_config.py` is the single source of pool config. Any new engine (C3) must go
through it. Values are tunable via `Settings.db_*` / `.env`.

---

## 7. Code changes — client (`broker-file-sync`)

### CL1 — Non-blocking startup  *(partly relevant to the reported log)*

- `GET /auth/me/theme`, `/settings/*`, `/settings/custom_strategy_categories`,
  `/settings/scheduler_triggers`, `/settings/main_column_order` are fetched on app launch. When
  the backend is slow they serialize into a multi-minute "app not responding" on startup.
- Fix: fetch them **concurrently**, each with a short timeout, and **fall back to the
  last-known-good value cached on disk** (they change rarely). The window opens immediately;
  values refresh in the background.

### CL2 — Incremental range fetch

- The client usually already holds days 1…59 and only needs day 60. Add an
  `after=<trade_date>` param to `/lmv-snapshot/range` (or a small `/lmv-snapshot/since`
  endpoint) so a refresh transfers **one day**, not sixty.
- Backend: trivial `WHERE trade_date > :after` on the wide table.

### CL3 — Already shipped (see §3)

Single `get_range` call, warm/cold timeout, backoff + last-good, `get_range(timeout=)`.

---

## 8. Architecture changes

Only two are worth doing at this scale; the rest are future.

| Change | Now? | Rationale |
|---|---|---|
| **Wide read model + response cache** (D1 + C1 + C2) | **Yes** | Turns the DB from a row-shipping bottleneck into a lookup. Removes the root cause independent of instance size. |
| **Workload-isolated pool / eventual read replica** (C3) | **Pool now, replica later** | Guarantees OLTP latency is never hostage to an analytical scan. |
| Server-owned scheduling (C5) | Soon | Correctness of the daily archive; feeds the wide table. |
| OLTP/analytical DB separation (Aurora reader, or replica) | Later | Only when analytical load justifies a second instance. |
| Cold historic data (`EodBar`) → S3 Parquet + DuckDB/Athena | Later | When `EodBar` is multiple GB and rarely queried live. |
| App tier: 2× behind an ALB, containerized, IaC | Later | Removes deploy downtime + single-EC2 SPOF; not a latency fix. |

---

## 9. Rollout plan

Each step is independently shippable and independently valuable.

| Step | Change | Downtime | Depends on |
|---|---|---|---|
| **0** | Upgrade AWS account to Paid Plan | none | — |
| **1** | **C1 — response cache** (backend) + **CL1 — non-blocking startup** (client) | none (rolling) | — |
| **2** | **I2 param group** + **I1 resize to `t3.small`** + **I7 AZ move** + **I3 Perf Insights** + **I4 backups** (one maintenance window) | ~3–5 min | Step 0 |
| **3** | **I5 alarms**, **I6 make RDS private**, **I8 SG cleanup**, **I9 delete snapshot** | none | Step 2 |
| **4** | **D1 + C2 — wide read model** + backfill migration; cut `get_snapshot_range` over behind a flag | none (rolling) | Step 1 |
| **5** | **C3 — analytics pool**, **C4 — gzip**, **CL2 — incremental fetch** | none | Step 4 |
| **6** | **D2 partitioning**, **C5 server-side jobs** | none | Step 4 |
| **Later** | I10–I14 / Aurora / ALB / replica — only if metrics say so | — | — |

**Steps 1 + 2 alone are expected to eliminate the read timeouts (G1, G3, G4).**
**Step 4 delivers the historic-data latency + query-cost goals (G2, G5).**

---

## 10. Cost summary (client-facing)

| Scenario | Monthly (pre-tax) | Monthly incl. 18 % GST | ₹ (~84/$) |
|---|---|---|---|
| **Today** (Free Plan) | ~$0 | ~$0 | ~₹0 |
| **Recommended fix** — Paid Plan + `db.t3.small` + tuning + cache + wide table | **≈ $60** | **≈ $71** | **≈ ₹6,000** |
| Same, but `db.t3.medium` (more growth headroom) | ≈ $99 | ≈ $117 | ≈ ₹9,800 |
| + ElastiCache (only when app runs >1 instance) | + $12 | + $14 | + ₹1,150 |
| + Multi-AZ (resilience, optional) | + $41 | + $48 | + ₹4,000 |

Notes for the client:

- Pay-as-you-go, **no contract, no lock-in**; the DB can be scaled back down at any time.
- The account **must** move off the Free Plan regardless — it auto-closes ~January 2027.
- The largest single line item is the database instance ($38.69 for `t3.small`). Everything
  else — the code changes, parameter tuning, the cache, the wide table — is **$0** and does
  most of the actual work.
- A 1-year Reserved Instance / Savings Plan on the DB would cut ~30–40 % off that $38.69 if a
  commitment is acceptable.

---

## 11. Risks & rollback

| Change | Risk | Mitigation / rollback |
|---|---|---|
| RDS resize (I1) | ~3–5 min outage; wrong sizing | Off-hours window; pre-resize manual snapshot; `modify-db-instance` back to `t3.micro` if needed |
| Parameter group (I2) | `work_mem` too high → memory pressure with many connections | 32 MB is conservative for 2 GB / ~30 conns; monitor `FreeableMemory`; revert param |
| Make RDS private (I6) | App loses DB access if SG rules wrong | Verify app SG → RDS SG rule **before** flipping `PubliclyAccessible`; keep a break-glass IP rule for 24 h |
| Wide table cutover (D1/C2) | Pivot bug → wrong values served | Feature flag with EAV fallback; compare wide vs EAV output for N days in CI; backfill is idempotent and rebuildable |
| Response cache (C1) | Stale data after an upload | Explicit invalidation on upload + short TTL safety net; cache is per-tenant, post-auth |
| Analytics pool (C3) | Misrouted query starves the wrong pool | Small, additive engine; route only the known-heavy services; falls back to shared engine |

---

## 12. Monitoring after rollout

- **Performance Insights** dashboard — top SQL by load, wait events.
- **CloudWatch alarms** (I5) — `FreeableMemory`, `SwapUsage`, `DatabaseConnections`, `DBLoad`,
  `CPUUtilization`, API 5xx.
- **Backend logs** — `log_min_duration_statement=1000` surfaces any query > 1 s; alert on
  `QueuePool` timeout strings.
- **Client `error.log`** — watch for `Read timed out`, `N-day column refresh failed`.
- Track G1–G6 weekly for one month post-rollout.
