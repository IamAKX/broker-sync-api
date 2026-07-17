from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Stock


async def bulk_get_or_create_stocks(
    session: AsyncSession, rows: list[tuple[str, str | None]]
) -> dict[str, int]:
    """rows: list of (symbol, display_name). Returns {symbol: stock_id}.

    Single batched INSERT ... ON CONFLICT DO UPDATE for every symbol in the
    payload — creates rows that don't exist yet, and refreshes display_name
    for rows that do. A NULL/omitted display_name in the payload never blanks
    out a previously-stored one (COALESCE keeps the existing value), so a
    corrected display_name from a later upload always wins over a stale one
    from an earlier upload of the same symbol.
    """
    # de-dupe symbols repeated within the same batch, keeping the last value
    deduped: dict[str, str | None] = {}
    for symbol, display_name in rows:
        deduped[symbol] = display_name
    if not deduped:
        return {}

    stmt = pg_insert(Stock).values(
        [{"symbol": symbol, "display_name": display_name} for symbol, display_name in deduped.items()]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Stock.symbol],
        set_={"display_name": func.coalesce(stmt.excluded.display_name, Stock.display_name)},
    ).returning(Stock.id, Stock.symbol)

    result = await session.execute(stmt)
    return {symbol: stock_id for stock_id, symbol in result.all()}


async def list_stocks(session: AsyncSession) -> list[Stock]:
    result = await session.execute(select(Stock).order_by(Stock.symbol))
    return list(result.scalars().all())


async def get_stock_by_symbol(session: AsyncSession, symbol: str) -> Stock | None:
    result = await session.execute(select(Stock).where(Stock.symbol == symbol))
    return result.scalar_one_or_none()
