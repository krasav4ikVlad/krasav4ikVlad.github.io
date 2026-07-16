"""TTL cache for heavy reports: Redis when available, in-memory fallback.

Realtime metrics must NOT go through this cache — only aggregated reports.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import time
from typing import Any, Awaitable, Callable

import redis.asyncio as aioredis

from .config import get_settings

log = logging.getLogger("app.cache")


def _json_default(o: Any) -> Any:
    from datetime import date, datetime
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


class TTLCache:
    """Redis-backed JSON cache that degrades to a process-local dict."""

    def __init__(self, redis_url: str = "") -> None:
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._redis_dead_until = 0.0
        self._local: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> aioredis.Redis | None:
        if not self._redis_url or time.monotonic() < self._redis_dead_until:
            return None
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._redis_url, socket_connect_timeout=2, socket_timeout=2,
                decode_responses=True)
        return self._redis

    def _mark_redis_dead(self) -> None:
        # don't retry a dead redis on every request
        self._redis_dead_until = time.monotonic() + 30

    async def get(self, key: str) -> Any | None:
        r = await self._get_redis()
        if r is not None:
            try:
                raw = await r.get(key)
                return json.loads(raw) if raw is not None else None
            except Exception:
                self._mark_redis_dead()
        entry = self._local.get(key)
        if entry and entry[0] > time.monotonic():
            return json.loads(entry[1])
        self._local.pop(key, None)
        return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        raw = json.dumps(value, ensure_ascii=False, default=_json_default)
        r = await self._get_redis()
        if r is not None:
            try:
                await r.set(key, raw, ex=ttl)
                return
            except Exception:
                self._mark_redis_dead()
        async with self._lock:
            if len(self._local) > 512:  # crude bound
                now = time.monotonic()
                self._local = {k: v for k, v in self._local.items() if v[0] > now}
                # still over the cap (all alive)? evict the oldest entries
                if len(self._local) > 512:
                    for stale_key in sorted(self._local,
                                            key=lambda k: self._local[k][0])[:256]:
                        del self._local[stale_key]
            self._local[key] = (time.monotonic() + ttl, raw)

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass


_cache: TTLCache | None = None


def get_cache() -> TTLCache:
    global _cache
    if _cache is None:
        _cache = TTLCache(get_settings().redis_url)
    return _cache


def cached(ttl: int, prefix: str) -> Callable:
    """Cache an async function's JSON-serializable result.

    The key is built from ``prefix`` and the call's kwargs (positional args
    are not allowed to avoid accidentally keying on connection objects).
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            key_src = json.dumps(kwargs, sort_keys=True, default=_json_default)
            key = f"vpndash:{prefix}:{hashlib.sha1(key_src.encode()).hexdigest()}"
            cache = get_cache()
            hit = await cache.get(key)
            if hit is not None:
                return hit
            result = await fn(**kwargs)
            if result is not None:
                await cache.set(key, json.loads(json.dumps(
                    result, ensure_ascii=False, default=_json_default)), ttl)
            return result
        return wrapper
    return decorator
