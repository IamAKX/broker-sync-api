from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_tenant_db
from app.schemas.historic import DateAvailabilityResponse, DeleteDayResponse, SnapshotResponse
from app.schemas.lmv_snapshot import LmvSnapshotUploadRequest, LmvSnapshotUploadResponse
from app.services import lmv_snapshot_service

router = APIRouter(prefix="/lmv-snapshot", tags=["lmv-snapshot"])


@router.post("/daily-upload", response_model=LmvSnapshotUploadResponse)
async def daily_upload(
    payload: LmvSnapshotUploadRequest, session: AsyncSession = Depends(get_tenant_db)
) -> LmvSnapshotUploadResponse:
    return await lmv_snapshot_service.upsert_lmv_snapshot(session, payload)


@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot(
    date_param: date | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_tenant_db),
) -> ORJSONResponse:
    """Returns a raw ORJSONResponse instead of the SnapshotResponse Pydantic
    model — bypasses FastAPI's response_model validation/serialization, which
    dominated latency on this table's wide payloads (up to ~78 metrics/stock).
    response_model stays declared above purely so /docs still shows the
    correct schema; the actual wire format is unchanged (see
    lmv_snapshot_service._pivot_snapshot).
    """
    data = await lmv_snapshot_service.get_snapshot(session, date_param)
    return ORJSONResponse(content=data)


@router.get("/latest", response_model=SnapshotResponse)
async def latest(session: AsyncSession = Depends(get_tenant_db)) -> ORJSONResponse:
    data = await lmv_snapshot_service.get_snapshot(session, None)
    return ORJSONResponse(content=data)


@router.get("/availability", response_model=DateAvailabilityResponse)
async def availability(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    session: AsyncSession = Depends(get_tenant_db),
) -> DateAvailabilityResponse:
    return await lmv_snapshot_service.get_date_availability(session, date_from, date_to)


@router.delete("/{trade_date}", response_model=DeleteDayResponse)
async def delete_day(
    trade_date: date, session: AsyncSession = Depends(get_tenant_db)
) -> DeleteDayResponse:
    return await lmv_snapshot_service.delete_lmv_snapshot_day(session, trade_date)
