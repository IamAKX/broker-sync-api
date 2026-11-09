from typing import Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class ChatAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    # Prior turns (oldest first) so follow-ups like "what about Banking?" work.
    # The server keeps no conversation state; the client owns the history.
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)


class ChatStatusOut(BaseModel):
    enabled: bool
    model_id: str
    suggested_questions: list[str]
