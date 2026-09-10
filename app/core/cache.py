"""Tiny in-process TTL cache for read-heavy endpoints.

Why in-process and not Redis: the API runs as a small number of gunicorn
workers on one box. Each worker keeps its own copy; a write (daily upload,
setting change) invalidates only the worker that served it, so other
workers can serve a stale value for up to the TTL. For this workload
(EOD-updated snapshot data, settings that change rarely) that window is
acceptable. Move to Redis via this same interface when the API scales to
more than one instance — see docs/PERFORMANCE_SCALING_PLAN.md C1.

Values are cached *ready to serialize* (plain dict / list), so a hit skips
the DB round trip, the row pivot, AND pydantic model construction — only
the orjson encode + gzip remain. Endpoints return the cached value inside
an ``ORJSONResponse`` directly, bypassing ``response_model`` validation on
the hot path.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any


class TTLCache:
    def __init__(self) -> None:
        # key -> (expires_monotonic, frozenset[tags], value)
        self._data: dict[str, tuple[float, frozenset[str], Any]] = {}
        # tag -> set of keys carrying it (for O(1) group invalidation)
        self._tags: dict[str, set[str]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, _tags, value = entry
            if time.monotonic() >= expires_at:
                self._evict_locked(key)
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: float, tags: Iterable[str] = ()) -> None:
        tagset = frozenset(tags)
        with self._lock:
            self._evict_locked(key)  # drop any prior tag links for this key
            self._data[key] = (time.monotonic() + ttl, tagset, value)
            for t in tagset:
                self._tags.setdefault(t, set()).add(key)

    def invalidate_key(self, key: str) -> None:
        with self._lock:
            self._evict_locked(key)

    def invalidate_tag(self, tag: str) -> int:
        with self._lock:
            keys = self._tags.pop(tag, set())
            for k in list(keys):
                entry = self._data.pop(k, None)
                if entry:
                    for other in entry[1]:
                        if other != tag:
                            s = self._tags.get(other)
                            if s:
                                s.discard(k)
                                if not s:
                                    self._tags.pop(other, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._tags.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._data),
                "tags": len(self._tags),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate_pct": round(100 * self.hits / total, 1) if total else 0,
            }

    def _evict_locked(self, key: str) -> None:
        entry = self._data.pop(key, None)
        if not entry:
            return
        for t in entry[1]:
            s = self._tags.get(t)
            if s:
                s.discard(key)
                if not s:
                    self._tags.pop(t, None)


cache = TTLCache()


async def get_or_set(
    key: str,
    ttl: float,
    tags: Iterable[str],
    producer: Callable[[], Awaitable[Any]],
) -> Any:
    """Return the cached value for *key*, else run *producer* (an async
    callable returning a serialization-ready dict/list), cache it, return it.

    Not locked across the producer call on purpose: a cache miss under
    concurrency may run *producer* more than once (a brief thundering herd
    on the very first request after expiry), which is cheaper and simpler
    than holding the lock through a multi-second DB+pivot call.
    """
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = await producer()
    cache.set(key, value, ttl, tags)
    return value


# ── tag / key helpers (single source of truth for cache-key shapes) ──────

def lmv_snapshot_tag(schema: str) -> str:
    return f"lmv-snapshot:{schema}"


def historic_tag(schema: str) -> str:
    return f"historic:{schema}"


def opening_range_tag(schema: str) -> str:
    return f"opening-range:{schema}"


def setting_key(schema: str, user_id: str, key: str) -> str:
    return f"setting:{schema}:{user_id}:{key}"
