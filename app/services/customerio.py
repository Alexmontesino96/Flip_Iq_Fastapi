"""Customer.io integration — identify users, track events."""

import asyncio
import logging
from functools import lru_cache

import customerio

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_cio() -> customerio.CustomerIO | None:
    if not settings.customerio_site_id or not settings.customerio_api_key:
        return None
    return customerio.CustomerIO(
        site_id=settings.customerio_site_id,
        api_key=settings.customerio_api_key,
    )


async def identify(user, platform: str = "web", signup_source: str = "organic") -> None:
    """Create or update a person in Customer.io. Never raises."""
    cio = _get_cio()
    if not cio:
        return

    def _sync():
        cio.identify(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name or "",
            plan=user.tier,
            platform=platform,
            signup_source=signup_source,
            created_at=int(user.created_at.timestamp()),
            supabase_id=user.supabase_id,
        )

    try:
        await asyncio.get_event_loop().run_in_executor(None, _sync)
        logger.debug("Customer.io identify ok for user %s", user.id)
    except Exception as e:
        logger.warning("Customer.io identify error: %s", e)


async def track(user_id: int, event_name: str, **data) -> None:
    """Track an event for a customer. Never raises."""
    cio = _get_cio()
    if not cio:
        return

    def _sync():
        cio.track(customer_id=str(user_id), name=event_name, **data)

    try:
        await asyncio.get_event_loop().run_in_executor(None, _sync)
        logger.debug("Customer.io track '%s' ok for user %s", event_name, user_id)
    except Exception as e:
        logger.warning("Customer.io track error: %s", e)


async def track_signup(user, platform: str = "web") -> None:
    """Identify + track signed_up event. Call on new user creation."""
    await identify(user, platform=platform, signup_source="organic")
    await track(user.id, "signed_up", plan=user.tier, platform=platform)


async def update_plan(user_id: int, new_tier: str) -> None:
    """Update the plan attribute on an existing person. Never raises."""
    cio = _get_cio()
    if not cio:
        return

    def _sync():
        cio.identify(id=str(user_id), plan=new_tier)

    try:
        await asyncio.get_event_loop().run_in_executor(None, _sync)
        logger.debug("Customer.io update_plan ok for user %s → %s", user_id, new_tier)
    except Exception as e:
        logger.warning("Customer.io update_plan error: %s", e)
