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

import gzip as _gzip
import threading
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from starlette.requests import Request
from starlette.responses import Response


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

# One asyncio.Lock per key so a burst of identical cache misses (many
# clients toggling the same strategy at once) runs the expensive producer
# ONCE and the rest await that result — instead of all of them hitting the
# DB + pivot + serialize at the same time and starving the worker pool.
# Keys are bounded (schemas x endpoints x day-counts ~= a few dozen), so
# the lock dict is never pruned.
_key_locks: dict[str, Any] = {}


async def get_or_set(
    key: str,
    ttl: float,
    tags: Iterable[str],
    producer: Callable[[], Awaitable[Any]],
) -> Any:
    """Return the cached value for *key*, else run *producer* (an async
    callable returning a serialization-ready dict/list), cache it, return it.
    Single-flighted per key."""
    import asyncio

    hit = cache.get(key)
    if hit is not None:
        return hit

    lock = _key_locks.get(key)
    if lock is None:
        lock = _key_locks.setdefault(key, asyncio.Lock())

    async with lock:
        hit = cache.get(key)  # a coroutine ahead of us may have just filled it
        if hit is not None:
            return hit
        value = await producer()
        cache.set(key, value, ttl, tags)
        return value


class _Encoded:
    """A response body serialized once and gzipped once, at cache-fill
    time. On a hit the endpoint just picks plain vs gzip bytes by the
    request's Accept-Encoding and sends them — no per-request orjson
    encode, no per-request gzip (both were ~1-4s on the event loop for a
    ~3MB /lmv-snapshot/range payload, even when the value itself was
    cached)."""

    __slots__ = ("json", "gz")

    def __init__(self, payload: Any) -> None:
        import orjson  # lazy — no prebuilt wheel on some dev pythons (mirrors fastapi's ORJSONResponse)

        self.json = orjson.dumps(payload)
        self.gz = _gzip.compress(self.json, compresslevel=6)


async def cached_response(
    request: Request,
    key: str,
    ttl: float,
    tags: Iterable[str],
    producer: Callable[[], Awaitable[Any]],
) -> Response:
    """get_or_set + serialize-once. *producer* returns a JSON-ready
    dict/list; the encoded form is what's cached."""
    import asyncio

    enc = cache.get(key)
    if enc is None:
        lock = _key_locks.get(key)
        if lock is None:
            lock = _key_locks.setdefault(key, asyncio.Lock())
        async with lock:
            enc = cache.get(key)
            if enc is None:
                payload = await producer()
                enc = await asyncio.to_thread(_Encoded, payload)
                cache.set(key, enc, ttl, tags)

    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(
            enc.gz, media_type="application/json",
            headers={"content-encoding": "gzip", "content-length": str(len(enc.gz))},
        )
    return Response(enc.json, media_type="application/json")


# ── tag / key helpers (single source of truth for cache-key shapes) ──────

def lmv_snapshot_tag(schema: str) -> str:
    return f"lmv-snapshot:{schema}"


def historic_tag(schema: str) -> str:
    return f"historic:{schema}"


def opening_range_tag(schema: str) -> str:
    return f"opening-range:{schema}"


def setting_key(schema: str, user_id: str, key: str) -> str:
    return f"setting:{schema}:{user_id}:{key}"
