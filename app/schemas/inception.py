from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class InceptionDateAvailability(BaseModel):
    trade_date: date
    has_data: bool


class InceptionAvailabilityResponse(BaseModel):
    date_from: date
    date_to: date
    dates: list[InceptionDateAvailability]


class InstrumentSummary(BaseModel):
    id: int
    symbol: str
    underlying_symbol: str | None
    display_name: str | None
    first_traded_date: date | None
    last_traded_date: date | None


class InstrumentListResponse(BaseModel):
    instruments: list[InstrumentSummary]


class ColumnInfoResponse(BaseModel):
    code: str
    description: str
    group: str  # 'raw' | 'derived'
    stateful_gap: bool


class ColumnListResponse(BaseModel):
    columns: list[ColumnInfoResponse]


class InstrumentSnapshotRow(BaseModel):
    instrument_id: int
    symbol: str
    underlying_symbol: str | None
    values: dict[str, Any]  # field/column name -> number | str | None


class InceptionSnapshotResponse(BaseModel):
    trade_date: date
    rows: list[InstrumentSnapshotRow]


class InceptionHmvResponse(BaseModel):
    date_from: date
    date_to: date
    as_of_date: date | None
    rows: list[InstrumentSnapshotRow]


class RecomputeRequest(BaseModel):
    symbol: str | None = None  # None = every canonical instrument


class RecomputeResponse(BaseModel):
    instruments_processed: int
    rows_upserted: int


# ── Strategy CRUD (mirrors app/schemas/strategies.py) ────────────────────────

class InceptionStrategyUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    category: str = Field(default="Daily", min_length=1, max_length=100)
    columns: list = Field(default_factory=list)
    row_filter: list = Field(default_factory=list)


class InceptionStrategyResponse(BaseModel):
    id: str
    name: str
    active: bool
    category: str
    columns: list
    row_filter: list


class InceptionStrategyListResponse(BaseModel):
    strategies: list[InceptionStrategyResponse]


# ── Formula variable CRUD (mirrors app/schemas/formula_variables.py) ─────────

class InceptionFormulaVariableUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    formula: list = Field(default_factory=list)


class InceptionFormulaVariableResponse(BaseModel):
    id: str
    name: str
    formula: list


class InceptionFormulaVariableListResponse(BaseModel):
    variables: list[InceptionFormulaVariableResponse]
