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
    func,
)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.sysutcdatetime())


class DailyStockValue(TenantBase):
    __tablename__ = "DailyStockValue"

    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("Stock.id"), primary_key=True)
    metric_id: Mapped[int] = mapped_column(Integer, ForeignKey("Metric.id"), primary_key=True)
    value_number: Mapped[float | None] = mapped_column(DECIMAL(18, 4), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.sysutcdatetime())

    stock: Mapped["Stock"] = relationship()
    metric: Mapped["Metric"] = relationship()

    __table_args__ = (
        # Serves timeseries reads: WHERE stock_id = ? AND metric_id = ? ORDER BY trade_date
        Index("ix_dsv_stock_metric_date", "stock_id", "metric_id", "trade_date"),
        # Serves snapshot reads: WHERE trade_date = ?
        Index("ix_dsv_date", "trade_date"),
    )
