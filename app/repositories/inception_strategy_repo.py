import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import InceptionStrategy

# Mirrors app/repositories/strategy_repo.py exactly (see InceptionStrategy's
# docstring for why this is a separate table/repo rather than a shared one).


async def fetch_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[InceptionStrategy]:
    stmt = (
        select(InceptionStrategy)
        .where(InceptionStrategy.user_id == user_id)
        .order_by(InceptionStrategy.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def fetch_one_for_user(
    session: AsyncSession, user_id: uuid.UUID, strategy_id: uuid.UUID
) -> InceptionStrategy | None:
    stmt = select(InceptionStrategy).where(
        InceptionStrategy.user_id == user_id, InceptionStrategy.id == strategy_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def apply_fields(
    strategy: InceptionStrategy, name: str, active: bool, category: str, columns: list, row_filter: list
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
) -> InceptionStrategy:
    existing = await fetch_one_for_user(session, user_id, strategy_id)
    if existing is not None:
        apply_fields(existing, name, active, category, columns, row_filter)
        return existing
    created = InceptionStrategy(
        id=strategy_id, user_id=user_id, name=name, active=active,
        category=category, columns=columns, row_filter=row_filter,
    )
    session.add(created)
    return created


async def delete_for_user(session: AsyncSession, user_id: uuid.UUID, strategy_id: uuid.UUID) -> bool:
    stmt = delete(InceptionStrategy).where(
        InceptionStrategy.user_id == user_id, InceptionStrategy.id == strategy_id
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0
