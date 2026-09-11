from pydantic import BaseModel, Field


class FormulaVariableUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    formula: list = Field(default_factory=list)


class FormulaVariableResponse(BaseModel):
    id: str
    name: str
    formula: list


class FormulaVariableListResponse(BaseModel):
    variables: list[FormulaVariableResponse]


class FormulaVariableImportItem(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=200)
    formula: list = Field(default_factory=list)


class FormulaVariableImportRequest(BaseModel):
    variables: list[FormulaVariableImportItem]


class FormulaVariableImportResponse(BaseModel):
    overwritten: int
    added: int
