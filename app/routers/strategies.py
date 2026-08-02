from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.strategies import (
    StrategyImportRequest,
    StrategyImportResponse,
    StrategyListResponse,
    StrategyResponse,
    StrategyUpsertRequest,
)
from app.services import strategy_service

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=StrategyListResponse)
async def list_strategies(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> StrategyListResponse:
    return await strategy_service.list_strategies(session, current_user.user_id)


@router.put("/import", response_model=StrategyImportResponse)
async def import_strategies(
    payload: StrategyImportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> StrategyImportResponse:
    """Bulk merge-by-name — backs the client's "Import All Strategies" menu
    action. Declared before "/{strategy_id}" so FastAPI doesn't try to parse
    "import" as a strategy id."""
    return await strategy_service.import_strategies(session, current_user.user_id, payload.strategies)


@router.put("/{strategy_id}", response_model=StrategyResponse)
async def upsert_strategy(
    strategy_id: str,
    payload: StrategyUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> StrategyResponse:
    return await strategy_service.upsert_strategy(
        session, current_user.user_id, strategy_id,
        payload.name, payload.active, payload.category, payload.columns, payload.row_filter,
    )


@router.delete("/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    await strategy_service.delete_strategy(session, current_user.user_id, strategy_id)
