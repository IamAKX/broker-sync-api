"""The ``inception_levels`` view: every Inception "Group A" code, computed the way the app does.

Why this exists: none of these codes (QT, PQC, 52WH, ATH, CQO, ...) is stored anywhere, so the
chatbot used to be told what each one *means* and left to write the arithmetic itself. It got it
wrong (asked for stocks near QT, it took the highest close of every day in the previous quarter
instead of the higher of the last two quarter-end closes, so its numbers disagreed with the
Inception screen). Definitions written by a model on the fly cannot be relied on, so the
calculation now lives here once, in SQL, and the model only ever *reads* a column.

The rules are a line-for-line port of the desktop's ``services/inception_formula_engine.
compute_group_a`` (the code behind the Inception / HMV grid and the LMV "Inception Field"
formulas), over the same data (near-month continuous futures bars):

* previous / current periods are calendar periods (quarter, Jan-Jun / Jul-Dec half-year, year,
  1 Apr financial year); "previous" is the calendar period before, blank when it has no bars;
* period open = first trading day's open, close = last trading day's close, high / low = extremes;
* tops / bottoms (QT/QB, HYT/HYB, YT/YB) = max / min of the *closes of the last two completed
  periods* (whichever of the two exist);
* 52WH / 52WL look back 364 calendar days (52 weeks), inclusive of both ends;
* ``% CHG PWC AND OPEN`` compares the previous week's close with the day's open.

``tests/test_inception_levels.py`` keeps the two in step: it runs this view in a real Postgres
against an independent Python reference of the same rules.

Performance: the per-row work is done by LATERAL index scans on ``EodBar``'s primary key
(instrument_id, trade_date), one per family of codes, so a query that pins ``trade_date`` (all
stocks on one date) or ``symbol`` (one stock over time) only touches the bars it needs, and only
for the families it selects. Timings on production data are in the commit that added this.
"""

from __future__ import annotations

VIEW_NAME = "inception_levels"

# 52 weeks, in calendar days. Fixed on purpose (desktop: inception_formula_engine.
# DEFAULT_WEEK_WINDOW_DAYS): it was once a per-user setting, got saved as 750 and made every
# "52-week high" a two-year high, so neither side reads a setting for it any more.
WEEK_WINDOW_DAYS = 364

# Inception code -> view column -> meaning. The glossary shown to the model
# (knowledge/glossary/inception-group-a.md) must list every one; tests/test_inception_levels.py checks.
RAW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("OPEN", "open"), ("HIGH", "high"), ("LOW", "low"), ("CLOSE", "close"),
    ("VOL", "volume"), ("OPENINT", "open_interest"),
)
CODE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("P.OPEN", "p_open", "Previous trading day's open"),
    ("P.HIGH", "p_high", "Previous trading day's high"),
    ("P.LOW", "p_low", "Previous trading day's low"),
    ("P.CLOSE", "p_close", "Previous trading day's close"),
    ("P.QTY", "p_qty", "Previous trading day's volume"),
    ("P.OI", "p_oi", "Previous trading day's open interest"),
    ("% CHG PDC AND OPEN", "pct_chg_pdc_open", "% change from the previous day's close to the day's open"),
    ("% CHG PWC AND OPEN", "pct_chg_pwc_open", "% change from the previous week's close to the day's open"),
    ("DAY % CHANGE", "day_pct_change", "% change from the previous day's close to the day's close"),
    ("52WH", "wh52", "52-week high: highest high of the last 52 weeks, including the date"),
    ("52WL", "wl52", "52-week low: lowest low of the last 52 weeks, including the date"),
    ("ATH", "ath", "All-time high: highest high in all history up to the date"),
    ("ATL", "atl", "All-time low: lowest low in all history up to the date"),
    ("CQH", "cqh", "Current quarter high, to the date"),
    ("CQO", "cqo", "Current quarter open (open of the quarter's first trading day)"),
    ("CQL", "cql", "Current quarter low, to the date"),
    ("PQH", "pqh", "Previous quarter high"),
    ("PQO", "pqo", "Previous quarter open"),
    ("PQL", "pql", "Previous quarter low"),
    ("PQC", "pqc", "Previous quarter close (close of its last trading day)"),
    ("QT", "qt", "Quarter top: the HIGHER of the closes of the last two completed quarters"),
    ("QB", "qb", "Quarter bottom: the LOWER of the closes of the last two completed quarters"),
    ("CHYH", "chyh", "Current half-year (Jan-Jun / Jul-Dec) high, to the date"),
    ("CHYO", "chyo", "Current half-year open"),
    ("CHYL", "chyl", "Current half-year low, to the date"),
    ("PHYH", "phyh", "Previous half-year high"),
    ("PHYO", "phyo", "Previous half-year open"),
    ("PHYL", "phyl", "Previous half-year low"),
    ("PHYC", "phyc", "Previous half-year close"),
    ("HYT", "hyt", "Half-year top: the higher of the closes of the last two completed half-years"),
    ("HYB", "hyb", "Half-year bottom: the lower of the closes of the last two completed half-years"),
    ("CYH", "cyh", "Current calendar year high, to the date"),
    ("CYO", "cyo", "Current calendar year open"),
    ("CYL", "cyl", "Current calendar year low, to the date"),
    ("PYH", "pyh", "Previous calendar year high"),
    ("PYO", "pyo", "Previous calendar year open"),
    ("PYL", "pyl", "Previous calendar year low"),
    ("PYC", "pyc", "Previous calendar year close"),
    ("YT", "yt", "Year top: the higher of the closes of the last two completed calendar years"),
    ("YB", "yb", "Year bottom: the lower of the closes of the last two completed calendar years"),
    ("CFYH", "cfyh", "Current financial year (1 Apr - 31 Mar) high, to the date"),
    ("CFYO", "cfyo", "Current financial year open"),
    ("CFYL", "cfyl", "Current financial year low, to the date"),
    ("PFYH", "pfyh", "Previous financial year high"),
    ("PFYO", "pfyo", "Previous financial year open"),
    ("PFYL", "pfyl", "Previous financial year low"),
    ("PFYC", "pfyc", "Previous financial year close"),
)
KEY_COLUMNS: tuple[str, ...] = ("symbol", "trade_date")
ALL_COLUMNS: tuple[str, ...] = (
    KEY_COLUMNS + tuple(col for _, col in RAW_COLUMNS) + tuple(col for _, col, _ in CODE_COLUMNS)
)


