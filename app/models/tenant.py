import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    DECIMAL,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import TenantBase


class Stock(TenantBase):
    __tablename__ = "Stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Metric(TenantBase):
    __tablename__ = "Metric"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    data_type: Mapped[str] = mapped_column(String(10), nullable=False)  # 'number' | 'text'
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class Holiday(TenantBase):
    __tablename__ = "Holiday"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class HistoricalStockValue(TenantBase):
    __tablename__ = "HistoricalStockValue"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("Stock.id"), primary_key=True)
    metric_id: Mapped[int] = mapped_column(Integer, ForeignKey("Metric.id"), primary_key=True)
    value_number: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    stock: Mapped["Stock"] = relationship()
    metric: Mapped["Metric"] = relationship()

    __table_args__ = (
        # Serves timeseries reads: WHERE stock_id = ? AND metric_id = ? ORDER BY trade_date
        Index("ix_hsv_stock_metric_date", "stock_id", "metric_id", "trade_date"),
        # Serves snapshot reads: WHERE trade_date = ?
        Index("ix_hsv_date", "trade_date"),
    )


class OpeningRangeCapture(TenantBase):
    """One row per (trade_date, stock): the highest High and lowest Low the
    client's Live Master View showed for that stock during the opening
    window of that trading day (default first 15 minutes after market open,
    but the window length is client-configured — see window_minutes).

    Deliberately its own table rather than more HistoricalStockValue/
    LmvDailySnapshot rows in the EAV catalog: High/Low-of-window is exactly
    two always-numeric columns per (stock, date), never a varying/user-defined
    set of metrics, so the EAV indirection would add cost (extra Metric
    lookups, wide pivot joins) without buying anything back.
    """

    __tablename__ = "OpeningRangeCapture"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("Stock.id"), primary_key=True)
    high: Mapped[float] = mapped_column(DECIMAL(18, 4), nullable=False)
    low: Mapped[float] = mapped_column(DECIMAL(18, 4), nullable=False)
    # Length of the capture window in minutes (e.g. 15) — client-configured
    # and stored per row since a client could change its setting between
    # trading days; lets a reader interpret older rows correctly even after
    # the configured window length changes going forward.
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    stock: Mapped["Stock"] = relationship()

    __table_args__ = (
        Index("ix_orc_date", "trade_date"),
    )


class LmvDailySnapshot(TenantBase):
    """Full daily archive of the Live Master View grid — every column shown on
    screen (imported broker columns + formula_engine-computed columns), minus
    strategy columns (those are user-defined and computed client-side only,
    never part of this archive).

    Deliberately separate from HistoricalStockValue: that table holds only the
    canonical raw inputs (Open/High/Low/Close/AvgRate/Quantity/DiffPcnt) that
    formula_engine.py recomputes everything else from, and must stay that way
    to avoid derived values drifting from their source of truth. This table is
    a pure audit/archive of what the LMV displayed that day and is never read
    back for recomputation — reuses the same Metric registry as
    HistoricalStockValue so a column name (e.g. "AvgRate") maps to one Metric
    row no matter which table it's valued in.
    """

    __tablename__ = "LmvDailySnapshot"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("Stock.id"), primary_key=True)
    metric_id: Mapped[int] = mapped_column(Integer, ForeignKey("Metric.id"), primary_key=True)
    value_number: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    stock: Mapped["Stock"] = relationship()
    metric: Mapped["Metric"] = relationship()

    __table_args__ = (
        Index("ix_lds_stock_metric_date", "stock_id", "metric_id", "trade_date"),
        Index("ix_lds_date", "trade_date"),
    )


