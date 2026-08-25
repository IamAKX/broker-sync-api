from datetime import date

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


# ── Raw bars (bulk feed the desktop client syncs its local Group A/B
# computation from — see app.services.inception_service.get_bars) ───────────

class BarRow(BaseModel):
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: int | None


class BarsResponse(BaseModel):
    date_from: date
    date_to: date
    rows: list[BarRow]


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


# ── Vendor sync (app/services/inception_vendor_sync_service.py — the
# "Fetch from Equal Solution" button) ─────────────────────────────────────

class VendorSyncRequest(BaseModel):
    """All optional: the desktop's Username/Password/Exchange fields (see
    that repo's screens/inception_settings.py) are sent as typed, so a
    field the user actually edited is honored — omitted/blank falls back
    to this server's own env config (Settings.eqldata_*) for email/
    password, or "NFOFUT" for exchange."""
    email: str | None = None
    password: str | None = None
    exchange: str | None = None


class VendorSyncResponse(BaseModel):
    status: str  # "ok" | "already_up_to_date"
    exchange: str
    date_from: date
    date_to: date
    last_available_before: date | None
    last_available_after: date | None
    instruments_added: int
    bars_written: int
