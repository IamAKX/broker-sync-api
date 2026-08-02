import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import UserSetting


async def fetch_setting(session: AsyncSession, user_id: uuid.UUID, key: str) -> UserSetting | None:
    stmt = select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_setting(session: AsyncSession, user_id: uuid.UUID, key: str, value) -> None:
    """Single-row upsert — same INSERT ... ON CONFLICT shape as
    opening_range_repo, sized down to one row since a settings write is
    always exactly one (user_id, key)."""
    stmt = pg_insert(UserSetting).values(user_id=user_id, key=key, value=value, updated_at=func.now())
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserSetting.user_id, UserSetting.key],
        set_={"value": stmt.excluded.value, "updated_at": func.now()},
    )
    await session.execute(stmt)
