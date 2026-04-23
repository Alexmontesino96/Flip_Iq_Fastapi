"""Admin endpoints: manual review requests for not-found products."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.database import get_db
from app.models.manual_review import ManualReviewRequest
from app.models.user import User
from app.models.analysis import Analysis
from app.models.product import Product

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
