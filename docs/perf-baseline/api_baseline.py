#!/usr/bin/env python3
"""Pre-upgrade API performance baseline.

Hits every READ endpoint (plus login, the entry point) sequentially, 3 reps each,
with a pause between calls (gentle - not a load test). Captures request, response
body, HTTP status, payload size, and curl's precise timing breakdown
(DNS / connect / TLS / TTFB / total).

Output dir: docs/perf-baseline/<date>-pre-upgrade/ in broker-sync-api.
Re-run the same script post-upgrade (new dir) and diff summary.md.
"""
import json, os, subprocess, sys, time, datetime, statistics

BASE = "http://13.206.231.240:8000"
TOK_FILE = "/Users/akash/Projects/BrokerFileSync/broker-file-sync/auth_session.json"
OUT_ROOT = "/Users/akash/Projects/BrokerFileSync/broker-sync-api/docs/perf-baseline"
REPS = 3
PAUSE_S = 2.0            # between every request
HEAVY_PAUSE_S = 6.0      # after a heavy request, give the DB room
MAX_TIME = 200

tok = json.load(open(TOK_FILE))["access_token"]
today = datetime.date.today().isoformat()
run_dir = os.path.join(OUT_ROOT, f"{today}-pre-upgrade")
bodies_dir = os.path.join(run_dir, "responses")
os.makedirs(bodies_dir, exist_ok=True)

# ---- date helpers ----
D = datetime.date.today()
def iso(d): return d.isoformat()
last_wk   = iso(D - datetime.timedelta(days=7))
last_mo   = iso(D - datetime.timedelta(days=32))
last_qtr  = iso(D - datetime.timedelta(days=95))
last_yr   = iso(D - datetime.timedelta(days=360))   # inception caps range at 366d
two_yr    = iso(D - datetime.timedelta(days=730))
today_iso = iso(D)
snap_date = "2026-09-09"   # known populated LMV/historic trade date

# ---- endpoint catalogue: (name, method, path, {params}, needs_auth, heavy, [json_body]) ----
EP = [
    # --- auth / entry ---
    ("auth.login",                "POST", "/auth/login", {}, False, False),
    ("auth.me",                   "GET",  "/auth/me", {}, True, False),
    ("auth.me.theme",             "GET",  "/auth/me/theme", {}, True, False),
    ("health",                    "GET",  "/health", {}, False, False),

    # --- small reference data ---
    ("data.stocks",               "GET",  "/data/stocks", {}, True, False),
    ("data.metrics",              "GET",  "/data/metrics", {}, True, False),
    ("holidays",                  "GET",  "/holidays", {}, True, False),

    # --- settings (the ones the client actually reads) ---
    ("settings.scheduler_triggers",       "GET", "/settings/scheduler_triggers", {}, True, False),
    ("settings.main_column_order",         "GET", "/settings/main_column_order", {}, True, False),
    ("settings.custom_strategy_categories","GET", "/settings/custom_strategy_categories", {}, True, False),
    ("settings.frozen_columns",            "GET", "/settings/frozen_columns", {}, True, False),
    ("settings.column_categories",         "GET", "/settings/column_categories", {}, True, False),
    ("settings.conditional_formatting",    "GET", "/settings/conditional_formatting", {}, True, False),
    ("settings.alert_window",              "GET", "/settings/alert_window", {}, True, False),

    # --- strategies / formulas / signals ---
    ("strategies.list",           "GET",  "/strategies", {}, True, False),
    ("formula_variables.list",    "GET",  "/formula-variables", {}, True, False),
    ("strategy_signals.p1",       "GET",  "/strategy-signals", {"page": 1, "page_size": 25}, True, False),
    ("strategy_signals.p1_big",   "GET",  "/strategy-signals", {"page": 1, "page_size": 100}, True, True),
    ("strategy_signals.filtered", "GET",  "/strategy-signals", {"direction": "BUY", "status": "open", "page_size": 100}, True, False),

    # --- opening range ---
    ("opening_range.latest",      "GET",  "/opening-range/latest", {}, True, False),
    ("opening_range.snapshot",    "GET",  "/opening-range/snapshot", {"date": today_iso}, True, False),
    ("opening_range.availability", "GET", "/opening-range/availability", {"from": last_qtr, "to": today_iso}, True, False),

    # --- LMV snapshot (tenant, EAV, the incident source) ---
    ("lmv.latest",                "GET",  "/lmv-snapshot/latest", {}, True, False),
    ("lmv.snapshot_date",         "GET",  "/lmv-snapshot/snapshot", {"date": snap_date}, True, False),
    ("lmv.range_20",              "GET",  "/lmv-snapshot/range", {"days": 20}, True, True),
    ("lmv.range_60",              "GET",  "/lmv-snapshot/range", {"days": 60}, True, True),
    ("lmv.range_90",              "GET",  "/lmv-snapshot/range", {"days": 90}, True, True),
    ("lmv.availability_1y",       "GET",  "/lmv-snapshot/availability", {"from": last_yr, "to": today_iso}, True, False),

    # --- historic snapshot (tenant, EAV) ---
    ("historic.latest",           "GET",  "/historic/latest", {}, True, False),
    ("historic.snapshot_date",    "GET",  "/historic/snapshot", {"date": snap_date}, True, False),
    ("historic.range_20",         "GET",  "/historic/range", {"days": 20}, True, True),
    ("historic.range_60",         "GET",  "/historic/range", {"days": 60}, True, True),
    ("historic.range_120",        "GET",  "/historic/range", {"days": 120}, True, True),
    ("historic.timeseries_1y",    "GET",  "/historic/timeseries", {"symbol": "RELIANCE", "metric": "Close", "from": last_yr, "to": today_iso}, True, False),
    ("historic.availability_1y",  "GET",  "/historic/availability", {"from": last_yr, "to": today_iso}, True, False),

    # --- inception (central, EodBar ~1M rows) ---
    ("inception.instruments",     "GET",  "/inception/instruments", {}, True, False),
    ("inception.availability_1y", "GET",  "/inception/availability", {"from": last_yr, "to": today_iso}, True, False),
    ("inception.strategies",      "GET",  "/inception/strategies", {}, True, False),
    ("inception.formula_variables","GET", "/inception/formula-variables", {}, True, False),
    ("inception.bars_1sym_1mo",   "GET",  "/inception/bars", {"from": last_mo, "to": today_iso, "symbols": "RELIANCE_I"}, True, False),
    ("inception.bars_5sym_1qtr",  "GET",  "/inception/bars", {"from": last_qtr, "to": today_iso, "symbols": ["RELIANCE_I", "TCS_I", "INFY_I", "HDFCBANK_I", "ICICIBANK_I"]}, True, True),
    ("inception.bars_all_1mo",    "GET",  "/inception/bars", {"from": last_mo, "to": today_iso}, True, True),
    ("inception.bars_all_1yr",    "GET",  "/inception/bars", {"from": last_yr, "to": today_iso}, True, True),
]

