import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import FormulaVariableNotFoundError
from app.models.tenant import FormulaVariable
from app.repositories.formula_variable_repo import delete_for_user, fetch_all_for_user, upsert_for_user
from app.schemas.formula_variables import (
    FormulaVariableImportItem,
    FormulaVariableImportResponse,
    FormulaVariableListResponse,
    FormulaVariableResponse,
)


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


async def import_variables(
    session: AsyncSession, user_id: str, items: list[FormulaVariableImportItem]
) -> FormulaVariableImportResponse:
    """Merges *items* into the user's formula variables by name — mirrors
    strategy_service.import_strategies' exact semantics (see its own
    docstring: an existing row's own id is kept on overwrite, only a
    genuinely new name gets the imported id). Backs the client's combined
    Export/Import All Data feature (app_window.py), where LMV formula
    variables previously had no bulk import at all."""
    uid = uuid.UUID(user_id)
    existing = await fetch_all_for_user(session, uid)
    by_name = {}
    for v in existing:
        by_name.setdefault(v.name, v)

    overwritten = 0
    added = 0
    for item in items:
        target = by_name.get(item.name)
        if target is not None:
            target.formula = item.formula
            overwritten += 1
        else:
            created = FormulaVariable(id=uuid.UUID(item.id), user_id=uid, name=item.name, formula=item.formula)
            session.add(created)
            by_name[item.name] = created
            added += 1

    await session.commit()
    return FormulaVariableImportResponse(overwritten=overwritten, added=added)
