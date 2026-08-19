from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import InceptionDerivedValue, Metric

# Same batch-upsert shape as historical_value_repo.py — see that module for
# the params-per-statement rationale. Each row binds 5 params here too
# (instrument_id, trade_date, metric_id, value_number, value_text).
_UPSERT_BATCH_SIZE = 400


class DerivedValueRow:
    __slots__ = ("instrument_id", "trade_date", "metric_id", "value_number", "value_text")

    def __init__(
        self,
        instrument_id: int,
        trade_date: date,
        metric_id: int,
        value_number: float | None,
        value_text: str | None,
    ):
        self.instrument_id = instrument_id
        self.trade_date = trade_date
        self.metric_id = metric_id
        self.value_number = value_number
        self.value_text = value_text


async def bulk_upsert_derived_values(session: AsyncSession, rows: list[DerivedValueRow]) -> int:
    total = 0
    for start in range(0, len(rows), _UPSERT_BATCH_SIZE):
        batch = rows[start : start + _UPSERT_BATCH_SIZE]
        if batch:
            total += await _upsert_batch(session, batch)
    return total


async def _upsert_batch(session: AsyncSession, batch: list[DerivedValueRow]) -> int:
    rows_data = [
        {
            "instrument_id": row.instrument_id,
            "trade_date": row.trade_date,
            "metric_id": row.metric_id,
            "value_number": row.value_number,
            "value_text": row.value_text,
        }
        for row in batch
    ]
    insert_stmt = pg_insert(InceptionDerivedValue).values(rows_data)
    insert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[
            InceptionDerivedValue.instrument_id,
            InceptionDerivedValue.trade_date,
            InceptionDerivedValue.metric_id,
        ],
        set_={
            "value_number": insert_stmt.excluded.value_number,
            "value_text": insert_stmt.excluded.value_text,
            "updated_at": func.now(),
        },
    )
    result = await session.execute(insert_stmt)
    return result.rowcount or 0


async def delete_values_for_instrument(session: AsyncSession, instrument_id: int) -> int:
    """Clears every stored derived value for one instrument before a fresh
    recompute — a code that's no longer produced (e.g. after a spec change)
    shouldn't linger with a stale value, unlike historical_value_repo's
    upload path where omitted metrics are deliberately left untouched (that
    endpoint receives a partial upload; recompute always regenerates the
    full column set)."""
    stmt = delete(InceptionDerivedValue).where(InceptionDerivedValue.instrument_id == instrument_id)
    result = await session.execute(stmt)
    return result.rowcount or 0


async def fetch_snapshot_rows(session: AsyncSession, trade_date: date, instrument_ids: list[int] | None = None):
    """Every derived value for one date, joined to its metric name — feeds
    the wide-pivot snapshot response, same shape as
    historical_value_repo.fetch_snapshot_rows."""
    stmt = (
        select(
            InceptionDerivedValue.instrument_id,
            Metric.name.label("metric_name"),
            InceptionDerivedValue.value_number,
            InceptionDerivedValue.value_text,
        )
        .join(InceptionDerivedValue.metric)
        .where(InceptionDerivedValue.trade_date == trade_date)
    )
    if instrument_ids is not None:
        stmt = stmt.where(InceptionDerivedValue.instrument_id.in_(instrument_ids))
    connection = await session.connection()
    result = await connection.execute(stmt)
    return result.mappings().all()


async def fetch_for_instrument_on_date(session: AsyncSession, instrument_id: int, trade_date: date):
    stmt = (
        select(Metric.name.label("metric_name"), InceptionDerivedValue.value_number, InceptionDerivedValue.value_text)
        .join(InceptionDerivedValue.metric)
        .where(
            InceptionDerivedValue.instrument_id == instrument_id,
            InceptionDerivedValue.trade_date == trade_date,
        )
    )
    connection = await session.connection()
    result = await connection.execute(stmt)
    return result.mappings().all()


async def fetch_period_rows(session: AsyncSession, trade_date: date, instrument_ids: list[int] | None = None):
    """Alias of fetch_snapshot_rows — the HMV period grid picks one
    representative trade_date per period (its last trading day) and reads
    that date's row exactly like a snapshot does."""
    return await fetch_snapshot_rows(session, trade_date, instrument_ids)


async def has_any_values(session: AsyncSession, instrument_id: int) -> bool:
    stmt = select(InceptionDerivedValue.trade_date).where(
        InceptionDerivedValue.instrument_id == instrument_id
    ).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None