def build_url(path, params):
    if not params:
        return BASE + path
    parts = []
    for k, v in params.items():
        if isinstance(v, list):
            for item in v:
                parts.append(f"{k}={item}")
        else:
            parts.append(f"{k}={v}")
    return f"{BASE}{path}?{'&'.join(parts)}"

def curl(name, method, url, needs_auth, body_path, login_body=None):
    fmt = "%{http_code}|%{size_download}|%{time_namelookup}|%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}"
    cmd = ["curl", "-s", "-o", body_path, "-w", fmt, "--max-time", str(MAX_TIME),
           "-X", method, url]
    if needs_auth:
        cmd += ["-H", f"Authorization: Bearer {tok}"]
    if login_body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(login_body)]
    t0 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.perf_counter() - t0
    w = p.stdout.strip()
    try:
        code, size, dns, conn, tls, ttfb, total = w.split("|")
        return {
            "http_status": int(code), "bytes": int(size),
            "t_dns_ms": round(float(dns) * 1000, 1),
            "t_connect_ms": round(float(conn) * 1000, 1),
            "t_tls_ms": round(float(tls) * 1000, 1),
            "t_ttfb_ms": round(float(ttfb) * 1000, 1),
            "t_total_ms": round(float(total) * 1000, 1),
            "wall_ms": round(wall * 1000, 1),
            "curl_err": p.stderr.strip() or None,
        }
    except ValueError:
        return {"http_status": None, "bytes": 0, "t_total_ms": round(wall * 1000, 1),
                "wall_ms": round(wall * 1000, 1), "curl_err": (p.stderr.strip() or w or "parse-failed")}

# login body from stored refresh token flow already gave us a token; do a real
# login only if creds are available. We don't have the password, so measure the
# refresh endpoint's cost as the "auth entry" proxy and mark login as skipped.
REFRESH_TOKEN = json.load(open(TOK_FILE)).get("refresh_token")

