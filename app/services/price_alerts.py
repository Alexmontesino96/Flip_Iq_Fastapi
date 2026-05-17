"""Price alert checker — notifies users when watchlist targets are hit."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.user import User
from app.models.watchlist import WatchlistItem, Watchlist

logger = logging.getLogger(__name__)


async def check_price_alerts(
    product_id: int,
    ebay_price: float | None,
    amazon_price: float | None,
    db: AsyncSession,
) -> None:
    """Check if any watchlist items have target_buy_price hit by new prices."""
    # Get the lowest available price
    prices = [p for p in [ebay_price, amazon_price] if p is not None]
    if not prices:
        return

    current_min_price = min(prices)

    # Find watchlist items watching this product with a target price
    result = await db.execute(
        select(WatchlistItem, Watchlist, User)
        .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
        .join(User, Watchlist.user_id == User.id)
        .where(
            WatchlistItem.product_id == product_id,
            WatchlistItem.target_buy_price.isnot(None),
        )
    )
    rows = result.all()

    for item, watchlist, user in rows:
        target = float(item.target_buy_price)
        if current_min_price <= target:
            # Price dropped below target — notify
            await _send_price_alert(user, item, current_min_price, target)


async def _send_price_alert(user: User, item: WatchlistItem, price: float, target: float) -> None:
    """Send push notification + Customer.io event for price drop."""
    product_title = item.product.title if item.product else f"Product #{item.product_id}"

    # OneSignal push
    if user.onesignal_subscription_id:
        from app.services import onesignal
        try:
            await onesignal.send_price_alert(
                subscription_id=user.onesignal_subscription_id,
                product_title=product_title,
                current_price=price,
                target_price=target,
            )
        except Exception as e:
            logger.warning("Push alert failed for user %d: %s", user.id, e)

    # Customer.io event
    try:
        from app.services import customerio
        await customerio.track(
            user.id,
            "price_alert_triggered",
            product=product_title,
            current_price=price,
            target_price=target,
            product_id=item.product_id,
        )
    except Exception as e:
        logger.warning("Customer.io price alert failed for user %d: %s", user.id, e)

    logger.info(
        "Price alert: user=%d product='%s' price=$%.2f target=$%.2f",
        user.id, product_title, price, target,
    )
