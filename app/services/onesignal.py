"""OneSignal integration — sync tags for Journeys automation."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.onesignal.com"


def _get_headers() -> dict[str, str]:
    return {
        "Authorization": f"Key {settings.onesignal_rest_api_key}",
        "Content-Type": "application/json",
    }


async def set_tags(subscription_id: str | None, tags: dict[str, str]) -> None:
    """Set tags on a OneSignal user by subscription ID. Never raises."""
    if not subscription_id or not settings.onesignal_app_id or not settings.onesignal_rest_api_key:
        return

    url = f"{BASE_URL}/apps/{settings.onesignal_app_id}/users/by/onesignal_subscription_id/{subscription_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(url, headers=_get_headers(), json={"tags": tags})
            if resp.status_code >= 400:
                logger.warning("OneSignal set_tags failed (%s): %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("OneSignal set_tags error: %s", e)


async def tag_new_user(subscription_id: str | None, user) -> None:
    """Tag a user as new so OneSignal Journeys can trigger onboarding."""
    await set_tags(subscription_id, {
        "new_user": "true",
        "tier": user.tier,
        "signup_date": user.created_at.strftime("%Y-%m-%d"),
    })


async def update_tier_tag(subscription_id: str | None, new_tier: str) -> None:
    """Update the tier tag after a subscription change."""
    await set_tags(subscription_id, {"tier": new_tier})


async def send_price_alert(
    subscription_id: str,
    product_title: str,
    current_price: float,
    target_price: float,
) -> None:
    """Send a push notification for a price drop alert. Never raises."""
    if not settings.onesignal_app_id or not settings.onesignal_rest_api_key:
        return

    url = f"{BASE_URL}/api/v1/notifications"
    payload = {
        "app_id": settings.onesignal_app_id,
        "include_subscription_ids": [subscription_id],
        "headings": {"en": "Price Drop Alert 📉"},
        "contents": {
            "en": f"{product_title} dropped to ${current_price:.2f} (your target: ${target_price:.2f})"
        },
        "data": {"type": "price_alert", "product_title": product_title, "price": current_price},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=_get_headers(), json=payload)
            if resp.status_code >= 400:
                logger.warning("OneSignal push failed (%s): %s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("OneSignal push error: %s", e)
