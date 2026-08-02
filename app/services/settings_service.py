import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.settings_repo import fetch_setting, upsert_setting
from app.schemas.settings import SettingResponse


async def get_setting(session: AsyncSession, user_id: str, key: str) -> SettingResponse:
    """Returns {"key": key, "value": None} when nothing's been saved yet —
    mirrors the client's own config_store.load_json(key, default) shape,
    where "no saved value" is a normal, non-error state the caller supplies
    its own default for, not a 404."""
    row = await fetch_setting(session, uuid.UUID(user_id), key)
    return SettingResponse(key=key, value=row.value if row is not None else None)


async def put_setting(session: AsyncSession, user_id: str, key: str, value) -> SettingResponse:
    await upsert_setting(session, uuid.UUID(user_id), key, value)
    await session.commit()
    return SettingResponse(key=key, value=value)
