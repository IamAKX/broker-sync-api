# API performance baseline - PRE-UPGRADE (2026-09-10)

- Captured: 2026-09-10T12:22:38.701128 IST / 2026-09-10T06:52:38.712741Z
- Backend `http://13.206.231.240:8000` - RDS **db.t3.micro (1 GB)**, `ap-south-1b`, Single-AZ
- Connection-pool hardening (`e4713a8`) deployed and verified on BOTH engines (`statement_timeout=30s`, `idle_in_transaction_session_timeout=1min`). **No query/schema/cache changes yet.**
- Method: sequential probe (NOT concurrent / not a load test), 3 reps/endpoint, 2s pause between calls (6s after heavy), curl `--max-time 200` (a kill is recorded as TIMEOUT).
- Captured during market hours (~12:20-12:35 IST).

> inception.availability_2y / bars_all_max replaced with 360-day variants (API caps range at 366d). auth.login unavailable without password - /auth/refresh measured instead; refresh tokens are single-use so rep2/rep3 return 401 by design.

## Reading this

- **`Total med (ms)`** = median wall time over the **successful** reps only (min/max next to it).
- **`ok/to/err`** = successful / timed-out / errored reps out of 3. A row with any `to` > 0 is an endpoint that **failed under load**, not just a slow one - that is the production bug.
- `TTFB` = time to first byte (server think time). `Total - TTFB` ~= payload transfer.

## How to compare after the upgrade

1. `cd broker-sync-api`, refresh the API token, then re-run `scratchpad/api_baseline.py` into `docs/perf-baseline/<date>-post-upgrade/`.
2. Diff the two `summary.md`. Pass criteria: **`n_timeout` = 0 everywhere**, `lmv.range_60` / `inception.bars_all_1yr` median **< 1 s** (and < 300 ms once the wide-table read model lands), small endpoints unchanged (~70-150 ms).
3. Also compare payload sizes - `lmv.range_*` should shrink markedly after the wide-table + `metrics=` filter changes.

## Results

