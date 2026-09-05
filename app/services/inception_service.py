import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    FormulaVariableNotFoundError,
    InvalidDateRangeError,
    StrategyNotFoundError,
)
from app.repositories import (
    inception_formula_variable_repo as ifv_repo,
    inception_strategy_repo as istrat_repo,
    instrument_repo,
)
from app.schemas.inception import (
    BarRow,
    BarsResponse,
    InceptionAvailabilityResponse,
    InceptionDateAvailability,
    InceptionFormulaVariableListResponse,
    InceptionFormulaVariableResponse,
    InceptionStrategyListResponse,
    InceptionStrategyResponse,
    InstrumentListResponse,
    InstrumentSummary,
)

_MAX_DATE_RANGE_DAYS = 366
# Bars is a bulk raw-data feed (the desktop client backfills/syncs its local
# Group A/B computation from this — see docs/plan on the client-side move),
# so it shares availability's tight per-request cap: the client always
# chunks a full historical backfill into ~1-year windows rather than asking
# for the whole 2000-2026 range in one request.
_MAX_BARS_RANGE_DAYS = 366


# ── Availability / instruments (central-only reads) ──────────────────────────

async def get_availability(
    central_session: AsyncSession, date_from: date, date_to: date
) -> InceptionAvailabilityResponse:
    if date_from > date_to:
        raise InvalidDateRangeError("date_from must be on or before date_to")
    if (date_to - date_from).days > _MAX_DATE_RANGE_DAYS:
        raise InvalidDateRangeError(f"date range cannot exceed {_MAX_DATE_RANGE_DAYS} days")

    trading_dates = await instrument_repo.get_trading_dates_in_range(central_session, date_from, date_to)
    dates = []
    current = date_from
    while current <= date_to:
        dates.append(InceptionDateAvailability(trade_date=current, has_data=current in trading_dates))
        current += timedelta(days=1)
    return InceptionAvailabilityResponse(date_from=date_from, date_to=date_to, dates=dates)


async def list_instruments(central_session: AsyncSession) -> InstrumentListResponse:
    rows = await instrument_repo.get_canonical_instruments(central_session)
    return InstrumentListResponse(instruments=[
        InstrumentSummary(
            id=r.id, symbol=r.symbol, underlying_symbol=r.underlying_symbol,
            display_name=r.display_name, first_traded_date=r.first_traded_date,
            last_traded_date=r.last_traded_date,
        )
        for r in rows
    ])


# ── Bars (bulk raw-data feed for the desktop client's local Group A/B
# computation — see docs on the client-side move; this endpoint has no
# knowledge of Group A/B at all, it's a plain OHLCV read) ────────────────────

def _optional_float(value) -> float | None:
    """EodBar's turnover/avg-traded-price columns (see app.services.
    inception_admin_sync_service) are nullable DECIMALs, unlike OHLC's
    always-present ones above — None must pass through as None, not
    float(None)."""
    return float(value) if value is not None else None


