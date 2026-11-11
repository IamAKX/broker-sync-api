"""Executes validated chatbot SQL inside a sandboxed, always-rolled-back transaction.

Isolation model (defence in depth, on top of sql_guard):
  * The model never sees real table names. Each request creates TEMP views
    (stocks, eod_daily, lmv_daily, ...) that point at the caller's own tenant
    schema — taken from the verified JWT, never from the client — and, for
    per-user data (signals, strategies), carry a hard-coded `user_id = <uuid>`
    filter. Nothing else is reachable by name.
  * `lmv_live` is a TEMP table filled, per request, with the Live Master View rows the
    caller's own desktop sent along with the question (empty when LMV isn't running).
    It lives only inside this transaction (ON COMMIT DROP + rollback).
  * After the views exist the transaction is switched to READ ONLY, a
    statement timeout is applied, and the whole thing is rolled back at the
    end, so the views vanish and nothing can ever be written.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.config import settings
from app.services.chat.default_sectors import DEFAULT_SECTOR_STOCKS

_SCHEMA_RE = re.compile(r"^[a-z0-9_]+$")
_CONTEXT_TTL_SECONDS = 300


class SqlExecutionError(RuntimeError):
    """The query was valid but failed in Postgres (timeout, bad column, ...).
    The message is fed back to the model for its one repair attempt."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool  # more rows existed than max_rows


@dataclass(frozen=True)
class DataContext:
    """What data is actually present for this caller right now — injected into
    the prompt so the model can say "latest EOD is 23-Oct" and use real LMV
    metric keys instead of guessing."""

    eod_latest: date | None
    lmv_latest: date | None
    lmv_metric_keys: list[str]
    signals_latest: datetime | None
    futures_latest: date | None
    # Earliest dates, so the model states real coverage instead of guessing it.
    eod_earliest: date | None = None
    lmv_earliest: date | None = None
    futures_earliest: date | None = None
    # "SECTOR (n stocks)" for every sector in stock_sectors, so the model uses real sector names.
    sectors: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveSnapshot:
    """The caller's Live Master View at question time (see schemas.chat.ChatLiveLmv)."""

    running: bool
    reason: str = ""
    warning: str = ""
    as_of: str = ""
    rows: tuple[dict[str, Any], ...] = ()   # {"symbol", "display_name", "sector", "metrics"}

    @property
    def usable(self) -> bool:
        return self.running and bool(self.rows)

    def metric_keys(self) -> list[str]:
        keys: set[str] = set()
        for row in self.rows:
            keys.update(row.get("metrics") or {})
        return sorted(keys)


_LIVE_DDL = "CREATE TEMP TABLE lmv_live (symbol text, display_name text, sector text, metrics jsonb) ON COMMIT DROP"
_LIVE_INSERT = """INSERT INTO lmv_live (symbol, display_name, sector, metrics)
    SELECT e->>'symbol', e->>'display_name', e->>'sector', COALESCE(e->'metrics', '{}'::jsonb)
    FROM jsonb_array_elements(CAST(:payload AS jsonb)) AS e"""


def _sector_select(schema: str, user_id: uuid.UUID) -> str:
    """SELECT symbol, sector: the user's own Config Editor > Sector Stock list (UserSetting
    key 'sector_stock', a JSON array of [sector, stock] pairs), or the built-in default list
    for a user who never saved one. Without this the model has no sector data and invents
    sector membership from memory (it once put AMBUJACEM, a cement stock, in Automobile)."""
    s = f'"{schema}"'
    uid = str(user_id)
    saved = f"""SELECT us.value FROM {s}."UserSetting" us
                WHERE us.user_id = '{uid}' AND us.key = 'sector_stock' AND jsonb_typeof(us.value) = 'array'
                  AND us.value <> '[]'::jsonb"""
    defaults = ", ".join(
        "('{}', '{}')".format(sym.replace("'", "''"), sec.replace("'", "''")) for sec, sym in DEFAULT_SECTOR_STOCKS
    )
    return f"""SELECT DISTINCT upper(btrim(e->>1)) AS symbol, upper(btrim(e->>0)) AS sector
               FROM ({saved}) v,
                    jsonb_array_elements(CASE WHEN jsonb_typeof(v.value) = 'array' THEN v.value ELSE '[]'::jsonb END) AS e
               WHERE jsonb_typeof(e) = 'array' AND btrim(COALESCE(e->>1, '')) <> '' AND btrim(COALESCE(e->>0, '')) <> ''
               UNION ALL
               SELECT d.symbol, d.sector FROM (VALUES {defaults}) AS d(symbol, sector)
               WHERE NOT EXISTS ({saved})"""