class SavedStrategy(TenantBase):
    """A user's Strategy Builder strategy, synced from the desktop client's
    local strategies.json. Private per user, not shared tenant-wide, even
    though it lives in the tenant schema alongside the tenant's other
    business data — strategies reference tenant-specific concepts (LMV
    column names, the tenant's own stock universe), so they belong here, but
    every query still filters by user_id (see repositories/strategy_repo.py)
    so two users on the same tenant never see each other's strategies.

    columns/row_filter are JSONB rather than normalized tables: both are
    recursive formula-token trees (see the client's services/strategy_store.py
    docstring for the token shape) that are always read/written as one whole
    object and never queried at the SQL level — normalizing them would mean
    a Column table, a Token table with parent/order columns, a FormatRule
    table, purely to reconstruct via joins what's otherwise one row fetch.
    """

    __tablename__ = "SavedStrategy"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Daily")
    columns: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    row_filter: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_saved_strategy_user", "user_id"),
    )


class FormulaVariable(TenantBase):
    """A user's reusable named formula (see the client's
    services/formula_variable_store.py docstring) — same private-per-user,
    JSONB-for-the-token-tree rationale as SavedStrategy above."""

    __tablename__ = "FormulaVariable"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    formula: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_formula_variable_user", "user_id"),
    )


class UserSetting(TenantBase):
    """Generic per-user key -> JSON value store, backing everything the
    desktop client keeps in its local config_data.json EXCEPT theme (theme
    lives on the central User row instead — it's an identity-level
    preference, not tenant business data). Mirrors the client's own
    services/config_store.py load_json(key, default)/save_json(key, value)
    shape exactly, so one GET/PUT pair covers config-editor tables, LMV
    highlight colors, custom strategy categories, and notification trigger
    config without a bespoke table per one of those."""

    __tablename__ = "UserSetting"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class StrategySignal(TenantBase):
    """Durable, per-user record of a Strategy Notifications signal (see the
    client's services/strategy_alerts/engine.py), synced from the desktop
    client whenever a real notification-worthy event fires — entry, a Target
    achieved, or a Stop Loss/Trailing Exit stop-out. A "pending" signal
    (still inside its debounce window, no alert fired yet) is deliberately
    NEVER synced here — same rationale as the client's own local write-through
    policy (services/strategy_alerts/state_store.py's docstring): only
    transitions that actually produced a notification are worth a durable,
    queryable record.

    id is CLIENT-generated (assigned once, at entry — see engine.py's
    _fire_entry) rather than server-generated, so the same signal's later
    Target/Stop-out updates upsert the same row instead of creating a new one
    each time — mirrors SavedStrategy/FormulaVariable's client-owned-id
    pattern above.

    Unlike SavedStrategy, deliberately NOT re-derived from strategy_id via a
    join — strategy_name/direction/sector are captured as of the moment each
    write happens (denormalized), so a signal's historical record stays
    accurate and independently readable even after its originating strategy
    is renamed or deleted (see docs/strategy-notifications.md: "deleting a
    strategy... leaves already-resolved history... alone").

    event_time is a single, always-populated "when" column (resolved_at if
    resolved, else entry_time — mirrors the client's screens/live_alerts.py
    _sort_key) that both the default sort and the Date/Time range filter key
    off of, rather than requiring every caller to COALESCE two columns.

    metrics/risk_reward are JSONB for the same reason as SavedStrategy's
    columns/row_filter: always read/written as one whole object, never
    queried at the SQL level, so normalizing them into their own tables would
    only add join overhead for zero query benefit.
    """

    __tablename__ = "StrategySignal"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    strategy_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(200), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)   # 'BUY' | 'SELL'
    # 'open' | 'stopped_out' | 'all_targets_achieved' — deliberately not
    # 'pending' (see class docstring); "open" covers both a just-fired entry
    # and every tick's running update until it resolves.
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    entry_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    running_high: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    running_low: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    score: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    risk_reward: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    event_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Serves the base list query: WHERE user_id = ? ORDER BY event_time DESC
        Index("ix_strategy_signal_user_event", "user_id", "event_time"),
        # One index per filterable column below — combined with the above via
        # Postgres bitmap AND scans for the combined-filter case; a per-user
        # table stays small enough (hundreds/thousands of rows) that this
        # doesn't need a bespoke composite index per filter combination.
        Index("ix_strategy_signal_strategy", "strategy_id"),
        Index("ix_strategy_signal_symbol", "symbol"),
        Index("ix_strategy_signal_sector", "sector"),
        Index("ix_strategy_signal_status", "status"),
        Index("ix_strategy_signal_direction", "direction"),
    )


