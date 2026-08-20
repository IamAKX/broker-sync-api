"""Column catalogue for Inception's historical EOD dataset — ports the ~70
derived columns from resources/Additional EOD Columns-Updated.xlsx (referred
to below as "the spec") into a single registry every other Inception module
reads from: services.inception_formula_engine (what to compute),
services.inception_strategy_engine (what a strategy formula can reference),
and routers.inception's GET /columns (what the client's field pickers show).

Every column here becomes one row in the tenant's Metric table (see
services.inception_formula_engine) — same "metric name is the column name"
convention services.historical_service already uses for HistoricalStockValue.

── RAW vs derived ───────────────────────────────────────────────────────────
RAW_FIELDS are EodBar's own columns (not computed, not stored in
InceptionDerivedValue) — included here only so callers building a unified
Strategy Builder field list don't need a second source. GROUP_A and GROUP_B
are genuinely derived/computed columns.

── Group A vs Group B ───────────────────────────────────────────────────────
GROUP_A: ~47 columns computable with a single forward pass per instrument —
running period (day/week/quarter/half-year/year/financial-year) high/low/
open/close bookkeeping, 52-week and all-time high/low, period top/bottom of
the last 2 periods. Pure function of (this instrument's full bar history,
this date) — no cross-day state beyond "what period am I in and what have I
seen in it so far".

GROUP_B: the spec's 24 "gap area" columns (12 daily + 12 weekly) — these are
genuinely stateful: whether an area is still "unfilled" depends on every day
since it opened, not just the current one, and each bucket (day/week ×
unfilled/filled × up/down) keeps up to 3 areas (recent-most first). See
services.inception_formula_engine's module docstring for the exact state
machine and the interpretation call made for what the spec's prose leaves
ambiguous (an "area" -> two price bounds + the date it opened). Every Group B
entry expands to 3 stored metrics: "<CODE> LOW", "<CODE> HIGH", "<CODE> DATE"
(a gap area is a price range, not a single number).
"""

RAW_FIELDS = ["OPEN", "HIGH", "LOW", "CLOSE", "VOL", "OPENINT"]

# code -> description, verbatim (deduped) from the spec's Column1/Column2 text.
GROUP_A: dict[str, str] = {
    "P.OPEN": "Previous day open",
    "P.HIGH": "Previous day high",
    "P.LOW": "Previous day low",
    "P.CLOSE": "Previous day close",
    "P.QTY": "Previous day volume",
    "P.OI": "Previous day open interest",
    "% CHG PDC AND OPEN": "Percentage change between previous day close and current day open",
    "% CHG PWC AND OPEN": "Percentage change between previous week close and current week open",
    "DAY % CHANGE": "Percentage change between current day close and previous day close",
    "52WH": "52 week high — max(highest high of all highs of last 52 weeks)",
    "52WL": "52 week low — min(lowest low of all lows of last 52 weeks)",
    "ATH": "All time high — max(highest high of all highs from the date of inception)",
    "ATL": "All time low — min(lowest low of all lows from the date of inception)",
    "CQH": "Current quarter high — max(highs of the current calendar quarter till the last working day); resets on the first trading day of the quarter",
    "CQO": "Current quarter open — open of current running calendar quarter",
    "CQL": "Current quarter low — min(lows of the current calendar quarter till the last working day); resets on the first trading day of the quarter",
    "PQH": "Previous quarter high — max(highest high of all highs of working days of previous calendar quarter)",
    "PQO": "Previous quarter open",
    "PQL": "Previous quarter low — min(lowest low of all lows of working days of previous calendar quarter)",
    "PQC": "Previous quarter close — close price on the last working day of previous calendar quarter",
    "QT": "Quarter top — max(PQC of last 2 quarters)",
    "QB": "Quarter bottom — min(PQC of last 2 quarters)",
    "CHYH": "Current half-year high; resets on the first trading day of the half year",
    "CHYO": "Current half-year open",
    "CHYL": "Current half-year low; resets on the first trading day of the half year",
    "PHYH": "Previous half-year high",
    "PHYO": "Previous half-year open",
    "PHYL": "Previous half-year low",
    "PHYC": "Previous half-year close — close price on the last working day of previous calendar half year",
    "HYT": "Half-year top — max(PHYC of last 2 half years)",
    "HYB": "Half-year bottom — min(PHYC of last 2 half years)",
    "CYH": "Current year high; resets on the first trading day of the year",
    "CYO": "Current year open",
    "CYL": "Current year low; resets on the first trading day of the year",
    "PYH": "Previous year high",
    "PYO": "Previous year open",
    "PYL": "Previous year low",
    "PYC": "Previous year close — close price on the last working day of previous calendar year",
    "YT": "Year top — max(PYC of last 2 years)",
    "YB": "Year bottom — min(PYC of last 2 years)",
    "CFYH": "Current financial year high (Apr 1 start); reset to zero on the first working day of the financial year",
    "CFYO": "Current financial year open",
    "CFYL": "Current financial year low (Apr 1 start); reset to zero on the first working day of the financial year",
    "PFYH": "Previous financial year high",
    "PFYO": "Previous financial year open",
    "PFYL": "Previous financial year low",
    "PFYC": "Previous financial year close — close price on the last working day (Mar 31) of previous financial year",
}

