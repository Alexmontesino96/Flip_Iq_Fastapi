"""Daily price tracker for watchlist products.

Fetches current eBay sold median and Amazon Buy Box price for all products
in user watchlists, stores in product_price_history, and triggers alerts.
"""

import asyncio
import logging
import statistics
import time
from datetime import date, datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.price_history import ProductPriceHistory
from app.models.product import Product

logger = logging.getLogger(__name__)


async def _fetch_ebay_price(product: Product) -> tuple[float | None, int | None]:
    """Fetch current eBay median sold price for a product. Returns (median, sold_count)."""
    from app.services.marketplace.ebay_scraper import scrape_sold_listings

    keyword = product.title
    if not keyword:
        return None, None

    proxy = settings.residential_proxy_url or None
    try:
        results = await scrape_sold_listings(keyword, limit=10, proxy_url=proxy)
        if not results:
            return None, None

        prices = []
        for r in results:
            price = r.get("soldPrice") or r.get("totalPrice")
            if price:
                try:
                    prices.append(float(price))
                except (ValueError, TypeError):
                    pass

        if not prices:
            return None, None

        return round(statistics.median(prices), 2), len(results)
    except Exception as e:
        logger.warning("eBay price fetch failed for '%s': %s", keyword, e)
        return None, None


async def _fetch_amazon_price(product: Product) -> float | None:
    """Fetch current Amazon Buy Box price via Keepa."""
    if not product.barcode or not settings.keepa_api_key:
        return None

    from app.services.marketplace.amazon import AmazonClient

    try:
        client = AmazonClient()
        products = await client._keepa_product_by_code(product.barcode)
        if not products:
            return None

        # Extract Buy Box price from first product
        keepa_product = products[0]
        stats = keepa_product.get("stats", {})
        buy_box = stats.get("buyBoxPrice")
        if buy_box and buy_box > 0:
            return round(buy_box / 100, 2)  # Keepa stores in cents

        # Fallback: current price from offers
        current = stats.get("current", [])
        if current and len(current) > 1 and current[1] and current[1] > 0:
            return round(current[1] / 100, 2)

        return None
    except Exception as e:
        logger.warning("Amazon price fetch failed for '%s': %s", product.title, e)
        return None


async def run_daily_price_update() -> dict:
    """Run the daily price update for all watchlist products.

    Returns summary dict with counts.
    """
    from app.services.price_alerts import check_price_alerts

    t0 = time.perf_counter()
    updated = 0
    errors = 0

    async with async_session() as db:
        # Get distinct product_ids that are in any watchlist
        result = await db.execute(text("""
            SELECT DISTINCT wi.product_id
            FROM watchlist_items wi
            JOIN products p ON p.id = wi.product_id
            WHERE p.title IS NOT NULL AND p.title != ''
        """))
        product_ids = [row[0] for row in result.fetchall()]

        if not product_ids:
            return {"products_updated": 0, "errors": 0, "duration_ms": 0}

        logger.info("Price tracker: %d products to update", len(product_ids))
        today = date.today()

        # Process in batches of 10
        batch_size = 10
        for i in range(0, len(product_ids), batch_size):
            batch = product_ids[i : i + batch_size]

            for pid in batch:
                try:
                    product = await db.get(Product, pid)
                    if not product:
                        continue

                    # Check if already updated today
                    existing = await db.execute(
                        select(ProductPriceHistory).where(
                            ProductPriceHistory.product_id == pid,
                            ProductPriceHistory.recorded_date == today,
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue  # Already done today

                    # Fetch prices in parallel
                    ebay_result, amazon_result = await asyncio.gather(
                        _fetch_ebay_price(product),
                        _fetch_amazon_price(product),
                        return_exceptions=True,
                    )

                    ebay_price, ebay_count = (
                        ebay_result if isinstance(ebay_result, tuple) else (None, None)
                    )
                    amazon_price = (
                        amazon_result if not isinstance(amazon_result, Exception) else None
                    )

                    # Skip if both failed
                    if ebay_price is None and amazon_price is None:
                        errors += 1
                        continue

                    # Insert price history
                    history = ProductPriceHistory(
                        product_id=pid,
                        recorded_date=today,
                        ebay_median_price=ebay_price,
                        amazon_price=amazon_price,
                        ebay_sold_count=ebay_count,
                        source="cron",
                    )
                    db.add(history)

                    # Update product's cached price
                    if ebay_price:
                        product.ebay_avg_sold_price = ebay_price
                    product.price_updated_at = datetime.now(timezone.utc)

                    await db.commit()
                    updated += 1

                    # Check alerts
                    await check_price_alerts(pid, ebay_price, amazon_price, db)

                except Exception as e:
                    logger.warning("Price update failed for product %d: %s", pid, e)
                    errors += 1
                    try:
                        await db.rollback()
                    except Exception:
                        pass

            # Delay between batches to respect rate limits
            if i + batch_size < len(product_ids):
                await asyncio.sleep(3)

    duration_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "Price tracker done: %d updated, %d errors, %dms",
        updated, errors, duration_ms,
    )
    return {"products_updated": updated, "errors": errors, "duration_ms": duration_ms}
