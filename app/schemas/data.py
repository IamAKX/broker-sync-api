from pydantic import BaseModel


class MetricOut(BaseModel):
    name: str
    data_type: str
    is_active: bool


class StockOut(BaseModel):
    symbol: str
    display_name: str | None
    is_active: bool
