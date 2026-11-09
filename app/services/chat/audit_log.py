"""Dedicated audit logs for the chatbot, written to their own files.

  chat_prompts.log  one JSON line per user prompt (who asked what)
  chat_queries.log  one JSON line per generated SQL (what the model wrote, and
                    whether it ran / was rejected)

They are separate from the app's stdout/journal log so they can be tailed,
rotated and retained independently. Row data is never written. Files live in
``settings.chat_log_dir`` (relative paths resolve against the service's working
directory, i.e. the app root on EC2). WatchedFileHandler is used because
gunicorn runs several workers: it appends safely from each and reopens the file
when logrotate renames it.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import WatchedFileHandler
from typing import Any

from app.core.config import settings

_loggers: dict[str, logging.Logger] = {}


def _get(name: str, filename: str) -> logging.Logger | None:
    if name in _loggers:
        return _loggers[name]
    try:
        os.makedirs(settings.chat_log_dir, exist_ok=True)
        handler = WatchedFileHandler(os.path.join(settings.chat_log_dir, filename), encoding="utf-8")
    except OSError:
        return None  # auditing must never break the chatbot
    handler.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger(f"chat_audit.{name}")
    lg.setLevel(logging.INFO)
    lg.propagate = False  # keep these out of the main app log
    lg.handlers = [handler]
    _loggers[name] = lg
    return lg


def _write(name: str, filename: str, record: dict[str, Any]) -> None:
    lg = _get(name, filename)
    if lg is None:
        return
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record}
    try:
        lg.info(json.dumps(record, default=str, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


def log_prompt(user: Any, question: str) -> None:
    _write(
        "prompts",
        "chat_prompts.log",
        {"user_id": user.user_id, "schema": user.schema_name, "question": question},
    )


def log_generated_query(
    user: Any, question: str, sql: str, status: str, error: str = "", attempt: int = 0
) -> None:
    """status: 'executed' | 'rejected' (guard) | 'db_error'."""
    _write(
        "queries",
        "chat_queries.log",
        {
            "user_id": user.user_id,
            "schema": user.schema_name,
            "attempt": attempt,
            "status": status,
            "question": question[:300],
            "sql": sql,
            "error": error or None,
        },
    )
