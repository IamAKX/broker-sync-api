from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.deps import get_tenant_db
from app.schemas.formula_variables import (
    FormulaVariableListResponse,
    FormulaVariableResponse,
    FormulaVariableUpsertRequest,
)
from app.services import formula_variable_service

router = APIRouter(prefix="/formula-variables", tags=["formula-variables"])


@router.get("", response_model=FormulaVariableListResponse)
async def list_variables(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> FormulaVariableListResponse:
    return await formula_variable_service.list_variables(session, current_user.user_id)


@router.put("/{variable_id}", response_model=FormulaVariableResponse)
async def upsert_variable(
    variable_id: str,
    payload: FormulaVariableUpsertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> FormulaVariableResponse:
    return await formula_variable_service.upsert_variable(
        session, current_user.user_id, variable_id, payload.name, payload.formula
    )


@router.delete("/{variable_id}", status_code=204)
async def delete_variable(
    variable_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_tenant_db),
) -> None:
    await formula_variable_service.delete_variable(session, current_user.user_id, variable_id)