def _view_ddl(schema: str, user_id: uuid.UUID) -> list[str]:
    s = f'"{schema}"'
    uid = str(user_id)
    return [
        f"CREATE TEMP VIEW stock_sectors AS {_sector_select(schema, user_id)}",
        f"""CREATE TEMP VIEW stocks AS
            SELECT symbol, display_name AS name FROM {s}."Stock" WHERE is_active""",
        f"""CREATE TEMP VIEW eod_daily AS
            SELECT h.trade_date, st.symbol,
                   MAX(h.value_number) FILTER (WHERE m.name = 'Open')     AS open,
                   MAX(h.value_number) FILTER (WHERE m.name = 'High')     AS high,
                   MAX(h.value_number) FILTER (WHERE m.name = 'Low')      AS low,
                   MAX(h.value_number) FILTER (WHERE m.name = 'Close')    AS close,
                   MAX(h.value_number) FILTER (WHERE m.name = 'AvgRate')  AS avg_rate,
                   MAX(h.value_number) FILTER (WHERE m.name = 'Quantity') AS volume,
                   MAX(h.value_number) FILTER (WHERE m.name = 'DiffPcnt') AS diff_pct
            FROM {s}."HistoricalStockValue" h
            JOIN {s}."Stock" st ON st.id = h.stock_id
            JOIN {s}."Metric" m ON m.id = h.metric_id
            GROUP BY h.trade_date, st.symbol""",
        f"""CREATE TEMP VIEW lmv_daily AS
            SELECT trade_date, symbol, display_name, metrics
            FROM {s}."LmvDailySnapshotWide\"""",
        f"""CREATE TEMP VIEW strategy_signals AS
            SELECT strategy_name, symbol, sector, direction, status, entry_time, entry_price,
                   exit_price, resolved_at, running_high, running_low, score, event_time,
                   risk_reward, metrics
            FROM {s}."StrategySignal" WHERE user_id = '{uid}'""",
        f"""CREATE TEMP VIEW my_strategies AS
            SELECT name, active, category, created_at, updated_at
            FROM {s}."SavedStrategy" WHERE user_id = '{uid}'""",
        """CREATE TEMP VIEW eod_futures_bars AS
            SELECT i.symbol, i.underlying_symbol, i.instrument_type, e.trade_date,
                   e.open, e.high, e.low, e.close, e.volume, e.open_interest, e.avg_rate
            FROM public."EodBar" e JOIN public."Instrument" i ON i.id = e.instrument_id""",
        f"""CREATE TEMP VIEW market_holidays AS
            SELECT holiday_date, name FROM {s}."Holiday\"""",
    ]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        return value
    return str(value)


