from datetime import date

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import LmvDailySnapshot

# Same batch sizing rationale as historical_value_repo._UPSERT_BATCH_SIZE.
_UPSERT_BATCH_SIZE = 400


class LmvSnapshotRow:
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


async def bulk_upsert_lmv_snapshot_values(session: AsyncSession, rows: list[LmvSnapshotRow]) -> int:
    """Upserts (trade_date, stock_id, metric_id) rows for the LMV daily snapshot
    archive — same batched INSERT ... ON CONFLICT shape as
    historical_value_repo.bulk_upsert_historical_values."""
    total = 0
    for start in range(0, len(rows), _UPSERT_BATCH_SIZE):
        batch = rows[start : start + _UPSERT_BATCH_SIZE]
        if not batch:
            continue
        total += await _upsert_batch(session, batch)
    return total


async def _upsert_batch(session: AsyncSession, batch: list[LmvSnapshotRow]) -> int:
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

    insert_stmt = pg_insert(LmvDailySnapshot).values(rows_data)
    insert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[LmvDailySnapshot.trade_date, LmvDailySnapshot.stock_id, LmvDailySnapshot.metric_id],
        set_={
            "value_number": insert_stmt.excluded.value_number,
            "value_text": insert_stmt.excluded.value_text,
            "updated_at": func.now(),
        },
    )
    result = await session.execute(insert_stmt)
    return result.rowcount or 0
