import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    FormulaVariableNotFoundError,
    InvalidDateRangeError,
    InvalidPeriodError,
    StrategyNotFoundError,
)
from app.models.central import Instrument
from app.repositories import (
    inception_derived_value_repo as derived_repo,
    inception_formula_variable_repo as ifv_repo,
    inception_strategy_repo as istrat_repo,
    instrument_repo,
)
from app.repositories.inception_derived_value_repo import DerivedValueRow
from app.repositories.metric_repo import bulk_get_or_create_metrics
from app.repositories.settings_repo import fetch_setting, upsert_setting
from app.schemas.inception import (
    ColumnInfoResponse,
    ColumnListResponse,
    CompileCheckResponse,
    InceptionAvailabilityResponse,
    InceptionDateAvailability,
    InceptionFormulaVariableListResponse,
    InceptionFormulaVariableResponse,
    InceptionHmvResponse,
    InceptionSnapshotResponse,
    InceptionStrategyListResponse,
    InceptionStrategyResponse,
    InstrumentListResponse,
    InstrumentSnapshotRow,
    InstrumentSummary,
    RecomputeResponse,
)
from app.services import inception_columns
from app.services.inception_formula_engine import compute_group_a, compute_group_b
from app.services.inception_strategy_engine import apply_strategy_columns, compile_check as _compile_check

_MAX_DATE_RANGE_DAYS = 366
_DEFAULT_GAP_THRESHOLD_PCT = 0.5
_GAP_THRESHOLD_SETTING_KEY = "inception_gap_threshold_pct"


# ── Availability / instruments / columns (central-only reads) ───────────────

async def get_availability(
    central_session: AsyncSession, date_from: date, date_to: date
) -> InceptionAvailabilityResponse:
    if date_from > date_to:
        raise InvalidDateRangeError("date_from must be on or before date_to")
    if (date_to - date_from).days > _MAX_DATE_RANGE_DAYS:
        raise InvalidDateRangeError(f"date range cannot exceed {_MAX_DATE_RANGE_DAYS} days")

    trading_dates = await instrument_repo.get_trading_dates_in_range(central_session, date_from, date_to)
    dates = []
    current = date_from
    while current <= date_to:
        dates.append(InceptionDateAvailability(trade_date=current, has_data=current in trading_dates))
        current += timedelta(days=1)
    return InceptionAvailabilityResponse(date_from=date_from, date_to=date_to, dates=dates)


async def list_instruments(central_session: AsyncSession) -> InstrumentListResponse:
    rows = await instrument_repo.get_canonical_instruments(central_session)
    return InstrumentListResponse(instruments=[
        InstrumentSummary(
            id=r.id, symbol=r.symbol, underlying_symbol=r.underlying_symbol,
            display_name=r.display_name, first_traded_date=r.first_traded_date,
            last_traded_date=r.last_traded_date,
        )
        for r in rows
    ])


def list_columns() -> ColumnListResponse:
    return ColumnListResponse(columns=[
        ColumnInfoResponse(code=c.code, description=c.description, group=c.group, stateful_gap=c.stateful_gap)
        for c in inception_columns.column_catalogue()
    ])


# ── Gap threshold (per-tenant config, stored via the shared UserSetting table) ─

async def get_gap_threshold_pct(tenant_session: AsyncSession, user_id: str) -> float:
    row = await fetch_setting(tenant_session, uuid.UUID(user_id), _GAP_THRESHOLD_SETTING_KEY)
    if row is None or not isinstance(row.value, (int, float)):
        return _DEFAULT_GAP_THRESHOLD_PCT
    return float(row.value)


async def set_gap_threshold_pct(tenant_session: AsyncSession, user_id: str, value: float) -> float:
    await upsert_setting(tenant_session, uuid.UUID(user_id), _GAP_THRESHOLD_SETTING_KEY, value)
    await tenant_session.commit()
    return value


