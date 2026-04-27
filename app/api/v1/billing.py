"""Billing endpoints: Stripe Checkout, Customer Portal, Webhook, Status."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import TIER_DAILY_LIMITS
from app.core.redis_client import get_redis
from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.billing import (
    CheckoutRequest,
    CheckoutResponse,
    PortalRequest,
    PortalResponse,
    SubscriptionStatus,
)
from app.services import stripe_service

logger = logging.getLogger("flipiq.billing")

router = APIRouter()


@router.get("/me")
async def get_my_usage(
    user: User = Depends(get_current_user),
    redis=Depends(get_redis),
):
    """Return current plan, daily limit, and scans used/remaining today."""
    tier = user.tier
    daily_limit = TIER_DAILY_LIMITS.get(tier, 5)
    used_today = 0
    reset_in = 0

    if redis:
        try:
            key = f"tier:{tier}:{user.id}"
            used_today = int(await redis.get(key) or 0)
            ttl = await redis.ttl(key)
            reset_in = max(ttl, 0)
        except Exception:
            pass

    return {
        "plan": tier,
        "daily_limit": daily_limit,
        "scans_used_today": used_today,
        "scans_remaining_today": max(daily_limit - used_today, 0),
        "reset_in_seconds": reset_in,
    }


_PLAN_CATALOG = [
    {
        "id": "free",
        "name": "Free",
        "price": 0,
        "original_price": None,
        "daily_limit": 5,
        "stripe_price_id": None,
        "tag": None,
        "ai_unlocked": False,
        "features": [
            "eBay comps only",
            "Keyword search",
            "Basic flip score",
            "1 watchlist",
        ],
    },
    {
        "id": "starter",
        "name": "Starter",
        "price": 9.99,
        "original_price": 14.99,
        "daily_limit": 30,
        "stripe_price_id": None,  # filled at runtime
        "tag": "Launch price",
        "ai_unlocked": True,
        "features": [
            "Everything in Free",
            "AI analysis unlocked",
            "eBay + Amazon comps",
            "Barcode scanning",
            "Push alerts",
            "5 watchlists",
        ],
    },
    {
        "id": "pro",
        "name": "Pro",
        "price": 19.99,
        "original_price": 29.99,
        "daily_limit": 100,
        "stripe_price_id": None,  # filled at runtime
        "tag": "Launch price",
        "ai_unlocked": True,
        "features": [
            "Everything in Starter",
            "Market Intelligence AI",
            "CSV export",
            "Push + email alerts",
            "Unlimited watchlists",
            "Priority support",
        ],
    },
]


@router.get("/plans")
async def list_plans():
    """Return all plans with prices, features, and Stripe price IDs."""
    plans = []
    for plan in _PLAN_CATALOG:
        entry = {**plan}
        # Fill stripe_price_id from config for paid plans
        cfg = stripe_service.PLAN_CONFIG.get(plan["id"])
        if cfg:
            entry["stripe_price_id"] = cfg["stripe_price_id"]
        plans.append(entry)
    return {"plans": plans}


@router.post("/change-plan")
async def change_plan(
    payload: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change plan for users with active subscription (upgrade/downgrade).

    - Upgrade (basic→premium): immediate, prorated.
    - Downgrade (premium→basic): takes effect at end of billing period.
    """
    try:
        result = await stripe_service.change_subscription_plan(
            user=user,
            new_price_id=payload.price_id,
            db=db,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error changing plan: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(
    payload: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Checkout Session for subscription purchase."""
    try:
        url = await stripe_service.create_checkout_session(
            user=user,
            price_id=payload.price_id,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            db=db,
        )
        return CheckoutResponse(checkout_url=url)
    except Exception as e:
        logger.error("Error creating checkout session: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/portal", response_model=PortalResponse)
async def create_portal(
    payload: PortalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a Stripe Customer Portal session for subscription management."""
    try:
        url = await stripe_service.create_portal_session(
            user=user,
            return_url=payload.return_url,
            db=db,
        )
        return PortalResponse(portal_url=url)
    except Exception as e:
        logger.error("Error creating portal session: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/status", response_model=SubscriptionStatus)
async def get_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current subscription status for the authenticated user."""
    sub = await stripe_service.get_subscription_status(user, db)
    if sub:
        return SubscriptionStatus(
            has_subscription=True,
            plan=sub["plan"],
            status=sub["status"],
            current_period_end=sub["current_period_end"],
            cancel_at_period_end=sub["cancel_at_period_end"],
            stripe_customer_id=user.stripe_customer_id,
        )
    return SubscriptionStatus(
        has_subscription=False,
        plan=user.tier,
        stripe_customer_id=user.stripe_customer_id,
    )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Stripe webhook events. No auth — verified by Stripe signature."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe_service.construct_webhook_event(payload, sig_header)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Signature verification failed: {e}")

    logger.info("Stripe webhook: %s", event.type)
    await stripe_service.handle_webhook_event(event, db)

    return JSONResponse(content={"status": "ok"})
