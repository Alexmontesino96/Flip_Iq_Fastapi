"""Admin endpoints: dashboard, usage metrics, manual reviews."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.database import get_db
from app.models.manual_review import ManualReviewRequest
from app.models.user import User
from app.models.analysis import Analysis
from app.models.product import Product
from app.models.subscription import Subscription
from app.models.webhook_event import WebhookEvent

logger = logging.getLogger("flipiq.admin")

router = APIRouter()

# Admin emails — only these users can access admin endpoints
ADMIN_EMAILS = {
    "alexmontesinocastro9@gmail.com",
    "alexmontesino96@icloud.com",
}


def _require_admin(user: User) -> None:
    if user.email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/reviews")
async def list_reviews(
    status: str = Query("pending", regex="^(pending|in_progress|resolved|dismissed|all)$"),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List manual review requests (admin only)."""
    _require_admin(user)

    query = select(ManualReviewRequest).order_by(ManualReviewRequest.created_at.desc())
    if status != "all":
        query = query.where(ManualReviewRequest.status == status)
    query = query.limit(limit)

    result = await db.execute(query)
    reviews = result.scalars().all()

    items = []
    for r in reviews:
        # Get user email
        user_email = None
        if r.user_id:
            u = await db.get(User, r.user_id)
            user_email = u.email if u else None

        items.append({
            "id": r.id,
            "query": r.query,
            "barcode": r.barcode,
            "cost_price": float(r.cost_price) if r.cost_price else None,
            "marketplace": r.marketplace,
            "status": r.status,
            "user_id": r.user_id,
            "user_email": user_email,
            "analysis_id": r.analysis_id,
            "resolved_analysis_id": r.resolved_analysis_id,
            "admin_notes": r.admin_notes,
            "created_at": r.created_at.isoformat(),
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        })

    return {"reviews": items, "total": len(items)}


