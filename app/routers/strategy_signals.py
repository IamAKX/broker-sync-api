from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.strategy_signals import (
    StrategySignalListResponse,
    StrategySignalResponse,
    StrategySignalUpsertRequest,
)
from app.services import strategy_signal_service

router = APIRouter(prefix="/strategy-signals", tags=["strategy-signals"])


@router.put("/{signal_id}", response_model=StrategySignalResponse)
async def upsert_signal(
    signal_id: str,
    payload: StrategySignalUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> StrategySignalResponse:
    return await strategy_signal_service.upsert_signal(
        session, current_user.user_id, signal_id, payload
    )


@router.get("", response_model=StrategySignalListResponse)
async def list_signals(
    strategy_id: str | None = None,
    direction: str | None = None,
    symbol: str | None = None,
    sector: str | None = None,
    status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    # int, not Literal[25, 50, 100] — FastAPI/Pydantic does NOT coerce a
    # query string ("25") into an int Literal member the way it does for a
    # plain int, so a bare Literal[int, ...] annotation here 422s on every
    # value including the objectively valid ones (confirmed live: the client
    # sent page_size=25 exactly, and this 422'd anyway). Validated against
    # the same allowed set manually in the service layer instead — see
    # strategy_signal_service.list_signals.
    page_size: int = 25,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> StrategySignalListResponse:
    return await strategy_signal_service.list_signals(
        session,
        current_user.user_id,
        strategy_id=strategy_id,
        direction=direction,
        symbol=symbol,
        sector=sector,
        status=status,
        start_time=start_time,
        end_time=end_time,
        page=max(1, page),
        page_size=page_size,
    )


@router.delete("", status_code=204)
async def clear_signals(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    await strategy_signal_service.clear_signals(session, current_user.user_id)
