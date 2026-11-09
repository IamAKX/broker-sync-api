"""The chatbot request pipeline: understand -> write SQL -> run it -> write the answer.

Yields (event_name, payload) tuples that the router relays as Server-Sent
Events, so the desktop UI can animate each stage as it happens:

  stage    {"stage": "understanding"|"generating_query"|"running_query"|"writing_answer"}
  rows     {"columns", "rows", "row_count", "truncated"}   preview for a result table
  token    {"text": "..."}                     answer text, streamed
  answer   {"text": "..."}                     a complete answer (refusals, knowledge, clarify)
  done     {"kind", "latency_ms", "input_tokens", "output_tokens", "row_count"}
  error    {"message": "..."}
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.logging import get_logger
from app.services.chat import audit_log, llm_client
from app.services.chat.llm_client import LlmError, ModelReply
from app.services.chat.prompts import ANSWER_SYSTEM, build_planner_system
from app.services.chat.sql_guard import UnsafeSqlError, validate_and_prepare
from app.services.chat.sql_runner import (
    QueryResult,
    SqlExecutionError,
    load_data_context,
    run_query,
    sandbox,
)

logger = get_logger(__name__)

Event = tuple[str, dict[str, Any]]

_IST = ZoneInfo("Asia/Kolkata")
_ROWS_PREVIEW = 50
_BLOCKED_FALLBACK = (
    "I can only help with markets, stocks and Fuku Lens data. "
    "Try asking about a stock, a sector, or one of your strategies."
)


def _clean_history(history: list[dict[str, str]]) -> list[dict]:
    """Turn client-supplied prior turns into valid alternating Converse messages.
    Client text is treated as untrusted conversation text only, capped in size."""
    msgs: list[dict] = []
    for turn in history[-(settings.chat_max_history_turns * 2):]:
        role = turn.get("role")
        text = (turn.get("text") or "").strip()[:2000]
        if role not in ("user", "assistant") or not text:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"][0]["text"] += "\n" + text
        else:
            msgs.append({"role": role, "content": [{"text": text}]})
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if msgs and msgs[-1]["role"] == "user":
        msgs.pop()  # the new question is appended by the caller
    return msgs


def _parse_plan(raw: str) -> dict[str, Any]:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    plan = json.loads(raw[start : end + 1])
    if plan.get("kind") not in {"sql", "knowledge", "unavailable", "refuse", "clarify"}:
        raise ValueError("unknown plan kind")
    return plan


def _result_for_model(result: QueryResult) -> str:
    rows = result.rows[: settings.chat_answer_max_rows]
    payload = {"columns": result.columns, "rows": rows}
    text = json.dumps(payload, default=str, separators=(",", ":"))
    notes = []
    if result.truncated:
        notes.append(f"The query hit the {settings.chat_sql_max_rows}-row limit; more rows exist.")
    if len(result.rows) > len(rows) or len(text) > settings.chat_answer_max_chars:
        notes.append(f"Only the first {len(rows)} of {len(result.rows)} returned rows are shown.")
    if len(text) > settings.chat_answer_max_chars:
        text = text[: settings.chat_answer_max_chars] + "...(cut)"
    return text + ("\nNOTE: " + " ".join(notes) if notes else "")


async def run_chat(
    session: AsyncSession,
    user: CurrentUser,
    question: str,
    history: list[dict[str, str]],
) -> AsyncIterator[Event]:
    started = time.monotonic()
    in_tokens = out_tokens = 0

    def ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        audit_log.log_prompt(user, question)
        yield "stage", {"stage": "understanding"}
        ctx = await load_data_context(session, schema_name=user.schema_name, user_id=user.user_id)
        system = build_planner_system(ctx, datetime.now(_IST).date())
        messages = _clean_history(history) + [
            {"role": "user", "content": [{"text": question}]}
        ]

        plan: dict[str, Any] | None = None
        result: QueryResult | None = None
        display_sql = ""
        last_error = ""

        for attempt in range(settings.chat_sql_max_retries + 1):
            reply = await llm_client.converse(
                model_id=settings.chat_model_id,
                system=system,
                messages=messages,
                max_tokens=settings.chat_sql_max_tokens,
            )
            in_tokens += reply.input_tokens
            out_tokens += reply.output_tokens

            if reply.blocked:
                yield "answer", {"text": reply.text or _BLOCKED_FALLBACK}
                _log(user, question, "blocked", None, 0, in_tokens, out_tokens, ms())
                yield "done", _done("blocked", ms(), in_tokens, out_tokens, 0)
                return
            try:
                plan = _parse_plan(reply.text)
            except (ValueError, json.JSONDecodeError):
                last_error = "Reply with a single valid JSON object as specified."
                plan = None
            else:
                if plan["kind"] != "sql":
                    break
                yield "stage", {"stage": "generating_query"}
                try:
                    prepared = validate_and_prepare(plan.get("sql") or "", max_rows=settings.chat_sql_max_rows)
                except UnsafeSqlError as exc:
                    last_error = f"Your SQL was rejected: {exc}"
                    audit_log.log_generated_query(
                        user, question, str(plan.get("sql") or ""), "rejected", str(exc), attempt
                    )
                else:
                    yield "stage", {"stage": "running_query"}
                    try:
                        async with sandbox(session, schema_name=user.schema_name, user_id=user.user_id):
                            result = await run_query(session, prepared.sql, max_rows=settings.chat_sql_max_rows)
                        display_sql = prepared.display_sql
                        audit_log.log_generated_query(user, question, display_sql, "executed", attempt=attempt)
                        break
                    except SqlExecutionError as exc:
                        last_error = f"The database rejected your SQL: {exc}"
                        audit_log.log_generated_query(
                            user, question, prepared.display_sql, "db_error", str(exc), attempt
                        )

            logger.info("chat_repair_attempt", attempt=attempt, error=last_error)
            messages = messages + [
                {"role": "assistant", "content": [{"text": reply.text or "{}"}]},
                {"role": "user", "content": [{"text": f"{last_error} Return a corrected JSON reply."}]},
            ]

        if plan is None or (plan["kind"] == "sql" and result is None):
            yield "error", {
                "message": "I couldn't turn that into a reliable query. Try rephrasing, "
                "or ask about a specific stock, date or strategy."
            }
            _log(user, question, "failed", None, 0, in_tokens, out_tokens, ms(), last_error)
            return

        if plan["kind"] != "sql":
            yield "answer", {"text": (plan.get("reply") or _BLOCKED_FALLBACK).strip()}
            _log(user, question, plan["kind"], None, 0, in_tokens, out_tokens, ms())
            yield "done", _done(plan["kind"], ms(), in_tokens, out_tokens, 0)
            return

        assert result is not None
        yield "rows", {
            "columns": result.columns,
            "rows": result.rows[:_ROWS_PREVIEW],
            "row_count": len(result.rows),
            "truncated": result.truncated or len(result.rows) > _ROWS_PREVIEW,
        }

        yield "stage", {"stage": "writing_answer"}
        answer_prompt = (
            f"SQL that was run:\n{display_sql}\n\nRESULT (JSON):\n{_result_for_model(result)}\n\n"
            "Write the reply to the user's question using only this result."
        )
        answer_messages = _clean_history(history) + [
            {
                "role": "user",
                "content": [{"text": question}, {"text": answer_prompt}],
            }
        ]
        final: ModelReply | None = None
        async for ev in llm_client.converse_stream(
            model_id=settings.chat_answer_model_id or settings.chat_model_id,
            system=ANSWER_SYSTEM,
            messages=answer_messages,
            max_tokens=settings.chat_answer_max_tokens,
        ):
            if ev.final is not None:
                final = ev.final
            elif ev.text:
                yield "token", {"text": ev.text}
        if final is not None:
            in_tokens += final.input_tokens
            out_tokens += final.output_tokens
            if final.blocked:
                yield "answer", {"text": _BLOCKED_FALLBACK}

        _log(user, question, "sql", display_sql, len(result.rows), in_tokens, out_tokens, ms())
        yield "done", _done("sql", ms(), in_tokens, out_tokens, len(result.rows))

    except LlmError as exc:
        yield "error", {"message": str(exc)}
    except Exception:  # noqa: BLE001 — never leak internals to the client
        logger.exception("chat_pipeline_failed", user_id=user.user_id)
        yield "error", {"message": "Something went wrong while answering. Please try again."}


def _done(kind: str, latency_ms: int, in_tokens: int, out_tokens: int, row_count: int) -> dict[str, Any]:
    return {
        "kind": kind,
        "latency_ms": latency_ms,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "row_count": row_count,
    }


def _log(user, question, kind, sql, rows, in_tokens, out_tokens, latency_ms, error: str = "") -> None:
    """Summary line for the main app log: who asked, outcome, cost. The prompt text and
    generated SQL go to the dedicated audit logs (audit_log.py), not here. No row data."""
    logger.info(
        "chat_query",
        user_id=user.user_id,
        schema=user.schema_name,
        kind=kind,
        row_count=rows,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        latency_ms=latency_ms,
        error=error or None,
    )
