import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import StrategyNotFoundError
from app.models.tenant import SavedStrategy
from app.repositories.strategy_repo import (
    apply_fields,
    delete_for_user,
    fetch_all_for_user,
    upsert_for_user,
)
from app.schemas.strategies import (
    StrategyImportItem,
    StrategyImportResponse,
    StrategyListResponse,
    StrategyResponse,
)


def _to_response(s: SavedStrategy) -> StrategyResponse:
    return StrategyResponse(
        id=str(s.id), name=s.name, active=s.active, category=s.category,
        columns=s.columns, row_filter=s.row_filter,
    )


async def list_strategies(session: AsyncSession, user_id: str) -> StrategyListResponse:
    rows = await fetch_all_for_user(session, uuid.UUID(user_id))
    return StrategyListResponse(strategies=[_to_response(r) for r in rows])


async def upsert_strategy(
    session: AsyncSession, user_id: str, strategy_id: str,
    name: str, active: bool, category: str, columns: list, row_filter: list,
) -> StrategyResponse:
    result = await upsert_for_user(
        session, uuid.UUID(user_id), uuid.UUID(strategy_id),
        name, active, category, columns, row_filter,
    )
    await session.commit()
    return _to_response(result)


async def delete_strategy(session: AsyncSession, user_id: str, strategy_id: str) -> None:
    deleted = await delete_for_user(session, uuid.UUID(user_id), uuid.UUID(strategy_id))
    if not deleted:
        raise StrategyNotFoundError("Strategy not found")
    await session.commit()


async def import_strategies(
    session: AsyncSession, user_id: str, items: list[StrategyImportItem]
) -> StrategyImportResponse:
    """Merges *items* into the user's strategies by name — an imported
    strategy whose name matches an existing one overwrites that existing
    row's fields in place (its own database id is kept, unlike the
    client-side merge which lets the imported id win, since mutating a
    primary key is unusual/risky; the client treats the server as truth on
    its next sync-down regardless, so this has no user-visible effect); a
    new name is inserted as a new row. Mirrors the client's own
    services/strategy_store.py::import_all semantics."""
    uid = uuid.UUID(user_id)
    existing = await fetch_all_for_user(session, uid)
    by_name = {}
    for s in existing:
        by_name.setdefault(s.name, s)

    overwritten = 0
    added = 0
    for item in items:
        target = by_name.get(item.name)
        if target is not None:
            apply_fields(target, item.name, item.active, item.category, item.columns, item.row_filter)
            overwritten += 1
        else:
            created = SavedStrategy(
                id=uuid.UUID(item.id), user_id=uid, name=item.name, active=item.active,
                category=item.category, columns=item.columns, row_filter=item.row_filter,
            )
            session.add(created)
            by_name[item.name] = created
            added += 1

    await session.commit()
    return StrategyImportResponse(overwritten=overwritten, added=added)
