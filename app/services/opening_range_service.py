from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    InvalidDateRangeError,
    InvalidOpeningRangeError,
    InvalidTradeDateError,
    TradeDateIsHolidayError,
)
from app.repositories.holiday_repo import is_holiday
from app.repositories.opening_range_repo import (
    OpeningRangeRow,
    bulk_upsert_opening_range,
    delete_values_for_date,
    fetch_latest_trade_date,
    fetch_opening_range_rows,
    fetch_trade_dates_in_range,
)
from app.repositories.stock_repo import bulk_get_or_create_stocks
from app.schemas.historic import DateAvailability, DateAvailabilityResponse, DeleteDayResponse
from app.schemas.opening_range import (
    OpeningRangeSnapshotResponse,
    OpeningRangeStock,
    OpeningRangeUploadRequest,
    OpeningRangeUploadResponse,
)

_MAX_DATE_RANGE_DAYS = 366


async def upsert_opening_range(
    session: AsyncSession, payload: OpeningRangeUploadRequest
) -> OpeningRangeUploadResponse:
    """Same validation and get-or-create shape as
    lmv_snapshot_service.upsert_lmv_snapshot, writing to the dedicated
    OpeningRangeCapture table instead. Stock rows are shared with every other
    table — a symbol already registered by a historic/LMV-snapshot upload is
    reused here, and vice versa.
    """
    if payload.trade_date > date.today():
        raise InvalidTradeDateError("trade_date cannot be in the future")
    if await is_holiday(session, payload.trade_date):
        raise TradeDateIsHolidayError(f"{payload.trade_date} is a market holiday — upload rejected")
    if not payload.rows:
        raise InvalidOpeningRangeError("rows cannot be empty")

    bad_rows = [row.symbol for row in payload.rows if row.high < row.low]
    if bad_rows:
        names = ", ".join(bad_rows[:10])
        suffix = f" (+{len(bad_rows) - 10} more)" if len(bad_rows) > 10 else ""
        raise InvalidOpeningRangeError(f"high is less than low for: {names}{suffix}")

    stock_pairs = [(row.symbol, row.display_name) for row in payload.rows]
    symbol_to_stock_id = await bulk_get_or_create_stocks(session, stock_pairs)

    value_rows = [
        OpeningRangeRow(
            trade_date=payload.trade_date,
            stock_id=symbol_to_stock_id[row.symbol],
            high=row.high,
            low=row.low,
            window_minutes=payload.window_minutes,
        )
        for row in payload.rows
    ]

    values_upserted = await bulk_upsert_opening_range(session, value_rows)
    await session.commit()

    return OpeningRangeUploadResponse(
        trade_date=payload.trade_date,
        stocks_upserted=len(symbol_to_stock_id),
        values_upserted=values_upserted,
    )


def _to_snapshot(trade_date: date, rows) -> OpeningRangeSnapshotResponse:
    stocks = [
        OpeningRangeStock(
            symbol=row["symbol"],
            display_name=row["display_name"],
            # DECIMAL(18,4) columns come back as Decimal — cast to float so
            # the response matches the schema's `float` fields exactly (see
            # lmv_snapshot_service._pivot_snapshot for the same rationale).
            high=float(row["high"]),
            low=float(row["low"]),
            window_minutes=row["window_minutes"],
        )
        for row in rows
    ]
    return OpeningRangeSnapshotResponse(trade_date=trade_date, stocks=stocks)


async def get_opening_range_snapshot(
    session: AsyncSession, trade_date: date | None
) -> OpeningRangeSnapshotResponse:
    resolved_date = trade_date or await fetch_latest_trade_date(session)
    if resolved_date is None:
        return OpeningRangeSnapshotResponse(trade_date=date.today(), stocks=[])
    rows = await fetch_opening_range_rows(session, resolved_date)
    return _to_snapshot(resolved_date, rows)


async def get_date_availability(
    session: AsyncSession, date_from: date, date_to: date
) -> DateAvailabilityResponse:
    if date_from > date_to:
        raise InvalidDateRangeError("date_from must be on or before date_to")
    if (date_to - date_from).days > _MAX_DATE_RANGE_DAYS:
        raise InvalidDateRangeError(f"date range cannot exceed {_MAX_DATE_RANGE_DAYS} days")

    present_dates = await fetch_trade_dates_in_range(session, date_from, date_to)

    dates = []
    current = date_from
    while current <= date_to:
        dates.append(DateAvailability(trade_date=current, has_data=current in present_dates))
        current += timedelta(days=1)

    return DateAvailabilityResponse(date_from=date_from, date_to=date_to, dates=dates)


async def delete_opening_range_day(session: AsyncSession, trade_date: date) -> DeleteDayResponse:
    """Idempotent, same as lmv_snapshot_service.delete_lmv_snapshot_day."""
    values_deleted = await delete_values_for_date(session, trade_date)
    await session.commit()
    return DeleteDayResponse(trade_date=trade_date, values_deleted=values_deleted)
