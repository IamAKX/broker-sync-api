import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import SavedStrategy


async def fetch_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[SavedStrategy]:
    stmt = (
        select(SavedStrategy)
        .where(SavedStrategy.user_id == user_id)
        .order_by(SavedStrategy.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def fetch_one_for_user(
    session: AsyncSession, user_id: uuid.UUID, strategy_id: uuid.UUID
) -> SavedStrategy | None:
    stmt = select(SavedStrategy).where(
        SavedStrategy.user_id == user_id, SavedStrategy.id == strategy_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_one_for_user_by_name(
    session: AsyncSession, user_id: uuid.UUID, name: str
) -> SavedStrategy | None:
    stmt = select(SavedStrategy).where(
        SavedStrategy.user_id == user_id, SavedStrategy.name == name
    )
    result = await session.execute(stmt.limit(1))
    return result.scalar_one_or_none()


def apply_fields(
    strategy: SavedStrategy, name: str, active: bool, category: str, columns: list, row_filter: list
) -> None:
    strategy.name = name
    strategy.active = active
    strategy.category = category
    strategy.columns = columns
    strategy.row_filter = row_filter


async def upsert_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    strategy_id: uuid.UUID,
    name: str,
    active: bool,
    category: str,
    columns: list,
    row_filter: list,
) -> SavedStrategy:
    """Create-or-update by id — same idempotent-PUT shape the client's own
    services/strategy_store.py::save_strategy already uses locally."""
    existing = await fetch_one_for_user(session, user_id, strategy_id)
    if existing is not None:
        apply_fields(existing, name, active, category, columns, row_filter)
        return existing
    created = SavedStrategy(
        id=strategy_id, user_id=user_id, name=name, active=active,
        category=category, columns=columns, row_filter=row_filter,
    )
    session.add(created)
    return created


async def delete_for_user(session: AsyncSession, user_id: uuid.UUID, strategy_id: uuid.UUID) -> bool:
    stmt = delete(SavedStrategy).where(
        SavedStrategy.user_id == user_id, SavedStrategy.id == strategy_id
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0
