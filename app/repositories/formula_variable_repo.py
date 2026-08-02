import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import FormulaVariable


async def fetch_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[FormulaVariable]:
    stmt = (
        select(FormulaVariable)
        .where(FormulaVariable.user_id == user_id)
        .order_by(FormulaVariable.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def fetch_one_for_user(
    session: AsyncSession, user_id: uuid.UUID, variable_id: uuid.UUID
) -> FormulaVariable | None:
    stmt = select(FormulaVariable).where(
        FormulaVariable.user_id == user_id, FormulaVariable.id == variable_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_for_user(
    session: AsyncSession, user_id: uuid.UUID, variable_id: uuid.UUID, name: str, formula: list
) -> FormulaVariable:
    existing = await fetch_one_for_user(session, user_id, variable_id)
    if existing is not None:
        existing.name = name
        existing.formula = formula
        return existing
    created = FormulaVariable(id=variable_id, user_id=user_id, name=name, formula=formula)
    session.add(created)
    return created


async def delete_for_user(session: AsyncSession, user_id: uuid.UUID, variable_id: uuid.UUID) -> bool:
    stmt = delete(FormulaVariable).where(
        FormulaVariable.user_id == user_id, FormulaVariable.id == variable_id
    )
    result = await session.execute(stmt)
    return (result.rowcount or 0) > 0