# 4 buckets x {UF, FD} x {GUP, GDN} x {1,2,3} — spec rows E12-E23 (daily) and
# E25-E36 (weekly). Each becomes 3 stored metrics: "<CODE> LOW"/"<CODE> HIGH"
# (the gap area's price bounds) and "<CODE> DATE" (when the gap opened).
_GAP_PERIODS = ("DAY", "WEEK")
_GAP_FILL_STATES = ("UF", "FD")   # unfilled, filled
_GAP_DIRECTIONS = ("GUP", "GDN")  # gap up, gap down
_GAP_RANKS = (1, 2, 3)            # recent-most .. 3rd recent-most

GROUP_B: dict[str, str] = {}
for _period in _GAP_PERIODS:
    _label = "Daily" if _period == "DAY" else "Weekly"
    for _fill in _GAP_FILL_STATES:
        _fill_label = "unfilled" if _fill == "UF" else "filled"
        for _direction in _GAP_DIRECTIONS:
            _dir_label = "up" if _direction == "GUP" else "down"
            for _rank in _GAP_RANKS:
                _rank_label = {1: "recent most", 2: "2nd recent most", 3: "3rd recent most"}[_rank]
                code = f"{_period} {_fill} {_direction} {_rank}"
                GROUP_B[code] = (
                    f"{_label} {_fill_label} gap-{_dir_label} area {_rank_label} — "
                    f"previous {_label.lower()[:-2] if _period == 'DAY' else 'week'} close vs "
                    f"current {'day' if _period == 'DAY' else 'week'} open, "
                    f"gap % beyond the configured threshold, "
                    f"{'not yet' if _fill == 'UF' else 'already'} closed back through"
                )
del _period, _label, _fill, _fill_label, _direction, _dir_label, _rank, _rank_label, code

GAP_METRIC_SUFFIXES = ("LOW", "HIGH", "DATE")


def gap_metric_names(gap_code: str) -> list[str]:
    return [f"{gap_code} {suffix}" for suffix in GAP_METRIC_SUFFIXES]


