import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ImportIdConflictError, StrategyNotFoundError
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
    services/strategy_store.py::import_all semantics.

    A newly-added row gets a FRESH id (uuid4), never *item.id* — issue
    #49: item.id is whatever id the EXPORTING account's own row had
    (services/strategy_store.py refreshes a strategy's id from its own
    server row on every load, so an exported file always carries real,
    already-claimed primary keys, not opaque client-side identifiers).
    Reusing it verbatim as a new row's primary key here used to raise an
    unhandled IntegrityError — a plain "Internal Server Error" with no
    useful detail — whenever it collided with an existing row's id in
    THIS account's own table (reachable via two users sharing one tenant,
    schema — SavedStrategy's own docstring already anticipates that case
    — or more simply just re-importing a stale export after the
    originally-matching name was renamed/deleted+recreated locally, so a
    later import falls into this "added" branch using an id that's still
    a live PK). Mirrors app/services/tenant_seed_service.py's own
    established convention of minting "a freshly generated id (Python
    uuid4, not the [source]'s own id)... a genuine independent copy, not
    a shared/aliased row" for exactly the same reason. The IntegrityError
    catch below is a defensive fallback for whatever edge case still gets
    past that (kept consistent with app/services/holiday_service.py's own
    try/except IntegrityError -> rollback -> friendly AppError
    convention), not the primary fix."""
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
                id=uuid.uuid4(), user_id=uid, name=item.name, active=item.active,
                category=item.category, columns=item.columns, row_filter=item.row_filter,
            )
            session.add(created)
            by_name[item.name] = created
            added += 1

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ImportIdConflictError(
            "Couldn't finish the import — one of the strategies conflicted "
            "with something already in your account. Try importing again; "
            "if this keeps happening, contact support."
        ) from exc
    return StrategyImportResponse(overwritten=overwritten, added=added)
