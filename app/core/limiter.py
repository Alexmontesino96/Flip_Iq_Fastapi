"""Rate limiting + soft analysis gate.

Tiers:
  - anonymous: 5 scans TOTAL (never resets, permanent counter)
  - free: 5 scans/day (registered user, resets every 24h)
  - starter: 30 scans/day ($14.99/mo)
  - pro: 100 scans/day ($29.99/mo)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger("flipiq.gate")

# ---------------------------------------------------------------------------
# Global rate limiter (slowapi, DDoS protection)
# ---------------------------------------------------------------------------
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)

# ---------------------------------------------------------------------------
# Soft analysis gate
# ---------------------------------------------------------------------------
ANON_LIMIT = 5        # 5 total, never resets
TTL_24H = 86400
TTL_30D = 2592000     # used by waitlist_route
VERIFIED_LIMIT = 100  # used by waitlist_route (legacy)

# Daily scan limits per tier (authenticated users)
TIER_DAILY_LIMITS: dict[str, int] = {
    "free": 5,
    "starter": 30,
    "pro": 100,
}


@dataclass
class GateResult:
    allowed: bool
    tier: str  # "anonymous" | "free" | "starter" | "pro"
    remaining: int = 0
    reset_in: int = 0


async def check_analysis_gate(
    request: Request, redis, db: AsyncSession | None = None,
    user=None,
) -> GateResult:
    """Check if the request is allowed to run an analysis.

    Returns GateResult without incrementing counters.
    Call increment_analysis_counter() after a successful analysis.

    Fail-open: any Redis error allows the request through.
    """
    # 1. Authenticated user → tier-based daily limit
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 40:
        user_tier = user.tier if user else "free"
        daily_limit = TIER_DAILY_LIMITS.get(user_tier, 5)

        if redis is None:
            return GateResult(allowed=True, tier=user_tier, remaining=daily_limit)

        try:
            user_id = str(user.id) if user else "unknown"
            key = f"tier:{user_tier}:{user_id}"
            count = int(await redis.get(key) or 0)
            if count >= daily_limit:
                ttl = await redis.ttl(key)
                return GateResult(
                    allowed=False, tier=user_tier, remaining=0, reset_in=max(ttl, 0)
                )
            return GateResult(
                allowed=True, tier=user_tier, remaining=daily_limit - count
            )
        except Exception as e:
            logger.warning("Redis error en auth gate, fail-open: %s", e)
            return GateResult(allowed=True, tier=user_tier, remaining=daily_limit)

    # 2. Anonymous — 5 scans TOTAL (never resets)
    if redis is None:
        return GateResult(allowed=True, tier="anonymous", remaining=ANON_LIMIT)

    try:
        return await _check_anon_gate(request, redis)
    except Exception as e:
        logger.warning("Redis error en gate check, fail-open: %s", e)
        from app.core.redis_client import invalidate_pool
        invalidate_pool()
        return GateResult(allowed=True, tier="anonymous", remaining=ANON_LIMIT)


async def _check_anon_gate(request: Request, redis) -> GateResult:
    """Anonymous gate: 5 scans total, never resets."""
    ip = get_remote_address(request)
    ip_key = f"anon:total:{ip}"
    count_ip = int(await redis.get(ip_key) or 0)

    client_id = request.headers.get("x-client-id", "")
    if client_id:
        client_key = f"anon:total:client:{client_id}"
        count_client = int(await redis.get(client_key) or 0)
        count = max(count_ip, count_client)
    else:
        count = count_ip

    if count >= ANON_LIMIT:
        return GateResult(
            allowed=False, tier="anonymous", remaining=0, reset_in=0
        )

    return GateResult(
        allowed=True, tier="anonymous", remaining=ANON_LIMIT - count
    )


async def increment_analysis_counter(
    request: Request, redis, gate: GateResult, user=None,
) -> None:
    """Increment the counter AFTER a successful analysis."""
    if redis is None:
        return

    # Authenticated user — increment tier-based daily counter
    if gate.tier in TIER_DAILY_LIMITS and user:
        try:
            key = f"tier:{gate.tier}:{user.id}"
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, TTL_24H)
        except Exception as e:
            logger.warning("Error incrementando contador tier: %s", e)
        return

    # Anonymous — permanent counter (no TTL)
    try:
        ip = get_remote_address(request)
        ip_key = f"anon:total:{ip}"
        await redis.incr(ip_key)  # no expire — permanent

        client_id = request.headers.get("x-client-id", "")
        if client_id:
            client_key = f"anon:total:client:{client_id}"
            await redis.incr(client_key)  # no expire — permanent
    except Exception as e:
        logger.warning("Error incrementando contador anon: %s", e)
