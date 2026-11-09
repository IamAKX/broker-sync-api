"""Thin async wrapper over the Bedrock Converse / ConverseStream APIs.

Converse is model-agnostic, so swapping Haiku for another model is a config
change (settings.chat_model_id). boto3 is synchronous; calls run on worker
threads so the event loop (and the other users' requests) are never blocked.
Credentials come from the default boto3 chain — the EC2 instance role in
production (see docs/BEDROCK_SETUP.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class BedrockError(RuntimeError):
    """A Bedrock failure with a message that is safe to show end users."""


@dataclass
class ModelReply:
    text: str = ""
    blocked: bool = False  # the Bedrock Guardrail intervened
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""


@dataclass
class StreamEvent:
    text: str = ""
    final: ModelReply | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _client():
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.chat_aws_region,
        config=Config(
            read_timeout=settings.chat_bedrock_read_timeout,
            connect_timeout=5,
            retries={"max_attempts": settings.chat_bedrock_max_attempts, "mode": "standard"},
        ),
    )


def _guardrail_config(*, stream: bool) -> dict[str, Any]:
    if not settings.chat_guardrail_id:
        return {}
    cfg: dict[str, Any] = {
        "guardrailIdentifier": settings.chat_guardrail_id,
        "guardrailVersion": settings.chat_guardrail_version,
    }
    if stream:
        cfg["streamProcessingMode"] = "sync"
    return {"guardrailConfig": cfg}


def _request(model_id: str, system: str, messages: list[dict], temperature: float, max_tokens: int, *, stream: bool):
    return {
        "modelId": model_id,
        "system": [{"text": system}],
        "messages": messages,
        "inferenceConfig": {
            "temperature": temperature,
            "maxTokens": max_tokens,
            **({"topP": settings.chat_top_p} if settings.chat_top_p is not None else {}),
        },
        **_guardrail_config(stream=stream),
    }


def guarded_text(text: str) -> dict:
    """A content block the guardrail evaluates (and the model reads as text)."""
    return {"guardContent": {"text": {"text": text}}}


def _friendly(exc: Exception) -> BedrockError:
    code = ""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
    logger.error("bedrock_call_failed", error=str(exc), code=code)
    if code in {"ThrottlingException", "ServiceQuotaExceededException"}:
        return BedrockError("The AI service is busy right now. Please try again in a moment.")
    if code in {"AccessDeniedException", "ResourceNotFoundException"}:
        return BedrockError("The AI model is not enabled for this account yet. Please contact support.")
    if code in {"ModelTimeoutException"} or "timed out" in str(exc).lower():
        return BedrockError("The AI service took too long to respond. Please try again.")
    return BedrockError("The AI service is unavailable right now. Please try again.")


async def converse(
    *, model_id: str, system: str, messages: list[dict], temperature: float, max_tokens: int
) -> ModelReply:
    request = _request(model_id, system, messages, temperature, max_tokens, stream=False)

    def _call() -> ModelReply:
        resp = _client().converse(**request)
        parts = resp["output"]["message"]["content"]
        usage = resp.get("usage", {})
        return ModelReply(
            text="".join(p.get("text", "") for p in parts),
            blocked=resp.get("stopReason") == "guardrail_intervened",
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            stop_reason=resp.get("stopReason", ""),
        )

    try:
        return await asyncio.to_thread(_call)
    except (ClientError, BotoCoreError) as exc:
        raise _friendly(exc) from exc


async def converse_stream(
    *, model_id: str, system: str, messages: list[dict], temperature: float, max_tokens: int
) -> AsyncIterator[StreamEvent]:
    """Yields StreamEvent(text=delta) as tokens arrive, then one StreamEvent(final=ModelReply)."""
    request = _request(model_id, system, messages, temperature, max_tokens, stream=True)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _worker() -> None:
        try:
            resp = _client().converse_stream(**request)
            for event in resp["stream"]:
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as exc:  # noqa: BLE001 — forwarded to the consumer
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(None, _worker)
    final = ModelReply()
    while True:
        event = await queue.get()
        if event is None:
            break
        if isinstance(event, Exception):
            if isinstance(event, (ClientError, BotoCoreError)):
                raise _friendly(event) from event
            raise event
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"]["delta"].get("text", "")
            if delta:
                final.text += delta
                yield StreamEvent(text=delta)
        elif "messageStop" in event:
            final.stop_reason = event["messageStop"].get("stopReason", "")
            final.blocked = final.stop_reason == "guardrail_intervened"
        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            final.input_tokens = usage.get("inputTokens", 0)
            final.output_tokens = usage.get("outputTokens", 0)
    yield StreamEvent(final=final)
