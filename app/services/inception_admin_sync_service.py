"""Admin Controls > Inception Sync (desktop client: screens/inception_admin_
sync.py in that repo) — copies the turnover/average-traded-price metrics
LMV's own daily grid already archives (hari_dss.LmvDailySnapshot) into
public.EodBar's own columns of the same name (see app/models/central.py's
EodBar, migrations/central/versions/0006_...), so Inception's Strategy
Builder can offer them as plain fields the same way OPEN/HIGH/LOW/CLOSE
already are — not recomputed from formula_engine.py client-side, just
copied over as already-computed values.

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
lines up (423 of ~433 Instrument rows), so that's the join this service
uses — Stock rows containing a digit (a contract date) are excluded
entirely. One LmvDailySnapshot value for "ADANIENT" is written into EVERY
Instrument row sharing that underlying_symbol (both the '_I' and '_II'
roll series), since these figures describe the underlying stock, not a
specific roll.

Only UPDATEs existing EodBar rows (instrument_id, trade_date already
present from the eqldata vendor feed) — never inserts a new EodBar row from
this path, since a row needs real OHLC to exist at all and this service has
none to offer. A LmvDailySnapshot value with no matching EodBar row (no
vendor bar for that instrument/date) is simply skipped, not an error.
"""
import re
from datetime import date

from sqlalchemy import bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.central import EodBar, Instrument
from app.models.tenant import LmvDailySnapshot, Metric, Stock

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
}

_DATED_SYMBOL_RE = re.compile(r"\d")


async def sync_lmv_metrics_to_eod_bar(tenant_session: AsyncSession, central_session: AsyncSession) -> dict:
    """Runs the copy described in this module's own docstring. Returns
    {"metrics": [{"name", "column", "candidate_rows", "rows_updated"}, ...],
    "date_from", "date_to", "symbols_matched"} — a per-metric breakdown
    rather than one grand total, since a metric with a lower "rows_updated"
    than "candidate_rows" (fewer EodBar rows already existed than
    LmvDailySnapshot values were available) is a real, useful signal, not
    noise to average away.
    """
    metric_rows = (await tenant_session.execute(
        select(Metric.id, Metric.name).where(Metric.name.in_(_METRIC_TO_COLUMN.keys()))
    )).all()
    metric_id_to_column = {mid: _METRIC_TO_COLUMN[name] for mid, name in metric_rows}
    if not metric_id_to_column:
        return {"metrics": [], "date_from": None, "date_to": None, "symbols_matched": 0}

    # Bare-symbol Stock rows only (see this module's docstring) — a plain
    # dict lookup (symbol -> stock_id) is enough; the reverse direction
    # (stock_id -> symbol) is what the snapshot query below actually needs,
    # built from the same query result.
    stock_rows = (await tenant_session.execute(
        select(Stock.id, Stock.symbol).where(~Stock.symbol.op("~")(r"\d"))
    )).all()
    stock_id_to_symbol = {sid: sym for sid, sym in stock_rows}
    if not stock_id_to_symbol:
        return {"metrics": [], "date_from": None, "date_to": None, "symbols_matched": 0}

    snapshot_rows = (await tenant_session.execute(
        select(
            LmvDailySnapshot.trade_date, LmvDailySnapshot.stock_id,
            LmvDailySnapshot.metric_id, LmvDailySnapshot.value_number,
        ).where(
            LmvDailySnapshot.metric_id.in_(metric_id_to_column.keys()),
            LmvDailySnapshot.stock_id.in_(stock_id_to_symbol.keys()),
        )
    )).all()

    underlying_symbols = {stock_id_to_symbol[sid] for _, sid, _, _ in snapshot_rows}
    instrument_rows = (await central_session.execute(
        select(Instrument.id, Instrument.underlying_symbol)
        .where(Instrument.underlying_symbol.in_(underlying_symbols))
    )).all()
    symbol_to_instrument_ids: dict[str, list[int]] = {}
    for iid, underlying in instrument_rows:
        symbol_to_instrument_ids.setdefault(underlying, []).append(iid)

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
        instrument_ids = symbol_to_instrument_ids.get(symbol) if symbol else None
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
        rows_updated = 0
        if params:
            stmt = (
                update(EodBar)
                .where(EodBar.instrument_id == bindparam("_iid"), EodBar.trade_date == bindparam("_td"))
                .values(**{column: bindparam("_val")})
            )
            # Same "Core connection, not ORM session" reasoning as
            # instrument_repo.update_instrument_date_bounds — an executemany
            # UPDATE with an explicit WHERE bindparam clause, not an ORM
            # bulk-by-primary-key operation.
            result = await connection.execute(stmt, params)
            rows_updated = result.rowcount
        metrics_summary.append({
            "name": name_by_column[column],
            "column": column,
            "candidate_rows": len(params),
            "rows_updated": rows_updated,
        })
    await central_session.commit()

    return {
        "metrics": metrics_summary,
        "date_from": min(dates) if dates else None,
        "date_to": max(dates) if dates else None,
        "symbols_matched": len(symbol_to_instrument_ids),
    }