async def get_bars(
    central_session: AsyncSession, date_from: date, date_to: date, symbols: list[str] | None = None,
) -> BarsResponse:
    if date_from > date_to:
        raise InvalidDateRangeError("date_from must be on or before date_to")
    if (date_to - date_from).days > _MAX_BARS_RANGE_DAYS:
        raise InvalidDateRangeError(f"date range cannot exceed {_MAX_BARS_RANGE_DAYS} days")

    if not symbols:
        # Default: every canonical ('_I') instrument — same universe
        # list_instruments/Strategy Builder already use, so the client never
        # wastes bandwidth syncing the '_II' roll series it never displays.
        canonical = await instrument_repo.get_canonical_instruments(central_session)
        symbols = [i.symbol for i in canonical]

    rows = await instrument_repo.get_bars_in_range(central_session, date_from, date_to, symbols)
    return BarsResponse(
        date_from=date_from, date_to=date_to,
        rows=[
            BarRow(
                symbol=r["symbol"], trade_date=r["trade_date"],
                open=float(r["open"]), high=float(r["high"]), low=float(r["low"]), close=float(r["close"]),
                volume=r["volume"], open_interest=r["open_interest"],
                avg_rate=_optional_float(r["avg_rate"]), patp=_optional_float(r["patp"]),
                pwatp=_optional_float(r["pwatp"]), pmatp=_optional_float(r["pmatp"]),
                cwatp=_optional_float(r["cwatp"]), cmatp=_optional_float(r["cmatp"]),
                day_to=_optional_float(r["day_to"]), pdto=_optional_float(r["pdto"]),
                cwto=_optional_float(r["cwto"]), pwto=_optional_float(r["pwto"]),
                call_strike_highest_oi=_optional_float(r["call_strike_highest_oi"]),
                call_strike_with_second_highest_oi=_optional_float(r["call_strike_with_second_highest_oi"]),
                put_strike_with_second_highest_oi=_optional_float(r["put_strike_with_second_highest_oi"]),
                today_put_highest_strike=_optional_float(r["today_put_highest_strike"]),
                max_pain=_optional_float(r["max_pain"]),
                vah=_optional_float(r["vah"]), poc=_optional_float(r["poc"]), val=_optional_float(r["val"]),
                or_high=_optional_float(r["or_high"]), or_low=_optional_float(r["or_low"]),
            )
            for r in rows
        ],
    )


# ── Strategy CRUD (mirrors strategy_service.py) ──────────────────────────────

def _strategy_to_response(s) -> InceptionStrategyResponse:
    return InceptionStrategyResponse(
        id=str(s.id), name=s.name, active=s.active, category=s.category,
        columns=s.columns, row_filter=s.row_filter,
    )


async def list_strategies(session: AsyncSession, user_id: str) -> InceptionStrategyListResponse:
    rows = await istrat_repo.fetch_all_for_user(session, uuid.UUID(user_id))
    return InceptionStrategyListResponse(strategies=[_strategy_to_response(r) for r in rows])


async def upsert_strategy(
    session: AsyncSession, user_id: str, strategy_id: str,
    name: str, active: bool, category: str, columns: list, row_filter: list,
) -> InceptionStrategyResponse:
    result = await istrat_repo.upsert_for_user(
        session, uuid.UUID(user_id), uuid.UUID(strategy_id), name, active, category, columns, row_filter,
    )
    await session.commit()
    return _strategy_to_response(result)


async def delete_strategy(session: AsyncSession, user_id: str, strategy_id: str) -> None:
    deleted = await istrat_repo.delete_for_user(session, uuid.UUID(user_id), uuid.UUID(strategy_id))
    if not deleted:
        raise StrategyNotFoundError("Inception strategy not found")
    await session.commit()


# ── Formula variable CRUD (mirrors formula_variable_service.py) ─────────────

def _variable_to_response(v) -> InceptionFormulaVariableResponse:
    return InceptionFormulaVariableResponse(id=str(v.id), name=v.name, formula=v.formula)


async def list_variables(session: AsyncSession, user_id: str) -> InceptionFormulaVariableListResponse:
    rows = await ifv_repo.fetch_all_for_user(session, uuid.UUID(user_id))
    return InceptionFormulaVariableListResponse(variables=[_variable_to_response(r) for r in rows])


async def upsert_variable(
    session: AsyncSession, user_id: str, variable_id: str, name: str, formula: list,
) -> InceptionFormulaVariableResponse:
    result = await ifv_repo.upsert_for_user(session, uuid.UUID(user_id), uuid.UUID(variable_id), name, formula)
    await session.commit()
    return _variable_to_response(result)


async def delete_variable(session: AsyncSession, user_id: str, variable_id: str) -> None:
    deleted = await ifv_repo.delete_for_user(session, uuid.UUID(user_id), uuid.UUID(variable_id))
    if not deleted:
        raise FormulaVariableNotFoundError("Inception formula variable not found")
    await session.commit()
