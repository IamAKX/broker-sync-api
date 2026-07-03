from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_tenant_db
from app.repositories.metric_repo import list_metrics
from app.repositories.stock_repo import list_stocks
from app.schemas.data import (
    MetricOut,
    SnapshotResponse,
    StockOut,
    TimeseriesResponse,
    UploadRequest,
    UploadResponse,
)
from app.services import data_service

router = APIRouter(prefix="/data", tags=["data"])


@router.post("/daily-upload", response_model=UploadResponse)
async def daily_upload(
    payload: UploadRequest, session: AsyncSession = Depends(get_tenant_db)
) -> UploadResponse:
    return await data_service.upsert_daily_upload(session, payload)


@router.get("/snapshot", response_model=SnapshotResponse)
async def snapshot(
    date_param: date | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_tenant_db),
) -> SnapshotResponse:
    return await data_service.get_snapshot(session, date_param)


@router.get("/latest", response_model=SnapshotResponse)
async def latest(session: AsyncSession = Depends(get_tenant_db)) -> SnapshotResponse:
    return await data_service.get_snapshot(session, None)


@router.get("/timeseries", response_model=TimeseriesResponse)
async def timeseries(
    symbol: str,
    metric: str,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    session: AsyncSession = Depends(get_tenant_db),
) -> TimeseriesResponse:
    return await data_service.get_timeseries(session, symbol, metric, date_from, date_to)


@router.get("/metrics", response_model=list[MetricOut])
async def metrics(session: AsyncSession = Depends(get_tenant_db)) -> list[MetricOut]:
    rows = await list_metrics(session)
    return [MetricOut(name=m.name, data_type=m.data_type, is_active=m.is_active) for m in rows]


@router.get("/stocks", response_model=list[StockOut])
async def stocks(session: AsyncSession = Depends(get_tenant_db)) -> list[StockOut]:
    rows = await list_stocks(session)
    return [StockOut(symbol=s.symbol, display_name=s.display_name, is_active=s.is_active) for s in rows]