# ── Display formulas ─────────────────────────────────────────────────────────
#
# Human-readable, NOT interpreted: same precedent as the desktop client's
# services/formula_tokens.py BUILTIN_FORMULAS for LMV's 56 built-in codes —
# "a formula shown here is a faithful description of what the engine computes
# — but the engine itself is NOT driven by these tokens". These strings exist
# so a user looking at e.g. "52WH" in Strategy Builder sees what it actually
# means (MAX_OF([HIGH], LAST 52 WEEKS)) instead of the tautological-looking
# pass-through "[52WH]" the column's real (executed) formula still is — the
# actual numbers always come from inception_formula_engine.py's tested code,
# never from parsing this string. See app/services/inception_service.py's
# _build_rows for where the real values come from, and
# inception_formula_engine.required_lookback_start for the companion "does
# the user's selected HMV date range even cover what this needs" check.
DISPLAY_FORMULA: dict[str, str] = {
    "P.OPEN": "AT([OPEN], PREVIOUS_TRADING_DAY)",
    "P.HIGH": "AT([HIGH], PREVIOUS_TRADING_DAY)",
    "P.LOW": "AT([LOW], PREVIOUS_TRADING_DAY)",
    "P.CLOSE": "AT([CLOSE], PREVIOUS_TRADING_DAY)",
    "P.QTY": "AT([VOL], PREVIOUS_TRADING_DAY)",
    "P.OI": "AT([OPENINT], PREVIOUS_TRADING_DAY)",
    "% CHG PDC AND OPEN": "([OPEN] - AT([CLOSE], PREVIOUS_TRADING_DAY)) / AT([CLOSE], PREVIOUS_TRADING_DAY) * 100",
    "% CHG PWC AND OPEN": "([OPEN] - AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_WEEK)) / AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_WEEK) * 100",
    "DAY % CHANGE": "([CLOSE] - AT([CLOSE], PREVIOUS_TRADING_DAY)) / AT([CLOSE], PREVIOUS_TRADING_DAY) * 100",
    "52WH": "MAX_OF([HIGH], LAST_52_WEEKS)",
    "52WL": "MIN_OF([LOW], LAST_52_WEEKS)",
    "ATH": "MAX_OF([HIGH], ALL_TIME)",
    "ATL": "MIN_OF([LOW], ALL_TIME)",
    "CQH": "MAX_OF([HIGH], CURRENT_QUARTER)",
    "CQO": "AT([OPEN], FIRST_TRADING_DAY_OF_QUARTER)",
    "CQL": "MIN_OF([LOW], CURRENT_QUARTER)",
    "PQH": "MAX_OF([HIGH], PREVIOUS_QUARTER)",
    "PQO": "AT([OPEN], FIRST_TRADING_DAY_OF_PREVIOUS_QUARTER)",
    "PQL": "MIN_OF([LOW], PREVIOUS_QUARTER)",
    "PQC": "AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_QUARTER)",
    "QT": "MAX(AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_QUARTER), AT([CLOSE], LAST_TRADING_DAY_OF_QUARTER_BEFORE_PREVIOUS))",
    "QB": "MIN(AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_QUARTER), AT([CLOSE], LAST_TRADING_DAY_OF_QUARTER_BEFORE_PREVIOUS))",
    "CHYH": "MAX_OF([HIGH], CURRENT_HALF_YEAR)",
    "CHYO": "AT([OPEN], FIRST_TRADING_DAY_OF_HALF_YEAR)",
    "CHYL": "MIN_OF([LOW], CURRENT_HALF_YEAR)",
    "PHYH": "MAX_OF([HIGH], PREVIOUS_HALF_YEAR)",
    "PHYO": "AT([OPEN], FIRST_TRADING_DAY_OF_PREVIOUS_HALF_YEAR)",
    "PHYL": "MIN_OF([LOW], PREVIOUS_HALF_YEAR)",
    "PHYC": "AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_HALF_YEAR)",
    "HYT": "MAX(AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_HALF_YEAR), AT([CLOSE], LAST_TRADING_DAY_OF_HALF_YEAR_BEFORE_PREVIOUS))",
    "HYB": "MIN(AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_HALF_YEAR), AT([CLOSE], LAST_TRADING_DAY_OF_HALF_YEAR_BEFORE_PREVIOUS))",
    "CYH": "MAX_OF([HIGH], CURRENT_YEAR)",
    "CYO": "AT([OPEN], FIRST_TRADING_DAY_OF_YEAR)",
    "CYL": "MIN_OF([LOW], CURRENT_YEAR)",
    "PYH": "MAX_OF([HIGH], PREVIOUS_YEAR)",
    "PYO": "AT([OPEN], FIRST_TRADING_DAY_OF_PREVIOUS_YEAR)",
    "PYL": "MIN_OF([LOW], PREVIOUS_YEAR)",
    "PYC": "AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_YEAR)",
    "YT": "MAX(AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_YEAR), AT([CLOSE], LAST_TRADING_DAY_OF_YEAR_BEFORE_PREVIOUS))",
    "YB": "MIN(AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_YEAR), AT([CLOSE], LAST_TRADING_DAY_OF_YEAR_BEFORE_PREVIOUS))",
    "CFYH": "MAX_OF([HIGH], CURRENT_FINANCIAL_YEAR)",
    "CFYO": "AT([OPEN], FIRST_TRADING_DAY_OF_FINANCIAL_YEAR)",
    "CFYL": "MIN_OF([LOW], CURRENT_FINANCIAL_YEAR)",
    "PFYH": "MAX_OF([HIGH], PREVIOUS_FINANCIAL_YEAR)",
    "PFYO": "AT([OPEN], FIRST_TRADING_DAY_OF_PREVIOUS_FINANCIAL_YEAR)",
    "PFYL": "MIN_OF([LOW], PREVIOUS_FINANCIAL_YEAR)",
    "PFYC": "AT([CLOSE], LAST_TRADING_DAY_OF_PREVIOUS_FINANCIAL_YEAR)",
}

