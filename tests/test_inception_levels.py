"""The chatbot's ``inception_levels`` view must give the same numbers as the app's Inception screens.

Issue #121: asked for stocks near QT, the model wrote its own "QT" (highest close of every day in
the previous quarter) and answered 1215.3 for ZYDUSLIFE where the Inception screen shows 1165.1.
The fix is that the model reads every Inception Group A code from this view. These tests pin the
view to the app's rules:

* ``reference_group_a`` below is an independent Python statement of those rules (a port of the
  desktop's ``services/inception_formula_engine.compute_group_a``);
* the parity tests run the real view in a real Postgres over generated bars and compare every
  column on every row. They need a Postgres they may create a scratch database in
  (``CHAT_TEST_PG_DSN``, default: the local server's ``postgres`` database) and are skipped when
  there is none.
"""

from __future__ import annotations

import os
import random
import re
import uuid
from collections import defaultdict
from datetime import date, timedelta

import pytest

from app.services.chat import inception_levels as il
from app.services.chat.knowledge import load_knowledge
from app.services.chat.sql_guard import validate_and_prepare

# ── the app's rules, stated independently of the SQL ─────────────────────────


def _quarter(d: date):
    return (d.year, (d.month - 1) // 3)


def _half(d: date):
    return (d.year, 0 if d.month <= 6 else 1)


def _year(d: date):
    return (d.year,)


def _fy(d: date):
    return (d.year if d.month >= 4 else d.year - 1,)


def _prev(key: tuple, per_year: int) -> tuple:
    if len(key) == 1:
        return (key[0] - 1,)
    y, n = key
    return (y, n - 1) if n > 0 else (y - 1, per_year - 1)


_PERIODS = (("Q", _quarter, 4, True), ("HY", _half, 2, True), ("Y", _year, 1, True), ("FY", _fy, 1, False))


def _pct(base, cur):
    return None if base is None or base == 0 or cur is None else (cur - base) / base * 100.0


def reference_group_a(bars: list[dict]) -> dict[date, dict]:
    """{trade_date: {code: value}} for ascending *bars* of one stock."""
    out: dict[date, dict] = {}
    for idx, bar in enumerate(bars):
        d = bar["trade_date"]
        seen = bars[: idx + 1]
        prev = bars[idx - 1] if idx else None
        row = {
            "P.OPEN": prev and prev["open"], "P.HIGH": prev and prev["high"], "P.LOW": prev and prev["low"],
            "P.CLOSE": prev and prev["close"], "P.QTY": prev and prev["volume"],
            "P.OI": prev and prev["open_interest"],
        }
        row["% CHG PDC AND OPEN"] = _pct(prev and prev["close"], bar["open"])
        row["DAY % CHANGE"] = _pct(prev and prev["close"], bar["close"])
        monday = d - timedelta(days=d.weekday())
        before_week = [b for b in seen if b["trade_date"] < monday]
        row["% CHG PWC AND OPEN"] = _pct(before_week[-1]["close"] if before_week else None, bar["open"])
        window = [b for b in seen if b["trade_date"] >= d - timedelta(days=364)]
        row["52WH"], row["52WL"] = max(b["high"] for b in window), min(b["low"] for b in window)
        row["ATH"], row["ATL"] = max(b["high"] for b in seen), min(b["low"] for b in seen)
        for prefix, keyf, per_year, has_top in _PERIODS:
            groups: dict[tuple, list[dict]] = defaultdict(list)
            for b in seen:
                groups[keyf(b["trade_date"])].append(b)
            cur_key = keyf(d)
            prev_key = _prev(cur_key, per_year)
            cur, prv, prv2 = groups[cur_key], groups.get(prev_key), groups.get(_prev(prev_key, per_year))
            row[f"C{prefix}H"], row[f"C{prefix}L"] = max(b["high"] for b in cur), min(b["low"] for b in cur)
            row[f"C{prefix}O"] = cur[0]["open"]
            row[f"P{prefix}H"] = max(b["high"] for b in prv) if prv else None
            row[f"P{prefix}L"] = min(b["low"] for b in prv) if prv else None
            row[f"P{prefix}O"] = prv[0]["open"] if prv else None
            row[f"P{prefix}C"] = prv[-1]["close"] if prv else None
            if has_top:
                closes = [g[-1]["close"] for g in (prv, prv2) if g]
                row[f"{prefix}T"] = max(closes) if closes else None
                row[f"{prefix}B"] = min(closes) if closes else None
        out[d] = row
    return out


def test_reference_reproduces_the_issue_121_case():
    """Q2 closes at 1111.4, Q3 closes at 1165.1 after trading as high as 1215.3 mid-quarter: QT is
    the higher quarter-END close (1165.1), not the highest close seen in the quarter (1215.3)."""
    def bar(d, close, high=None):
        return {"trade_date": d, "open": close, "high": high or close, "low": close - 1, "close": close,
                "volume": 1, "open_interest": 1}

    bars = [bar(date(2026, 6, 29), 1100.0), bar(date(2026, 6, 30), 1111.4),
            bar(date(2026, 8, 14), 1215.3, high=1230.0), bar(date(2026, 9, 30), 1165.1),
            bar(date(2026, 11, 9), 1216.9)]
    row = reference_group_a(bars)[date(2026, 11, 9)]
    assert (row["QT"], row["QB"], row["PQC"], row["PQH"]) == (1165.1, 1111.4, 1165.1, 1230.0)


# ── the view's shape, the prompt and the knowledge base (no database needed) ──


def test_every_group_a_code_has_exactly_one_column_in_the_view():
    codes = [c for c, _, _ in il.CODE_COLUMNS]
    cols = [c for _, c, _ in il.CODE_COLUMNS]
    assert len(codes) == len(set(codes)) == 47 and len(cols) == len(set(cols))
    ddl = il.view_ddl("tenant_x")
    assert ddl.startswith("CREATE TEMP VIEW inception_levels AS")
    for col in il.ALL_COLUMNS:
        assert re.search(rf"\b{col}\b", ddl), f"column {col} missing from the view"
    assert "UserSetting" not in ddl          # the 52-week window is fixed, never a per-user setting
    assert il.WEEK_WINDOW_DAYS == 364


def test_glossary_lists_every_code_with_its_column_and_tells_the_model_not_to_compute():
    kb = load_knowledge()
    body = kb.docs["glossary.inception-group-a"].body
    for code, col, _ in il.CODE_COLUMNS:
        assert f"| {code} | `{col}` |" in body, f"glossary row for {code} -> {col} is missing"
    assert "NEVER calculate" in body
    assert kb.docs["data-source.inception-levels"].load == "always"
    assert kb.docs["examples.inception-levels"].load == "always"


def test_planner_prompt_routes_inception_codes_to_the_view():
    from app.services.chat.prompts import build_planner_system
    from app.services.chat.sql_runner import DataContext

    ctx = DataContext(eod_latest=None, lmv_latest=None, lmv_metric_keys=[], signals_latest=None, futures_latest=None)
    prompt = build_planner_system(ctx, date(2026, 11, 12), "any stocks that are within 1 percentage of qt")
    assert "SELECT the matching inception_levels column" in prompt
    assert "FROM lmv_live l JOIN inception_levels v USING (symbol)" in prompt     # the live worked example
    assert "| QT | `qt` |" in prompt
    # The price side of a level comparison: live grid when running, else the view's own close.
    # (Seen on the deployed bot: it joined eod_daily's latest close, on another date, to the level.)
    assert "Never take the price for such a comparison from eod_daily or lmv_daily" in prompt


def test_guard_accepts_the_view_and_the_live_join():
    sql = """SELECT l.symbol, v.qt FROM lmv_live l JOIN inception_levels v USING (symbol)
             WHERE v.trade_date = (SELECT max(trade_date) FROM inception_levels)"""
    assert "inception_levels" in validate_and_prepare(sql, max_rows=50).sql


# ── parity with the reference, in a real Postgres ────────────────────────────

_SCHEMA = "t_levels"


def _generated_bars(rng: random.Random, start: date, end: date, *, gaps: bool) -> list[dict]:
    bars, price, d = [], rng.uniform(50, 5000), start
    quiet_from = start + timedelta(days=rng.randint(200, 500)) if gaps else None
    while d <= end:
        skip = d.weekday() >= 5 or rng.random() < 0.03
        # A long silence (a stock that stopped trading for months): previous periods go blank.
        if quiet_from and quiet_from <= d < quiet_from + timedelta(days=260):
            skip = True
        if not skip:
            o = round(price * rng.uniform(0.97, 1.03), 2)
            c = round(o * rng.uniform(0.96, 1.04), 2)
            bars.append({"trade_date": d, "open": o, "high": round(max(o, c) * rng.uniform(1.0, 1.02), 2),
                         "low": round(min(o, c) * rng.uniform(0.98, 1.0), 2), "close": c,
                         "volume": rng.randint(0, 10**6), "open_interest": rng.choice([None, rng.randint(1, 10**6)])})
            price = c
        d += timedelta(days=1)
    return bars


@pytest.fixture(scope="module")
def pg():
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("CHAT_TEST_PG_DSN", "dbname=postgres")
    name = f"chat_levels_test_{uuid.uuid4().hex[:10]}"
    try:
        admin = psycopg.connect(dsn, autocommit=True, connect_timeout=3)
        admin.execute(f'CREATE DATABASE "{name}"')
    except psycopg.Error as exc:
        pytest.skip(f"no Postgres available for the parity test ({str(exc).splitlines()[0]})")
    try:
        info = admin.info
        conn = psycopg.connect(host=info.host, port=info.port, user=info.user, password=info.password or None,
                               dbname=name, autocommit=True)
        conn.execute(f'''
            CREATE TABLE public."Instrument" (id bigserial PRIMARY KEY, symbol varchar(50) UNIQUE NOT NULL,
                                              underlying_symbol varchar(50));
            CREATE TABLE public."EodBar" (instrument_id bigint NOT NULL, trade_date date NOT NULL,
                open numeric(14,4) NOT NULL, high numeric(14,4) NOT NULL, low numeric(14,4) NOT NULL,
                close numeric(14,4) NOT NULL, volume bigint NOT NULL, open_interest bigint,
                PRIMARY KEY (instrument_id, trade_date));
            CREATE SCHEMA {_SCHEMA};
            CREATE TABLE {_SCHEMA}."Stock" (symbol varchar(50) UNIQUE NOT NULL, is_active boolean NOT NULL DEFAULT true);
            INSERT INTO {_SCHEMA}."Stock" (symbol) VALUES ('M&M'), ('AAA');
        ''')
        rng = random.Random(121)
        data: dict[str, list[dict]] = {
            "AAA": _generated_bars(rng, date(2022, 11, 14), date(2026, 11, 9), gaps=False),
            "BBB": _generated_bars(rng, date(2024, 2, 20), date(2026, 11, 9), gaps=True),
            "M_M": _generated_bars(rng, date(2026, 10, 5), date(2026, 11, 9), gaps=False),   # a new listing
        }
        for sym, bars in data.items():
            for suffix in ("_I", "_II"):       # the next-month series must never leak into the view
                iid = conn.execute('INSERT INTO public."Instrument" (symbol, underlying_symbol) VALUES (%s, %s) '
                                   "RETURNING id", (sym + suffix, sym)).fetchone()[0]
                with conn.cursor() as cur:
                    cur.executemany(
                        'INSERT INTO public."EodBar" VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                        [(iid, b["trade_date"], b["open"] + (suffix == "_II") * 7, b["high"] + (suffix == "_II") * 7,
                          b["low"], b["close"], b["volume"], b["open_interest"]) for b in bars])
        conn.execute("ANALYZE")
        conn.execute(il.view_ddl(_SCHEMA))
        yield conn, data
        conn.close()
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-4 + 1e-9 * abs(float(b))


