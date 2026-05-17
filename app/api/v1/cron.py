"""Cron job endpoints — secured by X-Cron-Secret header."""

import logging

from fastapi import APIRouter, HTTPException, Header

from app.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_secret(x_cron_secret: str = Header(...)) -> None:
    if not settings.cron_secret or x_cron_secret != settings.cron_secret:
        raise HTTPException(403, "Invalid cron secret")


@router.post("/price-update")
async def price_update(x_cron_secret: str = Header(...)):
    """Daily price update for all watchlist products.

    Triggered by external cron service (cron-job.org or Render Cron Job).
    Secured by X-Cron-Secret header.
    """
    _verify_secret(x_cron_secret)

    # Prevent concurrent runs with Redis lock
    redis = get_redis()
    if redis:
        lock = await redis.set("cron:price_update:running", "1", nx=True, ex=1800)
        if not lock:
            return {"status": "skipped", "reason": "already running"}

    try:
        from app.services.price_tracker import run_daily_price_update
        result = await run_daily_price_update()
        return {"status": "ok", **result}
    finally:
        if redis:
            await redis.delete("cron:price_update:running")
