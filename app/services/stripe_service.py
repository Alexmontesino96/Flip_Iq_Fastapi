"""Stripe billing service: checkout sessions, customer portal, webhook processing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.subscription import Subscription
from app.models.user import User

logger = logging.getLogger("flipiq.stripe")

stripe.api_key = settings.stripe_secret_key
stripe.api_version = "2025-04-30.basil"

# ---------------------------------------------------------------------------
# Plan configuration — map price IDs to internal tier names
# ---------------------------------------------------------------------------
PLAN_CONFIG: dict[str, dict] = {
    "starter": {
        "name": "Starter",
        "credits": 900,       # ~30/day
        "daily_limit": 30,
        "stripe_price_id": settings.stripe_price_starter,
    },
    "pro": {
        "name": "Pro",
        "credits": 3000,      # ~100/day
        "daily_limit": 100,
        "stripe_price_id": settings.stripe_price_pro,
    },
}

# Reverse lookup: stripe price_id → plan name (populated at startup)
_price_to_plan: dict[str, str] = {}

# Auto-register price IDs from config
for _plan_name, _plan_cfg in PLAN_CONFIG.items():
    _pid = _plan_cfg.get("stripe_price_id", "")
    if _pid:
        _price_to_plan[_pid] = _plan_name


def register_price(price_id: str, plan: str) -> None:
    """Register a Stripe price ID → plan mapping."""
    _price_to_plan[price_id] = plan


def plan_for_price(price_id: str) -> str:
    """Resolve plan name from price_id, default to 'starter'."""
    return _price_to_plan.get(price_id, "starter")


# ---------------------------------------------------------------------------
# Customer management
# ---------------------------------------------------------------------------
async def get_or_create_customer(user: User, db: AsyncSession) -> str:
    """Get existing Stripe customer or create one. Returns customer ID."""
    if user.stripe_customer_id:
        return user.stripe_customer_id

    customer = stripe.Customer.create(
        email=user.email,
        name=user.full_name or "",
        metadata={"user_id": str(user.id), "supabase_id": user.supabase_id},
    )
    user.stripe_customer_id = customer.id
    await db.commit()
    return customer.id


# ---------------------------------------------------------------------------
# Checkout Session (Stripe-hosted payment page)
# ---------------------------------------------------------------------------
async def create_checkout_session(
    user: User,
    price_id: str,
    success_url: str,
    cancel_url: str,
    db: AsyncSession,
) -> str:
    """Create a Stripe Checkout Session for subscription. Returns session URL."""
    customer_id = await get_or_create_customer(user, db)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        subscription_data={
            "metadata": {"user_id": str(user.id)},
        },
        allow_promotion_codes=True,
    )
    return session.url


# ---------------------------------------------------------------------------
# Customer Portal (self-service subscription management)
# ---------------------------------------------------------------------------
async def create_portal_session(
    user: User,
    return_url: str,
    db: AsyncSession,
) -> str:
    """Create a Stripe Customer Portal session. Returns portal URL."""
    customer_id = await get_or_create_customer(user, db)

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url,
    )
    return session.url


# ---------------------------------------------------------------------------
# Change plan (upgrade/downgrade existing subscription)
# ---------------------------------------------------------------------------
async def change_subscription_plan(
    user: User,
    new_price_id: str,
    db: AsyncSession,
) -> dict:
    """Change an existing subscription to a new price.

    - Upgrade: applied immediately, prorated.
    - Downgrade: scheduled for end of current period.
    """
    result = await db.execute(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.status.in_(("active", "trialing")),
        )
    )
    db_sub = result.scalar_one_or_none()
    if not db_sub:
        raise ValueError("No active subscription found")

    # Determine if upgrade or downgrade
    current_plan = db_sub.plan
    new_plan = plan_for_price(new_price_id)
    plan_order = {"starter": 1, "pro": 2}
    is_upgrade = plan_order.get(new_plan, 0) > plan_order.get(current_plan, 0)

    # Fetch subscription from Stripe
    sub = stripe.Subscription.retrieve(db_sub.stripe_subscription_id)
    item_id = sub.items.data[0].id

    if is_upgrade:
        # Upgrade: immediate, prorated
        updated = stripe.Subscription.modify(
            db_sub.stripe_subscription_id,
            items=[{"id": item_id, "price": new_price_id}],
            proration_behavior="create_prorations",
        )
    else:
        # Downgrade: at end of period
        updated = stripe.Subscription.modify(
            db_sub.stripe_subscription_id,
            items=[{"id": item_id, "price": new_price_id}],
            proration_behavior="none",
            billing_cycle_anchor="unchanged",
        )

    return {
        "plan": new_plan,
        "effective": "immediate" if is_upgrade else "end_of_period",
        "current_period_end": db_sub.current_period_end.isoformat() if db_sub.current_period_end else None,
    }


# ---------------------------------------------------------------------------
# Webhook event processing
# ---------------------------------------------------------------------------
def construct_webhook_event(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify and construct a Stripe webhook event."""
    return stripe.Webhook.construct_event(
        payload, sig_header, settings.stripe_webhook_secret
    )


async def handle_webhook_event(event: stripe.Event, db: AsyncSession) -> None:
    """Route webhook events to handlers."""
    handlers = {
        "checkout.session.completed": _handle_checkout_completed,
        "customer.subscription.updated": _handle_subscription_updated,
        "customer.subscription.deleted": _handle_subscription_deleted,
        "invoice.payment_failed": _handle_payment_failed,
        "invoice.payment_succeeded": _handle_payment_succeeded,
    }
    handler = handlers.get(event.type)
    if handler:
        await handler(event, db)
    else:
        logger.debug("Unhandled Stripe event: %s", event.type)


