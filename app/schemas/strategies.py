from pydantic import BaseModel, Field


class StrategyUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    category: str = Field(default="Daily", min_length=1, max_length=100)
    columns: list = Field(default_factory=list)
    row_filter: list = Field(default_factory=list)


class StrategyResponse(BaseModel):
    id: str
    name: str
    active: bool
    category: str
    columns: list
    row_filter: list


class StrategyListResponse(BaseModel):
    strategies: list[StrategyResponse]


class StrategyImportItem(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=200)
    active: bool = True
    category: str = Field(default="Daily", min_length=1, max_length=100)
    columns: list = Field(default_factory=list)
    row_filter: list = Field(default_factory=list)


class StrategyImportRequest(BaseModel):
    strategies: list[StrategyImportItem]


class StrategyImportResponse(BaseModel):
    overwritten: int
    added: int
