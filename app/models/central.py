import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Date, DateTime, DECIMAL, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import CentralBase


class Tenant(CentralBase):
    __tablename__ = "Tenant"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(CentralBase):
    __tablename__ = "User"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("Tenant.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'owner' | 'member'
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    # current_login_at is the active session's login time; last_login_at is the one
    # before that. Shifted on every login (current -> last, now -> current) so
    # last_login_at stays frozen at "the previous session" for the whole session —
    # never the login that's happening right now.
    current_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Personal UI preference, synced across every device the user logs into —
    # unlike SavedStrategy/FormulaVariable/UserSetting (tenant schema, since
    # those reference tenant-specific business data), theme is pure identity-
    # level preference with no tenant dependency, so it lives here instead.
    theme: Mapped[str] = mapped_column(String(10), nullable=False, server_default="light")

    tenant: Mapped["Tenant"] = relationship(back_populates="users")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(back_populates="user")


class RefreshToken(CentralBase):
    __tablename__ = "RefreshToken"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("User.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class Instrument(CentralBase):
    """Common/inception market data: one row per tradable symbol, shared across every
    tenant — not duplicated per tenant, since a symbol's identity is the same fact for
    everyone. Lives in `public` alongside Tenant/User rather than a separate schema, at
    the user's direction. No exchange dimension (deliberately dropped, see project
    notes) — flat, symbol-keyed. `underlying_symbol` strips a continuous-future's _I/_II
    suffix (e.g. 'ADANIENT_I' -> 'ADANIENT'); null for symbols that aren't a roll series.
    """

    __tablename__ = "Instrument"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    underlying_symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    instrument_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'EQ' | 'FUT_CONTINUOUS' | ...
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_traded_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_traded_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_instrument_underlying", "underlying_symbol"),
    )


class TradingCalendar(CentralBase):
    """Which dates actually traded — lets 'previous day'/'previous week' logic
    distinguish a real gap from a holiday. Populated from observed EodBar dates on
    ingestion, not a hand-maintained NSE holiday list (see project notes)."""

    __tablename__ = "TradingCalendar"

    calendar_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, nullable=False)


class EodBar(CentralBase):
    """The canonical raw daily bar — exactly what the vendor sends, upserted, never
    silently recalculated. Everything derived (technical indicators, period rollups)
    is reconstructable from this table alone. PK (instrument_id, trade_date) is what
    makes a re-upload for an already-loaded date overwrite in place instead of
    duplicating — see the ingestion upsert in the loader script.
    """

    __tablename__ = "EodBar"

    instrument_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("Instrument.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(DECIMAL(14, 4), nullable=False)
    high: Mapped[float] = mapped_column(DECIMAL(14, 4), nullable=False)
    low: Mapped[float] = mapped_column(DECIMAL(14, 4), nullable=False)
    close: Mapped[float] = mapped_column(DECIMAL(14, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # null for cash equities
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="eqldata")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    # Turnover/average-traded-price figures — NOT part of the eqldata vendor
    # feed (that's what "source" above always is for this table today), so
    # these stay null for every row until backfilled separately. Populated
    # by app.services.inception_admin_sync_service from each admin tenant's
    # own LmvDailySnapshot archive (which already carries these as
    # formula_engine-computed values from LMV's own daily grid — this
    # column set mirrors that Metric registry's names 1:1, snake_cased) —
    # see that service's own docstring for the sync/matching logic (join on
    # Instrument.underlying_symbol = Stock.symbol, since LMV's Stock rows
    # are the plain underlying, not Inception's continuous-roll series).
    # Nullable on every column: an instrument/date this hasn't been synced
    # for yet (or that LMV's own snapshot never covered) simply has no
    # value here, same "blank rather than crash" convention every reader
    # of these already uses (see inception_formula_builder_columns.py in
    # the desktop client repo).
    avg_rate: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    patp: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    pwatp: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    pmatp: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    cwatp: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    cmatp: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    day_to: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    pdto: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    cwto: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)
    pwto: Mapped[float | None] = mapped_column(DECIMAL(14, 4), nullable=True)

    __table_args__ = (
        Index("ix_eod_bar_date", "trade_date"),
    )