def _pct(base: str, current: str) -> str:
    # Same as the desktop's _pct: blank when there is no base or the base is zero.
    return (
        f"CASE WHEN {base} IS NULL OR {base} = 0 OR {current} IS NULL THEN NULL "
        f"ELSE round(({current} - {base}) / {base} * 100.0, 4) END"
    )


# "Value on the first / last trading day of a period" without sorting: min / max of a
# [day-number, value] pair orders by the day first, and the value rides along. (An ordered
# array_agg does the same job but sorts once per aggregate per row, several times slower.)
_DAY = "(x.trade_date - DATE '1900-01-01')::numeric"


def _first(value: str, where: str) -> str:
    return f"(min(ARRAY[{_DAY}, {value}]) FILTER (WHERE {where}))[2]"


def _last(value: str, where: str) -> str:
    return f"(max(ARRAY[{_DAY}, {value}]) FILTER (WHERE {where}))[2]"


_BARS = 'public."EodBar"'


def _lateral(alias: str, aggs: list[str], since: str | None) -> str:
    """One LEFT JOIN LATERAL aggregate over the same stock's bars up to the row's date (from
    ``since`` when given). Each family of codes gets its own, deliberately: Postgres drops a
    left-joined aggregate whose columns the query does not select, so a question about QT reads
    about nine months of bars and never pays for the all-time or yearly scans."""
    lower = f" AND x.trade_date >= {since}" if since else ""
    cols = ",\n               ".join(aggs)
    return f"""LEFT JOIN LATERAL (
        SELECT {cols}
        FROM {_BARS} x
        WHERE x.instrument_id = b.instrument_id AND x.trade_date <= b.trade_date{lower}
    ) {alias} ON true"""


def _period(prefix: str, start: str, prev_start: str, *, prev2_start: str | None) -> tuple[str, str]:
    """(lateral join, outer select expressions) for one period type. ``start`` / ``prev_start`` /
    ``prev2_start`` are date expressions on ``k`` (the row's period boundaries)."""
    p = prefix.lower()
    g = f"g{p}"
    cur = f"x.trade_date >= {start}"
    prev = f"x.trade_date >= {prev_start} AND x.trade_date < {start}"
    aggs = [
        f"max(x.high) FILTER (WHERE {cur}) AS c{p}h",
        f"min(x.low)  FILTER (WHERE {cur}) AS c{p}l",
        f"{_first('x.open', cur)} AS c{p}o",
        f"max(x.high) FILTER (WHERE {prev}) AS p{p}h",
        f"min(x.low)  FILTER (WHERE {prev}) AS p{p}l",
        f"{_first('x.open', prev)} AS p{p}o",
        f"{_last('x.close', prev)} AS p{p}c",
    ]
    outer = [f"{g}.c{p}h", f"{g}.c{p}o", f"{g}.c{p}l", f"{g}.p{p}h", f"{g}.p{p}o", f"{g}.p{p}l", f"{g}.p{p}c"]
    if prev2_start is not None:
        prev2 = f"x.trade_date >= {prev2_start} AND x.trade_date < {prev_start}"
        aggs.append(f"{_last('x.close', prev2)} AS p2{p}c")
        # GREATEST / LEAST skip NULLs, like the desktop's max()/min() over the closes that exist.
        outer += [f"GREATEST({g}.p{p}c, {g}.p2{p}c) AS {p}t", f"LEAST({g}.p{p}c, {g}.p2{p}c) AS {p}b"]
    return _lateral(g, aggs, prev2_start or prev_start), ", ".join(outer)


_NORM = "regexp_replace(upper({}), '[^A-Z0-9]', '', 'g')"


