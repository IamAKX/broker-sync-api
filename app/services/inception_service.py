import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    FormulaVariableNotFoundError,
    InvalidDateRangeError,
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
from app.services.inception_formula_engine import compute_group_a, compute_group_b, required_lookback_start

_MAX_DATE_RANGE_DAYS = 366
# HMV's range is only ever used for a set-membership trading-dates lookup and
# an O(1) per-column date comparison (_apply_range_gate) — never enumerated
# day-by-day the way get_availability's response is — so it doesn't need
# that same tight cap. It does need SOME cap (ATH/ATL's required lookback is
# "since inception", and the loaded dataset goes back to 2000 — see
# docs/INCEPTION_DATA.md) or the whole point of the range gate (blank a
# column the selected range can't support) would be moot for those two.
_MAX_HMV_RANGE_DAYS = 20 * 365
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
    # No date_from — View by Date has no range concept, a single day always
    # has its full history behind it, so nothing is ever range-gated here.
    rows = await _build_rows(central_session, tenant_session, trade_date, instruments, instrument_ids)
    return InceptionSnapshotResponse(trade_date=trade_date, rows=rows)


async def _build_rows(
    central_session: AsyncSession,
    tenant_session: AsyncSession,
    trade_date: date,
    instruments: list[Instrument],
    instrument_ids: list[int],
    date_from: date | None = None,
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

    # Bare Group B code (e.g. "DAY UF GUP 1") aliases its HIGH bound — the
    # single number most formulas want — so it's directly selectable as a
    # field, matching inception_columns.column_catalogue's bare-code entry.
    # LOW/DATE stay reachable under their own suffixed names for anyone who
    # wants the full range or the date it opened.
    for bucket in values_by_instrument.values():
        for code in inception_columns.GROUP_B:
            high_key = f"{code} HIGH"
            if high_key in bucket:
                bucket[code] = bucket[high_key]

    # HMV's range gate (see get_hmv) — blank out any column whose formula
    # needs more history than [date_from, trade_date] actually covers.
    if date_from is not None:
        for iid, bucket in values_by_instrument.items():
            _apply_range_gate(bucket, by_id.get(iid), trade_date, date_from)

    # Strategy columns are deliberately NOT computed here — evaluated
    # entirely on the desktop client instead (services/strategy_engine.py's
    # apply_strategies, same engine LMV's own live/historical views use),
    # against exactly the base (raw + Group A/B) values this function
    # returns. See services/inception_strategy_store.py (client repo) for
    # the rationale — this endpoint returns only base values now.
    out = []
    for iid, base_values in values_by_instrument.items():
        instrument = by_id.get(iid)
        if instrument is None:
            continue
        out.append(InstrumentSnapshotRow(
            instrument_id=iid, symbol=instrument.symbol, underlying_symbol=instrument.underlying_symbol,
            values=base_values,
        ))
    return out


def _f(v):
    return float(v) if v is not None else None


def _apply_range_gate(bucket: dict, instrument: Instrument | None, as_of_date: date, date_from: date) -> None:
    """Blanks out any Group A/B value in *bucket* whose formula needs more
    history than [date_from, as_of_date] covers — see get_hmv/module plan
    for the "52WH needs ~52 weeks; a 6-month range leaves it blank" rule.
    Mutates *bucket* in place.
    """
    first_traded = instrument.first_traded_date if instrument is not None else None
    for code in inception_columns.GROUP_A:
        if code not in bucket:
            continue
        required_start = required_lookback_start(code, as_of_date, first_traded)
        if required_start is not None and date_from > required_start:
            bucket[code] = None

    # Group B: no fixed window — blank a gap slot if the specific matched
    # area (its own DATE) opened before the visible range, not based on a
    # formula-level lookback.
    for code in inception_columns.GROUP_B:
        raw_date = bucket.get(f"{code} DATE")
        if not raw_date:
            continue
        try:
            opened_on = date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date
        except ValueError:
            continue
        if opened_on < date_from:
            bucket[code] = None
            bucket[f"{code} LOW"] = None
            bucket[f"{code} HIGH"] = None
            bucket[f"{code} DATE"] = None


# ── HMV (date-range grid) ─────────────────────────────────────────────────────

async def get_hmv(
    central_session: AsyncSession,
    tenant_session: AsyncSession,
    user_id: str,
    date_from: date,
    date_to: date,
    metrics: list[str],
) -> InceptionHmvResponse:
    if date_from > date_to:
        raise InvalidDateRangeError("date_from must be on or before date_to")
    if (date_to - date_from).days > _MAX_HMV_RANGE_DAYS:
        raise InvalidDateRangeError(f"date range cannot exceed {_MAX_HMV_RANGE_DAYS} days")

    trading_dates = await instrument_repo.get_trading_dates_in_range(central_session, date_from, date_to)
    as_of_date = max(trading_dates) if trading_dates else None
    if as_of_date is None:
        return InceptionHmvResponse(date_from=date_from, date_to=date_to, as_of_date=None, rows=[])

    instruments = await instrument_repo.get_canonical_instruments(central_session)
    instrument_ids = [i.id for i in instruments]
    # date_from is passed through so _build_rows can blank any column whose
    # formula needs more history than [date_from, as_of_date] covers — see
    # _apply_range_gate.
    rows = await _build_rows(
        central_session, tenant_session, as_of_date, instruments, instrument_ids, date_from=date_from,
    )

    if metrics:
        wanted = set(metrics)
        rows = [
            InstrumentSnapshotRow(
                instrument_id=r.instrument_id, symbol=r.symbol, underlying_symbol=r.underlying_symbol,
                values={k: v for k, v in r.values.items() if k in wanted},
            )
            for r in rows
        ]
    return InceptionHmvResponse(date_from=date_from, date_to=date_to, as_of_date=as_of_date, rows=rows)


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
