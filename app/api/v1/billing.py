"""Billing endpoints: Stripe Checkout, Customer Portal, Webhook, Status."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

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
