from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import OpeningRangeCapture, Stock

# Same batch sizing rationale as historical_value_repo._UPSERT_BATCH_SIZE.
_UPSERT_BATCH_SIZE = 400


class OpeningRangeRow:
    __slots__ = ("trade_date", "stock_id", "high", "low", "window_minutes")

    def __init__(
        self,
        trade_date: date,
        stock_id: int,
        high: float,
        low: float,
        window_minutes: int,
    ):
        self.trade_date = trade_date
        self.stock_id = stock_id
        self.high = high
        self.low = low
        self.window_minutes = window_minutes


async def bulk_upsert_opening_range(session: AsyncSession, rows: list[OpeningRangeRow]) -> int:
    """Upserts (trade_date, stock_id) rows — same batched INSERT ... ON CONFLICT
    shape as historical_value_repo.bulk_upsert_historical_values. Re-running the
    capture for a day that already has a row (e.g. "Run Now" after the scheduled
    fire already went through) overwrites it rather than erroring or duplicating."""
    total = 0
    for start in range(0, len(rows), _UPSERT_BATCH_SIZE):
        batch = rows[start : start + _UPSERT_BATCH_SIZE]
        if not batch:
            continue
        total += await _upsert_batch(session, batch)
    return total


async def _upsert_batch(session: AsyncSession, batch: list[OpeningRangeRow]) -> int:
    rows_data = [
        {
            "trade_date": row.trade_date,
            "stock_id": row.stock_id,
            "high": row.high,
            "low": row.low,
            "window_minutes": row.window_minutes,
        }
        for row in batch
    ]

    insert_stmt = pg_insert(OpeningRangeCapture).values(rows_data)
    insert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[OpeningRangeCapture.trade_date, OpeningRangeCapture.stock_id],
        set_={
            "high": insert_stmt.excluded.high,
            "low": insert_stmt.excluded.low,
            "window_minutes": insert_stmt.excluded.window_minutes,
            "updated_at": func.now(),
        },
    )
    result = await session.execute(insert_stmt)
    return result.rowcount or 0


async def fetch_opening_range_rows(session: AsyncSession, trade_date: date):
    """Executed on the Core connection rather than the ORM session — same
    rationale as lmv_snapshot_repo.fetch_snapshot_rows (avoids per-row ORM
    result overhead on what's otherwise a plain-column select)."""
    stmt = (
        select(
            Stock.symbol,
            Stock.display_name,
            OpeningRangeCapture.high,
            OpeningRangeCapture.low,
            OpeningRangeCapture.window_minutes,
        )
        .join(OpeningRangeCapture.stock)
        .where(OpeningRangeCapture.trade_date == trade_date)
        .order_by(Stock.symbol)
    )
    connection = await session.connection()
    result = await connection.execute(stmt)
    return result.mappings().all()


async def fetch_latest_trade_date(session: AsyncSession) -> date | None:
    stmt = select(func.max(OpeningRangeCapture.trade_date))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_trade_dates_in_range(
    session: AsyncSession, date_from: date, date_to: date
) -> set[date]:
    stmt = (
        select(OpeningRangeCapture.trade_date)
        .distinct()
        .where(OpeningRangeCapture.trade_date >= date_from)
        .where(OpeningRangeCapture.trade_date <= date_to)
    )
    result = await session.execute(stmt)
    return set(result.scalars().all())


async def delete_values_for_date(session: AsyncSession, trade_date: date) -> int:
    """Deletes every OpeningRangeCapture row for one trade_date. The shared
    Stock catalog row is left untouched."""
    stmt = delete(OpeningRangeCapture).where(OpeningRangeCapture.trade_date == trade_date)
    result = await session.execute(stmt)
    return result.rowcount or 0