# ── Recompute (writes InceptionDerivedValue) ─────────────────────────────────

async def recompute(
    central_session: AsyncSession, tenant_session: AsyncSession, user_id: str, symbol: str | None,
) -> RecomputeResponse:
    if symbol:
        one = await instrument_repo.get_instrument_by_symbol(central_session, symbol)
        instruments = [one] if one is not None else []
    else:
        instruments = await instrument_repo.get_canonical_instruments(central_session)

    threshold = await get_gap_threshold_pct(tenant_session, user_id)
    metric_names = inception_columns.all_derived_metric_names()
    metric_name_to_id = await bulk_get_or_create_metrics(
        tenant_session, {name: "number" for name in metric_names}
    )

    rows_upserted = 0
    for instrument in instruments:
        raw_bars = await instrument_repo.get_full_history(central_session, instrument.id)
        if not raw_bars:
            continue
        # DECIMAL columns come back as Decimal — cast to float up front so the
        # formula engine's arithmetic never mixes Decimal with the plain
        # floats it also works with (threshold_pct, 100.0 literals, etc.),
        # which raises TypeError rather than coercing.
        bars = [
            {
                "trade_date": b["trade_date"],
                "open": float(b["open"]), "high": float(b["high"]),
                "low": float(b["low"]), "close": float(b["close"]),
                "volume": b["volume"], "open_interest": b["open_interest"],
            }
            for b in raw_bars
        ]
        group_a = compute_group_a(bars)
        group_b = compute_group_b(bars, threshold)

        value_rows = []
        for bar in bars:
            d = bar["trade_date"]
            for code, value in group_a.get(d, {}).items():
                value_rows.append(_derived_row(instrument.id, d, metric_name_to_id[code], value))
            for gap_code, area in group_b.get(d, {}).items():
                low_name, high_name, date_name = inception_columns.gap_metric_names(gap_code)
                if area is None:
                    low, high, opened_on = None, None, None
                else:
                    low, high, opened_on = area
                value_rows.append(_derived_row(instrument.id, d, metric_name_to_id[low_name], low))
                value_rows.append(_derived_row(instrument.id, d, metric_name_to_id[high_name], high))
                value_rows.append(_derived_date_row(instrument.id, d, metric_name_to_id[date_name], opened_on))

        await derived_repo.delete_values_for_instrument(tenant_session, instrument.id)
        rows_upserted += await derived_repo.bulk_upsert_derived_values(tenant_session, value_rows)

    await tenant_session.commit()
    return RecomputeResponse(instruments_processed=len(instruments), rows_upserted=rows_upserted)


def _derived_row(instrument_id: int, trade_date: date, metric_id: int, value):
    numeric = value if isinstance(value, (int, float)) else None
    return DerivedValueRow(instrument_id, trade_date, metric_id, numeric, None)


def _derived_date_row(instrument_id: int, trade_date: date, metric_id: int, value: date | None):
    text = value.isoformat() if value is not None else None
    return DerivedValueRow(instrument_id, trade_date, metric_id, None, text)


# ── Snapshot (View by Date) ──────────────────────────────────────────────────

async def get_snapshot(
    central_session: AsyncSession, tenant_session: AsyncSession, user_id: str, trade_date: date,
) -> InceptionSnapshotResponse:
    instruments = await instrument_repo.get_canonical_instruments(central_session)
    instrument_ids = [i.id for i in instruments]
    rows = await _build_rows(central_session, tenant_session, user_id, trade_date, instruments, instrument_ids)
    return InceptionSnapshotResponse(trade_date=trade_date, rows=rows)