_GAP_DIR_LABEL = {"GUP": "UP", "GDN": "DOWN"}
_GAP_FILL_LABEL = {"UF": "UNFILLED", "FD": "FILLED"}
_GAP_PERIOD_LABEL = {"DAY": "DAILY", "WEEK": "WEEKLY"}


def _gap_display_formula(gap_code: str) -> str:
    """"DAY UF GUP 1" -> "GAP_AREA(UP, UNFILLED, RANK 1, DAILY)" — parsed
    from the code itself rather than hand-listed, so it can't drift out of
    sync with GROUP_B's own key format."""
    period, fill, direction, rank = gap_code.split(" ")
    return (
        f"GAP_AREA({_GAP_DIR_LABEL[direction]}, {_GAP_FILL_LABEL[fill]}, "
        f"RANK {rank}, {_GAP_PERIOD_LABEL[period]})"
    )


def all_derived_codes() -> list[str]:
    return list(GROUP_A) + list(GROUP_B)


def all_derived_metric_names() -> list[str]:
    """Every Metric row name a fully-computed instrument/date produces —
    Group A codes as-is, Group B codes expanded to their 3 suffixed metrics."""
    names = list(GROUP_A)
    for code in GROUP_B:
        names.extend(gap_metric_names(code))
    return names


def is_stateful_gap(code: str) -> bool:
    return code in GROUP_B


class ColumnInfo:
    __slots__ = ("code", "description", "group", "stateful_gap", "formula_display")

    def __init__(self, code: str, description: str, group: str, stateful_gap: bool,
                 formula_display: str | None = None):
        self.code = code
        self.description = description
        self.group = group
        self.stateful_gap = stateful_gap
        self.formula_display = formula_display


def column_catalogue() -> list[ColumnInfo]:
    """Full catalogue for GET /inception/columns — raw fields first (group
    'raw'), then Group A ('derived'), then Group B ('derived',
    stateful_gap=True).

    Each Group B code appears FOUR times: the bare code itself (e.g. "DAY UF
    GUP 1"), aliased server-side to its HIGH bound — the single number most
    formulas want — plus its three explicit LOW/HIGH/DATE sub-fields for
    anyone who wants the full range or the date it opened (see
    services.inception_service._build_rows' gap-alias step, and
    gap_metric_names below).

    formula_display carries the human-readable (not interpreted — see that
    module-level docstring above DISPLAY_FORMULA) formula string for every
    Group A/B code; None for raw fields (nothing to derive) and for a Group
    B code's own LOW/HIGH/DATE sub-fields (the description belongs to the
    bare code; repeating it three times added nothing).
    """
    out = [ColumnInfo(f, f"Raw uploaded field ({f})", "raw", False) for f in RAW_FIELDS]
    out += [
        ColumnInfo(code, desc, "derived", False, formula_display=DISPLAY_FORMULA.get(code))
        for code, desc in GROUP_A.items()
    ]
    for code, desc in GROUP_B.items():
        out.append(ColumnInfo(
            code, f"{desc} — alias for {code} HIGH", "derived", True,
            formula_display=_gap_display_formula(code),
        ))
        for name in gap_metric_names(code):
            out.append(ColumnInfo(name, desc, "derived", True))
    return out
