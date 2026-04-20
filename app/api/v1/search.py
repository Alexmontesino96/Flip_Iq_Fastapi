"""Autocomplete / typeahead search endpoint.

Strategy: local DB (trigram + prefix) → eBay Browse API fallback → persist new products.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.normalize import normalize_title
from app.database import async_session, get_db
from app.services.marketplace.ebay_browse import search_keywords

logger = logging.getLogger("flipiq.search")

router = APIRouter()

# Minimum local results before hitting eBay
_LOCAL_THRESHOLD = 5


@router.get("/suggest")
async def suggest(
    q: str = Query(..., min_length=2, max_length=100, description="Search query"),
    limit: int = Query(8, ge=1, le=25),
    db: AsyncSession = Depends(get_db),
):
    """Hybrid autocomplete: local DB first, eBay Browse API fallback.

    Returns suggestions ranked by: prefix match > trigram similarity > popularity.
    New products from eBay are persisted in the background.
    """
    normalized_q = normalize_title(q)

    # 1. Local DB query: prefix + ILIKE + popularity ranking
    local_results = await db.execute(
        text("""
            SELECT
                id, title, brand, category, image_url, barcode,
                ebay_avg_sold_price, search_count,
                CASE
                    WHEN normalized_title LIKE :prefix THEN 3
                    WHEN normalized_title LIKE :contains THEN 2
                    ELSE 1
                END AS match_rank
            FROM products
            WHERE normalized_title LIKE :contains
            ORDER BY match_rank DESC, search_count DESC
            LIMIT :limit
        """),
        {
            "prefix": f"{normalized_q}%",
            "contains": f"%{normalized_q}%",
            "limit": limit,
        },
    )
    local = [dict(r._mapping) for r in local_results]

    # 2. If we have enough local results, return immediately
    if len(local) >= _LOCAL_THRESHOLD:
        return {"source": "local", "results": _format_results(local)}

    # 3. Fallback to eBay Browse API
    try:
        ebay_items = await search_keywords(q, limit=limit)
    except Exception:
        logger.exception("eBay Browse API search failed for q=%r", q)
        return {"source": "local_only", "results": _format_results(local)}

    # 4. Merge + dedupe by normalized_title
    seen = {normalize_title(r["title"]) for r in local}
    merged = list(local)
    new_products = []

    for item in ebay_items:
        norm = normalize_title(item.title)
        if norm in seen or not norm:
            continue
        seen.add(norm)

        product_dict = {
            "title": item.title,
            "brand": item.brand,
            "category": item.category,
            "image_url": item.image_url,
            "ebay_epid": item.epid,
            "price_hint": item.price,
        }
        merged.append(product_dict)
        new_products.append({
            "title": item.title,
            "normalized_title": norm,
            "brand": item.brand,
            "category": item.category,
            "image_url": item.image_url,
            "ebay_epid": item.epid,
        })

    # 5. Persist new products in background (fire-and-forget)
    if new_products:
        asyncio.create_task(_persist_new_products(new_products))

    return {"source": "hybrid", "results": _format_results(merged[:limit])}


@router.post("/suggest/{product_id}/select")
async def record_selection(
    product_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Increment popularity when user selects a suggestion."""
    await db.execute(
        text("""
            UPDATE products
            SET search_count = search_count + 1,
                last_seen_at = :now
            WHERE id = :id
        """),
        {"id": product_id, "now": datetime.now(timezone.utc)},
    )
    await db.commit()
    return {"ok": True}


def _format_results(results: list[dict]) -> list[dict]:
    """Normalize result shape for frontend."""
    formatted = []
    for r in results:
        formatted.append({
            "id": r.get("id"),
            "title": r.get("title", ""),
            "brand": r.get("brand"),
            "category": r.get("category"),
            "image_url": r.get("image_url"),
            "barcode": r.get("barcode"),
            "price_hint": r.get("ebay_avg_sold_price") or r.get("price_hint"),
            "search_count": r.get("search_count", 0),
        })
    return formatted


async def _persist_new_products(products: list[dict]) -> None:
    """Save new products from eBay in background without blocking the response."""
    try:
        async with async_session() as db:
            for p in products:
                await db.execute(
                    text("""
                        INSERT INTO products
                            (title, normalized_title, brand, category, image_url, ebay_epid, created_at, updated_at)
                        VALUES
                            (:title, :normalized_title, :brand, :category, :image_url, :ebay_epid, NOW(), NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    p,
                )
            await db.commit()
        logger.info("Persisted %d new products from eBay", len(products))
    except Exception:
        logger.exception("Failed to persist new products")