async def _build_rows(
    central_session: AsyncSession,
    tenant_session: AsyncSession,
    user_id: str,
    trade_date: date,
    instruments: list[Instrument],
    instrument_ids: list[int],
) -> list[InstrumentSnapshotRow]:
    by_id = {i.id: i for i in instruments}
    raw_rows = await instrument_repo.get_bars_for_date(central_session, trade_date, instrument_ids)
    derived_rows = await derived_repo.fetch_snapshot_rows(tenant_session, trade_date, instrument_ids)

    values_by_instrument: dict[int, dict] = {iid: {} for iid in instrument_ids}
    for r in raw_rows:
        values_by_instrument.setdefault(r["instrument_id"], {}).update({
            "OPEN": _f(r["open"]), "HIGH": _f(r["high"]), "LOW": _f(r["low"]), "CLOSE": _f(r["close"]),
            "VOL": _f(r["volume"]), "OPENINT": _f(r["open_interest"]),
        })
    for r in derived_rows:
        bucket = values_by_instrument.setdefault(r["instrument_id"], {})
        # Cast Decimal -> float here (not left as Decimal) — same rationale as
        # historical_service._pivot_snapshot: leaving it as Decimal makes
        # Pydantic fall back to a warning-emitting coercion path per value,
        # which dominates latency on a wide payload like this one.
        bucket[r["metric_name"]] = _f(r["value_number"]) if r["value_number"] is not None else r["value_text"]

    strategies, variables_by_name = await _active_strategies_and_variables(tenant_session, user_id)

    out = []
    for iid, base_values in values_by_instrument.items():
        instrument = by_id.get(iid)
        if instrument is None:
            continue
        extra = apply_strategy_columns(strategies, base_values, variables_by_name)
        out.append(InstrumentSnapshotRow(
            instrument_id=iid, symbol=instrument.symbol, underlying_symbol=instrument.underlying_symbol,
            values={**base_values, **extra},
        ))
    return out


def _f(v):
    return float(v) if v is not None else None


async def _active_strategies_and_variables(tenant_session: AsyncSession, user_id: str):
    strategy_rows = await istrat_repo.fetch_all_for_user(tenant_session, uuid.UUID(user_id))
    strategies = [
        {"id": str(s.id), "name": s.name, "columns": s.columns, "row_filter": s.row_filter}
        for s in strategy_rows if s.active
    ]
    variable_rows = await ifv_repo.fetch_all_for_user(tenant_session, uuid.UUID(user_id))
    variables_by_name = {v.name: v.formula for v in variable_rows}
    return strategies, variables_by_name


# ── HMV (period grid) ────────────────────────────────────────────────────────

def _period_bounds(period_type: str, period: str) -> tuple[date, date]:
    try:
        if period_type == "year":
            y = int(period)
            return date(y, 1, 1), date(y, 12, 31)
        if period_type == "financial_year":
            y = int(period)
            return date(y, 4, 1), date(y + 1, 3, 31)
        if period_type == "quarter":
            y_str, q_str = period.split("-Q")
            y, q = int(y_str), int(q_str)
            if q not in (1, 2, 3, 4):
                raise ValueError
            start_month = (q - 1) * 3 + 1
            end_month = start_month + 2
            # last day of end_month, via next-month-minus-one-day
            if end_month == 12:
                end = date(y, 12, 31)
            else:
                end = date(y, end_month + 1, 1) - timedelta(days=1)
            return date(y, start_month, 1), end
        if period_type == "half_year":
            y_str, h_str = period.split("-H")
            y, h = int(y_str), int(h_str)
            if h not in (1, 2):
                raise ValueError
            return (date(y, 1, 1), date(y, 6, 30)) if h == 1 else (date(y, 7, 1), date(y, 12, 31))
    except (ValueError, IndexError) as exc:
        raise InvalidPeriodError(f"Could not parse period '{period}' for period_type '{period_type}'") from exc
    raise InvalidPeriodError(f"Unknown period_type '{period_type}'")


