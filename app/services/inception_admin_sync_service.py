"""Admin Controls > Inception Sync (desktop client: screens/inception_admin_
sync.py in that repo) — copies figures LMV's own daily grid already
archives (hari_dss.LmvDailySnapshot) into public.EodBar's own columns of
the same name (see app/models/central.py's EodBar, migrations/central/
versions/0006_.../0007_...), so Inception's Strategy Builder can offer them
as plain fields the same way OPEN/HIGH/LOW/CLOSE already are — not
recomputed from formula_engine.py client-side, just copied over as
already-computed values. Covers the turnover/average-traded-price metrics
(Avg Rate, PATP, ...), the options-OI/max-pain sheet columns
(callstrikehighestoi, Max Pain, ...), and Market Profile (VAH/POC/VAL) —
all via the SAME LmvDailySnapshot-archived path, see `_METRIC_TO_COLUMN`.

OR.High/OR.Low are the one exception: they come from a separate table,
hari_dss.OpeningRangeCapture, not LmvDailySnapshot/Metric at all — see
`_sync_opening_range` below for that different join.

Restricted to one named admin account (app.core.deps.require_admin_email),
not a role — the ONLY tenant schema this ever reads from is that admin's
own ("hari_dss"), because app.db.deps.get_tenant_db resolves its schema
from the SAME verified current_user this endpoint's dependency already
checked; there's no separate "which tenant" input to this service at all,
by construction, not by convention.

── Matching LmvDailySnapshot rows to EodBar rows ────────────────────────────
hari_dss.Stock.symbol is NOT the same identifier space as public.Instrument.
symbol: Instrument holds Inception's own continuous-roll futures series
('ADANIENT_I'/'ADANIENT_II'), while Stock holds two different kinds of rows
per underlying — a bare "ADANIENT" (the one LmvDailySnapshot's own
turnover/ATP metrics are actually keyed against) and one "ADANIENT
29-Sep-2026"-style dated-contract row per expiry (irrelevant here). Verified
directly against the live data (not assumed): matching Instrument.
underlying_symbol against Stock's BARE (no-digit) symbols is what actually
lines up — Stock rows containing a digit (a contract date) are excluded
entirely. One LmvDailySnapshot value for "ADANIENT" is written into EVERY
Instrument row sharing that underlying_symbol (both the '_I' and '_II'
roll series), since these figures describe the underlying stock, not a
specific roll.

The match itself is by _normalize_symbol (below), not exact string
equality — an exact join originally matched only 423 of ~433 Instrument
rows; the other ~10 all differed by punctuation/spacing alone (e.g. LMV's
"GVT&D" vs Instrument's "GVT_D") and were silently getting none of these
figures at all — see issue #18.

Only UPDATEs existing EodBar rows (instrument_id, trade_date already
present from the eqldata vendor feed) — never inserts a new EodBar row from
this path, since a row needs real OHLC to exist at all and this service has
none to offer. A LmvDailySnapshot value with no matching EodBar row (no
vendor bar for that instrument/date) is simply skipped, not an error.
"""
import re
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.models.central import Instrument
from app.models.tenant import LmvDailySnapshot, Metric, OpeningRangeCapture, Stock

# Batched single UPDATE...FROM (VALUES ...) statements, not one bindparam +
# executemany-style bulk UPDATE. Matters for a reason beyond just style: an
# executemany UPDATE's rowcount is DBAPI-defined and unreliable — confirmed
# directly against this exact endpoint (psycopg/asyncpg both just report -1
# for it, even though every row is written correctly) — while a real single
# UPDATE statement's own rowcount is always accurate. 500 rows/statement
# keeps each statement's param count (3 per row: instrument_id, trade_date,
# value) comfortably under Postgres's 65535 limit with room to spare, same
# order of magnitude as app/repositories/instrument_repo.py's own
# _EOD_BAR_UPSERT_BATCH_SIZE.
_UPDATE_BATCH_SIZE = 500

