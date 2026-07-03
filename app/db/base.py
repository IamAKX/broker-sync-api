from sqlalchemy.orm import DeclarativeBase


class CentralBase(DeclarativeBase):
    """Metadata for tables in the shared `dbo` schema (Tenant, User, RefreshToken)."""


class TenantBase(DeclarativeBase):
    """Metadata for tables replayed into each per-tenant schema (Stock, Metric, DailyStockValue)."""
