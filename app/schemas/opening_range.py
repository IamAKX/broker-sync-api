from datetime import date

from pydantic import BaseModel, Field

# A trading day is at most ~6h15m (NSE: 09:15-15:30) — 390 minutes covers
# every real broker session with headroom, while still catching an obviously
# wrong client-side config value (e.g. minutes vs. seconds mixed up).
_MAX_WINDOW_MINUTES = 390


class OpeningRangeUploadRow(BaseModel):
    symbol: str = Field(min_length=1, max_length=50)
    display_name: str | None = None
    high: float
    low: float


class OpeningRangeUploadRequest(BaseModel):
    trade_date: date
    window_minutes: int = Field(gt=0, le=_MAX_WINDOW_MINUTES)
    rows: list[OpeningRangeUploadRow]


class OpeningRangeUploadResponse(BaseModel):
    trade_date: date
    stocks_upserted: int
    values_upserted: int


class OpeningRangeStock(BaseModel):
    symbol: str
    display_name: str | None
    high: float
    low: float
    window_minutes: int


class OpeningRangeSnapshotResponse(BaseModel):
    trade_date: date
    stocks: list[OpeningRangeStock]
