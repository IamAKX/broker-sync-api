from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import InvalidTradeDateError, TradeDateIsHolidayError
from app.repositories.holiday_repo import is_holiday
from app.repositories.lmv_snapshot_repo import LmvSnapshotRow, bulk_upsert_lmv_snapshot_values
from app.repositories.metric_repo import bulk_get_or_create_metrics
from app.repositories.stock_repo import bulk_get_or_create_stocks
from app.schemas.lmv_snapshot import LmvSnapshotUploadRequest, LmvSnapshotUploadResponse


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
            value_rows.append(
                LmvSnapshotRow(
                    trade_date=payload.trade_date,
                    stock_id=stock_id,
                    metric_id=metric_id,
                    value_number=value if is_number else None,
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