def test_view_matches_the_app_rules_on_every_row_and_column(pg):
    conn, data = pg
    cols = [c for _, c, _ in il.CODE_COLUMNS]
    spelled = {"AAA": "AAA", "BBB": "BBB", "M_M": "M&M"}      # the user's own spelling wins
    checked = 0
    for sym, bars in data.items():
        ref = reference_group_a(bars)
        rows = conn.execute(f"SELECT trade_date, open, close, {', '.join(cols)} FROM inception_levels "
                            "WHERE symbol = %s ORDER BY trade_date", (spelled[sym],)).fetchall()
        assert [r[0] for r in rows] == [b["trade_date"] for b in bars]      # one row per bar, near-month only
        for r, bar in zip(rows, bars):
            assert _same(r[1], bar["open"]) and _same(r[2], bar["close"])
            for (code, col, _), got in zip(il.CODE_COLUMNS, r[3:]):
                assert _same(got, ref[r[0]][code]), f"{sym} {r[0]} {code}: view={got} app={ref[r[0]][code]}"
                checked += 1
    assert checked > 70_000


def test_latest_date_query_shape_used_in_the_examples_runs(pg):
    conn, data = pg
    rows = conn.execute("""SELECT symbol, qt, wh52 FROM inception_levels
                           WHERE trade_date = (SELECT max(trade_date) FROM inception_levels) ORDER BY symbol""").fetchall()
    assert [r[0] for r in rows] == ["AAA", "BBB", "M&M"]
    assert rows[2][1] is None            # listed this quarter: no completed quarter yet, QT is blank
