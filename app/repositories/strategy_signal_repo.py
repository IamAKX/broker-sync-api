import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import StrategySignal


async def fetch_one_for_user(
    session: AsyncSession, user_id: uuid.UUID, signal_id: uuid.UUID
) -> StrategySignal | None:
    stmt = select(StrategySignal).where(
        StrategySignal.user_id == user_id, StrategySignal.id == signal_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_for_user(
    session: AsyncSession, user_id: uuid.UUID, signal_id: uuid.UUID, **fields
) -> StrategySignal:
    """Create-or-update by id — same idempotent-PUT shape as
    strategy_repo.upsert_for_user. The client assigns *signal_id* once, at
    entry, and reuses it for every later Target/Stop-out update to the same
    signal (see app/models/tenant.py's StrategySignal docstring)."""
    existing = await fetch_one_for_user(session, user_id, signal_id)
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        return existing
    created = StrategySignal(id=signal_id, user_id=user_id, **fields)
    session.add(created)
    return created


async def fetch_page_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    strategy_id: uuid.UUID | None = None,
    direction: str | None = None,
    symbol: str | None = None,
    sector: str | None = None,
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[StrategySignal], int]:
    """Every filter argument is combined with AND — omitting one (leaving it
    None) drops it from the WHERE clause entirely rather than matching NULL."""
    conditions = [StrategySignal.user_id == user_id]
    if strategy_id is not None:
        conditions.append(StrategySignal.strategy_id == strategy_id)
    if direction is not None:
        conditions.append(StrategySignal.direction == direction)
    if symbol is not None:
        conditions.append(StrategySignal.symbol == symbol)
    if sector is not None:
        conditions.append(StrategySignal.sector == sector)
    if status is not None:
        conditions.append(StrategySignal.status == status)
    if start_time is not None:
        conditions.append(StrategySignal.event_time >= start_time)
    if end_time is not None:
        conditions.append(StrategySignal.event_time <= end_time)

    count_stmt = select(func.count()).select_from(StrategySignal).where(*conditions)
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        select(StrategySignal)
        .where(*conditions)
        .order_by(StrategySignal.event_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all()), total


async def delete_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = delete(StrategySignal).where(StrategySignal.user_id == user_id)
    result = await session.execute(stmt)
    return result.rowcount or 0