@asynccontextmanager
async def sandbox(
    session: AsyncSession, *, schema_name: str, user_id: str, live: LiveSnapshot | None = None
) -> AsyncIterator[AsyncSession]:
    if not _SCHEMA_RE.match(schema_name):
        raise SqlExecutionError("Invalid tenant schema.")
    uid = uuid.UUID(user_id)
    await session.rollback()  # start from a clean transaction regardless of the dependency's state
    try:
        for ddl in _view_ddl(schema_name, uid):
            await session.execute(text(ddl))
        # Always present, so a query against it never fails on a missing relation;
        # it simply has no rows unless the desktop sent a running LMV.
        await session.execute(text(_LIVE_DDL))
        if live is not None and live.usable:
            await session.execute(
                text(_LIVE_INSERT), {"payload": json.dumps(list(live.rows), default=str, allow_nan=False)}
            )
        await session.execute(text(f"SET LOCAL statement_timeout = {int(settings.chat_sql_timeout_ms)}"))
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        yield session
    finally:
        await session.rollback()


async def run_query(session: AsyncSession, sql: str, *, max_rows: int) -> QueryResult:
    try:
        result = await session.execute(text(sql))
        columns = list(result.keys())
        fetched = result.fetchmany(max_rows + 1)
    except DBAPIError as exc:
        message = str(getattr(exc, "orig", exc)).strip().splitlines()[0]
        # A failed statement aborts the transaction; further statements in it
        # would all fail, so the caller must re-enter sandbox() to retry.
        raise SqlExecutionError(message) from exc
    truncated = len(fetched) > max_rows
    rows = [[_jsonable(v) for v in row] for row in fetched[:max_rows]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)


async def load_data_context(session: AsyncSession, *, schema_name: str, user_id: str) -> DataContext:
    """Small, cached per (tenant, user) summary of what data currently exists."""
    key = f"chat:ctx:{schema_name}:{user_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    if not _SCHEMA_RE.match(schema_name):
        raise SqlExecutionError("Invalid tenant schema.")
    s = f'"{schema_name}"'
    uid = str(uuid.UUID(user_id))
    await session.rollback()
    try:
        eod = (await session.execute(text(f'SELECT max(trade_date) FROM {s}."HistoricalStockValue"'))).scalar()
        eod_min = (await session.execute(text(f'SELECT min(trade_date) FROM {s}."HistoricalStockValue"'))).scalar()
        lmv = (await session.execute(text(f'SELECT max(trade_date) FROM {s}."LmvDailySnapshotWide"'))).scalar()
        lmv_min = (await session.execute(text(f'SELECT min(trade_date) FROM {s}."LmvDailySnapshotWide"'))).scalar()
        keys: list[str] = []
        if lmv is not None:
            rows = await session.execute(
                text(
                    f'SELECT DISTINCT jsonb_object_keys(metrics) AS k FROM {s}."LmvDailySnapshotWide" '
                    "WHERE trade_date = :d ORDER BY 1"
                ),
                {"d": lmv},
            )
            keys = [r[0] for r in rows]
        sig = (
            await session.execute(
                text(f'SELECT max(event_time) FROM {s}."StrategySignal" WHERE user_id = :u'), {"u": uid}
            )
        ).scalar()
        fut = (await session.execute(text('SELECT max(trade_date) FROM public."EodBar"'))).scalar()
        fut_min = (await session.execute(text('SELECT min(trade_date) FROM public."EodBar"'))).scalar()
        sector_rows = await session.execute(
            text(
                "SELECT sector, count(*) FROM ("
                + _sector_select(schema_name, uuid.UUID(user_id))
                + ") x GROUP BY sector ORDER BY sector"
            )
        )
        sectors = tuple(f"{r[0]} ({r[1]})" for r in sector_rows)
    except DBAPIError as exc:
        raise SqlExecutionError(str(getattr(exc, "orig", exc)).splitlines()[0]) from exc
    finally:
        await session.rollback()
    ctx = DataContext(eod_latest=eod, lmv_latest=lmv, lmv_metric_keys=keys, signals_latest=sig, futures_latest=fut,
                      eod_earliest=eod_min, lmv_earliest=lmv_min, futures_earliest=fut_min, sectors=sectors)
    cache.set(key, ctx, _CONTEXT_TTL_SECONDS)
    return ctx
