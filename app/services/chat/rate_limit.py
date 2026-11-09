"""Per-user sliding-window rate limit for chat questions (each one costs two
model calls). In-process, so the effective ceiling is limit x gunicorn workers —
fine as a cost/abuse brake; see docs/CHATBOT.md."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.config import settings
from app.exceptions import ChatRateLimitedError

_WINDOW_SECONDS = 60.0
_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def check_rate_limit(user_id: str) -> None:
    limit = settings.chat_rate_limit_per_minute
    if limit <= 0:
        return
    now = time.monotonic()
    with _lock:
        hits = _hits[user_id]
        while hits and now - hits[0] > _WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            raise ChatRateLimitedError(
                f"You can ask up to {limit} questions per minute. Please wait a few seconds."
            )
        hits.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
