from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import HistoricalStockValue, Metric, Stock

# Postgres allows 65535 params per statement; each row binds 5 params (trade_date,
# stock_id, metric_id, value_number, value_text). 400 is a conservative batch size kept
# for readability and to bound per-statement lock scope, not because of a param ceiling.
_UPSERT_BATCH_SIZE = 400


class HistoricalValueRow:
    __slots__ = ("trade_date", "stock_id", "metric_id", "value_number", "value_text")

    def __init__(
        self,
        trade_date: date,
        stock_id: int,
        metric_id: int,
        value_number: float | None,
        value_text: str | None,
    ):
        self.trade_date = trade_date
        self.stock_id = stock_id
        self.metric_id = metric_id
        self.value_number = value_number
        self.value_text = value_text


async def bulk_upsert_historical_values(session: AsyncSession, rows: list[HistoricalValueRow]) -> int:
    """Upserts (trade_date, stock_id, metric_id) rows using a single SQL
    INSERT ... ON CONFLICT per batch, instead of one round-trip per row.

    Values omitted from a re-upload for an existing date are left untouched (no
    deletion), matching BACKEND_ARCHITECTURE.md §2.5.
    """
    total = 0
    for start in range(0, len(rows), _UPSERT_BATCH_SIZE):
        batch = rows[start : start + _UPSERT_BATCH_SIZE]
        if not batch:
            continue
        total += await _upsert_batch(session, batch)
    return total


async def _upsert_batch(session: AsyncSession, batch: list[HistoricalValueRow]) -> int:
    rows_data = [
        {
            "trade_date": row.trade_date,
            "stock_id": row.stock_id,
            "metric_id": row.metric_id,
            "value_number": row.value_number,
            "value_text": row.value_text,
        }
        for row in batch
    ]

    insert_stmt = pg_insert(HistoricalStockValue).values(rows_data)
    insert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[HistoricalStockValue.trade_date, HistoricalStockValue.stock_id, HistoricalStockValue.metric_id],
        set_={
            "value_number": insert_stmt.excluded.value_number,
            "value_text": insert_stmt.excluded.value_text,
            "updated_at": func.now(),
        },
    )
    result = await session.execute(insert_stmt)
    return result.rowcount or 0


async def fetch_snapshot_rows(session: AsyncSession, trade_date: date):
    """Rows for one date, joined to stock/metric names — feeds the wide-pivot
    snapshot response. Uses the ix_hsv_date index.
    """
    stmt = (
        select(
            Stock.symbol,
            Stock.display_name,
            Metric.name.label("metric_name"),
            HistoricalStockValue.value_number,
            HistoricalStockValue.value_text,
        )
        .join(HistoricalStockValue.stock)
        .join(HistoricalStockValue.metric)
        .where(HistoricalStockValue.trade_date == trade_date)
    )
    result = await session.execute(stmt)
    return result.mappings().all()


async def fetch_latest_trade_date(session: AsyncSession) -> date | None:
    stmt = select(func.max(HistoricalStockValue.trade_date))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_trade_dates_in_range(
    session: AsyncSession, date_from: date, date_to: date
) -> set[date]:
    """Distinct trade_dates with any recorded value in [date_from, date_to] —
    uses the ix_hsv_date index.
    """
    stmt = (
        select(HistoricalStockValue.trade_date)
        .distinct()
        .where(HistoricalStockValue.trade_date >= date_from)
        .where(HistoricalStockValue.trade_date <= date_to)
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def fetch_timeseries_rows(
    session: AsyncSession,
    stock_id: int,
    metric_id: int,
    date_from: date | None,
    date_to: date | None,
):
    """Uses ix_hsv_stock_metric_date (stock_id, metric_id, trade_date) — the index is
    ordered to serve exactly this WHERE + ORDER BY shape without a sort operator.
    """
    stmt = select(
        HistoricalStockValue.trade_date,
        HistoricalStockValue.value_number,
        HistoricalStockValue.value_text,
    ).where(HistoricalStockValue.stock_id == stock_id, HistoricalStockValue.metric_id == metric_id)

    if date_from is not None:
        stmt = stmt.where(HistoricalStockValue.trade_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(HistoricalStockValue.trade_date <= date_to)
    stmt = stmt.order_by(HistoricalStockValue.trade_date)

    result = await session.execute(stmt)
    return result.mappings().all()
