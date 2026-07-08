from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import HistoricalStockValue, Metric, Stock

# Postgres allows 65535 params per statement; each row binds 5 params (trade_date,
# stock_id, metric_id, value_number, value_text). 400 is a conservative batch size kept
# for readability and to bound per-statement lock scope, not because of a param ceiling.
_UPSERT_BATCH_SIZE = 400


class HistoricalValueRow:
    __slots__ = ("trade_date", "stock_id", "metric_id", "value_number", "value_text")

    def __init__(
        self,
        trade_date: date,
        stock_id: int,
        metric_id: int,
        value_number: float | None,
        value_text: str | None,
    ):
        self.trade_date = trade_date
        self.stock_id = stock_id
        self.metric_id = metric_id
        self.value_number = value_number
        self.value_text = value_text


async def bulk_upsert_historical_values(session: AsyncSession, rows: list[HistoricalValueRow]) -> int:
    """Upserts (trade_date, stock_id, metric_id) rows using a single SQL
    INSERT ... ON CONFLICT per batch, instead of one round-trip per row.

    Values omitted from a re-upload for an existing date are left untouched (no
    deletion), matching BACKEND_ARCHITECTURE.md §2.5.
    """
    total = 0
    for start in range(0, len(rows), _UPSERT_BATCH_SIZE):
        batch = rows[start : start + _UPSERT_BATCH_SIZE]
        if not batch:
            continue
        total += await _upsert_batch(session, batch)
    return total


async def _upsert_batch(session: AsyncSession, batch: list[HistoricalValueRow]) -> int:
    values_clauses = []
    params: dict[str, object] = {}
    for i, row in enumerate(batch):
        values_clauses.append(f"(:trade_date_{i}, :stock_id_{i}, :metric_id_{i}, :value_number_{i}, :value_text_{i})")
        params[f"trade_date_{i}"] = row.trade_date
        params[f"stock_id_{i}"] = row.stock_id
        params[f"metric_id_{i}"] = row.metric_id
        params[f"value_number_{i}"] = row.value_number
        params[f"value_text_{i}"] = row.value_text

    hsv_table = HistoricalStockValue.__table__.name

    sql = f"""
        INSERT INTO "{hsv_table}" (trade_date, stock_id, metric_id, value_number, value_text)
        VALUES {", ".join(values_clauses)}
        ON CONFLICT (trade_date, stock_id, metric_id) DO UPDATE SET
            value_number = EXCLUDED.value_number,
            value_text = EXCLUDED.value_text,
            updated_at = now();
    """
    result = await session.execute(text(sql), params)
    return result.rowcount or 0


async def fetch_snapshot_rows(session: AsyncSession, trade_date: date):
    """Rows for one date, joined to stock/metric names — feeds the wide-pivot
    snapshot response. Uses the ix_hsv_date index.
    """
    sql = text(
        f"""
        SELECT s.symbol, s.display_name, m.name AS metric_name, hsv.value_number, hsv.value_text
        FROM "{HistoricalStockValue.__table__.name}" hsv
        JOIN "{Stock.__table__.name}" s ON s.id = hsv.stock_id
        JOIN "{Metric.__table__.name}" m ON m.id = hsv.metric_id
        WHERE hsv.trade_date = :trade_date
        """
    )
    result = await session.execute(sql, {"trade_date": trade_date})
    return result.mappings().all()


async def fetch_latest_trade_date(session: AsyncSession) -> date | None:
    sql = text(f'SELECT MAX(trade_date) AS latest FROM "{HistoricalStockValue.__table__.name}"')
    result = await session.execute(sql)
    return result.scalar_one_or_none()


async def fetch_trade_dates_in_range(
    session: AsyncSession, date_from: date, date_to: date
) -> set[date]:
    """Distinct trade_dates with any recorded value in [date_from, date_to] —
    uses the ix_hsv_date index.
    """
    sql = text(
        f"""
        SELECT DISTINCT trade_date
        FROM "{HistoricalStockValue.__table__.name}"
        WHERE trade_date >= :date_from AND trade_date <= :date_to
        """
    )
    result = await session.execute(sql, {"date_from": date_from, "date_to": date_to})
    return set(result.scalars().all())


async def fetch_timeseries_rows(
    session: AsyncSession,
    stock_id: int,
    metric_id: int,
    date_from: date | None,
    date_to: date | None,
):
    """Uses ix_hsv_stock_metric_date (stock_id, metric_id, trade_date) — the index is
    ordered to serve exactly this WHERE + ORDER BY shape without a sort operator.
    """
    sql = f"""
        SELECT trade_date, value_number, value_text
        FROM "{HistoricalStockValue.__table__.name}"
        WHERE stock_id = :stock_id AND metric_id = :metric_id
    """
    params: dict[str, object] = {"stock_id": stock_id, "metric_id": metric_id}
    if date_from is not None:
        sql += " AND trade_date >= :date_from"
        params["date_from"] = date_from
    if date_to is not None:
        sql += " AND trade_date <= :date_to"
        params["date_to"] = date_to
    sql += " ORDER BY trade_date"

    result = await session.execute(text(sql), params)
    return result.mappings().all()
