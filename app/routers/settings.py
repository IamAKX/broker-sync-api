from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache, get_or_set, setting_key
from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.settings import SettingListResponse, SettingResponse, SettingUpdateRequest
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])

# Settings change rarely and are read on every client startup / poll.
_SETTING_TTL = 300


@router.get("", response_model=SettingListResponse)
async def list_settings(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> SettingListResponse:
    """Every settings row this user has, regardless of key. Used only by
    the desktop client's File > Export All Data — a rare, deliberate
    action, not a hot poll path like GET /settings/{key} — so this isn't
    cached the way that one is; keeps the invalidation story simple (no
    second cache entry to keep in sync with every put_setting)."""
    return await settings_service.list_settings(session, current_user.user_id)


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(
    key: str,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    ckey = setting_key(current_user.schema_name, current_user.user_id, key)

    async def _load():
        resp = await settings_service.get_setting(session, current_user.user_id, key)
        return {"key": resp.key, "value": resp.value}

    payload = await get_or_set(ckey, _SETTING_TTL, [], _load)
    return ORJSONResponse(payload)


@router.put("/{key}", response_model=SettingResponse)
async def put_setting(
    key: str,
    payload: SettingUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> SettingResponse:
    result = await settings_service.put_setting(session, current_user.user_id, key, payload.value)
    cache.invalidate_key(setting_key(current_user.schema_name, current_user.user_id, key))
    return result