class InceptionDerivedValue(TenantBase):
    """Group A/B computed columns (see app.services.inception_columns) for
    the shared, central Instrument/EodBar dataset (app.models.central) — one
    row per (instrument, trade_date, metric), same EAV shape as
    HistoricalStockValue/LmvDailySnapshot above, reusing the same Metric
    registry (a derived column's name, e.g. "52WH" or "DAY UF GUP 1 LOW",
    becomes one Metric row exactly like an LMV column name does).

    Tenant-scoped even though the raw EodBar data it's computed from is
    central/shared — because the Group B gap-tracking columns depend on a
    per-tenant configurable threshold (see UserSetting key
    "inception_gap_threshold_pct"), two tenants with different thresholds
    would legitimately get different values for the same instrument/date, so
    this can't be a shared table the way EodBar is.

    instrument_id deliberately has NO ForeignKey to the central Instrument
    table: tenant sessions run with a schema_translate_map that rewrites
    every unqualified table reference (including a bare "Instrument.id" FK
    target) to the tenant's own schema, where Instrument doesn't exist —
    only `public` (central) has it. Referential integrity here is
    maintained at the application layer (app.services.inception_service only
    ever writes instrument_ids it just read from the central repo) instead.
    """

    __tablename__ = "InceptionDerivedValue"

    instrument_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    metric_id: Mapped[int] = mapped_column(Integer, ForeignKey("Metric.id"), primary_key=True)
    value_number: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    metric: Mapped["Metric"] = relationship()

    __table_args__ = (
        # Serves "every computed column for one instrument, e.g. for the
        # Strategy Builder field-value lookup on one row".
        Index("ix_idv_instrument_date", "instrument_id", "trade_date"),
        # Serves "one metric across time for one instrument" (HMV period grid).
        Index("ix_idv_instrument_metric_date", "instrument_id", "metric_id", "trade_date"),
        # Serves "every instrument's value for one date" (View by Date / snapshot).
        Index("ix_idv_date", "trade_date"),
    )


class InceptionStrategy(TenantBase):
    """A user's Inception Strategy Builder strategy — identical shape to
    SavedStrategy above (see that class's docstring for the columns/
    row_filter token-tree rationale), but a genuinely separate table rather
    than a shared one with a domain flag: this tenant schema has no
    per-tenant Alembic migration path (only create_all() for brand-new
    tables — see app.services.provisioning_service), so retrofitting a
    "domain" column onto the existing live SavedStrategy table isn't safely
    automatable the way adding a whole new table is. This also directly
    satisfies "Inception strategies must stay fully separate from LMV's",
    since the two now literally can't appear in the same query.

    Fields reference Inception's own field universe (raw OPEN/HIGH/LOW/
    CLOSE/VOL/OPENINT + Group A/B derived columns + InceptionFormulaVariable
    names) instead of LMV column names — evaluated server-side by
    app.services.inception_strategy_engine, not by the desktop client.
    """

    __tablename__ = "InceptionStrategy"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="Daily")
    columns: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    row_filter: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_inception_strategy_user", "user_id"),
    )


class InceptionFormulaVariable(TenantBase):
    """Inception's own reusable named formula — same shape/rationale as
    FormulaVariable above, kept as a separate table for the same reason as
    InceptionStrategy (no per-tenant migration path + "fully separate" by
    design)."""

    __tablename__ = "InceptionFormulaVariable"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    formula: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_inception_formula_variable_user", "user_id"),
    )
