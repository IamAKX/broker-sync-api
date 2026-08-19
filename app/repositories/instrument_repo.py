from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.central import EodBar, Instrument, TradingCalendar

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


async def get_full_history(session: AsyncSession, instrument_id: int):
    """Every EodBar row for one instrument, oldest first — the formula engine
    walks this in a single forward pass per instrument (see
    services.inception_formula_engine).
    """
    stmt = (
        select(
            EodBar.trade_date, EodBar.open, EodBar.high, EodBar.low, EodBar.close,
            EodBar.volume, EodBar.open_interest,
        )
        .where(EodBar.instrument_id == instrument_id)
        .order_by(EodBar.trade_date)
    )
    result = await session.execute(stmt)
    return result.mappings().all()


async def get_bar_for_date(session: AsyncSession, instrument_id: int, trade_date: date):
    stmt = select(
        EodBar.open, EodBar.high, EodBar.low, EodBar.close, EodBar.volume, EodBar.open_interest,
    ).where(EodBar.instrument_id == instrument_id, EodBar.trade_date == trade_date)
    result = await session.execute(stmt)
    return result.mappings().first()