def _symbol_sql(schema: str) -> str:
    """The stock's symbol as the user's own grid spells it. The futures feed writes BAJAJ_AUTO /
    M_M / NAM_INDIA where the Live Master View, the sector list and the user say BAJAJ-AUTO / M&M /
    NAM-INDIA; matched the way the desktop does (letters and digits only), so a plain
    ``JOIN ... USING (symbol)`` against lmv_live / lmv_daily / stock_sectors never drops them."""
    return f"""COALESCE((SELECT st.symbol FROM "{schema}"."Stock" st
                         WHERE {_NORM.format("st.symbol")} = {_NORM.format("i.underlying_symbol")}
                         ORDER BY st.is_active DESC, st.symbol LIMIT 1), i.underlying_symbol)"""


def view_select(schema: str) -> str:
    """The SELECT behind the view (``schema``, the caller's tenant schema, must already be
    validated). The numbers come only from the shared ``public`` bar tables; the tenant schema is
    used just to spell the symbol the user's way."""
    q_join, q_out = _period("q", "k.q_start", "k.pq_start", prev2_start="k.p2q_start")
    hy_join, hy_out = _period("hy", "k.hy_start", "k.phy_start", prev2_start="k.p2hy_start")
    y_join, y_out = _period("y", "k.y_start", "k.py_start", prev2_start="k.p2y_start")
    fy_join, fy_out = _period("fy", "k.fy_start", "k.pfy_start", prev2_start=None)
    # The previous trading day's bar, and the close of the last trading day before this week: one
    # backward index probe each. The probe is wrapped in an aggregate (max over its single row) so
    # that Postgres can prove the join yields exactly one row and drop it when the query does not
    # select these columns. Without that, Postgres 16 ran the probes once per bar in history for
    # "SELECT max(trade_date) FROM inception_levels" (5 s and more on production).
    def probe(alias: str, before: str, cols: tuple[str, ...]) -> str:
        return f"""LEFT JOIN LATERAL (
        SELECT {", ".join(f"max(z.{c}) AS {c}" for c in cols)}
        FROM (SELECT {", ".join(f"x.{c}" for c in cols)} FROM {_BARS} x
              WHERE x.instrument_id = b.instrument_id AND x.trade_date < {before}
              ORDER BY x.trade_date DESC LIMIT 1) z
    ) {alias} ON true"""

    prev_join = probe("pb", "b.trade_date", ("open", "high", "low", "close", "volume", "open_interest"))
    week_join = probe("wb", "k.week_start", ("close",))
    w52_join = _lateral("g52", ["max(x.high) AS wh52", "min(x.low) AS wl52"], "k.w52_start")
    all_join = _lateral("a", ["max(x.high) AS ath", "min(x.low) AS atl"], None)
    return f"""
    SELECT {_symbol_sql(schema)} AS symbol, b.trade_date,
           b.open, b.high, b.low, b.close, b.volume, b.open_interest,
           pb.open AS p_open, pb.high AS p_high, pb.low AS p_low, pb.close AS p_close,
           pb.volume AS p_qty, pb.open_interest AS p_oi,
           {_pct("pb.close", "b.open")} AS pct_chg_pdc_open,
           {_pct("wb.close", "b.open")} AS pct_chg_pwc_open,
           {_pct("pb.close", "b.close")} AS day_pct_change,
           g52.wh52, g52.wl52, a.ath, a.atl,
           {q_out},
           {hy_out},
           {y_out},
           {fy_out}
    FROM public."Instrument" i
    JOIN {_BARS} b ON b.instrument_id = i.id
    CROSS JOIN LATERAL (
        SELECT d.q_start, (d.q_start - interval '3 months')::date AS pq_start,
               (d.q_start - interval '6 months')::date AS p2q_start,
               d.hy_start, (d.hy_start - interval '6 months')::date AS phy_start,
               (d.hy_start - interval '12 months')::date AS p2hy_start,
               d.y_start, (d.y_start - interval '1 year')::date AS py_start,
               (d.y_start - interval '2 years')::date AS p2y_start,
               d.fy_start, (d.fy_start - interval '1 year')::date AS pfy_start,
               date_trunc('week', b.trade_date)::date AS week_start,
               b.trade_date - {WEEK_WINDOW_DAYS} AS w52_start
        FROM (SELECT date_trunc('quarter', b.trade_date)::date AS q_start,
                     make_date(extract(year FROM b.trade_date)::int,
                               CASE WHEN extract(month FROM b.trade_date) <= 6 THEN 1 ELSE 7 END, 1) AS hy_start,
                     date_trunc('year', b.trade_date)::date AS y_start,
                     make_date(extract(year FROM b.trade_date)::int
                               - CASE WHEN extract(month FROM b.trade_date) < 4 THEN 1 ELSE 0 END, 4, 1) AS fy_start
             ) d
    ) k
    {prev_join}
    {week_join}
    {w52_join}
    {all_join}
    {q_join}
    {hy_join}
    {y_join}
    {fy_join}
    WHERE i.symbol = i.underlying_symbol || '_I'"""


def view_ddl(schema: str) -> str:
    return f"CREATE TEMP VIEW {VIEW_NAME} AS {view_select(schema)}"
