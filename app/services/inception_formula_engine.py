"""Computes every Group A / Group B column (see services.inception_columns)
for one instrument's full EodBar history, in a single forward pass.

Called by services.inception_service.recompute_instrument (persists the
result into InceptionDerivedValue) and by compile-time formula validation
(see services.inception_strategy_engine's dummy-float note).

── Period bookkeeping (Group A) ─────────────────────────────────────────────
"Current period" columns (CQO/CQH/CQL, CHYO/…, CYO/…, CFYO/…) are a running
accumulator that resets whenever the bar's period key changes; "previous
period" columns (PQO/PQH/PQL/PQC, …) and the last-2-periods top/bottom
columns (QT/QB, HYT/HYB, YT/YB) are looked up from a pre-computed table of
every period's full aggregate (built in one pass over the whole history
before the per-day walk) — a period's own aggregate can't be "previous" for
that period's own days but *is* immediately available as "previous" the
moment the next period starts, which is exactly the spec's "resets on the
first trading day of the period" behavior.

Calendar rules: week = ISO week (Monday..Sunday, matching the existing
services/formula_engine.py convention in the desktop client); quarter =
calendar quarter (Jan-Mar/Apr-Jun/Jul-Sep/Oct-Dec); half-year = Jan-Jun/
Jul-Dec; financial year = Apr 1 - Mar 31 (Indian FY, keyed by its starting
calendar year).

52WH/52WL use a rolling 364-calendar-day window (the standard "52-week
high/low" definition), not a trading-day count — a deque evicts bars older
than the window on each step, O(n) amortized over the whole history.

── Group B: gap-area tracking ───────────────────────────────────────────────
The spec's prose ("previous day close and current day open on the recent
most day where the %chg... is more than the threshold, and the price after
that gap-up hasn't closed below the previous day close") leaves two things
for an implementer to decide, resolved here as follows — flagged clearly so
this can be corrected if it doesn't match intent:

1. An "area" is a PRICE RANGE, not a single number: the two bounds are
   min/max(previous-period-close, current-period-open). Stored as two
   metrics, "<CODE> LOW"/"<CODE> HIGH", plus "<CODE> DATE" (when the gap
   opened) — see inception_columns.gap_metric_names.
2. "Closed back through" (what flips UF -> FD) means a subsequent bar's
   close crosses back to the opposite side of the PREVIOUS close (the
   pre-gap reference price): a gap-up area fills when close <= that
   previous close; a gap-down area fills the mirror way (close >= it).

State per instrument/direction/period-type: two FIFOs (unfilled, filled),
each holding at most 3 areas, most-recent-first (rank 1 = index 0). A new
day/week can (a) open a new gap (pushed onto the front of `unfilled`,
trimming to 3) if |pct change| exceeds the tenant's configured threshold,
independently of whether it also (b) fills one or more already-open areas of
the SAME direction (each filled area is removed from `unfilled` and pushed
onto the front of `filled`, trimming to 3) — both can happen on the same
day for different pre-existing areas.

Weekly gap columns run the identical state machine one level up: "day" bars
become week bars (open = first trading day's open, close = last trading
day's close) synthesized from the same daily history, and the state machine
advances once per completed week instead of once per day.
"""

import bisect
from collections import deque
from datetime import date, timedelta

_FIFO_CAP = 3
_WEEK_WINDOW_DAYS = 364


# ── period key helpers ───────────────────────────────────────────────────────

