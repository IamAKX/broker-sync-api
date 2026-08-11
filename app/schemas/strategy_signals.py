from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

_ALLOWED_PAGE_SIZES = (25, 50, 100)


class StrategySignalUpsertRequest(BaseModel):
    strategy_id: str
    strategy_name: str = Field(min_length=1, max_length=200)
    symbol: str = Field(min_length=1, max_length=50)
    sector: str | None = Field(default=None, max_length=100)
    direction: Literal["BUY", "SELL"]
    status: Literal["open", "stopped_out", "all_targets_achieved"]
    entry_time: datetime | None = None
    entry_price: float | None = None
    resolved_at: datetime | None = None
    running_high: float | None = None
    running_low: float | None = None
    score: float | None = None
    risk_reward: dict | None = None
    metrics: dict = Field(default_factory=dict)


class StrategySignalResponse(BaseModel):
    id: str
    strategy_id: str
    strategy_name: str
    symbol: str
    sector: str | None
    direction: str
    status: str
    entry_time: datetime | None
    entry_price: float | None
    resolved_at: datetime | None
    running_high: float | None
    running_low: float | None
    score: float | None
    risk_reward: dict | None
    metrics: dict
    event_time: datetime


class StrategySignalListResponse(BaseModel):
    items: list[StrategySignalResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
