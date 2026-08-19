# Inception Data — Common Historical EOD Store

Status: **implemented (partial)** — schema live, first dataset loaded. This documents
what actually exists in the database today. For the full architectural proposal this
implementation is a first slice of (formula/indicator layer, partitioning, more
instrument types), see the design artifact linked at the bottom.

Inception Data is a **tenant-independent** dataset: historical End-of-Day (EOD) market
prices, shared and read-only for every tenant, stored once rather than duplicated per
tenant schema — unlike `Stock`/`Metric`/`HistoricalStockValue`, which stay per-tenant.

---

## 1. Where it lives

Three new tables in the **`public` schema** — the same schema as `Tenant`/`User`/
`RefreshToken` — on the existing dev RDS instance (`brokersync-dev-db`, same physical
database as every tenant schema). Not a separate schema: this was a deliberate call
partway through design (an earlier draft proposed a dedicated `market_data` schema;
`public` was chosen instead since it's already this app's "common, non-tenant" schema).

Managed the same way `Tenant`/`User` are — through the **central Alembic chain**
(`alembic -c alembic_central.ini upgrade head`), not the per-tenant `create_all()` path.
Current migration: `f7b0766c55ad` ("Add common market data tables: Instrument,
TradingCalendar, EodBar"), on top of `0005`.

Models: `app/models/central.py` (`Instrument`, `TradingCalendar`, `EodBar` classes,
appended after `RefreshToken`).

---

## 2. Table structure

### `Instrument` — one row per tradable symbol

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGINT` (identity) | PK |
| `symbol` | `VARCHAR(50)` | **UNIQUE**, e.g. `'ADANIENT_I'` |
| `underlying_symbol` | `VARCHAR(50)`, nullable | `_I`/`_II` roll suffix stripped, e.g. `'ADANIENT'` |
| `instrument_type` | `VARCHAR(20)` | e.g. `'FUT_CONTINUOUS'` |
| `display_name` | `VARCHAR(200)`, nullable | not populated yet |
| `first_traded_date` | `DATE`, nullable | earliest date with a bar, computed post-load |
| `last_traded_date` | `DATE`, nullable | latest date with a bar, computed post-load |
| `is_active` | `BOOLEAN` | defaults `TRUE` |

Indexes: `Instrument_pkey` (id), `Instrument_symbol_key` (unique, symbol),
`ix_instrument_underlying` (underlying_symbol).

No `exchange_id` / `Exchange` table — dropped from the design deliberately (see §5).

### `EodBar` — the canonical raw daily bar

| Column | Type | Notes |
|---|---|---|
| `instrument_id` | `BIGINT` | PK (composite), FK → `Instrument.id` |
| `trade_date` | `DATE` | PK (composite) |
| `open` / `high` / `low` / `close` | `NUMERIC(14,4)` | not null |
| `volume` | `BIGINT` | not null — vendor's `VOL` column, day's traded quantity |
| `open_interest` | `BIGINT`, nullable | vendor's `OPENINT` — populated for these futures rows, would be null for cash equities |
| `source` | `VARCHAR(30)` | `'eqldata'` for every row currently |
| `ingested_at` | `TIMESTAMP` | server default `now()`, overwritten on every re-upsert |

Primary key: **`(instrument_id, trade_date)`**. This is what makes the whole store
idempotent — see §4.

Indexes: `EodBar_pkey` (instrument_id, trade_date), `ix_eod_bar_date` (trade_date, for
"all instruments as of one date" reads).

### `TradingCalendar` — which dates actually traded

| Column | Type | Notes |
|---|---|---|
| `calendar_date` | `DATE` | PK |
| `is_trading_day` | `BOOLEAN` | always `TRUE` today (see §6) |

Populated from dates *observed* in the loaded data, not an authoritative NSE holiday
list — see the caveat in §6.

### Relationships

```
Instrument (1) ──< EodBar (many)
     id            instrument_id (FK)
```

No `Exchange`, `FormulaDefinition`, `PeriodBar`, or `DerivedEodValue` tables exist yet —
those were scoped as **tenant-side** additions in the design discussion (formulas are
tenant-configurable, e.g. per-tenant gap thresholds, so they can't be a shared table) and
have not been implemented.

---

## 3. What's actually loaded right now

| | |
|---|---|
| Source | `inception-stock-data/eod_data/` — Equal Solution (`eqldata`) vendor CSVs |
| Segment | `NFOFUT` only (NSE F&O continuous futures) — no equity (`NSEEQ`) data yet |
| Files loaded | 157 monthly CSVs (`YYYY/MM/NFOFUT.csv`) |
| `EodBar` rows | 769,119 |
| `Instrument` rows | 426 (214 `_I` current-month + 212 `_II` next-month continuous series) |
| `TradingCalendar` rows | 3,221 distinct trading days |
| Date range | 2013-08-12 → 2026-08-18 |
| `instrument_type` | 100% `FUT_CONTINUOUS` (no other type loaded yet) |

Every `Instrument.symbol` is a continuous roll series (`_I`/`_II`), not a dated expiry
contract (`ABB26AUGFUT`-style) — confirmed with the vendor that `_I` carries history
back to 2000; this account's actual data starts 2013-08-12 (an account/plan limit, not
a data-availability one).

---

## 4. How it was loaded — and why re-running it is safe

One-off loader script (not yet promoted into the app — see §7):
`load_eod_data.py`, run manually against the RDS instance.

**Per file:**
1. Parse the CSV (`EXCHANGE,INSTRUMENT,DTYYYYMMDD,OPEN,HIGH,LOW,CLOSE,VOL,OPENINT`).
2. Any symbol not yet in `Instrument` gets inserted (`underlying_symbol` derived by
   stripping `_I`/`_II`, `instrument_type` set to `'FUT_CONTINUOUS'`).
3. Rows are upserted into `EodBar` in batches of 5,000, via:

   ```sql
   INSERT INTO "EodBar"
       (instrument_id, trade_date, open, high, low, close, volume, open_interest, source, ingested_at)
   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
   ON CONFLICT (instrument_id, trade_date) DO UPDATE SET
       open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
       volume = EXCLUDED.volume, open_interest = EXCLUDED.open_interest,
       source = EXCLUDED.source, ingested_at = now()
   ```

**Why this can't duplicate data:** the primary key *is* `(instrument_id, trade_date)`.
Postgres physically cannot hold two rows for the same instrument on the same date — a
second load for a date that's already present overwrites that row's values in place
(`DO UPDATE`) instead of inserting a new one. Re-running the entire loader against the
same files is a safe no-op (same values written back); re-running it after the source
files change is a safe correction (new values overwrite old, `ingested_at` shows when).

`Instrument` and `TradingCalendar` inserts use `ON CONFLICT ... DO NOTHING` instead —
a symbol's identity and a date's "was this a trading day" fact don't get corrected by
re-running the loader, only added to.

**After all files:** one `UPDATE ... FROM (SELECT MIN/MAX(trade_date) ...)` sets
`first_traded_date`/`last_traded_date` on every touched instrument.

Total run time: ~355 seconds for all 769k rows.

---

## 5. Notable design decisions made along the way

| Decision | What happened |
|---|---|
| Schema placement | `public`, not a dedicated `market_data` schema — simpler, reuses the existing central-schema machinery |
| Exchange dimension | Dropped entirely — no `Exchange` table, no `exchange_id` anywhere. `Instrument` is flat, keyed by `symbol` alone. Addable later as one nullable column if multi-exchange support becomes real |
| `volume` column name | Kept as `volume` (vendor's `VOL`) — **not** renamed to `quantity`; an earlier pass renamed it to align with `formula_engine.py`'s `RAW_QUANTITY` field, then reverted — those are a separate, unrelated feature and shouldn't be conflated |
| `OPENINT` | Kept as a new, standalone column — no existing equivalent in the app before this |
| Partitioning | Not implemented — the original proposal recommended yearly `PARTITION BY RANGE(trade_date)` on `EodBar` as cheap future-proofing, but it was skipped for this first pass to keep the Alembic autogenerate flow simple. At 769k rows this isn't a performance problem; revisit if/when volume grows substantially |
| Staging table | The original proposal described a `staging_eod_row` + `ingestion_batch` pair for production-grade bulk loads (COPY → staging → upsert). This first load skipped that and upserted directly from parsed CSV rows — simpler for a one-off run, worth adding back if this becomes a recurring scheduled job |

---

## 6. Known gaps / caveats

- **`TradingCalendar` is not an authoritative holiday calendar.** It only contains
  dates where at least one loaded bar exists. It will be missing any date the vendor
  has no futures data for even if NSE was open (e.g. a date where every future's
  continuous series happened to roll with no trade recorded) and cannot distinguish
  "holiday" from "just no data for this segment yet."
- **No equity (`NSEEQ`) data.** Everything loaded is `NFOFUT` continuous futures. The
  business requirement's "~300 stocks" may mean cash equities, not futures — this is
  still an open question (see the design artifact's Open Questions).
- **No derived/formula layer.** 52-week high, gaps, quarter rollups, etc. — none of
  that exists yet. Scoped as tenant-side tables per the design discussion, not started.
- **`display_name` is empty** for every instrument — not populated by this loader.
- **Not wired into the API.** No router/service/repository reads from these tables yet
  — they exist in the database only.

---

## 7. Reproducing / extending this load

```bash
source .venv/bin/activate
SQL_SERVER=brokersync-dev-db.c1qg6a66213t.ap-south-1.rds.amazonaws.com \
SQL_DATABASE=brokersync \
SQL_USER=brokersync_admin \
SQL_PASSWORD='<see docs/AWS_DEPLOYMENT.md>' \
python3 load_eod_data.py
```

The script currently hardcodes the source path
(`/Users/akash/Projects/inception-stock-data/eod_data`) and only globs `NFOFUT.csv`
files — extending it to other exchange segments (`NSEEQ`, `NFOOPTSTK`, ...) means
widening that glob and deciding the right `instrument_type` per segment.

---

## Related documents

- Full architecture proposal (formula/indicator layer, partitioning, caching, API
  design, benchmarking plan): see the *Market Data Core* design artifact from this
  project's planning session.
- [`docs/BACKEND_ARCHITECTURE.md`](BACKEND_ARCHITECTURE.md) — how this fits the rest of
  the app's HLD/LLD.
- [`docs/AWS_ARCHITECTURE.md`](AWS_ARCHITECTURE.md) / [`docs/AWS_DEPLOYMENT.md`](AWS_DEPLOYMENT.md) —
  the RDS instance this data lives on.
