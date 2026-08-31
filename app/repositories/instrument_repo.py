from datetime import date

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.central import EodBar, Instrument, TradingCalendar

# Postgres allows 65535 params per statement; an EodBar row binds 9 params
# (instrument_id, trade_date, open, high, low, close, volume, open_interest,
# source — ingested_at is func.now(), not a bound param). 400 mirrors app/
# repositories/historical_value_repo.py's own conservative batch size —
# same rationale (readability + bounded per-statement lock scope), not a
# param-count ceiling.
_EOD_BAR_UPSERT_BATCH_SIZE = 400
_ROLL_SUFFIXES = ("_I", "_II")

# Per-request note: continuous-future symbol groups are loaded as two rows —
# a "_I" (current/near month) and "_II" (next month) roll series, e.g.
# 'ABB_I'/'ABB_II' — see docs/INCEPTION_DATA.md §2-3. Every default,
# symbol-per-underlying view (View by Date, HMV, Strategy Builder fields)
# uses the "_I" row as canonical per product direction; "_II" stays reachable
# by its exact symbol (get_instrument_by_symbol) but never appears as a
# default row so a grid doesn't show the same underlying twice.
_CANONICAL_SUFFIX = "_I"
_NON_CANONICAL_SUFFIX = "_II"


async def get_canonical_instruments(session: AsyncSession) -> list[Instrument]:
    """One row per underlying symbol group — the '_I' instrument only,
    ordered by underlying_symbol. Falls back to any instrument whose symbol
    doesn't end in the roll suffix at all (a future non-futures instrument
    type wouldn't have one), so this stays correct if instrument_type
    diversifies beyond FUT_CONTINUOUS later.
    """
    stmt = (
        select(Instrument)
        .where(
            Instrument.is_active.is_(True),
            ~Instrument.symbol.like(f"%{_NON_CANONICAL_SUFFIX}"),
        )
        .order_by(Instrument.underlying_symbol, Instrument.symbol)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_instrument_by_symbol(session: AsyncSession, symbol: str) -> Instrument | None:
    result = await session.execute(select(Instrument).where(Instrument.symbol == symbol))
    return result.scalar_one_or_none()


async def get_instruments_by_ids(session: AsyncSession, instrument_ids: list[int]) -> list[Instrument]:
    if not instrument_ids:
        return []
    result = await session.execute(select(Instrument).where(Instrument.id.in_(instrument_ids)))
    return list(result.scalars().all())


async def get_trading_dates_in_range(session: AsyncSession, date_from: date, date_to: date) -> set[date]:
    stmt = select(TradingCalendar.calendar_date).where(
        TradingCalendar.calendar_date >= date_from,
        TradingCalendar.calendar_date <= date_to,
        TradingCalendar.is_trading_day.is_(True),
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def get_bars_for_date(session: AsyncSession, trade_date: date, instrument_ids: list[int] | None = None):
    """Every EodBar row for one date, joined to its instrument's symbol —
    optionally restricted to a specific instrument_id set (e.g. the
    canonical '_I' universe). Uses ix_eod_bar_date.
    """
    stmt = (
        select(
            EodBar.instrument_id,
            Instrument.symbol,
            Instrument.underlying_symbol,
            EodBar.open, EodBar.high, EodBar.low, EodBar.close,
            EodBar.volume, EodBar.open_interest,
        )
        .join(Instrument, Instrument.id == EodBar.instrument_id)
        .where(EodBar.trade_date == trade_date)
    )
    if instrument_ids is not None:
        stmt = stmt.where(EodBar.instrument_id.in_(instrument_ids))
    result = await session.execute(stmt)
    return result.mappings().all()


async def get_bars_in_range(
    session: AsyncSession, date_from: date, date_to: date, symbols: list[str] | None = None,
):
    """Every EodBar row in [date_from, date_to] across one or more
    instruments, joined to each row's symbol — feeds GET /inception/bars,
    the bulk raw-bar feed the desktop client backfills/syncs its local
    Group A/B computation from (see services.inception_service.get_bars).
    Ordered by (trade_date, instrument_id) so a chunked caller can resume
    from the last row's date. Uses ix_eod_bar_date. *symbols*, when given,
    restricts to those exact symbols (e.g. a single-instrument re-sync)
    rather than the default full canonical universe.
    """
    stmt = (
        select(
            Instrument.symbol,
            EodBar.trade_date,
            EodBar.open, EodBar.high, EodBar.low, EodBar.close,
            EodBar.volume, EodBar.open_interest,
            EodBar.avg_rate, EodBar.patp, EodBar.pwatp, EodBar.pmatp,
            EodBar.cwatp, EodBar.cmatp, EodBar.day_to, EodBar.pdto,
            EodBar.cwto, EodBar.pwto,
        )
        .join(Instrument, Instrument.id == EodBar.instrument_id)
        .where(EodBar.trade_date >= date_from, EodBar.trade_date <= date_to)
        .order_by(EodBar.trade_date, EodBar.instrument_id)
    )
    if symbols is not None:
        stmt = stmt.where(Instrument.symbol.in_(symbols))
    result = await session.execute(stmt)
    return result.mappings().all()


async def get_bar_for_date(session: AsyncSession, instrument_id: int, trade_date: date):
    stmt = select(
        EodBar.open, EodBar.high, EodBar.low, EodBar.close, EodBar.volume, EodBar.open_interest,
    ).where(EodBar.instrument_id == instrument_id, EodBar.trade_date == trade_date)
    result = await session.execute(stmt)
    return result.mappings().first()


# ── Vendor ingest (app/services/inception_vendor_sync_service.py — the
# "Fetch from Equal Solution" button, see docs/INCEPTION_DATA.md §4 for the
# ON CONFLICT upsert convention this mirrors from the original one-off
# loader script) ─────────────────────────────────────────────────────────

def _underlying_symbol(symbol: str) -> str | None:
    for suffix in _ROLL_SUFFIXES:
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return None


async def get_latest_trade_date(session: AsyncSession) -> date | None:
    """MAX(trade_date) across every EodBar row — "the last day data is
    available" for inception_vendor_sync_service's incremental-fetch gap
    calculation (fetch from here+1 through today). Uses ix_eod_bar_date."""
    result = await session.execute(select(func.max(EodBar.trade_date)))
    return result.scalar_one_or_none()


async def get_or_create_instruments(session: AsyncSession, symbols: list[str]) -> dict[str, Instrument]:
    """{symbol: Instrument} for every symbol in *symbols* — inserts any that
    don't exist yet (underlying_symbol derived by stripping _I/_II,
    instrument_type 'FUT_CONTINUOUS', matching the loader script's own
    convention) via ON CONFLICT DO NOTHING (so a symbol that already exists
    is left completely untouched, never re-inserted or reset), then selects
    the full row for every symbol — both brand-new and pre-existing ones
    come back with a real id and (for pre-existing ones) their current
    first_traded_date/last_traded_date, which the caller needs to decide
    what NOT to overwrite (see update_instrument_date_bounds)."""
    if not symbols:
        return {}
    rows = [
        {"symbol": sym, "underlying_symbol": _underlying_symbol(sym), "instrument_type": "FUT_CONTINUOUS"}
        for sym in symbols
    ]
    insert_stmt = pg_insert(Instrument).values(rows).on_conflict_do_nothing(index_elements=[Instrument.symbol])
    await session.execute(insert_stmt)

    result = await session.execute(select(Instrument).where(Instrument.symbol.in_(symbols)))
    return {i.symbol: i for i in result.scalars().all()}


async def bulk_upsert_eod_bars(session: AsyncSession, rows: list[dict]) -> int:
    """rows: [{"instrument_id", "trade_date", "open", "high", "low",
    "close", "volume", "open_interest", "source"}, ...]. Same batched
    INSERT ... ON CONFLICT ... DO UPDATE pattern as historical_value_repo.
    bulk_upsert_historical_values — see that function's own docstring for
    why it's batched. Returns the total row count affected (insert or
    update, Postgres counts both as "affected" for ON CONFLICT DO UPDATE)."""
    total = 0
    for start in range(0, len(rows), _EOD_BAR_UPSERT_BATCH_SIZE):
        batch = rows[start : start + _EOD_BAR_UPSERT_BATCH_SIZE]
        if not batch:
            continue
        insert_stmt = pg_insert(EodBar).values(batch)
        insert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[EodBar.instrument_id, EodBar.trade_date],
            set_={
                "open": insert_stmt.excluded.open,
                "high": insert_stmt.excluded.high,
                "low": insert_stmt.excluded.low,
                "close": insert_stmt.excluded.close,
                "volume": insert_stmt.excluded.volume,
                "open_interest": insert_stmt.excluded.open_interest,
                "source": insert_stmt.excluded.source,
                "ingested_at": func.now(),
            },
        )
        result = await session.execute(insert_stmt)
        total += result.rowcount or 0
    return total


async def bulk_upsert_trading_calendar(session: AsyncSession, trade_dates: set[date]) -> None:
    """Marks every date in *trade_dates* as a trading day — ON CONFLICT DO
    NOTHING, matching the loader script's convention (a date's "was this a
    trading day" fact is only ever added, never corrected by a re-sync)."""
    if not trade_dates:
        return
    rows = [{"calendar_date": d, "is_trading_day": True} for d in trade_dates]
    insert_stmt = (
        pg_insert(TradingCalendar).values(rows)
        .on_conflict_do_nothing(index_elements=[TradingCalendar.calendar_date])
    )
    await session.execute(insert_stmt)


async def update_instrument_date_bounds(session: AsyncSession, bounds: list[dict]) -> None:
    """bounds: [{"id", "first_traded_date", "last_traded_date"}, ...] —
    already-resolved final values (inception_vendor_sync_service computes
    these against each instrument's EXISTING bounds first, so a
    previously-loaded instrument's first_traded_date is never moved
    backward or reset — only a brand-new instrument this sync discovered
    gets one set for the first time). One executemany-style UPDATE instead
    of one round trip per instrument."""
    if not bounds:
        return
    stmt = (
        update(Instrument)
        .where(Instrument.id == bindparam("_id"))
        .values(first_traded_date=bindparam("first_traded_date"), last_traded_date=bindparam("last_traded_date"))
    )
    params = [
        {"_id": b["id"], "first_traded_date": b["first_traded_date"], "last_traded_date": b["last_traded_date"]}
        for b in bounds
    ]
    # Executed on the underlying Core connection, not the ORM session — see
    # historical_value_repo.fetch_snapshot_rows's own comment for the same
    # pattern. Required here for a different reason than that one: ANY
    # update(<mapped class>) executed via session.execute() with a list of
    # param dicts always routes through SQLAlchemy's ORM bulk-persistence
    # machinery (orm/persistence.py), regardless of an explicit WHERE
    # bindparam clause — which then insists on its own "bulk UPDATE by
    # Primary Key" convention (a literal "id"-named key per dict) and
    # raises InvalidRequestError otherwise. connection.execute() is a plain
    # Core executemany UPDATE — no ORM interpretation, no identity-map
    # synchronization to opt out of either (nothing after this reads the
    # in-session Instrument objects again).
    connection = await session.connection()
    await connection.execute(stmt, params)
