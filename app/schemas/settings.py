from typing import Any

from pydantic import BaseModel


class SettingResponse(BaseModel):
    key: str
    value: Any


class SettingUpdateRequest(BaseModel):
    value: Any
