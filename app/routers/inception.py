from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_central_db, get_tenant_db
from app.schemas.inception import (
    ColumnListResponse,
    InceptionAvailabilityResponse,
    InceptionFormulaVariableListResponse,
    InceptionFormulaVariableResponse,
    InceptionFormulaVariableUpsertRequest,
    InceptionHmvResponse,
    InceptionSnapshotResponse,
    InceptionStrategyListResponse,
    InceptionStrategyResponse,
    InceptionStrategyUpsertRequest,
    InstrumentListResponse,
    RecomputeRequest,
    RecomputeResponse,
)
from app.services import inception_service

router = APIRouter(prefix="/inception", tags=["inception"])


# ── Central reads (shared across tenants — no gap-threshold dependency) ──────

@router.get("/availability", response_model=InceptionAvailabilityResponse)
async def availability(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    central_session: AsyncSession = Depends(get_central_db),
) -> InceptionAvailabilityResponse:
    return await inception_service.get_availability(central_session, date_from, date_to)


@router.get("/instruments", response_model=InstrumentListResponse)
async def instruments(central_session: AsyncSession = Depends(get_central_db)) -> InstrumentListResponse:
    return await inception_service.list_instruments(central_session)


@router.get("/columns", response_model=ColumnListResponse)
async def columns() -> ColumnListResponse:
    return inception_service.list_columns()


# ── Tenant-scoped reads (derived values + active strategies are per-tenant) ──

@router.get("/snapshot", response_model=InceptionSnapshotResponse)
async def snapshot(
    date_param: date = Query(alias="date"),
    current_user: CurrentUser = Depends(get_current_user),
    central_session: AsyncSession = Depends(get_central_db),
    tenant_session: AsyncSession = Depends(get_tenant_db),
) -> InceptionSnapshotResponse:
    return await inception_service.get_snapshot(
        central_session, tenant_session, current_user.user_id, date_param
    )


@router.get("/hmv", response_model=InceptionHmvResponse)
async def hmv(
    date_from: date = Query(alias="from"),
    date_to: date = Query(alias="to"),
    metrics: list[str] = Query(default_factory=list),
    current_user: CurrentUser = Depends(get_current_user),
    central_session: AsyncSession = Depends(get_central_db),
    tenant_session: AsyncSession = Depends(get_tenant_db),
) -> InceptionHmvResponse:
    return await inception_service.get_hmv(
        central_session, tenant_session, current_user.user_id, date_from, date_to, metrics
    )


@router.post("/recompute", response_model=RecomputeResponse)
async def recompute(
    payload: RecomputeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    central_session: AsyncSession = Depends(get_central_db),
    tenant_session: AsyncSession = Depends(get_tenant_db),
) -> RecomputeResponse:
    return await inception_service.recompute(
        central_session, tenant_session, current_user.user_id, payload.symbol
    )


# ── Strategy CRUD ─────────────────────────────────────────────────────────────

@router.get("/strategies", response_model=InceptionStrategyListResponse)
async def list_strategies(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> InceptionStrategyListResponse:
    return await inception_service.list_strategies(session, current_user.user_id)


@router.put("/strategies/{strategy_id}", response_model=InceptionStrategyResponse)
async def upsert_strategy(
    strategy_id: str,
    payload: InceptionStrategyUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> InceptionStrategyResponse:
    return await inception_service.upsert_strategy(
        session, current_user.user_id, strategy_id,
        payload.name, payload.active, payload.category, payload.columns, payload.row_filter,
    )


@router.delete("/strategies/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    await inception_service.delete_strategy(session, current_user.user_id, strategy_id)


# ── Formula variable CRUD ─────────────────────────────────────────────────────

@router.get("/formula-variables", response_model=InceptionFormulaVariableListResponse)
async def list_variables(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> InceptionFormulaVariableListResponse:
    return await inception_service.list_variables(session, current_user.user_id)


@router.put("/formula-variables/{variable_id}", response_model=InceptionFormulaVariableResponse)
async def upsert_variable(
    variable_id: str,
    payload: InceptionFormulaVariableUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> InceptionFormulaVariableResponse:
    return await inception_service.upsert_variable(
        session, current_user.user_id, variable_id, payload.name, payload.formula
    )


@router.delete("/formula-variables/{variable_id}", status_code=204)
async def delete_variable(
    variable_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    await inception_service.delete_variable(session, current_user.user_id, variable_id)
