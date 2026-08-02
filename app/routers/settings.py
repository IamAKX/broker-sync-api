from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.settings import SettingResponse, SettingUpdateRequest
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(
    key: str,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> SettingResponse:
    return await settings_service.get_setting(session, current_user.user_id, key)


@router.put("/{key}", response_model=SettingResponse)
async def put_setting(
    key: str,
    payload: SettingUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> SettingResponse:
    return await settings_service.put_setting(session, current_user.user_id, key, payload.value)
