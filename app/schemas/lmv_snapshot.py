from datetime import date

from pydantic import BaseModel

from app.schemas.historic import UploadRow


class LmvSnapshotUploadRequest(BaseModel):
    trade_date: date
    rows: list[UploadRow]


class LmvSnapshotUploadResponse(BaseModel):
    trade_date: date
    stocks_upserted: int
    metrics_registered: int
    values_upserted: int
