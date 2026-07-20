from datetime import date

from pydantic import BaseModel, Field

MetricValue = float | str | None


class UploadRow(BaseModel):
    symbol: str = Field(min_length=1, max_length=50)
    display_name: str | None = None
    metrics: dict[str, MetricValue] = Field(default_factory=dict)


class UploadRequest(BaseModel):
    trade_date: date
    rows: list[UploadRow]


class UploadResponse(BaseModel):
    trade_date: date
    stocks_upserted: int
    metrics_registered: int
    values_upserted: int


class DeleteDayResponse(BaseModel):
    trade_date: date
    values_deleted: int


class StockSnapshot(BaseModel):
    symbol: str
    display_name: str | None
    metrics: dict[str, MetricValue]


class SnapshotResponse(BaseModel):
    trade_date: date
    stocks: list[StockSnapshot]


class TimeseriesPoint(BaseModel):
    trade_date: date
    value: MetricValue


class TimeseriesResponse(BaseModel):
    symbol: str
    metric: str
    points: list[TimeseriesPoint]


class DateAvailability(BaseModel):
    trade_date: date
    has_data: bool


class DateAvailabilityResponse(BaseModel):
    date_from: date
    date_to: date
    dates: list[DateAvailability]