results = []
print(f"Writing to {run_dir}\n")
for name, method, path, params, needs_auth, heavy in EP:
    url = build_url(path, params)
    reps_data = []
    if name == "auth.login":
        # can't do a true login without the password; measure /auth/refresh instead
        name = "auth.refresh"; method = "POST"; url = BASE + "/auth/refresh"
        login_body = {"refresh_token": REFRESH_TOKEN}
        needs_auth = False
    else:
        login_body = None
    for i in range(1, REPS + 1):
        bp = os.path.join(bodies_dir, f"{name}.rep{i}.json")
        m = curl(name, method, url, needs_auth, bp, login_body)
        m["rep"] = i
        reps_data.append(m)
        tag = "HEAVY" if heavy else ""
        print(f"  [{i}/{REPS}] {name:32s} {m.get('http_status')} "
              f"{m['bytes']:>10} B  ttfb={m.get('t_ttfb_ms','?')}ms  total={m['t_total_ms']}ms {tag}")
        time.sleep(HEAVY_PAUSE_S if heavy else PAUSE_S)
    totals = [r["t_total_ms"] for r in reps_data if r.get("http_status")]
    ttfbs = [r.get("t_ttfb_ms") for r in reps_data if r.get("t_ttfb_ms")]
    results.append({
        "name": name, "method": method, "url": url, "params": params,
        "heavy": heavy,
        "http_status": reps_data[-1].get("http_status"),
        "bytes": reps_data[-1].get("bytes"),
        "reps": reps_data,
        "median_total_ms": round(statistics.median(totals), 1) if totals else None,
        "min_total_ms": min(totals) if totals else None,
        "max_total_ms": max(totals) if totals else None,
        "median_ttfb_ms": round(statistics.median(ttfbs), 1) if ttfbs else None,
    })

meta = {
    "captured_at": datetime.datetime.now().isoformat(),
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "base_url": BASE,
    "note": "PRE-UPGRADE baseline. RDS db.t3.micro (1GB), single AZ ap-south-1b, "
            "connection-pool hardening (e4713a8) deployed, no query/schema changes yet. "
            "Captured during market hours; sequential (not concurrent) probe, 3 reps, "
            "pause between calls.",
    "reps_per_endpoint": REPS,
}
json.dump({"meta": meta, "results": results}, open(os.path.join(run_dir, "results.json"), "w"), indent=2)

# ---- summary.md ----
def fmt_bytes(n):
    if n is None: return "-"
    for u in ["B", "KB", "MB"]:
        if n < 1024: return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} GB"

lines = [
    f"# API performance baseline - PRE-UPGRADE ({today})",
    "",
    f"- Captured: {meta['captured_at']} (IST), {meta['captured_at_utc']}",
    f"- Backend: {BASE}  |  RDS `db.t3.micro` (1 GB), `ap-south-1b`, Single-AZ",
    f"- Pool hardening (`e4713a8`) deployed. No query/schema/cache changes yet.",
    f"- Method: sequential probe, {REPS} reps/endpoint, pause between calls (not a load test).",
    "",
    "Re-run `scratchpad/api_baseline.py` post-upgrade into a new `*-post-upgrade/` dir and diff this table.",
    "",
    "| Endpoint | Params | Status | Payload | TTFB (median) | Total ms (median) | min | max | Heavy |",
    "|---|---|---|---|---|---|---|---|---|",
]
for r in results:
    p = r["params"]
    ps = ", ".join(f"{k}={v}" for k, v in p.items()) if p else "-"
    if len(ps) > 46: ps = ps[:43] + "..."
    lines.append(
        f"| `{r['name']}` | {ps} | {r['http_status']} | {fmt_bytes(r['bytes'])} | "
        f"{r['median_ttfb_ms'] or '-'} | **{r['median_total_ms'] or '-'}** | "
        f"{r['min_total_ms'] or '-'} | {r['max_total_ms'] or '-'} | {'yes' if r['heavy'] else ''} |"
    )
lines += ["", "## Heavy endpoints (the ones that matter for the upgrade)", ""]
for r in sorted([x for x in results if x["heavy"]], key=lambda x: -(x["median_total_ms"] or 0)):
    lines.append(f"- **`{r['name']}`** ({', '.join(f'{k}={v}' for k,v in r['params'].items())}) "
                 f"-> {r['http_status']}, {fmt_bytes(r['bytes'])}, median {r['median_total_ms']} ms "
                 f"(range {r['min_total_ms']}-{r['max_total_ms']} ms)")
open(os.path.join(run_dir, "summary.md"), "w").write("\n".join(lines) + "\n")
print(f"\nDone. {len(results)} endpoints. See:\n  {run_dir}/summary.md\n  {run_dir}/results.json\n  {run_dir}/responses/")
