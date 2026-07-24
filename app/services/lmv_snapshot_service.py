from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import InvalidDateRangeError, InvalidTradeDateError, TradeDateIsHolidayError
from app.repositories.holiday_repo import is_holiday
from app.repositories.lmv_snapshot_repo import (
    LmvSnapshotRow,
    bulk_upsert_lmv_snapshot_values,
    delete_values_for_date,
    fetch_latest_trade_date,
    fetch_snapshot_rows,
    fetch_trade_dates_in_range,
)
from app.repositories.metric_repo import bulk_get_or_create_metrics
from app.repositories.stock_repo import bulk_get_or_create_stocks
from app.schemas.historic import (
    DateAvailability,
    DateAvailabilityResponse,
    DeleteDayResponse,
    SnapshotResponse,
    StockSnapshot,
)
from app.schemas.lmv_snapshot import LmvSnapshotUploadRequest, LmvSnapshotUploadResponse

_MAX_DATE_RANGE_DAYS = 366


def _infer_data_type(value: float | str | None) -> str:
    return "number" if isinstance(value, (int, float)) else "text"


async def upsert_lmv_snapshot(
    session: AsyncSession, payload: LmvSnapshotUploadRequest
) -> LmvSnapshotUploadResponse:
    """Same validation and get-or-create shape as
    historical_service.upsert_historical_upload, writing to the separate
    LmvDailySnapshot archive table instead of HistoricalStockValue. Stock and
    Metric rows are shared across both tables — a symbol or column name
    already registered by a historic upload is reused here, and vice versa.
    """
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

    value_rows: list[LmvSnapshotRow] = []
    for row in payload.rows:
        stock_id = symbol_to_stock_id[row.symbol]
        for metric_name, value in row.metrics.items():
            metric_id = metric_name_to_id[metric_name]
            is_number = metric_types[metric_name] == "number"
            # A metric's type is inferred from its first-seen value; later rows for the
            # same metric can still carry a non-numeric value (e.g. a blank cell in the
            # live grid), which must not be passed to the NUMERIC(18,4) column as-is.
            numeric_value = value if isinstance(value, (int, float)) else None
            value_rows.append(
                LmvSnapshotRow(
                    trade_date=payload.trade_date,
                    stock_id=stock_id,
                    metric_id=metric_id,
                    value_number=numeric_value if is_number else None,
                    value_text=None if is_number else value,
                )
            )

    values_upserted = await bulk_upsert_lmv_snapshot_values(session, value_rows)
    await session.commit()

    return LmvSnapshotUploadResponse(
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
        # value during response serialization, which dominates latency on wide payloads
        # (this table can be ~78 metrics/stock vs historic's ~7).
        value = float(row["value_number"]) if row["value_number"] is not None else row["value_text"]
        by_symbol[symbol].metrics[row["metric_name"]] = value

    return SnapshotResponse(trade_date=trade_date, stocks=list(by_symbol.values()))


async def get_snapshot(session: AsyncSession, trade_date: date | None) -> SnapshotResponse:
    resolved_date = trade_date or await fetch_latest_trade_date(session)
    if resolved_date is None:
        return SnapshotResponse(trade_date=date.today(), stocks=[])
    rows = await fetch_snapshot_rows(session, resolved_date)
    return _pivot_snapshot(resolved_date, rows)


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


async def delete_lmv_snapshot_day(session: AsyncSession, trade_date: date) -> DeleteDayResponse:
    """Deletes every LmvDailySnapshot value for trade_date. Idempotent, same as
    historical_service.delete_historical_day — never touches HistoricalStockValue."""
    values_deleted = await delete_values_for_date(session, trade_date)
    await session.commit()
    return DeleteDayResponse(trade_date=trade_date, values_deleted=values_deleted)
