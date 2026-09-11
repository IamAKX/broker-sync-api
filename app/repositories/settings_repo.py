import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import UserSetting


async def fetch_setting(session: AsyncSession, user_id: uuid.UUID, key: str) -> UserSetting | None:
    stmt = select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[UserSetting]:
    """Every settings row this user has, regardless of key — backs
    GET /settings (list), used by the desktop client's Export All Data:
    the client's own local cache only ever holds whatever keys a screen
    has actually loaded this session (see config_store.py's per-key
    load_json), so it's not a reliable source for "every setting this
    user has" the way this direct query is."""
    stmt = select(UserSetting).where(UserSetting.user_id == user_id).order_by(UserSetting.key)
    result = await session.execute(stmt)
    return list(result.scalars().all())


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
