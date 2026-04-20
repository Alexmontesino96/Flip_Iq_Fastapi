"""Seed initial category configuration.

Run: python -m scripts.seed_categories

Creates 8 reseller categories with:
- Engine default overrides (shipping, return reserve, etc.)
- Channel enablement per category
- Fee schedules with eBay category-specific rates
- Default shipping templates
"""

import asyncio
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

CATEGORIES = [
    {
        "slug": "sneakers",
        "display_name": "Sneakers & Athletic Shoes",
        "ebay_category_ids": [15709, 93427],
        "engine_defaults": {
            "return_reserve_pct": 0.08,
            "shipping_cost": 15.00,
            "packaging_cost": 2.00,
            "risk_cv_threshold": 0.55,
            "confidence_burstiness_threshold": 0.25,
            "trend_demand_delta": 20,
            "execution_high_ticket_threshold": 200,
        },
    },
    {
        "slug": "trading_cards",
        "display_name": "Trading Cards & TCG",
        "ebay_category_ids": [183454, 261328, 183050],
        "engine_defaults": {
            "return_reserve_pct": 0.02,
            "shipping_cost": 1.50,
            "packaging_cost": 1.00,
            "risk_cv_threshold": 0.75,
            "risk_dispersion_threshold": 0.70,
            "confidence_sample_size": 10,
        },
    },
    {
        "slug": "electronics",
        "display_name": "Electronics & Gadgets",
        "ebay_category_ids": [9355, 175673, 171485, 178893, 169291, 164, 112529, 3676],
        "engine_defaults": {
            "return_reserve_pct": 0.08,
            "shipping_cost": 12.00,
            "packaging_cost": 3.00,
            "execution_high_ticket_threshold": 200,
        },
    },
    {
        "slug": "clothing",
        "display_name": "Clothing & Accessories",
        "ebay_category_ids": [11450, 40054],
        "engine_defaults": {
            "return_reserve_pct": 0.15,
            "shipping_cost": 8.00,
            "packaging_cost": 1.50,
            "risk_cv_threshold": 0.70,
        },
    },
    {
        "slug": "collectibles",
        "display_name": "Collectibles & Action Figures",
        "ebay_category_ids": [261068, 246, 1],
        "engine_defaults": {
            "return_reserve_pct": 0.03,
            "shipping_cost": 8.00,
            "packaging_cost": 2.00,
            "confidence_sample_size": 10,
        },
    },
    {
        "slug": "books_media",
        "display_name": "Books & Media",
        "ebay_category_ids": [267, 11232, 11233],
        "engine_defaults": {
            "return_reserve_pct": 0.03,
            "shipping_cost": 4.00,
            "packaging_cost": 1.00,
        },
    },
    {
        "slug": "home_garden",
        "display_name": "Home & Garden",
        "ebay_category_ids": [11700, 3034],
        "engine_defaults": {
            "return_reserve_pct": 0.05,
            "shipping_cost": 15.00,
            "packaging_cost": 3.00,
        },
    },
    {
        "slug": "toys",
        "display_name": "Toys & Building Sets",
        "ebay_category_ids": [11116, 220],
        "engine_defaults": {
            "return_reserve_pct": 0.05,
            "shipping_cost": 10.00,
            "packaging_cost": 2.00,
        },
    },
]

# Channel config per category: (category_slug, channel, is_enabled)
CATEGORY_CHANNELS = [
    # sneakers
    ("sneakers", "ebay", True),
    ("sneakers", "stockx", True),
    ("sneakers", "mercadolibre", True),
    ("sneakers", "facebook", True),
    ("sneakers", "amazon_fba", False),
    # trading_cards
    ("trading_cards", "ebay", True),
    ("trading_cards", "tcgplayer", True),
    ("trading_cards", "amazon_fba", False),
    # electronics
    ("electronics", "ebay", True),
    ("electronics", "amazon_fba", True),
    ("electronics", "mercadolibre", True),
    ("electronics", "facebook", True),
    # clothing
    ("clothing", "ebay", True),
    ("clothing", "mercadolibre", True),
    ("clothing", "poshmark", True),
    ("clothing", "amazon_fba", False),
    # collectibles
    ("collectibles", "ebay", True),
    ("collectibles", "amazon_fba", True),
    # books_media
    ("books_media", "ebay", True),
    ("books_media", "amazon_fba", True),
    # home_garden
    ("home_garden", "ebay", True),
    ("home_garden", "amazon_fba", True),
    ("home_garden", "facebook", True),
    # toys
    ("toys", "ebay", True),
    ("toys", "amazon_fba", True),
]

