import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import FormulaVariableNotFoundError
from app.models.tenant import FormulaVariable
from app.repositories.formula_variable_repo import delete_for_user, fetch_all_for_user, upsert_for_user
from app.schemas.formula_variables import FormulaVariableListResponse, FormulaVariableResponse


def _to_response(v: FormulaVariable) -> FormulaVariableResponse:
    return FormulaVariableResponse(id=str(v.id), name=v.name, formula=v.formula)


async def list_variables(session: AsyncSession, user_id: str) -> FormulaVariableListResponse:
    rows = await fetch_all_for_user(session, uuid.UUID(user_id))
    return FormulaVariableListResponse(variables=[_to_response(r) for r in rows])


async def upsert_variable(
    session: AsyncSession, user_id: str, variable_id: str, name: str, formula: list
) -> FormulaVariableResponse:
    result = await upsert_for_user(session, uuid.UUID(user_id), uuid.UUID(variable_id), name, formula)
    await session.commit()
    return _to_response(result)


async def delete_variable(session: AsyncSession, user_id: str, variable_id: str) -> None:
    deleted = await delete_for_user(session, uuid.UUID(user_id), uuid.UUID(variable_id))
    if not deleted:
        raise FormulaVariableNotFoundError("Formula variable not found")
    await session.commit()