# name (as registered in hari_dss.Metric) -> EodBar column name. "Avg Rate"
# (id 23, the populated one) not "AvgRate" (id 18, zero rows — a dead
# duplicate registration, confirmed directly against the data) — see this
# module's own docstring.
_METRIC_TO_COLUMN = {
    "Avg Rate": "avg_rate",
    "PATP": "patp",
    "PWATP": "pwatp",
    "PMATP": "pmatp",
    "CWATP": "cwatp",
    "CMATP": "cmatp",
    "DAY TO": "day_to",
    "PDTO": "pdto",
    "CWTO": "cwto",
    "PWTO": "pwto",
    # Options-OI/max-pain sheet columns (ReliableSoftware/NiftyInvest) and
    # Market Profile (VAH/POC/VAL) — same LmvDailySnapshot-archived path,
    # case-sensitive names exactly as config_defaults.MAIN_COLUMN_NAME_DATA/
    # broker_db_source.MARKET_PROFILE_HEADERS register them as LMV headers
    # (verify against the desktop repo before renaming either side).
    "callstrikehighestoi": "call_strike_highest_oi",
    "Callstrikewithsecondhighestoi": "call_strike_with_second_highest_oi",
    "PutStrikeWithsecondHighestOI": "put_strike_with_second_highest_oi",
    "TodayPutHighestStrike": "today_put_highest_strike",
    "Max Pain": "max_pain",
    "VAH": "vah",
    "POC": "poc",
    "VAL": "val",
}

# OpeningRangeCapture's own two columns -> EodBar column. Not a Metric-
# archived value (see this module's docstring) so it's not part of
# _METRIC_TO_COLUMN, but _sync_opening_range below produces metrics_summary
# entries in the exact same shape so both paths report through one
# response list.
_OR_CAPTURE_TO_COLUMN = {"high": "or_high", "low": "or_low"}

_DATED_SYMBOL_RE = re.compile(r"\d")

# Collapses punctuation differences between hari_dss.Stock's bare-symbol
# spelling (e.g. "GVT&D", "M&M", "BAJAJ AUTO") and public.Instrument's own
# underscore-only spelling for the same instrument ("GVT_D", "M_M",
# "BAJAJ_AUTO") down to one comparable key — an exact-string join between
# the two silently dropped every stock whose spelling differs this way
# (confirmed: 423 of ~433 Instrument rows matched before this fix, the
# other ~10 exactly the set issue #18 reported: 360ONE, BAJAJ AUTO, GVT&D,
# M&M, NAM-INDIA, NIFTYNXT50 — none of their turnover/ATP/OI/max-pain/
# Market Profile/OR figures were ever copied to EodBar). Same technique
# (and same non-alphanumeric-strip regex) as the desktop client's own
# services.inception_sector._normalize/services.lmv_inception_fields.
# _normalize_inception_symbol/services.strategy_engine._norm_for_inception
# — all solving this identical LMV-vs-Inception spelling mismatch, proven
# collision-free across this same symbol universe already.
_SYMBOL_PUNCT_RE = re.compile(r"[^A-Z0-9]")


def _normalize_symbol(symbol) -> str:
    return _SYMBOL_PUNCT_RE.sub("", str(symbol or "").strip().upper())


async def sync_lmv_metrics_to_eod_bar(tenant_session: AsyncSession, central_session: AsyncSession) -> dict:
    """Runs the copy described in this module's own docstring. Returns
    {"metrics": [{"name", "column", "candidate_rows", "rows_updated"}, ...],
    "date_from", "date_to", "symbols_matched"} — a per-metric breakdown
    rather than one grand total, since a metric with a lower "rows_updated"
    than "candidate_rows" (fewer EodBar rows already existed than
    LmvDailySnapshot values were available) is a real, useful signal, not
    noise to average away. Runs BOTH the LmvDailySnapshot-archived metrics
    (_METRIC_TO_COLUMN) and the separate OpeningRangeCapture-sourced OR.
    High/OR.Low sync (_sync_opening_range) and merges them into one
    response — same "metrics" list shape for both, so the desktop sync
    screen (a generic loop over that list) shows both without caring which
    path a given row came from.
    """
    # Bare-symbol Stock rows only (see this module's docstring) — shared by
    # both sync paths below, since both key off the same hari_dss.Stock
    # identifier space.
    stock_rows = (await tenant_session.execute(
        select(Stock.id, Stock.symbol).where(~Stock.symbol.op("~")(r"\d"))
    )).all()
    stock_id_to_symbol = {sid: sym for sid, sym in stock_rows}
    if not stock_id_to_symbol:
        return {"metrics": [], "date_from": None, "date_to": None, "symbols_matched": 0}

    metrics_summary: list[dict] = []
    dates: list[date] = []
    symbols_matched: set[str] = set()

    metric_summary, metric_dates, metric_symbols = await _sync_lmv_metrics(
        tenant_session, central_session, stock_id_to_symbol,
    )
    metrics_summary += metric_summary
    dates += metric_dates
    symbols_matched |= metric_symbols

    or_summary, or_dates, or_symbols = await _sync_opening_range(
        tenant_session, central_session, stock_id_to_symbol,
    )
    metrics_summary += or_summary
    dates += or_dates
    symbols_matched |= or_symbols

    await central_session.commit()

    return {
        "metrics": metrics_summary,
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
        "symbols_matched": len(symbols_matched),
    }


