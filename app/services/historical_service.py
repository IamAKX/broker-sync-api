from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import InvalidDateRangeError, InvalidTradeDateError, TradeDateIsHolidayError
from app.repositories.historical_value_repo import (
    HistoricalValueRow,
    bulk_upsert_historical_values,
    delete_values_for_date,
    fetch_latest_trade_date,
    fetch_snapshot_rows,
    fetch_timeseries_rows,
    fetch_trade_dates_in_range,
)
from app.repositories.holiday_repo import is_holiday
from app.repositories.metric_repo import bulk_get_or_create_metrics, get_metric_by_name
from app.repositories.stock_repo import bulk_get_or_create_stocks, get_stock_by_symbol
from app.schemas.historic import (
    DateAvailability,
    DateAvailabilityResponse,
    DeleteDayResponse,
    SnapshotResponse,
    StockSnapshot,
    TimeseriesPoint,
    TimeseriesResponse,
    UploadRequest,
    UploadResponse,
)

_MAX_TRADE_DATE_FUTURE_DAYS = 1
_MAX_DATE_RANGE_DAYS = 366


def _infer_data_type(value: float | str | None) -> str:
    return "number" if isinstance(value, (int, float)) else "text"


async def upsert_historical_upload(session: AsyncSession, payload: UploadRequest) -> UploadResponse:
    if payload.trade_date > date.today():
        raise InvalidTradeDateError("trade_date cannot be in the future")
    if await is_holiday(session, payload.trade_date):
        raise TradeDateIsHolidayError(f"{payload.trade_date} is a market holiday — upload rejected")

    stock_pairs = [(row.symbol, row.display_name) for row in payload.rows]
    symbol_to_stock_id = await bulk_get_or_create_stocks(session, stock_pairs)

    metric_types: dict[str, str] = {}
    for row in payload.rows:
        for metric_name, value in row.metrics.items():
            metric_types.setdefault(metric_name, _infer_data_type(value))
    metric_name_to_id = await bulk_get_or_create_metrics(session, metric_types)

    value_rows: list[HistoricalValueRow] = []
    for row in payload.rows:
        stock_id = symbol_to_stock_id[row.symbol]
        for metric_name, value in row.metrics.items():
            metric_id = metric_name_to_id[metric_name]
            is_number = metric_types[metric_name] == "number"
            # A metric's type is inferred from its first-seen value; later rows for the
            # same metric can still carry a non-numeric value (e.g. a blank cell), which
            # must not be passed to the NUMERIC(18,4) column as-is.
            numeric_value = value if isinstance(value, (int, float)) else None
            value_rows.append(
                HistoricalValueRow(
                    trade_date=payload.trade_date,
                    stock_id=stock_id,
                    metric_id=metric_id,
                    value_number=numeric_value if is_number else None,
                    value_text=None if is_number else value,
                )
            )

    values_upserted = await bulk_upsert_historical_values(session, value_rows)
    await session.commit()

    return UploadResponse(
        trade_date=payload.trade_date,
        stocks_upserted=len(symbol_to_stock_id),
        metrics_registered=len(metric_name_to_id),
        values_upserted=values_upserted,
    )


def _pivot_snapshot(trade_date: date, rows) -> SnapshotResponse:
    by_symbol: dict[str, StockSnapshot] = {}
    for row in rows:
        symbol = row["symbol"]
        if symbol not in by_symbol:
            by_symbol[symbol] = StockSnapshot(symbol=symbol, display_name=row["display_name"], metrics={})
        # value_number comes back as Decimal (NUMERIC(18,4) column); cast to float here
        # so it matches the float | str | None schema exactly — leaving it as Decimal
        # makes Pydantic fall back to a warning-emitting coercion path on every single
        # value during response serialization, which dominates latency on wide payloads.
        value = float(row["value_number"]) if row["value_number"] is not None else row["value_text"]
        by_symbol[symbol].metrics[row["metric_name"]] = value

    return SnapshotResponse(trade_date=trade_date, stocks=list(by_symbol.values()))


async def get_snapshot(session: AsyncSession, trade_date: date | None) -> SnapshotResponse:
    resolved_date = trade_date or await fetch_latest_trade_date(session)
    if resolved_date is None:
        return SnapshotResponse(trade_date=date.today(), stocks=[])
    rows = await fetch_snapshot_rows(session, resolved_date)
    return _pivot_snapshot(resolved_date, rows)


async def get_timeseries(
    session: AsyncSession, symbol: str, metric: str, date_from: date | None, date_to: date | None
) -> TimeseriesResponse:
    stock = await get_stock_by_symbol(session, symbol)
    metric_row = await get_metric_by_name(session, metric)
    if stock is None or metric_row is None:
        return TimeseriesResponse(symbol=symbol, metric=metric, points=[])

    rows = await fetch_timeseries_rows(session, stock.id, metric_row.id, date_from, date_to)
    points = [
        TimeseriesPoint(
            trade_date=row["trade_date"],
            value=float(row["value_number"]) if row["value_number"] is not None else row["value_text"],
        )
        for row in rows
    ]
    return TimeseriesResponse(symbol=symbol, metric=metric, points=points)


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


async def delete_historical_day(session: AsyncSession, trade_date: date) -> DeleteDayResponse:
    """Deletes every stock/metric value recorded for trade_date. Idempotent —
    deleting a date with no data is not an error, matching how reads treat
    missing data as a normal case (see get_snapshot).
    """
    values_deleted = await delete_values_for_date(session, trade_date)
    await session.commit()
    return DeleteDayResponse(trade_date=trade_date, values_deleted=values_deleted)
