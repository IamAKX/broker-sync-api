from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_tenant_db
from app.schemas.historic import DateAvailabilityResponse, DeleteDayResponse
from app.schemas.opening_range import (
    OpeningRangeSnapshotResponse,
    OpeningRangeUploadRequest,
    OpeningRangeUploadResponse,
)
from app.services import opening_range_service

router = APIRouter(prefix="/opening-range", tags=["opening-range"])


@router.post("/daily-upload", response_model=OpeningRangeUploadResponse)
async def daily_upload(
    payload: OpeningRangeUploadRequest, session: AsyncSession = Depends(get_tenant_db)
) -> OpeningRangeUploadResponse:
    return await opening_range_service.upsert_opening_range(session, payload)


@router.get("/snapshot", response_model=OpeningRangeSnapshotResponse)
async def snapshot(
    date_param: date | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_tenant_db),
) -> OpeningRangeSnapshotResponse:
    return await opening_range_service.get_opening_range_snapshot(session, date_param)


@router.get("/latest", response_model=OpeningRangeSnapshotResponse)
async def latest(session: AsyncSession = Depends(get_tenant_db)) -> OpeningRangeSnapshotResponse:
    return await opening_range_service.get_opening_range_snapshot(session, None)


@router.get("/availability", response_model=DateAvailabilityResponse)
async def availability(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    session: AsyncSession = Depends(get_tenant_db),
) -> DateAvailabilityResponse:
    return await opening_range_service.get_date_availability(session, date_from, date_to)


@router.delete("/{trade_date}", response_model=DeleteDayResponse)
async def delete_day(
    trade_date: date, session: AsyncSession = Depends(get_tenant_db)
) -> DeleteDayResponse:
    return await opening_range_service.delete_opening_range_day(session, trade_date)
