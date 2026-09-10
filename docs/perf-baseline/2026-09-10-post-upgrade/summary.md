# API performance baseline - POST-UPGRADE (2026-09-10)

- Captured: 2026-09-10T14:23:36.199836 (IST), 2026-09-10T08:53:36.200025+00:00
- Backend: http://13.206.231.240:8000  |  RDS `db.t3.small` (2 GB) + tuned params; EC2 `t3.small`, gunicorn 3 workers
- Full stack deployed: connection-pool hardening + in-process response cache + `LmvDailySnapshotWide` read model.
- Payloads shown are UNCOMPRESSED (the probe doesn't send Accept-Encoding); real clients send gzip and get ~1/3 the bytes.
- Method: sequential probe, 3 reps/endpoint, pause between calls (not a load test).

Re-run `scratchpad/api_baseline.py` post-upgrade into a new `*-post-upgrade/` dir and diff this table.

| Endpoint | Params | Status | Payload | TTFB (median) | Total ms (median) | min | max | Heavy |
|---|---|---|---|---|---|---|---|---|
| `auth.refresh` | - | 401 | 74 B | 81.4 | **81.8** | 78.9 | 89.2 |  |
| `auth.me` | - | 200 | 193 B | 76.7 | **77.2** | 76.5 | 78.6 |  |
| `auth.me.theme` | - | 200 | 16 B | 74.0 | **74.2** | 72.7 | 75.6 |  |
| `health` | - | 200 | 15 B | 92.0 | **92.3** | 73.7 | 95.9 |  |
| `data.stocks` | - | 200 | 32 KB | 85.2 | **123.9** | 123.6 | 123.9 |  |
| `data.metrics` | - | 200 | 12 KB | 88.4 | **88.9** | 87.5 | 90.7 |  |
| `holidays` | - | 200 | 111 B | 98.4 | **98.8** | 75.5 | 130.1 |  |
| `settings.scheduler_triggers` | - | 200 | 340 B | 79.9 | **80.4** | 72.1 | 80.8 |  |
| `settings.main_column_order` | - | 200 | 38 B | 70.9 | **71.2** | 70.8 | 72.7 |  |
| `settings.custom_strategy_categories` | - | 200 | 53 B | 79.3 | **79.7** | 76.7 | 81.4 |  |
| `settings.frozen_columns` | - | 200 | 37 B | 78.2 | **78.7** | 78.7 | 80.7 |  |
| `settings.column_categories` | - | 200 | 40 B | 76.4 | **77.0** | 70.2 | 84.0 |  |
| `settings.conditional_formatting` | - | 200 | 45 B | 71.7 | **72.2** | 70.7 | 72.9 |  |
| `settings.alert_window` | - | 200 | 35 B | 77.3 | **77.7** | 77.2 | 79.3 |  |
| `strategies.list` | - | 200 | 357 KB | 101.6 | **247.4** | 244.5 | 279.6 |  |
| `formula_variables.list` | - | 200 | 136 KB | 96.2 | **206.1** | 194.3 | 326.8 |  |
| `strategy_signals.p1` | page=1, page_size=25 | 200 | 20 KB | 90.1 | **128.4** | 117.5 | 150.4 |  |
| `strategy_signals.p1_big` | page=1, page_size=100 | 200 | 81 KB | 87.5 | **160.8** | 154.7 | 167.8 | yes |
| `strategy_signals.filtered` | direction=BUY, status=open, page_size=100 | 200 | 81 KB | 93.9 | **166.4** | 164.8 | 179.4 |  |
| `opening_range.latest` | - | 200 | 20 KB | 92.1 | **125.7** | 116.3 | 127.9 |  |
| `opening_range.snapshot` | date=2026-09-10 | 200 | 20 KB | 82.0 | **117.7** | 112.4 | 125.0 |  |
| `opening_range.availability` | from=2026-06-07, to=2026-09-10 | 200 | 4 KB | 84.2 | **84.8** | 80.1 | 97.4 |  |
| `lmv.latest` | - | 200 | 286 KB | 75.7 | **221.3** | 213.3 | 221.4 |  |
| `lmv.snapshot_date` | date=2026-09-09 | 200 | 286 KB | 76.6 | **218.5** | 216.1 | 390.0 |  |
| `lmv.range_20` | days=20 | 200 | 5 MB | 78.8 | **752.1** | 656.5 | 3408.5 | yes |
| `lmv.range_60` | days=60 | 200 | 10 MB | 77.4 | **1216.4** | 1050.3 | 1291.2 | yes |
| `lmv.range_90` | days=90 | 200 | 10 MB | 75.5 | **1077.8** | 1049.1 | 1874.5 | yes |
| `lmv.availability_1y` | from=2025-09-15, to=2026-09-10 | 200 | 16 KB | 187.1 | **221.4** | 218.4 | 226.7 |  |
| `historic.latest` | - | 200 | 36 KB | 95.0 | **131.1** | 119.4 | 137.6 |  |
| `historic.snapshot_date` | date=2026-09-09 | 200 | 36 KB | 87.9 | **127.6** | 117.8 | 129.0 |  |
| `historic.range_20` | days=20 | 200 | 710 KB | 2059.8 | **3155.0** | 2192.5 | 3217.9 | yes |
| `historic.range_60` | days=60 | 200 | 2 MB | 77.7 | **413.8** | 398.5 | 415.8 | yes |
| `historic.range_120` | days=120 | 200 | 3 MB | 78.4 | **481.2** | 463.2 | 499.6 | yes |
| `historic.timeseries_1y` | symbol=RELIANCE, metric=Close, from=2025-09... | 200 | 4 KB | 88.8 | **89.2** | 83.0 | 91.2 |  |
| `historic.availability_1y` | from=2025-09-15, to=2026-09-10 | 200 | 16 KB | 106.8 | **141.7** | 139.3 | 143.4 |  |
| `inception.instruments` | - | 200 | 31 KB | 91.0 | **129.1** | 128.5 | 1060.6 |  |
| `inception.availability_1y` | from=2025-09-15, to=2026-09-10 | 200 | 16 KB | 110.5 | **157.5** | 147.4 | 273.9 |  |
| `inception.strategies` | - | 200 | 11 KB | 97.4 | **99.9** | 81.4 | 110.3 |  |
| `inception.formula_variables` | - | 200 | 823 B | 98.0 | **98.5** | 83.6 | 99.1 |  |
| `inception.bars_1sym_1mo` | from=2026-08-09, to=2026-09-10, symbols=REL... | 200 | 13 KB | 81.8 | **117.0** | 111.8 | 133.4 |  |
| `inception.bars_5sym_1qtr` | from=2026-06-07, to=2026-09-10, symbols=['R... | 200 | 173 KB | 102.6 | **218.9** | 209.8 | 219.1 | yes |
| `inception.bars_all_1mo` | from=2026-08-09, to=2026-09-10 | 200 | 3 MB | 391.0 | **816.1** | 812.7 | 3527.6 | yes |
| `inception.bars_all_1yr` | from=2025-09-15, to=2026-09-10 | 200 | 24 MB | 84.7 | **2527.3** | 2430.1 | 2719.6 | yes |

## Heavy endpoints (the ones that matter for the upgrade)

- **`historic.range_20`** (days=20) -> 200, 710 KB, median 3155.0 ms (range 2192.5-3217.9 ms)
- **`inception.bars_all_1yr`** (from=2025-09-15, to=2026-09-10) -> 200, 24 MB, median 2527.3 ms (range 2430.1-2719.6 ms)
- **`lmv.range_60`** (days=60) -> 200, 10 MB, median 1216.4 ms (range 1050.3-1291.2 ms)
- **`lmv.range_90`** (days=90) -> 200, 10 MB, median 1077.8 ms (range 1049.1-1874.5 ms)
- **`inception.bars_all_1mo`** (from=2026-08-09, to=2026-09-10) -> 200, 3 MB, median 816.1 ms (range 812.7-3527.6 ms)
- **`lmv.range_20`** (days=20) -> 200, 5 MB, median 752.1 ms (range 656.5-3408.5 ms)
- **`historic.range_120`** (days=120) -> 200, 3 MB, median 481.2 ms (range 463.2-499.6 ms)
- **`historic.range_60`** (days=60) -> 200, 2 MB, median 413.8 ms (range 398.5-415.8 ms)
- **`inception.bars_5sym_1qtr`** (from=2026-06-07, to=2026-09-10, symbols=['RELIANCE_I', 'TCS_I', 'INFY_I', 'HDFCBANK_I', 'ICICIBANK_I']) -> 200, 173 KB, median 218.9 ms (range 209.8-219.1 ms)
- **`strategy_signals.p1_big`** (page=1, page_size=100) -> 200, 81 KB, median 160.8 ms (range 154.7-167.8 ms)


## vs pre-upgrade (sequential probe, median total ms)

| Endpoint | pre | post | |
|---|---|---|---|
| `lmv.range_60` | 6091 ms, **1/3 timed out** | **1216 ms**, 0 timeout | 5x + no timeout |
| `lmv.range_90` | 5917 ms, **2/3 timed out** | **1078 ms**, 0 timeout | 5.5x + no timeout |
| `lmv.range_20` | 3745 ms | **752 ms** | 5x |
| `inception.bars_all_1yr` (24 MB) | 7892 ms | **2527 ms** | 3x |
| `historic.range_120` | 1791 ms | **481 ms** | 3.7x |
| `historic.range_60` | 1336 ms | **414 ms** | 3.2x |
| small endpoints (auth/settings/latest) | 70-410 ms | 70-250 ms | same/better |
| **timeouts across the whole run** | **5** | **0** | |

A separate 12-concurrent stress test (session notes): pre = 24 timeouts + worker OOM;
post = **0 timeouts**, small endpoints stay 70-230 ms under load, `/lmv-snapshot/range`
server-side **median 1 ms** on a cache hit.
