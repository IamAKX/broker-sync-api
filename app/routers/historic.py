from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache, cached_response, historic_tag
from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.historic import (
    DateAvailabilityResponse,
    DeleteDayResponse,
    SnapshotRangeResponse,
    SnapshotResponse,
    TimeseriesResponse,
    UploadRequest,
    UploadResponse,
)
from app.services import historical_service

router = APIRouter(prefix="/historic", tags=["historic"])

_RANGE_TTL = 600
_SNAPSHOT_TTL = 600


@router.post("/daily-upload", response_model=UploadResponse)
async def daily_upload(
    payload: UploadRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> UploadResponse:
    result = await historical_service.upsert_historical_upload(session, payload)
    cache.invalidate_tag(historic_tag(current_user.schema_name))
    return result


@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot(
    request: Request,
    date_param: date | None = Query(default=None, alias="date"),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    schema = current_user.schema_name
    key = f"hist-snapshot:{schema}:{date_param.isoformat() if date_param else 'latest'}"
    return await cached_response(
        request, key, _SNAPSHOT_TTL, [historic_tag(schema)],
        lambda: historical_service.get_snapshot_payload(session, date_param),
    )


@router.get("/latest", response_model=SnapshotResponse)
async def latest(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    schema = current_user.schema_name
    return await cached_response(
        request, f"hist-snapshot:{schema}:latest", _SNAPSHOT_TTL, [historic_tag(schema)],
        lambda: historical_service.get_snapshot_payload(session, None),
    )


@router.get("/range", response_model=SnapshotRangeResponse)
async def snapshot_range(
    request: Request,
    days: int = Query(default=20, ge=1, le=120),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    schema = current_user.schema_name
    return await cached_response(
        request, f"hist-range:{schema}:{days}", _RANGE_TTL, [historic_tag(schema)],
        lambda: historical_service.get_snapshot_range_payload(session, days),
    )


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


@router.delete("/{trade_date}", response_model=DeleteDayResponse)
async def delete_day(
    trade_date: date,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> DeleteDayResponse:
    result = await historical_service.delete_historical_day(session, trade_date)
    cache.invalidate_tag(historic_tag(current_user.schema_name))
    return result
