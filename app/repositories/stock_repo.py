from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Stock


async def bulk_get_or_create_stocks(
    session: AsyncSession, rows: list[tuple[str, str | None]]
) -> dict[str, int]:
    """rows: list of (symbol, display_name). Returns {symbol: stock_id}.

    Single round-trip for the existing lookup, single batched INSERT for anything
    missing — avoids one query per stock on every upload.
    """
    symbols = [symbol for symbol, _ in rows]
    if not symbols:
        return {}

    existing = await session.execute(select(Stock.id, Stock.symbol).where(Stock.symbol.in_(symbols)))
    symbol_to_id = {symbol: stock_id for stock_id, symbol in existing.all()}

    missing = [(symbol, display_name) for symbol, display_name in rows if symbol not in symbol_to_id]
    # de-dupe symbols repeated within the same batch
    seen: set[str] = set()
    deduped_missing = []
    for symbol, display_name in missing:
        if symbol in seen:
            continue
        seen.add(symbol)
        deduped_missing.append((symbol, display_name))

    if deduped_missing:
        stmt = insert(Stock).values(
            [{"symbol": symbol, "display_name": display_name} for symbol, display_name in deduped_missing]
        )
        await session.execute(stmt)
        refreshed = await session.execute(
            select(Stock.id, Stock.symbol).where(Stock.symbol.in_([s for s, _ in deduped_missing]))
        )
        symbol_to_id.update({symbol: stock_id for stock_id, symbol in refreshed.all()})

    return symbol_to_id


async def list_stocks(session: AsyncSession) -> list[Stock]:
    result = await session.execute(select(Stock).order_by(Stock.symbol))
    return list(result.scalars().all())


async def get_stock_by_symbol(session: AsyncSession, symbol: str) -> Stock | None:
    result = await session.execute(select(Stock).where(Stock.symbol == symbol))
    return result.scalar_one_or_none()
