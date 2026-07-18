from datetime import date

from pydantic import BaseModel, Field


class HolidayOut(BaseModel):
    id: int
    holiday_date: date
    name: str


class HolidayCreateRequest(BaseModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=200)


class HolidayUpdateRequest(BaseModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=200)
