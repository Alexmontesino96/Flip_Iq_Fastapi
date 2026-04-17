"""Async Redis client — singleton pool, fail-open on connection errors."""

import logging

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("flipiq.redis")

_pool: aioredis.Redis | None = None


_last_fail: float = 0.0
_RECONNECT_COOLDOWN = 30  # seconds between reconnection attempts


async def get_redis() -> aioredis.Redis | None:
    """Return shared Redis connection. Returns None if unavailable.

    If previously connected, returns the pool directly (fail-open is
    handled by callers catching Redis exceptions).
    If pool is None, re-attempts connection with a cooldown to avoid
    hammering a down Redis on every request.
    """
    global _pool, _last_fail
    if _pool is not None:
        return _pool

    import time
    now = time.monotonic()
    if now - _last_fail < _RECONNECT_COOLDOWN:
        return None

    try:
        _pool = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        await _pool.ping()
        logger.info("Redis conectado: %s", settings.redis_url.split("@")[-1])
        return _pool
    except Exception as e:
        logger.warning("Redis no disponible, gate desactivado: %s", e)
        _last_fail = now
        _pool = None
        return None


def invalidate_pool() -> None:
    """Mark pool as stale so next get_redis() attempts reconnection."""
    global _pool
    _pool = None


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
