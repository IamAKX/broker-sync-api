from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_tenant_db
from app.schemas.historic import (
    DateAvailabilityResponse,
    SnapshotResponse,
    TimeseriesResponse,
    UploadRequest,
    UploadResponse,
)
from app.services import historical_service

router = APIRouter(prefix="/historic", tags=["historic"])


@router.post("/daily-upload", response_model=UploadResponse)
async def daily_upload(
    payload: UploadRequest, session: AsyncSession = Depends(get_tenant_db)
) -> UploadResponse:
    return await historical_service.upsert_historical_upload(session, payload)


@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot(
    date_param: date | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_tenant_db),
) -> SnapshotResponse:
    return await historical_service.get_snapshot(session, date_param)


@router.get("/latest", response_model=SnapshotResponse)
async def latest(session: AsyncSession = Depends(get_tenant_db)) -> SnapshotResponse:
    return await historical_service.get_snapshot(session, None)


@router.get("/timeseries", response_model=TimeseriesResponse)
async def timeseries(
    symbol: str,
    metric: str,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_tenant_db),
) -> TimeseriesResponse:
    return await historical_service.get_timeseries(session, symbol, metric, date_from, date_to)


@router.get("/availability", response_model=DateAvailabilityResponse)
async def availability(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    session: AsyncSession = Depends(get_tenant_db),
) -> DateAvailabilityResponse:
    return await historical_service.get_date_availability(session, date_from, date_to)
