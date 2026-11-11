import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.db.tenant_session import build_tenant_sessionmaker
from app.exceptions import ChatDisabledError
from app.schemas.chat import ChatAskRequest, ChatStatusOut
from app.services.chat.pipeline import run_chat
from app.services.chat.rate_limit import check_rate_limit
from app.services.chat.sql_runner import LiveSnapshot
from app.services.chat.suggestions import SUGGESTED_QUESTIONS
from app.services.provisioning_service import ensure_tenant_schema_tables

router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/status", response_model=ChatStatusOut)
async def chat_status_route(_: CurrentUser = Depends(get_current_user)) -> ChatStatusOut:
    return ChatStatusOut(
        enabled=settings.chat_enabled,
        model_id=settings.chat_model_id,
        suggested_questions=SUGGESTED_QUESTIONS,
    )


@router.post("/ask")
async def chat_ask_route(
    payload: ChatAskRequest, user: CurrentUser = Depends(get_current_user)
) -> StreamingResponse:
    """Streams the answer as Server-Sent Events (see services/chat/pipeline.py for
    the event vocabulary). The DB session is opened inside the generator, not via a
    yield-dependency, because the dependency would be torn down before the stream
    finishes being consumed."""
    if not settings.chat_enabled:
        raise ChatDisabledError("The AI assistant is not enabled for this deployment.")
    check_rate_limit(user.user_id)
    question = payload.question.strip()[: settings.chat_max_question_chars]
    history = [t.model_dump() for t in payload.history]
    live = None
    if payload.live is not None:
        live = LiveSnapshot(
            running=payload.live.running,
            reason=payload.live.reason,
            warning=payload.live.warning,
            as_of=payload.live.as_of,
            rows=payload.live.row_dicts() if payload.live.running else (),
        )

    async def stream() -> AsyncIterator[str]:
        sessionmaker = build_tenant_sessionmaker(user.schema_name)
        async with sessionmaker() as session:
            await ensure_tenant_schema_tables(session, user.schema_name)
            async for event, data in run_chat(session, user, question, history, live):
                yield _sse(event, data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        # Content-Encoding makes GZipMiddleware pass the body through untouched —
        # otherwise it would buffer the stream and the UI would see no live updates.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Content-Encoding": "identity"},
    )
