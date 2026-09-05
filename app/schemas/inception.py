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
    # Turnover/average-traded-price figures — populated only where Admin
    # Controls > Inception Sync (app.services.inception_admin_sync_service)
    # has copied them over from LMV's own archive; null everywhere else,
    # same "blank rather than crash" convention every other optional field
    # here already has.
    avg_rate: float | None = None
    patp: float | None = None
    pwatp: float | None = None
    pmatp: float | None = None
    cwatp: float | None = None
    cmatp: float | None = None
    day_to: float | None = None
    pdto: float | None = None
    cwto: float | None = None
    pwto: float | None = None
    # Options-OI/max-pain sheet columns, Market Profile, and Opening Range —
    # same "populated only where Admin Controls > Inception Sync has run"
    # convention as the fields above (OR.High/OR.Low specifically come from
    # a different source table on the backend, hari_dss.OpeningRangeCapture,
    # not LmvDailySnapshot — see app.services.inception_admin_sync_service —
    # but that distinction is invisible here, both are just nullable floats).
    call_strike_highest_oi: float | None = None
    call_strike_with_second_highest_oi: float | None = None
    put_strike_with_second_highest_oi: float | None = None
    today_put_highest_strike: float | None = None
    max_pain: float | None = None
    vah: float | None = None
    poc: float | None = None
    val: float | None = None
    or_high: float | None = None
    or_low: float | None = None


class BarsResponse(BaseModel):
    date_from: date
    date_to: date
    rows: list[BarRow]


# ── Admin Controls > Inception Sync (app.services.inception_admin_sync_
# service — restricted to app.core.deps.require_admin_email) ────────────────

class InceptionAdminSyncMetricResult(BaseModel):
    name: str
    column: str
    candidate_rows: int
    rows_updated: int


class InceptionAdminSyncResponse(BaseModel):
    metrics: list[InceptionAdminSyncMetricResult]
    date_from: date | None
    date_to: date | None
    symbols_matched: int


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
