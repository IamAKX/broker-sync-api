"""Thin async wrapper over the Anthropic Messages API (direct, via api.anthropic.com).

Messages use a minimal internal shape - ``{"role": ..., "content": [{"text": ...}]}`` -
that is translated to Anthropic content blocks here, so the pipeline stays provider-agnostic.
The API key comes from settings.anthropic_api_key (env ANTHROPIC_API_KEY); it is never
sent to clients or logged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import anthropic

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LlmError(RuntimeError):
    """An LLM failure with a message that is safe to show end users."""


@dataclass
class ModelReply:
    text: str = ""
    blocked: bool = False  # the model declined to answer (stop_reason == "refusal")
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""


@dataclass
class StreamEvent:
    text: str = ""
    final: ModelReply | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _client() -> anthropic.AsyncAnthropic:
    if not settings.anthropic_api_key:
        raise LlmError("The AI assistant is not configured yet. Please contact support.")
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.chat_llm_timeout,
        max_retries=settings.chat_llm_max_retries,
    )


def _messages(messages: list[dict]) -> list[dict]:
    return [
        {"role": m["role"], "content": [{"type": "text", "text": b["text"]} for b in m["content"]]}
        for m in messages
    ]


def _request(model_id: str, system: str, messages: list[dict], max_tokens: int) -> dict:
    # No temperature/top_p: the API rejects them for current models ("Extra inputs are not
    # permitted"), so determinism comes from the planner prompt and the SQL validator.
    return {
        "model": model_id,
        "system": system,
        "messages": _messages(messages),
        "max_tokens": max_tokens,
    }


def _friendly(exc: Exception) -> LlmError:
    logger.error("llm_call_failed", error=type(exc).__name__, detail=str(exc)[:300])
    if isinstance(exc, anthropic.RateLimitError):
        return LlmError("The AI service is busy right now. Please try again in a moment.")
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return LlmError("The AI assistant is not available for this account yet. Please contact support.")
    if isinstance(exc, anthropic.APITimeoutError):
        return LlmError("The AI service took too long to respond. Please try again.")
    return LlmError("The AI service is unavailable right now. Please try again.")


def _reply(msg: Any) -> ModelReply:
    return ModelReply(
        text="".join(b.text for b in msg.content if getattr(b, "type", "") == "text"),
        blocked=msg.stop_reason == "refusal",
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        stop_reason=msg.stop_reason or "",
    )


async def converse(
    *, model_id: str, system: str, messages: list[dict], max_tokens: int
) -> ModelReply:
    try:
        msg = await _client().messages.create(**_request(model_id, system, messages, max_tokens))
        return _reply(msg)
    except anthropic.APIError as exc:
        raise _friendly(exc) from exc


async def converse_stream(
    *, model_id: str, system: str, messages: list[dict], max_tokens: int
) -> AsyncIterator[StreamEvent]:
    """Yields StreamEvent(text=delta) as tokens arrive, then one StreamEvent(final=ModelReply)."""
    try:
        async with _client().messages.stream(
            **_request(model_id, system, messages, max_tokens)
        ) as stream:
            async for delta in stream.text_stream:
                if delta:
                    yield StreamEvent(text=delta)
            final = _reply(await stream.get_final_message())
    except anthropic.APIError as exc:
        raise _friendly(exc) from exc
    yield StreamEvent(final=final)