async def _sync_lmv_metrics(
    tenant_session: AsyncSession, central_session: AsyncSession, stock_id_to_symbol: dict[int, str],
) -> tuple[list[dict], list[date], set[str]]:
    """The turnover/ATP + options-OI/max-pain + Market Profile metrics —
    everything in _METRIC_TO_COLUMN, all archived via hari_dss.
    LmvDailySnapshot/Metric. Does NOT commit — the caller does, once, after
    both sync paths have run."""
    metric_rows = (await tenant_session.execute(
        select(Metric.id, Metric.name).where(Metric.name.in_(_METRIC_TO_COLUMN.keys()))
    )).all()
    metric_id_to_column = {mid: _METRIC_TO_COLUMN[name] for mid, name in metric_rows}
    if not metric_id_to_column:
        return [], [], set()

    snapshot_rows = (await tenant_session.execute(
        select(
            LmvDailySnapshot.trade_date, LmvDailySnapshot.stock_id,
            LmvDailySnapshot.metric_id, LmvDailySnapshot.value_number,
        ).where(
            LmvDailySnapshot.metric_id.in_(metric_id_to_column.keys()),
            LmvDailySnapshot.stock_id.in_(stock_id_to_symbol.keys()),
        )
    )).all()

    # Instrument.underlying_symbol AND Stock's bare symbol are both fetched
    # for the FULL matched universe (not filtered to underlying_symbols'
    # exact spellings), then joined by _normalize_symbol — see that
    # helper's own docstring for why an exact-string join here silently
    # dropped ~10 real stocks (issue #18).
    instrument_rows = (await central_session.execute(
        select(Instrument.id, Instrument.underlying_symbol)
    )).all()
    symbol_to_instrument_ids: dict[str, list[int]] = {}
    for iid, underlying in instrument_rows:
        symbol_to_instrument_ids.setdefault(_normalize_symbol(underlying), []).append(iid)

    # column -> [{"_iid":, "_td":, "_val":}, ...], one entry per (instrument,
    # date) this metric has a real value for AND a matching Instrument row
    # exists for — expanding one LmvDailySnapshot value out to every roll
    # series ('_I' and '_II') sharing that underlying.
    params_by_column: dict[str, list[dict]] = {col: [] for col in metric_id_to_column.values()}
    dates: list[date] = []
    for trade_date, stock_id, metric_id, value in snapshot_rows:
        if value is None:
            continue
        symbol = stock_id_to_symbol.get(stock_id)
        instrument_ids = symbol_to_instrument_ids.get(_normalize_symbol(symbol)) if symbol else None
        if not instrument_ids:
            continue
        column = metric_id_to_column[metric_id]
        dates.append(trade_date)
        for iid in instrument_ids:
            params_by_column[column].append({"_iid": iid, "_td": trade_date, "_val": value})

    connection = await central_session.connection()
    metrics_summary = []
    name_by_column = {v: k for k, v in _METRIC_TO_COLUMN.items()}
    for column, params in params_by_column.items():
        rows_updated = await _bulk_update_column(connection, column, params) if params else 0
        metrics_summary.append({
            "name": name_by_column[column],
            "column": column,
            "candidate_rows": len(params),
            "rows_updated": rows_updated,
        })

    return metrics_summary, dates, set(symbol_to_instrument_ids)


