from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.deps import get_tenant_db
from app.repositories.metric_repo import list_metrics
from app.repositories.stock_repo import list_stocks
from app.schemas.data import MetricOut, StockOut

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/metrics", response_model=list[MetricOut])
async def metrics(session: AsyncSession = Depends(get_tenant_db)) -> list[MetricOut]:
    rows = await list_metrics(session)
    return [MetricOut(name=m.name, data_type=m.data_type, is_active=m.is_active) for m in rows]


@router.get("/stocks", response_model=list[StockOut])
async def stocks(session: AsyncSession = Depends(get_tenant_db)) -> list[StockOut]:
    rows = await list_stocks(session)
    return [StockOut(symbol=s.symbol, display_name=s.display_name, is_active=s.is_active) for s in rows]
