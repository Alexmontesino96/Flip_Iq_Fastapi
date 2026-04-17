"""Rate limiting + soft analysis gate.

- slowapi limiter: global DDoS protection (60/min per IP)
- check_analysis_gate(): soft gate for analysis endpoint (3 free → email → 100/day)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

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
ANON_LIMIT = 3
VERIFIED_LIMIT = 100
TTL_24H = 86400
TTL_30D = 2592000


@dataclass
class GateResult:
    allowed: bool
    tier: str  # "authenticated" | "verified" | "anonymous"
    remaining: int = 0
    reset_in: int = 0


async def check_analysis_gate(request: Request, redis) -> GateResult:
    """Check if the request is allowed to run an analysis.

    Returns GateResult without incrementing counters.
    Call increment_analysis_counter() after a successful analysis.

    Fail-open: any Redis error allows the request through.
    """
    # 1. Authenticated user (Supabase JWT) → unlimited
    # NOTE: this only bypasses the rate-limit gate; real auth validation
    # happens in the auth dependency. Any long Bearer header skips the gate.
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 40:
        return GateResult(allowed=True, tier="authenticated", remaining=VERIFIED_LIMIT)

    # Redis unavailable → fail open
    if redis is None:
        return GateResult(allowed=True, tier="anonymous", remaining=ANON_LIMIT)

    try:
        return await _check_gate_redis(request, redis)
    except Exception as e:
        logger.warning("Redis error en gate check, fail-open: %s", e)
        # Invalidate stale pool so next request attempts reconnection
        from app.core.redis_client import invalidate_pool
        invalidate_pool()
        return GateResult(allowed=True, tier="anonymous", remaining=ANON_LIMIT)


async def _check_gate_redis(request: Request, redis) -> GateResult:
    """Inner gate logic — all Redis calls here so caller can catch errors."""
    # 2. Email-verified cookie
    email: str | None = None
    token = request.cookies.get("flipiq_verified")
    if token:
        email = await redis.get(f"email_token:{token}")

    # 2b. Fallback: X-Verified-Email header (cross-domain where cookies fail)
    if not email:
        header_email = request.headers.get("x-verified-email", "").strip().lower()
        if header_email and await redis.get(f"waitlist:{header_email}"):
            email = header_email

    if email:
        count = int(await redis.get(f"verified:{email}") or 0)
        if count >= VERIFIED_LIMIT:
            ttl = await redis.ttl(f"verified:{email}")
            return GateResult(
                allowed=False, tier="verified", remaining=0, reset_in=max(ttl, 0)
            )
        return GateResult(
            allowed=True, tier="verified", remaining=VERIFIED_LIMIT - count
        )

    # 3. Anonymous — 3/day by IP + optional X-Client-ID
    ip = get_remote_address(request)
    ip_key = f"anon:ip:{ip}"
    count_ip = int(await redis.get(ip_key) or 0)

    client_id = request.headers.get("x-client-id", "")
    if client_id:
        client_key = f"anon:client:{client_id}"
        count_client = int(await redis.get(client_key) or 0)
        count = max(count_ip, count_client)
    else:
        count = count_ip

    if count >= ANON_LIMIT:
        ttl = await redis.ttl(ip_key)
        return GateResult(
            allowed=False, tier="anonymous", remaining=0, reset_in=max(ttl, 0)
        )

    return GateResult(
        allowed=True, tier="anonymous", remaining=ANON_LIMIT - count
    )


async def increment_analysis_counter(request: Request, redis, gate: GateResult) -> None:
    """Increment the counter AFTER a successful analysis."""
    if redis is None or gate.tier == "authenticated":
        return

    try:
        if gate.tier == "verified":
            email: str | None = None
            token = request.cookies.get("flipiq_verified")
            if token:
                email = await redis.get(f"email_token:{token}")
            if not email:
                email = request.headers.get("x-verified-email", "").strip().lower() or None
            if email:
                key = f"verified:{email}"
                count = await redis.incr(key)
                if count == 1:
                    await redis.expire(key, TTL_24H)
            return

        # Anonymous
        ip = get_remote_address(request)
        ip_key = f"anon:ip:{ip}"
        count = await redis.incr(ip_key)
        if count == 1:
            await redis.expire(ip_key, TTL_24H)

        client_id = request.headers.get("x-client-id", "")
        if client_id:
            client_key = f"anon:client:{client_id}"
            c = await redis.incr(client_key)
            if c == 1:
                await redis.expire(client_key, TTL_24H)
    except Exception as e:
        logger.warning("Error incrementando contador: %s", e)
