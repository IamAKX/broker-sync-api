import asyncio
import time

import pytest

from app.core import cache as cache_mod
from app.core.cache import TTLCache, get_or_set


def test_get_set_hit_and_miss():
    c = TTLCache()
    assert c.get("k") is None
    c.set("k", {"v": 1}, ttl=10)
    assert c.get("k") == {"v": 1}
    assert c.stats()["hits"] == 1
    assert c.stats()["misses"] == 1


def test_ttl_expiry(monkeypatch):
    c = TTLCache()
    t = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: t[0])
    c.set("k", "v", ttl=5)
    t[0] = 1004.9
    assert c.get("k") == "v"
    t[0] = 1005.1
    assert c.get("k") is None


def test_tag_invalidation_drops_all_keys_with_that_tag():
    c = TTLCache()
    c.set("a", 1, ttl=100, tags=["grp", "x"])
    c.set("b", 2, ttl=100, tags=["grp"])
    c.set("c", 3, ttl=100, tags=["other"])
    n = c.invalidate_tag("grp")
    assert n == 2
    assert c.get("a") is None and c.get("b") is None
    assert c.get("c") == 3
    # the co-tag "x" must not leave a dangling reference
    assert "x" not in c.stats() or c.stats()["tags"] == 1


def test_key_invalidation():
    c = TTLCache()
    c.set("a", 1, ttl=100, tags=["grp"])
    c.invalidate_key("a")
    assert c.get("a") is None
    assert c.invalidate_tag("grp") == 0  # tag link cleaned up too


def test_reset_key_replaces_tags():
    c = TTLCache()
    c.set("a", 1, ttl=100, tags=["t1"])
    c.set("a", 2, ttl=100, tags=["t2"])
    assert c.get("a") == 2
    assert c.invalidate_tag("t1") == 0     # old tag no longer points at "a"
    assert c.invalidate_tag("t2") == 1


def test_get_or_set_runs_producer_once_then_serves_cache():
    c = cache_mod.cache
    c.clear()
    calls = []

    async def producer():
        calls.append(1)
        return {"n": len(calls)}

    async def run():
        r1 = await get_or_set("key", 100, ["tag"], producer)
        r2 = await get_or_set("key", 100, ["tag"], producer)
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1 == r2 == {"n": 1}
    assert calls == [1]
    assert cache_mod.cache.invalidate_tag("tag") == 1


def test_get_or_set_reruns_after_invalidation():
    c = cache_mod.cache
    c.clear()
    state = {"v": 0}

    async def producer():
        state["v"] += 1
        return {"v": state["v"]}

    async def run():
        a = await get_or_set("k", 100, [], producer)
        cache_mod.cache.invalidate_key("k")
        b = await get_or_set("k", 100, [], producer)
        return a, b

    a, b = asyncio.run(run())
    assert a == {"v": 1}
    assert b == {"v": 2}
