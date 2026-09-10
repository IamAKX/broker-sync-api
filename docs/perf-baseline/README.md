# API performance baselines

Timed captures of every **read** API, used to measure the effect of the
performance work in [`../PERFORMANCE_SCALING_PLAN.md`](../PERFORMANCE_SCALING_PLAN.md).

## Layout

```
perf-baseline/
├── api_baseline.py                 # the capture script (self-contained, curl-based)
├── 2026-09-10-pre-upgrade/         # one dir per capture run
│   ├── summary.md                  # human-readable table + observations  <- read this
│   ├── results.json                # full machine-readable timings (all reps)
│   └── responses/                  # one sample body per endpoint (rep 1)
│                                   #   bodies > 200 KB are truncated to a 60 KB head;
│                                   #   the full byte size is in results.json + the note
└── <date>-post-upgrade/            # created after the DB resize / wide-table change
```

## Running a capture

```bash
cd broker-sync-api

# 1. get a fresh API token into broker-file-sync/auth_session.json
#    (the script reuses the desktop client's stored session; refresh if expired)
RT=$(python3 -c "import json;print(json.load(open('../broker-file-sync/auth_session.json'))['refresh_token'])")
curl -s -X POST http://13.206.231.240:8000/auth/refresh \
  -H 'Content-Type: application/json' -d "{\"refresh_token\":\"$RT\"}" \
  | python3 -c "import sys,json;r=json.load(sys.stdin);json.dump({'access_token':r['access_token'],'refresh_token':r['refresh_token']},open('../broker-file-sync/auth_session.json','w'))"

# 2. edit api_baseline.py -> `run_dir` suffix ("-post-upgrade"), then:
python3 docs/perf-baseline/api_baseline.py
```

It runs **sequentially** (one request at a time, 3 reps, 2–6 s pause) — **not** a
load test — so it's safe-ish to run against the small instance, but the heavy
`range` / `bars` queries can still stall for a live user. Prefer off-market hours.

## Comparing pre vs post

Diff the two `summary.md` tables. What matters:

| Metric | Pre-upgrade (2026-09-10) | Target post |
|---|---|---|
| `n_timeout` (any endpoint) | `lmv.range_60` 1/3, `lmv.range_90` 2/3, `inception.bars_all_360d` 1/3 | **0 everywhere** |
| `lmv.range_60` median total | ~6.1 s (when it succeeds) | **< 1 s**, then **< 300 ms** after the wide-table read model |
| `inception.bars_all_1yr` median | ~7.9 s, 25 MB | **< 1.5 s** |
| Small endpoints (auth/settings/latest) | 70–410 ms | unchanged |
| `lmv.range_*` payload size | 6 MB (20d) / 10 MB (60d) | shrinks with the wide table + `metrics=` filter |

## Notes / caveats for this baseline

- **Concurrency = 1.** The production failures happen under *concurrent* load
  (many LMV clients + strategy toggles). A single sequential caller already hit
  50 % timeouts on the heavy range queries, so the real-world picture is worse.
  A follow-up concurrent load test (k6 / Locust) is listed in the plan.
- **Captured during market hours**, ~12:20–12:35 IST 2026-09-10, so the DB was
  under real user load — representative, but not perfectly repeatable.
- `auth.login` needs a password we don't hold, so `auth/refresh` is measured as
  the auth-entry proxy. Refresh tokens are single-use → its rep 2/3 return 401.
- `inception/{availability,bars}` cap the date range at 366 days (400 beyond).
- Connection-pool hardening (`e4713a8`) was already deployed at capture time;
  the schema / query / cache changes were **not**.