@router.get("/reviews/stats")
async def review_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get counts by status (admin only)."""
    _require_admin(user)

    result = await db.execute(
        select(ManualReviewRequest.status, func.count())
        .group_by(ManualReviewRequest.status)
    )
    counts = {row[0]: row[1] for row in result.all()}
    return {
        "pending": counts.get("pending", 0),
        "in_progress": counts.get("in_progress", 0),
        "resolved": counts.get("resolved", 0),
        "dismissed": counts.get("dismissed", 0),
    }


@router.patch("/reviews/{review_id}")
async def update_review(
    review_id: int,
    status: str | None = None,
    admin_notes: str | None = None,
    resolved_analysis_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a review request: change status, add notes, link resolved analysis."""
    _require_admin(user)

    review = await db.get(ManualReviewRequest, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    if status:
        review.status = status
    if admin_notes is not None:
        review.admin_notes = admin_notes
    if resolved_analysis_id is not None:
        # Verify the analysis exists
        analysis = await db.get(Analysis, resolved_analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="Analysis not found")
        review.resolved_analysis_id = resolved_analysis_id
        review.status = "resolved"
        review.resolved_at = datetime.now(timezone.utc)

    await db.commit()

    return {
        "id": review.id,
        "status": review.status,
        "resolved_analysis_id": review.resolved_analysis_id,
        "admin_notes": review.admin_notes,
        "resolved_at": review.resolved_at.isoformat() if review.resolved_at else None,
    }


@router.delete("/reviews/{review_id}")
async def dismiss_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Dismiss a review request (mark as not actionable)."""
    _require_admin(user)

    review = await db.get(ManualReviewRequest, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    review.status = "dismissed"
    await db.commit()
    return {"id": review.id, "status": "dismissed"}


# ---------------------------------------------------------------------------
# Dashboard & usage metrics
# ---------------------------------------------------------------------------

TIER_DAILY_LIMITS = {"free": 5, "starter": 30, "pro": 100}


@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Overview metrics: users, analyses, subscriptions, webhook errors."""
    _require_admin(user)

    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    # Users by tier
    tier_rows = (await db.execute(
        select(User.tier, func.count()).group_by(User.tier)
    )).all()
    users_by_tier = {r[0]: r[1] for r in tier_rows}
    total_users = sum(users_by_tier.values())

    # New users
    new_today = (await db.execute(
        select(func.count()).where(User.created_at >= today)
    )).scalar() or 0
    new_week = (await db.execute(
        select(func.count()).where(User.created_at >= week_ago)
    )).scalar() or 0
    new_month = (await db.execute(
        select(func.count()).where(User.created_at >= month_ago)
    )).scalar() or 0

    # Analyses
    analyses_today = (await db.execute(
        select(func.count()).select_from(Analysis).where(Analysis.created_at >= today)
    )).scalar() or 0
    analyses_week = (await db.execute(
        select(func.count()).select_from(Analysis).where(Analysis.created_at >= week_ago)
    )).scalar() or 0
    analyses_month = (await db.execute(
        select(func.count()).select_from(Analysis).where(Analysis.created_at >= month_ago)
    )).scalar() or 0

    # Analyses by tier (today)
    tier_analysis_rows = (await db.execute(
        select(User.tier, func.count())
        .select_from(Analysis)
        .join(User, Analysis.user_id == User.id)
        .where(Analysis.created_at >= today)
        .group_by(User.tier)
    )).all()
    analyses_by_tier = {r[0]: r[1] for r in tier_analysis_rows}

    # Active subscriptions
    sub_rows = (await db.execute(
        select(Subscription.provider, func.count())
        .where(Subscription.status.in_(("active", "billing_retry", "trialing")))
        .group_by(Subscription.provider)
    )).all()
    subs_by_provider = {r[0]: r[1] for r in sub_rows}

    # Webhook errors last 24h
    webhook_errors = (await db.execute(
        select(func.count()).select_from(WebhookEvent)
        .where(WebhookEvent.status == "error", WebhookEvent.created_at >= now - timedelta(hours=24))
    )).scalar() or 0

    return {
        "users": {
            "total": total_users,
            "free": users_by_tier.get("free", 0),
            "starter": users_by_tier.get("starter", 0),
            "pro": users_by_tier.get("pro", 0),
        },
        "users_new": {"today": new_today, "week": new_week, "month": new_month},
        "analyses": {"today": analyses_today, "week": analyses_week, "month": analyses_month},
        "analyses_by_tier": {
            "free": analyses_by_tier.get("free", 0),
            "starter": analyses_by_tier.get("starter", 0),
            "pro": analyses_by_tier.get("pro", 0),
        },
        "subscriptions": {
            "active": sum(subs_by_provider.values()),
            "apple": subs_by_provider.get("apple", 0),
            "stripe": subs_by_provider.get("stripe", 0),
        },
        "webhook_errors_24h": webhook_errors,
    }


@router.get("/usage")
async def usage(
    period: str = Query("today", regex="^(today|week|month)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Usage breakdown by tier: active users, analyses, utilization."""
    _require_admin(user)

    now = datetime.now(timezone.utc)
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - timedelta(days=7)
    else:
        since = now - timedelta(days=30)

    rows = (await db.execute(
        select(
            User.tier,
            func.count(func.distinct(Analysis.user_id)).label("users_active"),
            func.count(Analysis.id).label("analyses"),
        )
        .select_from(Analysis)
        .join(User, Analysis.user_id == User.id)
        .where(Analysis.created_at >= since)
        .group_by(User.tier)
    )).all()

    daily_usage = []
    for row in rows:
        tier = row[0]
        users_active = row[1]
        analyses = row[2]
        limit = TIER_DAILY_LIMITS.get(tier, 5)
        avg = round(analyses / max(users_active, 1), 2)
        daily_usage.append({
            "tier": tier,
            "users_active": users_active,
            "analyses": analyses,
            "avg_per_user": avg,
            "limit": limit,
            "utilization_pct": round(avg / limit * 100, 1) if limit > 0 else 0,
        })

    return {"daily_usage": daily_usage, "period": period}


@router.get("/users")
async def list_users(
    tier: str | None = None,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List users with analysis counts."""
    _require_admin(user)

    query = (
        select(
            User.id, User.email, User.tier, User.created_at,
            func.count(Analysis.id).label("analysis_count"),
            func.max(Analysis.created_at).label("last_analysis"),
        )
        .outerjoin(Analysis, Analysis.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    if tier:
        query = query.where(User.tier == tier)

    rows = (await db.execute(query)).all()

    return {
        "users": [
            {
                "id": r[0],
                "email": r[1],
                "tier": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "analysis_count": r[4],
                "last_analysis": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]
    }


@router.get("/subscriptions")
async def list_subscriptions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List active subscriptions with user info."""
    _require_admin(user)

    rows = (await db.execute(
        select(
            Subscription.id, Subscription.provider, Subscription.plan,
            Subscription.status, Subscription.created_at,
            Subscription.apple_original_transaction_id,
            Subscription.stripe_subscription_id,
            User.email,
        )
        .join(User, Subscription.user_id == User.id)
        .order_by(Subscription.created_at.desc())
    )).all()

    return {
        "subscriptions": [
            {
                "id": r[0],
                "provider": r[1],
                "plan": r[2],
                "status": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "transaction_id": r[5] or r[6],
                "user_email": r[7],
            }
            for r in rows
        ]
    }


@router.get("/webhook-events")
async def list_webhook_events(
    status: str | None = None,
    provider: str | None = None,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List webhook events with optional filters."""
    _require_admin(user)

    query = select(WebhookEvent).order_by(WebhookEvent.created_at.desc()).limit(limit)
    if status:
        query = query.where(WebhookEvent.status == status)
    if provider:
        query = query.where(WebhookEvent.provider == provider)

    rows = (await db.execute(query)).scalars().all()

    return {
        "events": [
            {
                "id": e.id,
                "provider": e.provider,
                "event_type": e.event_type,
                "event_subtype": e.event_subtype,
                "status": e.status,
                "user_id": e.user_id,
                "transaction_id": e.transaction_id,
                "error_message": e.error_message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ]
    }
