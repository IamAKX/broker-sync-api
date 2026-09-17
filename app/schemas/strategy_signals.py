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
    # "trade_cancelled" — a Target/Stop Loss computed on the wrong side of
    # the entry price for this signal's own direction (issue #32 on the
    # desktop client repo): that trade could never have been legitimately
    # taken, so the client resolves straight to this instead of tracking a
    # normal open signal. See that repo's services/strategy_alerts/
    # engine.py::_invalid_metrics.
    # "intraday_closed" — issue #43 on the desktop client repo: a strategy
    # configured alert_mode="intraday" has its still-open signal force-
    # resolved at the day's alert-window close (Current price as the exit)
    # instead of carrying into the next day. See that repo's
    # services/strategy_alerts/engine.py::close_intraday_signals.
    status: Literal[
        "open", "stopped_out", "all_targets_achieved", "trade_cancelled", "intraday_closed",
    ]
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
