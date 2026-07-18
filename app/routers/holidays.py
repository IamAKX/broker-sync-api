from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_tenant_db
from app.schemas.holidays import HolidayCreateRequest, HolidayOut, HolidayUpdateRequest
from app.services import holiday_service

router = APIRouter(prefix="/holidays", tags=["holidays"])


@router.get("", response_model=list[HolidayOut])
async def list_holidays_route(
    year: int | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
) -> list[HolidayOut]:
    return await holiday_service.get_holidays(session, year)


@router.post("", response_model=HolidayOut, status_code=status.HTTP_201_CREATED)
async def create_holiday_route(
    payload: HolidayCreateRequest, session: AsyncSession = Depends(get_tenant_db)
) -> HolidayOut:
    return await holiday_service.add_holiday(session, payload.holiday_date, payload.name)


@router.patch("/{holiday_id}", response_model=HolidayOut)
async def update_holiday_route(
    holiday_id: int, payload: HolidayUpdateRequest, session: AsyncSession = Depends(get_tenant_db)
) -> HolidayOut:
    return await holiday_service.edit_holiday(session, holiday_id, payload.holiday_date, payload.name)


@router.delete("/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holiday_route(
    holiday_id: int, session: AsyncSession = Depends(get_tenant_db)
) -> None:
    await holiday_service.remove_holiday(session, holiday_id)