async def get_hmv(
    central_session: AsyncSession,
    tenant_session: AsyncSession,
    user_id: str,
    period_type: str,
    period: str,
    metrics: list[str],
) -> InceptionHmvResponse:
    start, end = _period_bounds(period_type, period)
    trading_dates = await instrument_repo.get_trading_dates_in_range(central_session, start, end)
    as_of_date = max(trading_dates) if trading_dates else None
    if as_of_date is None:
        return InceptionHmvResponse(period_type=period_type, period=period, as_of_date=None, rows=[])

    instruments = await instrument_repo.get_canonical_instruments(central_session)
    instrument_ids = [i.id for i in instruments]
    rows = await _build_rows(central_session, tenant_session, user_id, as_of_date, instruments, instrument_ids)

    if metrics:
        wanted = set(metrics)
        rows = [
            InstrumentSnapshotRow(
                instrument_id=r.instrument_id, symbol=r.symbol, underlying_symbol=r.underlying_symbol,
                values={k: v for k, v in r.values.items() if k in wanted},
            )
            for r in rows
        ]
    return InceptionHmvResponse(period_type=period_type, period=period, as_of_date=as_of_date, rows=rows)


# ── Strategy CRUD (mirrors strategy_service.py) ──────────────────────────────

def _strategy_to_response(s) -> InceptionStrategyResponse:
    return InceptionStrategyResponse(
        id=str(s.id), name=s.name, active=s.active, category=s.category,
        columns=s.columns, row_filter=s.row_filter,
    )


async def list_strategies(session: AsyncSession, user_id: str) -> InceptionStrategyListResponse:
    rows = await istrat_repo.fetch_all_for_user(session, uuid.UUID(user_id))
    return InceptionStrategyListResponse(strategies=[_strategy_to_response(r) for r in rows])


async def upsert_strategy(
    session: AsyncSession, user_id: str, strategy_id: str,
    name: str, active: bool, category: str, columns: list, row_filter: list,
) -> InceptionStrategyResponse:
    result = await istrat_repo.upsert_for_user(
        session, uuid.UUID(user_id), uuid.UUID(strategy_id), name, active, category, columns, row_filter,
    )
    await session.commit()
    return _strategy_to_response(result)


async def delete_strategy(session: AsyncSession, user_id: str, strategy_id: str) -> None:
    deleted = await istrat_repo.delete_for_user(session, uuid.UUID(user_id), uuid.UUID(strategy_id))
    if not deleted:
        raise StrategyNotFoundError("Inception strategy not found")
    await session.commit()


# ── Formula variable CRUD (mirrors formula_variable_service.py) ─────────────

def _variable_to_response(v) -> InceptionFormulaVariableResponse:
    return InceptionFormulaVariableResponse(id=str(v.id), name=v.name, formula=v.formula)


async def list_variables(session: AsyncSession, user_id: str) -> InceptionFormulaVariableListResponse:
    rows = await ifv_repo.fetch_all_for_user(session, uuid.UUID(user_id))
    return InceptionFormulaVariableListResponse(variables=[_variable_to_response(r) for r in rows])


async def upsert_variable(
    session: AsyncSession, user_id: str, variable_id: str, name: str, formula: list,
) -> InceptionFormulaVariableResponse:
    result = await ifv_repo.upsert_for_user(session, uuid.UUID(user_id), uuid.UUID(variable_id), name, formula)
    await session.commit()
    return _variable_to_response(result)


async def delete_variable(session: AsyncSession, user_id: str, variable_id: str) -> None:
    deleted = await ifv_repo.delete_for_user(session, uuid.UUID(user_id), uuid.UUID(variable_id))
    if not deleted:
        raise FormulaVariableNotFoundError("Inception formula variable not found")
    await session.commit()


async def check_formula(session: AsyncSession, user_id: str, formula: list) -> CompileCheckResponse:
    variable_rows = await ifv_repo.fetch_all_for_user(session, uuid.UUID(user_id))
    variables_by_name = {v.name: v.formula for v in variable_rows}
    ok, error = _compile_check(formula, variables_by_name)
    return CompileCheckResponse(ok=ok, error=error)