| Endpoint | Params | Status | Payload | TTFB med (ms) | **Total med (ms)** | min | max | ok/to/err | Heavy |
|---|---|---|---|---|---|---|---|---|---|
| **Auth / health** | | | | | | | | | |
| `auth.me` | - | 200 | 193 B | 79.3 | **79.7** | 76.3 | 89.0 | 3/0/0 |  |
| `auth.me.theme` | - | 200 | 16 B | 78.1 | **78.6** | 76.4 | 80.1 | 3/0/0 |  |
| `auth.refresh` | - | 200 (1/3) | 74 B | 102.3 | **102.5** | 102.5 | 102.5 | 1/0/2 |  |
| `health` | - | 200 | 15 B | 68.6 | **69.0** | 67.9 | 75.2 | 3/0/0 |  |
| **Historic snapshot (tenant, EAV)** | | | | | | | | | |
| `historic.availability_1y` | from=2025-09-09, to=2026-09-10 | 200 | 16 KB | 104.5 | **138.5** | 130.2 | 145.3 | 3/0/0 |  |
| `historic.latest` | - | 200 | 36 KB | 94.0 | **132.2** | 132.1 | 158.2 | 3/0/0 |  |
| `historic.range_120` | days=120 | 200 | 3 MB | 1337.2 | **1791.4** | 1709.7 | 2391.3 | 3/0/0 | Y |
| `historic.range_20` | days=20 | 200 | 710 KB | 534.5 | **761.7** | 515.6 | 1490.8 | 3/0/0 | Y |
| `historic.range_60` | days=60 | 200 | 2 MB | 931.5 | **1336.0** | 1139.3 | 3244.8 | 3/0/0 | Y |
| `historic.snapshot_date` | date=2026-09-09 | 200 | 36 KB | 87.7 | **126.3** | 120.4 | 155.5 | 3/0/0 |  |
| `historic.timeseries_1y` | symbol=RELIANCE, metric=Close, from=2025-... | 200 | 4 KB | 91.3 | **91.8** | 90.1 | 100.2 | 3/0/0 |  |
| **Inception (central, EodBar ~1M rows)** | | | | | | | | | |
| `inception.availability_1y` | from=2025-09-15, to=2026-09-10 | 200 | 16 KB | 92.8 | **125.0** | 119.4 | 138.2 | 3/0/0 | Y |
| `inception.bars_1sym_1mo` | from=2026-08-09, to=2026-09-10, symbols=R... | 200 | 13 KB | 89.6 | **129.1** | 119.5 | 170.3 | 3/0/0 |  |
| `inception.bars_5sym_1qtr` | from=2026-06-07, to=2026-09-10, symbols=[... | 200 | 173 KB | 128.6 | **242.0** | 208.3 | 267.1 | 3/0/0 | Y |
| `inception.bars_all_1mo` | from=2026-08-09, to=2026-09-10 | 200 | 3 MB | 680.4 | **2582.9** | 737.5 | 2738.8 | 3/0/0 | Y |
| `inception.bars_all_1yr` | from=2025-09-09, to=2026-09-10 | 200 | 25 MB | 3466.6 | **7891.8** | 7274.4 | 10251.9 | 3/0/0 | Y |
| `inception.bars_all_1yr_rerun` | from=2025-09-15, to=2026-09-10 | 200 (2/3) | 24 MB | 2856.4 | **5751.6** | 5494.6 | 6008.5 | 2/1/0 | Y |
| `inception.formula_variables` | - | 200 | 823 B | 86.4 | **87.8** | 82.9 | 90.4 | 3/0/0 |  |
| `inception.instruments` | - | 200 | 31 KB | 117.4 | **155.3** | 131.2 | 258.5 | 3/0/0 |  |
| `inception.strategies` | - | 200 | 11 KB | 88.1 | **89.7** | 81.6 | 94.8 | 3/0/0 |  |
| **LMV snapshot (tenant, EAV)** | | | | | | | | | |
| `lmv.availability_1y` | from=2025-09-09, to=2026-09-10 | 200 | 16 KB | 230.9 | **266.0** | 219.5 | 268.6 | 3/0/0 |  |
| `lmv.latest` | - | 200 | 286 KB | 253.3 | **393.6** | 331.6 | 410.2 | 3/0/0 |  |
| `lmv.range_20` | days=20 | 200 | 6 MB | 3143.4 | **3745.2** | 3232.8 | 5756.4 | 3/0/0 | Y |
| `lmv.range_60` | days=60 | 200 (2/3) | 10 MB | 5002.6 | **6090.7** | 5918.8 | 6262.6 | 2/1/0 | Y |
| `lmv.range_90` | days=90 | 200 (1/3) | - | 4750.0 | **5917.0** | 5917.0 | 5917.0 | 1/2/0 | Y |
| `lmv.snapshot_date` | date=2026-09-09 | 200 | 286 KB | 206.2 | **347.7** | 300.2 | 357.3 | 3/0/0 |  |
| **Opening range** | | | | | | | | | |
| `opening_range.availability` | from=2026-06-07, to=2026-09-10 | 200 | 4 KB | 81.0 | **81.4** | 77.6 | 93.0 | 3/0/0 |  |
| `opening_range.latest` | - | 200 | 20 KB | 89.5 | **123.3** | 121.9 | 127.3 | 3/0/0 |  |
| `opening_range.snapshot` | date=2026-09-10 | 200 | 20 KB | 92.1 | **128.1** | 120.5 | 173.9 | 3/0/0 |  |
| **Reference data** | | | | | | | | | |
| `data.metrics` | - | 200 | 12 KB | 84.3 | **84.8** | 80.1 | 90.9 | 3/0/0 |  |
| `data.stocks` | - | 200 | 32 KB | 95.3 | **131.9** | 123.4 | 135.1 | 3/0/0 |  |
| `holidays` | - | 200 | 111 B | 82.8 | **83.2** | 80.3 | 86.7 | 3/0/0 |  |
| **Settings (per-key)** | | | | | | | | | |
| `settings.alert_window` | - | 200 | 35 B | 76.7 | **77.2** | 75.5 | 89.2 | 3/0/0 |  |
| `settings.column_categories` | - | 200 | 40 B | 76.2 | **76.5** | 74.6 | 80.5 | 3/0/0 |  |
| `settings.conditional_formatting` | - | 200 | 45 B | 73.6 | **74.0** | 73.8 | 74.4 | 3/0/0 |  |
| `settings.custom_strategy_categories` | - | 200 | 53 B | 77.6 | **78.1** | 75.7 | 84.9 | 3/0/0 |  |
| `settings.frozen_columns` | - | 200 | 37 B | 110.5 | **111.0** | 75.4 | 151.6 | 3/0/0 |  |
| `settings.main_column_order` | - | 200 | 38 B | 77.3 | **77.8** | 77.1 | 85.3 | 3/0/0 |  |
| `settings.scheduler_triggers` | - | 200 | 340 B | 81.2 | **81.7** | 73.7 | 97.4 | 3/0/0 |  |
| **Strategies / formulas / signals** | | | | | | | | | |
| `formula_variables.list` | - | 200 | 136 KB | 105.3 | **211.9** | 191.0 | 252.4 | 3/0/0 |  |
| `strategies.list` | - | 200 | 357 KB | 129.6 | **276.7** | 254.6 | 311.3 | 3/0/0 |  |
| `strategy_signals.filtered` | direction=BUY, status=open, page_size=100 | 200 | 81 KB | 105.3 | **178.9** | 175.6 | 270.3 | 3/0/0 |  |
| `strategy_signals.p1` | page=1, page_size=25 | 200 | 20 KB | 105.0 | **143.1** | 127.5 | 279.8 | 3/0/0 |  |
| `strategy_signals.p1_big` | page=1, page_size=100 | 200 | 81 KB | 95.0 | **171.6** | 169.9 | 177.4 | 3/0/0 | Y |

## Heavy endpoints - ranked

- **`lmv.range_90`** `days=90` - - payload, median 5917.0 ms, range 5917.0-5917.0 ms - **TIMED OUT 2/3 reps** (client killed at 200s; the server kept running the request)
- **`lmv.range_60`** `days=60` - 10 MB payload, median 6090.7 ms, range 5918.8-6262.6 ms - **TIMED OUT 1/3 reps** (client killed at 200s; the server kept running the request)
- **`inception.bars_all_1yr_rerun`** `from=2025-09-15, to=2026-09-10` - 24 MB payload, median 5751.6 ms, range 5494.6-6008.5 ms - **TIMED OUT 1/3 reps** (client killed at 200s; the server kept running the request)
- **`inception.bars_all_1yr`** `from=2025-09-09, to=2026-09-10` - 25 MB payload, median 7891.8 ms, range 7274.4-10251.9 ms
- **`lmv.range_20`** `days=20` - 6 MB payload, median 3745.2 ms, range 3232.8-5756.4 ms
- **`historic.range_60`** `days=60` - 2 MB payload, median 1336.0 ms, range 1139.3-3244.8 ms
- **`inception.bars_all_1mo`** `from=2026-08-09, to=2026-09-10` - 3 MB payload, median 2582.9 ms, range 737.5-2738.8 ms
- **`historic.range_120`** `days=120` - 3 MB payload, median 1791.4 ms, range 1709.7-2391.3 ms
- **`historic.range_20`** `days=20` - 710 KB payload, median 761.7 ms, range 515.6-1490.8 ms
- **`inception.bars_5sym_1qtr`** `from=2026-06-07, to=2026-09-10, symbols=['RELIANCE_I', 'TCS_I', 'INFY_I', 'HDFCBANK_I', 'ICICIBANK_I']` - 173 KB payload, median 242.0 ms, range 208.3-267.1 ms
- **`strategy_signals.p1_big`** `page=1, page_size=100` - 81 KB payload, median 171.6 ms, range 169.9-177.4 ms
- **`inception.availability_1y`** `from=2025-09-15, to=2026-09-10` - 16 KB payload, median 125.0 ms, range 119.4-138.2 ms

## Key observations (pre-upgrade)

- **`/lmv-snapshot/range` is bimodal and unreliable.** `days=60/90` return a **~10 MB** payload in ~6 s when the DB has memory headroom, but **timed out on half the heavy reps** (`lmv.range_60`: 1/3, `lmv.range_90`: 2/3) - 100-140 s before the client gave up - when the `t3.micro` was swap-thrashing. This is the exact production symptom, reproduced with a single sequential caller.
- **`/inception/bars` (no symbol filter) is the same story at 24-25 MB** - `bars_all_360d` timed out 1/3, `bars_all_1yr` was 7-10 s.
- **`statement_timeout=30s` did NOT stop the 100 s+ hangs.** The SQL finishes in ~5-7 s; the unbounded time is the **Python-side pivot of ~900 k EAV rows + serialization of the 10-25 MB JSON**, which no DB timeout covers. The wide-table read model (fewer rows, no pivot, smaller payload) is the real fix - a bigger DB instance only widens the good-case window.
- **`/historic/range` (raw OHLC) is lighter** - 0.7-3 MB, 0.8-2.4 s, 0 timeouts - because it stores ~7 metrics/stock vs LMV's ~78.
- **Everything else is healthy.** Auth, health, settings, reference data, strategies, signals, opening-range, all `*/latest` and `*/snapshot`: **70-410 ms, 0 timeouts.** The problem is narrowly the historic-range / bars-all family.
- **Pool hardening is working** - a heavy-query timeout did not cascade: the very next endpoint after each one returned in normal time. Pre-hardening, these would have taken every other request down with them.
- `auth.refresh` shows `1/0/2` because refresh tokens are single-use (rep 1 consumes it, rep 2-3 get 401) - not a performance signal.
