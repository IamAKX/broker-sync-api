import uuid
from datetime import date, datetime

from sqlalchemy import (
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
