from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache, cached_response, lmv_snapshot_tag
from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.historic import (
    DateAvailabilityResponse,
    DeleteDayResponse,
    SnapshotRangeResponse,
    SnapshotResponse,
)
from app.schemas.lmv_snapshot import LmvSnapshotUploadRequest, LmvSnapshotUploadResponse
from app.services import lmv_snapshot_service

router = APIRouter(prefix="/lmv-snapshot", tags=["lmv-snapshot"])

# Read-through cache TTLs. Snapshot data only changes on a daily upload
# (invalidated explicitly below); the TTL is just a safety net that also
# bounds how long a *different* gunicorn worker can serve a stale copy.
_RANGE_TTL = 600     # 10 min
_SNAPSHOT_TTL = 600


@router.post("/daily-upload", response_model=LmvSnapshotUploadResponse)
async def daily_upload(
    payload: LmvSnapshotUploadRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> LmvSnapshotUploadResponse:
    result = await lmv_snapshot_service.upsert_lmv_snapshot(session, payload)
    cache.invalidate_tag(lmv_snapshot_tag(current_user.schema_name))
    return result


@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot(
    request: Request,
    date_param: date | None = Query(default=None, alias="date"),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    schema = current_user.schema_name
    key = f"lmv-snapshot:{schema}:{date_param.isoformat() if date_param else 'latest'}"
    return await cached_response(
        request, key, _SNAPSHOT_TTL, [lmv_snapshot_tag(schema)],
        lambda: lmv_snapshot_service.get_snapshot_payload(session, date_param),
    )


@router.get("/latest", response_model=SnapshotResponse)
async def latest(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    schema = current_user.schema_name
    return await cached_response(
        request, f"lmv-snapshot:{schema}:latest", _SNAPSHOT_TTL, [lmv_snapshot_tag(schema)],
        lambda: lmv_snapshot_service.get_snapshot_payload(session, None),
    )


@router.get("/range", response_model=SnapshotRangeResponse)
async def snapshot_range(
    request: Request,
    days: int = Query(default=20, ge=1, le=90),
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    schema = current_user.schema_name
    return await cached_response(
        request, f"lmv-range:{schema}:{days}", _RANGE_TTL, [lmv_snapshot_tag(schema)],
        lambda: lmv_snapshot_service.get_snapshot_range_payload(session, days),
    )


@router.get("/availability", response_model=DateAvailabilityResponse)
async def availability(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    session: AsyncSession = Depends(get_tenant_db),
) -> DateAvailabilityResponse:
    return await lmv_snapshot_service.get_date_availability(session, date_from, date_to)


@router.delete("/{trade_date}", response_model=DeleteDayResponse)
async def delete_day(
    trade_date: date,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> DeleteDayResponse:
    result = await lmv_snapshot_service.delete_lmv_snapshot_day(session, trade_date)
    cache.invalidate_tag(lmv_snapshot_tag(current_user.schema_name))
    return result
