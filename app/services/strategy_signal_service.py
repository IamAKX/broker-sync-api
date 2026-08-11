import uuid
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import InvalidPageSizeError
from app.models.tenant import StrategySignal
from app.repositories.strategy_signal_repo import (
    delete_all_for_user,
    fetch_page_for_user,
    upsert_for_user,
)
from app.schemas.strategy_signals import (
    _ALLOWED_PAGE_SIZES,
    StrategySignalListResponse,
    StrategySignalResponse,
    StrategySignalUpsertRequest,
)


def _f(value) -> float | None:
    return float(value) if value is not None else None


def _to_response(row: StrategySignal) -> StrategySignalResponse:
    return StrategySignalResponse(
        id=str(row.id),
        strategy_id=str(row.strategy_id),
        strategy_name=row.strategy_name,
        symbol=row.symbol,
        sector=row.sector,
        direction=row.direction,
        status=row.status,
        entry_time=row.entry_time,
        entry_price=_f(row.entry_price),
        resolved_at=row.resolved_at,
        running_high=_f(row.running_high),
        running_low=_f(row.running_low),
        score=_f(row.score),
        risk_reward=row.risk_reward,
        metrics=row.metrics or {},
        event_time=row.event_time,
    )


async def upsert_signal(
    session: AsyncSession, user_id: str, signal_id: str, payload: StrategySignalUpsertRequest
) -> StrategySignalResponse:
    # resolved_at wins once set (a resolved signal's "when" is when it
    # resolved, not when it entered) — falls back to entry_time for a still-
    # open signal, since every synced signal has at least fired its entry
    # (see StrategySignal's docstring: "pending" is never synced).
    event_time = payload.resolved_at or payload.entry_time
    result = await upsert_for_user(
        session,
        uuid.UUID(user_id),
        uuid.UUID(signal_id),
        strategy_id=uuid.UUID(payload.strategy_id),
        strategy_name=payload.strategy_name,
        symbol=payload.symbol,
        sector=payload.sector,
        direction=payload.direction,
        status=payload.status,
        entry_time=payload.entry_time,
        entry_price=payload.entry_price,
        resolved_at=payload.resolved_at,
        running_high=payload.running_high,
        running_low=payload.running_low,
        score=payload.score,
        risk_reward=payload.risk_reward,
        metrics=payload.metrics,
        event_time=event_time,
    )
    await session.commit()
    return _to_response(result)


async def list_signals(
    session: AsyncSession,
    user_id: str,
    *,
    strategy_id: str | None = None,
    direction: str | None = None,
    symbol: str | None = None,
    sector: str | None = None,
    status: str | None = None,
    start_time=None,
    end_time=None,
    page: int = 1,
    page_size: int = 25,
) -> StrategySignalListResponse:
    if page_size not in _ALLOWED_PAGE_SIZES:
        raise InvalidPageSizeError(
            f"page_size must be one of {', '.join(str(s) for s in _ALLOWED_PAGE_SIZES)}"
        )
    rows, total = await fetch_page_for_user(
        session,
        uuid.UUID(user_id),
        strategy_id=uuid.UUID(strategy_id) if strategy_id else None,
        direction=direction,
        symbol=symbol,
        sector=sector,
        status=status,
        start_time=start_time,
        end_time=end_time,
        page=page,
        page_size=page_size,
    )
    total_pages = ceil(total / page_size) if total else 0
    return StrategySignalListResponse(
        items=[_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


async def clear_signals(session: AsyncSession, user_id: str) -> None:
    await delete_all_for_user(session, uuid.UUID(user_id))
    await session.commit()
