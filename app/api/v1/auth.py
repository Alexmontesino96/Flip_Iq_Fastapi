import logging

import httpx
import stripe
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import get_current_user
from app.database import get_db
from app.models.analysis import Analysis, AnalysisFeedback
from app.models.manual_review import ManualReviewRequest
from app.models.subscription import Subscription
from app.models.user import User
from app.models.watchlist import Watchlist
from app.schemas.user import UserOut, UserUpdate

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.full_name is not None:
        user.full_name = payload.full_name
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/me", status_code=200)
async def delete_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the authenticated user's account and all associated data.

    Required by Apple App Store guidelines (section 5.1.1).
    Steps: cancel Stripe subscription → delete Supabase Auth user →
    anonymize DB references → delete user record.
    """
    user_id = user.id
    errors: list[str] = []

    # 1. Cancel Stripe subscription if active
    if user.stripe_customer_id:
        try:
            stripe.api_key = settings.stripe_secret_key
            subs = stripe.Subscription.list(
                customer=user.stripe_customer_id, status="active", limit=10,
            )
            for sub in subs.auto_paging_iter():
                stripe.Subscription.cancel(sub.id)
                logger.info("Cancelled Stripe subscription %s for user %d", sub.id, user_id)
        except stripe.StripeError as e:
            logger.warning("Stripe cancellation failed for user %d: %s", user_id, e)
            errors.append(f"stripe: {e}")

    # 2. Delete user from Supabase Auth
    if user.supabase_id and settings.supabase_url and settings.supabase_service_role_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.delete(
                    f"{settings.supabase_url}/auth/v1/admin/users/{user.supabase_id}",
                    headers={
                        "apikey": settings.supabase_service_role_key,
                        "Authorization": f"Bearer {settings.supabase_service_role_key}",
                    },
                )
                if resp.status_code not in (200, 204, 404):
                    logger.warning(
                        "Supabase user deletion returned %d for user %d: %s",
                        resp.status_code, user_id, resp.text,
                    )
                    errors.append(f"supabase: HTTP {resp.status_code}")
                else:
                    logger.info("Deleted Supabase Auth user %s for user %d", user.supabase_id, user_id)
        except httpx.HTTPError as e:
            logger.warning("Supabase deletion failed for user %d: %s", user_id, e)
            errors.append(f"supabase: {e}")

    # 3. Anonymize DB references (preserve data for audit, remove user link)
    await db.execute(
        update(Analysis).where(Analysis.user_id == user_id).values(user_id=None)
    )
    await db.execute(
        update(AnalysisFeedback).where(AnalysisFeedback.user_id == user_id).values(user_id=None)
    )
    await db.execute(
        update(ManualReviewRequest).where(ManualReviewRequest.user_id == user_id).values(user_id=None)
    )

    # 4. Delete watchlists (cascade deletes watchlist items)
    from sqlalchemy import select, delete
    watchlist_ids = (await db.execute(
        select(Watchlist.id).where(Watchlist.user_id == user_id)
    )).scalars().all()
    if watchlist_ids:
        from app.models.watchlist import WatchlistItem
        await db.execute(
            delete(WatchlistItem).where(WatchlistItem.watchlist_id.in_(watchlist_ids))
        )
        await db.execute(
            delete(Watchlist).where(Watchlist.user_id == user_id)
        )

    # 5. Delete subscription record
    await db.execute(
        delete(Subscription).where(Subscription.user_id == user_id)
    )

    # 6. Delete user
    await db.delete(user)
    await db.commit()

    logger.info("Account deleted for user %d (email=%s)", user_id, user.email)

    result = {"detail": "Account deleted successfully"}
    if errors:
        result["warnings"] = errors
    return result