async def _sync_opening_range(
    tenant_session: AsyncSession, central_session: AsyncSession, stock_id_to_symbol: dict[int, str],
) -> tuple[list[dict], list[date], set[str]]:
    """OR.High/OR.Low: unlike every metric in _METRIC_TO_COLUMN, these come
    from hari_dss.OpeningRangeCapture directly (its own per-stock/per-date
    High/Low, not a Metric-archived value) — see that table's own
    docstring. Same underlying-symbol matching + roll-series fan-out as
    _sync_lmv_metrics, just against a different source table. Does NOT
    commit — the caller does, once, after both sync paths have run."""
    capture_rows = (await tenant_session.execute(
        select(OpeningRangeCapture.trade_date, OpeningRangeCapture.stock_id,
               OpeningRangeCapture.high, OpeningRangeCapture.low)
        .where(OpeningRangeCapture.stock_id.in_(stock_id_to_symbol.keys()))
    )).all()
    if not capture_rows:
        return [], [], set()

    # See sync_lmv_metrics_to_eod_bar's own _normalize_symbol docstring —
    # same exact-string-join gap (issue #18), fixed identically here.
    instrument_rows = (await central_session.execute(
        select(Instrument.id, Instrument.underlying_symbol)
    )).all()
    symbol_to_instrument_ids: dict[str, list[int]] = {}
    for iid, underlying in instrument_rows:
        symbol_to_instrument_ids.setdefault(_normalize_symbol(underlying), []).append(iid)

    params_by_column: dict[str, list[dict]] = {col: [] for col in _OR_CAPTURE_TO_COLUMN.values()}
    dates: list[date] = []
    for trade_date, stock_id, high, low in capture_rows:
        symbol = stock_id_to_symbol.get(stock_id)
        instrument_ids = symbol_to_instrument_ids.get(_normalize_symbol(symbol)) if symbol else None
        if not instrument_ids:
            continue
        dates.append(trade_date)
        for iid in instrument_ids:
            for field, value in (("high", high), ("low", low)):
                if value is None:
                    continue
                params_by_column[_OR_CAPTURE_TO_COLUMN[field]].append(
                    {"_iid": iid, "_td": trade_date, "_val": value}
                )

    connection = await central_session.connection()
    metrics_summary = []
    name_by_column = {"or_high": "OR.High", "or_low": "OR.Low"}
    for column, params in params_by_column.items():
        rows_updated = await _bulk_update_column(connection, column, params) if params else 0
        metrics_summary.append({
            "name": name_by_column[column],
            "column": column,
            "candidate_rows": len(params),
            "rows_updated": rows_updated,
        })

    return metrics_summary, dates, set(symbol_to_instrument_ids)


async def _bulk_update_column(connection: AsyncConnection, column: str, params: list[dict]) -> int:
    """Writes params ([{"_iid", "_td", "_val"}, ...]) into EodBar.<column>
    via batched UPDATE ... FROM (VALUES ...) statements — see this
    module's own _UPDATE_BATCH_SIZE comment for why a real single
    statement per batch, not one bindparam-executemany call, is what
    makes the returned row count trustworthy. *column* is always one of
    _METRIC_TO_COLUMN's own values (never end-user input), so splicing it
    directly into the SQL text is safe; every actual value stays a bound
    parameter.
    """
    total = 0
    for start in range(0, len(params), _UPDATE_BATCH_SIZE):
        batch = params[start:start + _UPDATE_BATCH_SIZE]
        # Explicit casts are required — asyncpg's prepared-statement
        # protocol needs each parameter's type resolved up front and does
        # NOT infer it from the later WHERE/SET comparison the way a
        # plain-text/psycopg2 round trip would (confirmed directly:
        # "operator does not exist: bigint = text" without these). Written
        # as CAST(:x AS bigint), not :x::bigint — SQLAlchemy's text()
        # treats a bare "::" as an escaped literal colon, not Postgres's
        # cast operator (confirmed directly too: "syntax error at or near
        # ':'" with :: included), so CAST(...) is what actually parses
        # here in either driver.
        values_sql = ", ".join(
            f"(CAST(:iid{i} AS bigint), CAST(:td{i} AS date), CAST(:val{i} AS numeric))"
            for i in range(len(batch))
        )
        bind = {}
        for i, p in enumerate(batch):
            bind[f"iid{i}"] = p["_iid"]
            bind[f"td{i}"] = p["_td"]
            bind[f"val{i}"] = p["_val"]
        stmt = text(
            f'UPDATE "EodBar" SET {column} = v.val '
            f'FROM (VALUES {values_sql}) AS v(iid, td, val) '
            f'WHERE "EodBar".instrument_id = v.iid AND "EodBar".trade_date = v.td'
        )
        result = await connection.execute(stmt, bind)
        total += result.rowcount
    return total
