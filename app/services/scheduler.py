"""Internal scheduler — runs daily jobs without external cron dependency.

Uses asyncio.create_task with a sleep loop that checks every 30 minutes
if it's time to run the daily price update. Protected by Redis lock to
prevent duplicate runs across restarts or multiple workers.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger("flipiq.scheduler")

_task: asyncio.Task | None = None


def start_scheduler() -> asyncio.Task | None:
    """Launch the scheduler background task. Call from lifespan startup."""
    global _task
    if not settings.cron_price_enabled:
        logger.info("Scheduler disabled (CRON_PRICE_ENABLED=false)")
        return None

    _task = asyncio.create_task(_scheduler_loop())
    logger.info("Scheduler started — price update at %02d:00 UTC daily", settings.cron_price_hour)
    return _task


async def _scheduler_loop():
    """Main loop: checks every 30 min if it's time to run the daily job."""
    last_run_date: str | None = None

    # Wait 60s after startup to let the app stabilize
    await asyncio.sleep(60)

    while True:
        try:
            now = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")

            # Run if: correct hour AND hasn't run today
            if now.hour == settings.cron_price_hour and last_run_date != today_str:
                # Redis lock to prevent concurrent runs across workers
                from app.core.redis_client import get_redis

                redis = get_redis()
                can_run = True
                if redis:
                    lock = await redis.set(
                        "cron:price_update:running", "1", nx=True, ex=1800
                    )
                    can_run = bool(lock)

                if can_run:
                    logger.info("Scheduler: starting daily price update")
                    try:
                        from app.services.price_tracker import run_daily_price_update

                        result = await run_daily_price_update()
                        last_run_date = today_str
                        logger.info("Scheduler: price update done — %s", result)
                    except Exception as e:
                        logger.error("Scheduler: price update failed — %s", e)
                    finally:
                        if redis:
                            await redis.delete("cron:price_update:running")
                else:
                    logger.info("Scheduler: price update already running (lock held)")
                    last_run_date = today_str  # Don't retry this hour

        except asyncio.CancelledError:
            logger.info("Scheduler: shutting down")
            break
        except Exception as e:
            logger.error("Scheduler loop error: %s", e)

        # Check every 30 minutes
        await asyncio.sleep(1800)