async def _handle_checkout_completed(event: stripe.Event, db: AsyncSession) -> None:
    """Process successful checkout — create/update subscription record."""
    session = event.data.object
    if session.mode != "subscription":
        return

    subscription_id = session.subscription
    customer_id = session.customer

    # Fetch full subscription from Stripe
    sub = stripe.Subscription.retrieve(subscription_id)
    price_id = sub.items.data[0].price.id
    plan = plan_for_price(price_id)

    # Find user by stripe_customer_id
    result = await db.execute(
        select(User).where(User.stripe_customer_id == customer_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        logger.error("No user found for Stripe customer %s", customer_id)
        return

    await _upsert_subscription(db, user, sub, plan)


async def _handle_subscription_updated(event: stripe.Event, db: AsyncSession) -> None:
    """Subscription changed (upgrade, downgrade, renewal, payment status)."""
    sub = event.data.object
    price_id = sub.items.data[0].price.id
    plan = plan_for_price(price_id)

    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == sub.id
        )
    )
    db_sub = result.scalar_one_or_none()
    if not db_sub:
        logger.warning("Subscription %s not found in DB", sub.id)
        return

    db_sub.status = sub.status
    db_sub.plan = plan
    db_sub.stripe_price_id = price_id
    db_sub.cancel_at_period_end = sub.cancel_at_period_end
    db_sub.current_period_start = _ts(sub.current_period_start)
    db_sub.current_period_end = _ts(sub.current_period_end)
    db_sub.updated_at = datetime.now(timezone.utc)

    # Update user tier based on subscription status
    user = await db.get(User, db_sub.user_id)
    if user:
        user.tier = plan if sub.status in ("active", "trialing") else "free"
        _set_credits(user, plan if sub.status in ("active", "trialing") else "free")

    await db.commit()


async def _handle_subscription_deleted(event: stripe.Event, db: AsyncSession) -> None:
    """Subscription canceled/expired."""
    sub = event.data.object

    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == sub.id
        )
    )
    db_sub = result.scalar_one_or_none()
    if not db_sub:
        return

    db_sub.status = "canceled"
    db_sub.updated_at = datetime.now(timezone.utc)

    user = await db.get(User, db_sub.user_id)
    if user:
        user.tier = "free"
        _set_credits(user, "free")

    await db.commit()


async def _handle_payment_succeeded(event: stripe.Event, db: AsyncSession) -> None:
    """Invoice paid — reset daily credits for the billing period."""
    invoice = event.data.object
    subscription_id = getattr(invoice, "subscription", None)
    if not subscription_id:
        return

    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == subscription_id
        )
    )
    db_sub = result.scalar_one_or_none()
    if not db_sub:
        return

    user = await db.get(User, db_sub.user_id)
    if user:
        _set_credits(user, db_sub.plan)
        await db.commit()
        logger.info("Credits reset for user %s (plan=%s)", user.id, db_sub.plan)


async def _handle_payment_failed(event: stripe.Event, db: AsyncSession) -> None:
    """Invoice payment failed — log it, Stripe handles retries."""
    invoice = event.data.object
    logger.warning(
        "Payment failed for customer %s, subscription %s",
        getattr(invoice, "customer", None),
        getattr(invoice, "subscription", None),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _upsert_subscription(
    db: AsyncSession, user: User, sub, plan: str,
) -> Subscription:
    """Create or update subscription record and sync user tier."""
    result = await db.execute(
        select(Subscription).where(
            Subscription.stripe_subscription_id == sub.id
        )
    )
    db_sub = result.scalar_one_or_none()

    price_id = sub.items.data[0].price.id
    now = datetime.now(timezone.utc)

    if db_sub:
        db_sub.status = sub.status
        db_sub.plan = plan
        db_sub.stripe_price_id = price_id
        db_sub.cancel_at_period_end = sub.cancel_at_period_end
        db_sub.current_period_start = _ts(sub.current_period_start)
        db_sub.current_period_end = _ts(sub.current_period_end)
        db_sub.updated_at = now
    else:
        db_sub = Subscription(
            user_id=user.id,
            stripe_subscription_id=sub.id,
            stripe_price_id=price_id,
            status=sub.status,
            plan=plan,
            cancel_at_period_end=sub.cancel_at_period_end,
            current_period_start=_ts(sub.current_period_start),
            current_period_end=_ts(sub.current_period_end),
            created_at=now,
            updated_at=now,
        )
        db.add(db_sub)

    # Sync user tier
    user.tier = plan if sub.status in ("active", "trialing") else "free"
    _set_credits(user, user.tier)

    await db.commit()
    return db_sub


def _set_credits(user: User, tier: str) -> None:
    """Set credits_remaining based on tier."""
    credits_map = {"free": 150, "starter": 900, "pro": 3000}
    user.credits_remaining = credits_map.get(tier, 150)


def _ts(unix_ts: int | None) -> datetime | None:
    """Convert Unix timestamp to timezone-aware datetime."""
    if unix_ts is None:
        return None
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Subscription status query
# ---------------------------------------------------------------------------
async def get_subscription_status(user: User, db: AsyncSession) -> dict | None:
    """Get current subscription status for a user."""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        return None

    return {
        "id": sub.stripe_subscription_id,
        "status": sub.status,
        "plan": sub.plan,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "cancel_at_period_end": sub.cancel_at_period_end,
    }