# Fee schedules: (channel, category_slug, fee_rate, fee_fixed, fee_note, price_min, price_max)
FEE_SCHEDULES = [
    # Global defaults (no category)
    ("ebay", None, 0.1325, 0.0, "eBay standard final value fee", None, None),
    ("amazon_fba", None, 0.15, 3.50, "Amazon FBA referral + fulfillment", None, None),
    ("mercadolibre", None, 0.16, 0.0, "MercadoLibre standard commission", None, None),
    ("facebook", None, 0.05, 0.0, "Facebook Marketplace selling fee", None, None),
    # Sneakers >$150 on eBay = 0% FVF (authentication program)
    ("ebay", "sneakers", 0.0, 0.0, "Sneakers >$150 authenticated: 0% FVF", 150.01, None),
    ("ebay", "sneakers", 0.1325, 0.0, "Sneakers ≤$150: standard FVF", None, 150.00),
    # Books/media on eBay = 14.55%
    ("ebay", "books_media", 0.1455, 0.0, "eBay books & media category rate", None, None),
    # StockX
    ("stockx", None, 0.095, 0.0, "StockX seller fee ~9.5%", None, None),
    # Poshmark
    ("poshmark", None, 0.20, 0.0, "Poshmark 20% on sales >$15", 15.01, None),
    ("poshmark", None, 0.0, 2.95, "Poshmark flat $2.95 on sales ≤$15", None, 15.00),
    # TCGplayer
    ("tcgplayer", None, 0.1089, 0.0, "TCGplayer standard ~10.89%", None, None),
]

VALID_FROM = date(2024, 1, 1)


async def seed(db: AsyncSession) -> None:
    """Insert seed data, skipping existing rows."""
    # 1. Categories
    for cat in CATEGORIES:
        existing = await db.execute(
            text("SELECT id FROM categories WHERE slug = :slug"),
            {"slug": cat["slug"]},
        )
        if existing.first():
            print(f"  ⏭ Category '{cat['slug']}' already exists, skipping")
            continue

        await db.execute(
            text("""
                INSERT INTO categories (slug, display_name, ebay_category_ids, engine_defaults)
                VALUES (:slug, :display_name, :ebay_ids, CAST(:defaults AS jsonb))
            """),
            {
                "slug": cat["slug"],
                "display_name": cat["display_name"],
                "ebay_ids": cat["ebay_category_ids"],
                "defaults": __import__("json").dumps(cat["engine_defaults"]),
            },
        )
        print(f"  + Category '{cat['slug']}'")

    await db.flush()

    # 2. Category channels
    for cat_slug, channel, enabled in CATEGORY_CHANNELS:
        cat_row = await db.execute(
            text("SELECT id FROM categories WHERE slug = :slug"),
            {"slug": cat_slug},
        )
        cat = cat_row.first()
        if not cat:
            continue

        existing = await db.execute(
            text("SELECT id FROM category_channels WHERE category_id = :cid AND channel = :ch"),
            {"cid": cat[0], "ch": channel},
        )
        if existing.first():
            continue

        await db.execute(
            text("""
                INSERT INTO category_channels (category_id, channel, is_enabled)
                VALUES (:cid, :ch, :enabled)
            """),
            {"cid": cat[0], "ch": channel, "enabled": enabled},
        )
        print(f"  + Channel '{channel}' for '{cat_slug}' (enabled={enabled})")

    # 3. Fee schedules
    for channel, cat_slug, rate, fixed, note, pmin, pmax in FEE_SCHEDULES:
        cat_id = None
        if cat_slug:
            cat_row = await db.execute(
                text("SELECT id FROM categories WHERE slug = :slug"),
                {"slug": cat_slug},
            )
            cat = cat_row.first()
            cat_id = cat[0] if cat else None

        # Check if similar fee schedule exists
        existing = await db.execute(
            text("""
                SELECT id FROM fee_schedules
                WHERE channel = :ch AND category_id IS NOT DISTINCT FROM :cid
                  AND fee_rate = :rate AND fee_fixed = :fixed
                  AND price_min IS NOT DISTINCT FROM :pmin
                  AND price_max IS NOT DISTINCT FROM :pmax
            """),
            {"ch": channel, "cid": cat_id, "rate": rate, "fixed": fixed, "pmin": pmin, "pmax": pmax},
        )
        if existing.first():
            continue

        await db.execute(
            text("""
                INSERT INTO fee_schedules (channel, category_id, fee_rate, fee_fixed, fee_note,
                                           price_min, price_max, valid_from)
                VALUES (:ch, :cid, :rate, :fixed, :note, :pmin, :pmax, :vf)
            """),
            {
                "ch": channel, "cid": cat_id, "rate": rate, "fixed": fixed,
                "note": note, "pmin": pmin, "pmax": pmax, "vf": VALID_FROM,
            },
        )
        label = f"{channel}" + (f"/{cat_slug}" if cat_slug else "")
        print(f"  + Fee '{label}': {rate*100:.2f}% + ${fixed}")

    await db.commit()
    print("\nSeed complete!")


async def main():
    print("Seeding category configuration...")
    async with async_session() as db:
        await seed(db)


if __name__ == "__main__":
    asyncio.run(main())
