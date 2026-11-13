from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=4000)


class ChatLiveLmv(BaseModel):
    """The desktop's Live Master View at the moment the question was asked. Sent with
    every question so "right now" questions can be answered from live data; when LMV is
    not running, ``running`` is false and ``reason`` says what the user has to do."""

    running: bool = False
    reason: str = Field(default="", max_length=600)
    warning: str = Field(default="", max_length=600)   # running, but e.g. the live feed is stale
    as_of: str = Field(default="", max_length=40)      # desktop clock, "2026-11-11 10:32:05"
    # Columnar to keep the upload small: ``columns`` are the LMV column names; each row is
    # [symbol, display_name, sector, <one value per column, null where the cell is empty>].
    columns: list[str] = Field(default_factory=list, max_length=600)
    rows: list[list[Any]] = Field(default_factory=list, max_length=1500)

    def row_dicts(self) -> tuple[dict[str, Any], ...]:
        """Rows in the lmv_live shape; malformed rows are skipped rather than failing the question."""
        out = []
        for row in self.rows:
            if len(row) < 3 or not isinstance(row[0], str) or not row[0].strip():
                continue
            metrics = {
                str(c)[:120]: v for c, v in zip(self.columns, row[3:])
                if v is not None and isinstance(v, (str, int, float, bool))
            }
            out.append({
                "symbol": row[0].strip()[:64],
                "display_name": str(row[1])[:128] if row[1] is not None else None,
                "sector": str(row[2])[:128] if row[2] is not None else None,
                "metrics": metrics,
            })
        return tuple(out)


class ChatAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    # Prior turns (oldest first) so follow-ups like "what about Banking?" work.
    # The server keeps no conversation state; the client owns the history.
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    # Optional: omitted by clients that have no Live Master View (then live questions
    # get the archived-data behaviour).
    live: ChatLiveLmv | None = None


class ChatStatusOut(BaseModel):
    enabled: bool
    model_id: str
    suggested_questions: list[str]


class ChatTranslateRequest(BaseModel):
    """One chat answer to show in another language. English is never requested:
    the desktop keeps the original and switches back to it locally."""

    text: str = Field(min_length=1, max_length=20000)
    target: Literal["ta"] = "ta"


class ChatTranslateOut(BaseModel):
    text: str
    target: str