def _quarter_key(d: date) -> tuple[int, int]:
    return (d.year, (d.month - 1) // 3 + 1)


def _prev_quarter_key(key: tuple[int, int]) -> tuple[int, int]:
    y, q = key
    return (y, q - 1) if q > 1 else (y - 1, 4)


def _half_year_key(d: date) -> tuple[int, int]:
    return (d.year, 1 if d.month <= 6 else 2)


def _prev_half_year_key(key: tuple[int, int]) -> tuple[int, int]:
    y, h = key
    return (y, 1) if h == 2 else (y - 1, 2)


def _year_key(d: date) -> int:
    return d.year


def _prev_year_key(key: int) -> int:
    return key - 1


def _fy_key(d: date) -> int:
    """Indian financial year (Apr 1 - Mar 31), labeled by its starting
    calendar year — e.g. 2025-06-01 and 2026-03-31 are both FY 2025."""
    return d.year if d.month >= 4 else d.year - 1


def _prev_fy_key(key: int) -> int:
    return key - 1


def _week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return (iso[0], iso[1])


# Unlike quarter/half_year/year/fy, ISO week numbers don't arithmetic cleanly
# across year boundaries (52/53-week years) — "previous week" is resolved by
# sorted-key lookup instead (see _prev_by_table/_prev2_by_table and
# compute_group_b's bisect), so there's no _prev_week_key function here.

_PERIOD_TYPES = ("quarter", "half_year", "year", "fy", "week")
_KEY_FUNCS = {
    "quarter": _quarter_key, "half_year": _half_year_key,
    "year": _year_key, "fy": _fy_key, "week": _week_key,
}
_PREV_KEY_FUNCS = {
    "quarter": _prev_quarter_key, "half_year": _prev_half_year_key,
    "year": _prev_year_key, "fy": _prev_fy_key,
}


def _period_start(period_type: str, key) -> date:
    """Inverse of _KEY_FUNCS — the first calendar date of the period *key*
    identifies. Used only by required_lookback_start below (the forward
    pass above never needs this — it just tracks accumulators as it walks
    bars in order)."""
    if period_type == "quarter":
        y, q = key
        return date(y, (q - 1) * 3 + 1, 1)
    if period_type == "half_year":
        y, h = key
        return date(y, 1 if h == 1 else 7, 1)
    if period_type == "year":
        return date(key, 1, 1)
    if period_type == "fy":
        return date(key, 4, 1)
    if period_type == "week":
        y, w = key
        return date.fromisocalendar(y, w, 1)
    raise ValueError(f"unknown period_type {period_type!r}")


# ── HMV range gate: "does the user's selected date range cover what this
# column's formula needs?" ────────────────────────────────────────────────
#
# Paired with inception_columns.DISPLAY_FORMULA — same day/week/quarter/
# half-year/year/FY period vocabulary that formula describes, so a code's
# lookback requirement is derived from the SAME period-key machinery the
# forward pass above uses (not a second, hand-maintained mapping that could
# drift out of sync).

_FIXED_LOOKBACK_DAYS = {
    "P.OPEN": 7, "P.HIGH": 7, "P.LOW": 7, "P.CLOSE": 7, "P.QTY": 7, "P.OI": 7,
    "% CHG PDC AND OPEN": 7, "DAY % CHANGE": 7,
    "% CHG PWC AND OPEN": 14,
    "52WH": _WEEK_WINDOW_DAYS, "52WL": _WEEK_WINDOW_DAYS,
}

_CURRENT_PERIOD_CODES = {
    "CQH": "quarter", "CQO": "quarter", "CQL": "quarter",
    "CHYH": "half_year", "CHYO": "half_year", "CHYL": "half_year",
    "CYH": "year", "CYO": "year", "CYL": "year",
    "CFYH": "fy", "CFYO": "fy", "CFYL": "fy",
}

# 1 period back — PQH/PQO/PQL/PQC etc. only ever look at the immediately
# preceding quarter/half-year/year/FY.
_PREV_PERIOD_CODES = {
    "PQH": "quarter", "PQO": "quarter", "PQL": "quarter", "PQC": "quarter",
    "PHYH": "half_year", "PHYO": "half_year", "PHYL": "half_year", "PHYC": "half_year",
    "PYH": "year", "PYO": "year", "PYL": "year", "PYC": "year",
    "PFYH": "fy", "PFYO": "fy", "PFYL": "fy", "PFYC": "fy",
}

# 2 periods back — QT/QB etc. are max/min of PQC over the last 2 periods
# (see inception_columns.GROUP_A's own descriptions), so they need the
# period *before* the previous one too.
_PREV2_PERIOD_CODES = {
    "QT": "quarter", "QB": "quarter",
    "HYT": "half_year", "HYB": "half_year",
    "YT": "year", "YB": "year",
}


def required_lookback_start(code: str, as_of_date: date, first_traded_date: date | None = None) -> date | None:
    """The earliest `date_from` that makes *code* non-blank when HMV is
    evaluated as of *as_of_date* — see services.inception_service._build_rows'
    range gate, the only caller. None means "no requirement" (raw fields,
    unrecognized codes — never gated) OR, for ATH/ATL specifically, "can't
    say" when *first_traded_date* isn't supplied (permissive fallback:
    ungated rather than wrongly always-blank).

    Group B (gap) codes are deliberately NOT handled here — which historical
    gap (if any) is currently showing is data-dependent, not a fixed window,
    so the caller checks each gap's own DATE value directly instead.
    """
    if code in _FIXED_LOOKBACK_DAYS:
        return as_of_date - timedelta(days=_FIXED_LOOKBACK_DAYS[code])
    if code in ("ATH", "ATL"):
        return first_traded_date
    if code in _CURRENT_PERIOD_CODES:
        pt = _CURRENT_PERIOD_CODES[code]
        return _period_start(pt, _KEY_FUNCS[pt](as_of_date))
    if code in _PREV_PERIOD_CODES:
        pt = _PREV_PERIOD_CODES[code]
        prev_key = _PREV_KEY_FUNCS[pt](_KEY_FUNCS[pt](as_of_date))
        return _period_start(pt, prev_key)
    if code in _PREV2_PERIOD_CODES:
        pt = _PREV2_PERIOD_CODES[code]
        two_back_key = _PREV_KEY_FUNCS[pt](_PREV_KEY_FUNCS[pt](_KEY_FUNCS[pt](as_of_date)))
        return _period_start(pt, two_back_key)
    return None


class _PeriodAgg:
    __slots__ = ("open", "high", "low", "close", "first_date", "last_date")

    def __init__(self, bar: dict):
        self.open = bar["open"]
        self.high = bar["high"]
        self.low = bar["low"]
        self.close = bar["close"]
        self.first_date = bar["trade_date"]
        self.last_date = bar["trade_date"]

    def extend(self, bar: dict):
        self.high = max(self.high, bar["high"])
        self.low = min(self.low, bar["low"])
        self.close = bar["close"]
        self.last_date = bar["trade_date"]


def _build_period_aggregates(bars: list[dict]) -> dict[str, dict]:
    """One pass: {period_type: {key: _PeriodAgg}} for every period actually
    present in *bars*."""
    tables: dict[str, dict] = {pt: {} for pt in _PERIOD_TYPES}
    for bar in bars:
        d = bar["trade_date"]
        for pt in _PERIOD_TYPES:
            key = _KEY_FUNCS[pt](d)
            table = tables[pt]
            if key not in table:
                table[key] = _PeriodAgg(bar)
            else:
                table[key].extend(bar)
    return tables


def _prev_by_table(period_type: str, key, table: dict, sorted_keys: list | None = None):
    """The previous period's aggregate. For quarter/half_year/year/fy this is
    calendar arithmetic; for week (ISO week numbers don't arithmetic cleanly
    across year boundaries) it's "whichever observed week key sorts
    immediately before *key*", found via bisect against a *sorted_keys* list
    the caller precomputes once per instrument (not per day — this table can
    have thousands of weeks, so re-sorting per lookup would be O(n^2)).
    Weeks are always contiguous in a trading calendar (no multi-week gaps),
    so this is equivalent to true "previous week" for every week that has
    any data at all.
    """
    if period_type == "week":
        pos = bisect.bisect_left(sorted_keys, key)
        return table.get(sorted_keys[pos - 1]) if pos > 0 else None
    prev_key = _PREV_KEY_FUNCS[period_type](key)
    return table.get(prev_key)


def _prev2_by_table(period_type: str, key, table: dict, sorted_keys: list | None = None):
    """The period before the previous one — for QT/QB, HYT/HYB, YT/YB."""
    if period_type == "week":
        pos = bisect.bisect_left(sorted_keys, key)
        return table.get(sorted_keys[pos - 2]) if pos > 1 else None
    prev_key = _PREV_KEY_FUNCS[period_type](key)
    prev2_key = _PREV_KEY_FUNCS[period_type](prev_key)
    return table.get(prev2_key)


_GROUP_A_PREFIX = {"quarter": "Q", "half_year": "HY", "year": "Y", "fy": "FY"}


def compute_group_a(bars: list[dict]) -> dict[date, dict[str, float | date | None]]:
    """bars: ascending-by-trade_date EodBar rows (as dicts with trade_date/
    open/high/low/close/volume/open_interest). Returns {trade_date: {code:
    value}} for every Group A code in inception_columns.GROUP_A.
    """
    if not bars:
        return {}

    period_tables = _build_period_aggregates(bars)
    sorted_week_keys = sorted(period_tables["week"])
    result: dict[date, dict] = {}

    running: dict[str, dict] = {pt: None for pt in ("quarter", "half_year", "year", "fy")}
    window: deque = deque()   # (date, high, low) for the 52-week roll
    ath = None
    atl = None
    prev_bar = None

    for bar in bars:
        d = bar["trade_date"]
        row: dict[str, float | date | None] = {}

        # Previous-day raw values
        row["P.OPEN"] = prev_bar["open"] if prev_bar else None
        row["P.HIGH"] = prev_bar["high"] if prev_bar else None
        row["P.LOW"] = prev_bar["low"] if prev_bar else None
        row["P.CLOSE"] = prev_bar["close"] if prev_bar else None
        row["P.QTY"] = prev_bar["volume"] if prev_bar else None
        row["P.OI"] = prev_bar["open_interest"] if prev_bar else None

        pdc = prev_bar["close"] if prev_bar else None
        row["% CHG PDC AND OPEN"] = _pct(pdc, bar["open"])
        row["DAY % CHANGE"] = _pct(pdc, bar["close"])

        pwc_agg = _prev_by_table("week", _week_key(d), period_tables["week"], sorted_week_keys)
        row["% CHG PWC AND OPEN"] = _pct(pwc_agg.close if pwc_agg else None, bar["open"])

        # All-time / 52-week
        ath = bar["high"] if ath is None else max(ath, bar["high"])
        atl = bar["low"] if atl is None else min(atl, bar["low"])
        row["ATH"] = ath
        row["ATL"] = atl

        window.append((d, bar["high"], bar["low"]))
        cutoff = d - timedelta(days=_WEEK_WINDOW_DAYS)
        while window and window[0][0] < cutoff:
            window.popleft()
        row["52WH"] = max(h for _, h, _ in window)
        row["52WL"] = min(lo for _, _, lo in window)

        # Quarter / half-year / year / financial-year — current + previous + top/bottom
        for pt in ("quarter", "half_year", "year", "fy"):
            key = _KEY_FUNCS[pt](d)
            state = running[pt]
            if state is None or state["key"] != key:
                state = {"key": key, "open": bar["open"], "high": bar["high"], "low": bar["low"]}
                running[pt] = state
            else:
                state["high"] = max(state["high"], bar["high"])
                state["low"] = min(state["low"], bar["low"])

            prefix = _GROUP_A_PREFIX[pt]
            row[f"C{prefix}O"] = state["open"]
            row[f"C{prefix}H"] = state["high"]
            row[f"C{prefix}L"] = state["low"]

            prev_agg = _prev_by_table(pt, key, period_tables[pt])
            row[f"P{prefix}O"] = prev_agg.open if prev_agg else None
            row[f"P{prefix}H"] = prev_agg.high if prev_agg else None
            row[f"P{prefix}L"] = prev_agg.low if prev_agg else None
            row[f"P{prefix}C"] = prev_agg.close if prev_agg else None

            if pt != "fy":  # QT/QB, HYT/HYB, YT/YB — no financial-year equivalent in the spec
                prev2_agg = _prev2_by_table(pt, key, period_tables[pt])
                closes = [a.close for a in (prev_agg, prev2_agg) if a is not None]
                top_code = {"quarter": "QT", "half_year": "HYT", "year": "YT"}[pt]
                bot_code = {"quarter": "QB", "half_year": "HYB", "year": "YB"}[pt]
                row[top_code] = max(closes) if closes else None
                row[bot_code] = min(closes) if closes else None

        result[d] = row
        prev_bar = bar

    return result


def _pct(base: float | None, current: float | None) -> float | None:
    if base is None or current is None or base == 0:
        return None
    return (current - base) / base * 100.0


# ── Group B: gap-area state machine ─────────────────────────────────────────

class _GapArea:
    __slots__ = ("low", "high", "opened_on", "reference_close")

    def __init__(self, low: float, high: float, opened_on: date, reference_close: float):
        self.low = low
        self.high = high
        self.opened_on = opened_on
        self.reference_close = reference_close  # the pre-gap close a fill must cross


def _to_period_bars(bars: list[dict], period_type: str) -> list[dict]:
    """Synthesize one bar per period (open=first bar's open, close=last
    bar's close, high/low = period extremes) — used for the WEEK gap
    columns, which run the same state machine one level up from daily bars.
    """
    out: list[dict] = []
    current_key = None
    current: dict | None = None
    for bar in bars:
        key = _KEY_FUNCS[period_type](bar["trade_date"])
        if key != current_key:
            if current is not None:
                out.append(current)
            current = {
                "trade_date": bar["trade_date"], "open": bar["open"],
                "high": bar["high"], "low": bar["low"], "close": bar["close"],
            }
            current_key = key
        else:
            current["high"] = max(current["high"], bar["high"])
            current["low"] = min(current["low"], bar["low"])
            current["close"] = bar["close"]
    if current is not None:
        out.append(current)
    return out


def _run_gap_state_machine(period_bars: list[dict], threshold_pct: float) -> dict[date, dict[str, dict]]:
    """period_bars: ascending bars (daily, or the week-synthesized bars from
    _to_period_bars). Returns {period_end_date: {"UF_GUP": [...], "FD_GUP":
    [...], "UF_GDN": [...], "FD_GDN": [...]}} — each list holds up to 3
    _GapArea, most-recent-first — as of that bar (i.e. after that bar's
    close is applied).
    """
    fifos = {"UF_GUP": [], "FD_GUP": [], "UF_GDN": [], "FD_GDN": []}
    out: dict[date, dict] = {}
    prev_close = None

    for bar in period_bars:
        d = bar["trade_date"]

        # 1) Does today's close fill any still-open area?
        for direction, unfilled_key, filled_key, fills in (
            ("GUP", "UF_GUP", "FD_GUP", lambda area: bar["close"] <= area.reference_close),
            ("GDN", "UF_GDN", "FD_GDN", lambda area: bar["close"] >= area.reference_close),
        ):
            still_open = []
            for area in fifos[unfilled_key]:
                if fills(area):
                    fifos[filled_key] = ([area] + fifos[filled_key])[:_FIFO_CAP]
                else:
                    still_open.append(area)
            fifos[unfilled_key] = still_open[:_FIFO_CAP]

        # 2) Does today open a new gap?
        if prev_close is not None and prev_close != 0:
            pct = (bar["open"] - prev_close) / prev_close * 100.0
            if pct > threshold_pct:
                area = _GapArea(min(prev_close, bar["open"]), max(prev_close, bar["open"]), d, prev_close)
                fifos["UF_GUP"] = ([area] + fifos["UF_GUP"])[:_FIFO_CAP]
            elif pct < -threshold_pct:
                area = _GapArea(min(prev_close, bar["open"]), max(prev_close, bar["open"]), d, prev_close)
                fifos["UF_GDN"] = ([area] + fifos["UF_GDN"])[:_FIFO_CAP]

        out[d] = {k: list(v) for k, v in fifos.items()}
        prev_close = bar["close"]

    return out


def compute_group_b(bars: list[dict], threshold_pct: float) -> dict[date, dict[str, tuple]]:
    """Returns {trade_date: {gap_code: (low, high, opened_on) | None}} for
    every code in inception_columns.GROUP_B.

    DAY codes reflect state as of that day's own close. WEEK codes only
    change value on the first trading day of a new week (mirrors "% CHG PWC
    AND OPEN"/CWO's own "FIRST DAY OF THE WEEK" update cadence) — every day
    within a week sees the gap state as of the END of the previous
    (fully-completed) week, not the in-progress current week.
    """
    daily_state = _run_gap_state_machine(bars, threshold_pct)
    week_bars = _to_period_bars(bars, "week")
    weekly_state_by_date = _run_gap_state_machine(week_bars, threshold_pct)
    # Reindex by the week's own (year, week_num) key rather than its date —
    # trade_date on a synthesized week bar is that week's FIRST day (see
    # _to_period_bars), so this is exactly "which week is this".
    weekly_state_by_key = {
        _week_key(wb["trade_date"]): weekly_state_by_date[wb["trade_date"]]
        for wb in week_bars
    }
    sorted_week_keys = sorted(weekly_state_by_key)
    empty_fifos = {"UF_GUP": [], "FD_GUP": [], "UF_GDN": [], "FD_GDN": []}

    result: dict[date, dict[str, tuple]] = {}
    for bar in bars:
        d = bar["trade_date"]
        row: dict[str, tuple | None] = {}
        _fill_gap_row(row, "DAY", daily_state[d])

        this_week_key = _week_key(d)
        pos = bisect.bisect_left(sorted_week_keys, this_week_key)
        week_fifos = weekly_state_by_key[sorted_week_keys[pos - 1]] if pos > 0 else empty_fifos
        _fill_gap_row(row, "WEEK", week_fifos)

        result[d] = row

    return result


def _fill_gap_row(row: dict, period_label: str, fifos: dict[str, list]) -> None:
    for fifo_key, out_fill, out_dir in (
        ("UF_GUP", "UF", "GUP"), ("FD_GUP", "FD", "GUP"),
        ("UF_GDN", "UF", "GDN"), ("FD_GDN", "FD", "GDN"),
    ):
        areas = fifos.get(fifo_key, [])
        for rank in (1, 2, 3):
            code = f"{period_label} {out_fill} {out_dir} {rank}"
            if rank <= len(areas):
                area = areas[rank - 1]
                row[code] = (area.low, area.high, area.opened_on)
            else:
                row[code] = None
