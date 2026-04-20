"""Category-based configuration resolver.

Three-level merge: GLOBAL_DEFAULTS → category.engine_defaults → category_channels.engine_overrides.
All engines receive a ResolvedConfig (or None for backward compat).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("flipiq.category_config")

# ---------------------------------------------------------------------------
# Global defaults — single source of truth for all 43+ engine constants.
# These are the current hardcoded values extracted from each engine.
# Only keys present in category/channel overrides will be replaced.
# ---------------------------------------------------------------------------

GLOBAL_DEFAULTS: dict[str, object] = {
    # Fees (profit_engine.py)
    "fee_rate": 0.1325,
    "fee_fixed": 0.0,
    "fee_note": None,
    # Profit engine
    "return_reserve_pct": 0.05,
    "shipping_cost": 0.0,
    "packaging_cost": 0.0,
    # Risk engine (risk_engine.py)
    "risk_cv_threshold": 0.60,
    "risk_dispersion_threshold": 0.60,
    "risk_cv_weight": 35,
    "risk_dispersion_weight": 30,
    "risk_outlier_weight": 20,
    "risk_sample_weight": 15,
    "risk_sample_cap": 15,
    # Velocity engine (velocity_engine.py)
    "velocity_coefficient": 25,
    "velocity_scaling": 30,
    "velocity_ceiling": 100,
    "velocity_very_fast": 1.0,
    "velocity_healthy": 0.5,
    "velocity_moderate": 0.1,
    # Confidence engine (confidence_engine.py)
    "confidence_sample_size": 20,
    "confidence_weights": [0.30, 0.25, 0.20, 0.15, 0.10],
    "confidence_burstiness_threshold": 0.3,
    "confidence_burstiness_multiplier": 40,
    "confidence_burstiness_cap": 15,
    "confidence_title_risk_multiplier": 20,
    "confidence_window_expansion_penalty": 10.0,
    # Trend engine (trend_engine.py)
    "trend_high_coverage": 0.5,
    "trend_high_min_count": 5,
    "trend_medium_coverage": 0.35,
    "trend_medium_min_count": 3,
    "trend_low_coverage": 0.2,
    "trend_low_min_count": 2,
    "trend_demand_delta": 15,
    # Pricing engine (pricing_engine.py)
    "pricing_min_spread": 0.10,
    "pricing_spread_factor": 0.30,
    "pricing_cv_threshold": 0.45,
    # Competition engine (competition_engine.py)
    "competition_hhi_concentrated": 0.25,
    "competition_hhi_moderate": 0.15,
    # Execution engine (execution_engine.py)
    "execution_high_ticket_threshold": 300,
    "execution_electronics_tokens": [
        "console", "electronics", "phone", "laptop",
        "tablet", "camera", "gaming",
    ],
}


# ---------------------------------------------------------------------------
# ResolvedConfig — the product of merging 3 levels
# ---------------------------------------------------------------------------

@dataclass
class ResolvedConfig:
    """Fully resolved configuration for a single analysis run."""

    # Fees
    fee_rate: float = 0.1325
    fee_fixed: float = 0.0
    fee_note: str | None = None
    # Profit
    return_reserve_pct: float = 0.05
    shipping_cost: float = 0.0
    packaging_cost: float = 0.0
    # Risk
    risk_cv_threshold: float = 0.60
    risk_dispersion_threshold: float = 0.60
    risk_cv_weight: int = 35
    risk_dispersion_weight: int = 30
    risk_outlier_weight: int = 20
    risk_sample_weight: int = 15
    risk_sample_cap: int = 15
    # Velocity
    velocity_coefficient: int = 25
    velocity_scaling: int = 30
    velocity_ceiling: int = 100
    velocity_very_fast: float = 1.0
    velocity_healthy: float = 0.5
    velocity_moderate: float = 0.1
    # Confidence
    confidence_sample_size: int = 20
    confidence_weights: list[float] = field(default_factory=lambda: [0.30, 0.25, 0.20, 0.15, 0.10])
    confidence_burstiness_threshold: float = 0.3
    confidence_burstiness_multiplier: float = 40
    confidence_burstiness_cap: float = 15
    confidence_title_risk_multiplier: float = 20
    confidence_window_expansion_penalty: float = 10.0
    # Trend
    trend_high_coverage: float = 0.5
    trend_high_min_count: int = 5
    trend_medium_coverage: float = 0.35
    trend_medium_min_count: int = 3
    trend_low_coverage: float = 0.2
    trend_low_min_count: int = 2
    trend_demand_delta: float = 15
    # Pricing
    pricing_min_spread: float = 0.10
    pricing_spread_factor: float = 0.30
    pricing_cv_threshold: float = 0.45
    # Competition
    competition_hhi_concentrated: float = 0.25
    competition_hhi_moderate: float = 0.15
    # Execution
    execution_high_ticket_threshold: float = 300
    execution_electronics_tokens: list[str] = field(
        default_factory=lambda: ["console", "electronics", "phone", "laptop", "tablet", "camera", "gaming"]
    )
    # Metadata
    category_slug: str | None = None
    channel: str = "ebay"
    config_source: str = "global"  # "global" | "category" | "channel"
    observation_mode: bool = False


def _build_config(merged: dict) -> ResolvedConfig:
    """Build a ResolvedConfig from a merged dict, ignoring unknown keys."""
    valid_fields = {f.name for f in ResolvedConfig.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in merged.items() if k in valid_fields and v is not None}
    return ResolvedConfig(**kwargs)


# ---------------------------------------------------------------------------
# resolve_config — the main entry point
# ---------------------------------------------------------------------------

async def resolve_config(
    category_slug: str | None,
    channel: str = "ebay",
    sale_price: float | None = None,
    db: AsyncSession | None = None,
) -> ResolvedConfig:
    """Resolve a full config by merging global → category → channel levels.

    If db is None or category_slug is None, returns global defaults.
    """
    merged = dict(GLOBAL_DEFAULTS)
    merged["channel"] = channel
    config_source = "global"

    if not category_slug or db is None:
        merged["category_slug"] = category_slug
        merged["config_source"] = config_source
        return _build_config(merged)

    # Level 2: Category overrides
    from app.models.category_config import Category, CategoryChannel, FeeSchedule

    cat_row = await db.execute(
        select(Category).where(Category.slug == category_slug, Category.is_active.is_(True))
    )
    category = cat_row.scalar_one_or_none()

    if category is None:
        merged["category_slug"] = category_slug
        merged["config_source"] = config_source
        return _build_config(merged)

    if category.engine_defaults:
        merged.update(category.engine_defaults)
        config_source = "category"

    merged["category_slug"] = category_slug
    merged["observation_mode"] = category.observation_mode

    # Level 3: Channel overrides
    ch_row = await db.execute(
        select(CategoryChannel).where(
            CategoryChannel.category_id == category.id,
            CategoryChannel.channel == channel,
        )
    )
    channel_config = ch_row.scalar_one_or_none()

    if channel_config and channel_config.engine_overrides:
        merged.update(channel_config.engine_overrides)
        config_source = "channel"

    # Fee schedule (most specific: category+channel+price bracket)
    fee = await _resolve_fee(db, channel, category.id, sale_price)
    if fee:
        merged["fee_rate"] = float(fee["fee_rate"])
        merged["fee_fixed"] = float(fee["fee_fixed"])
        if fee.get("fee_note"):
            merged["fee_note"] = fee["fee_note"]

    merged["config_source"] = config_source
    return _build_config(merged)


async def _resolve_fee(
    db: AsyncSession,
    channel: str,
    category_id: int,
    sale_price: float | None,
) -> dict | None:
    """Find the most specific fee schedule row for this channel+category+price."""
    today = date.today()

    # Query: category-specific first, then global fallback
    result = await db.execute(
        text("""
            SELECT fee_rate, fee_fixed, fee_note, category_id
            FROM fee_schedules
            WHERE channel = :channel
              AND valid_from <= :today
              AND (valid_to IS NULL OR valid_to >= :today)
              AND (category_id = :cat_id OR category_id IS NULL)
              AND (price_min IS NULL OR price_min <= :price)
              AND (price_max IS NULL OR price_max >= :price)
            ORDER BY
                category_id IS NULL ASC,
                price_min IS NULL ASC
            LIMIT 1
        """),
        {
            "channel": channel,
            "today": today,
            "cat_id": category_id,
            "price": sale_price or 0,
        },
    )
    row = result.first()
    if row:
        return dict(row._mapping)
    return None


# ---------------------------------------------------------------------------
# map_to_category_slug — maps eBay category ID to our category slug
# ---------------------------------------------------------------------------

async def map_to_category_slug(
    ebay_category_id: int | None,
    db: AsyncSession,
) -> str | None:
    """Map an eBay category ID to our internal category slug.

    Checks categories.ebay_category_ids array contains the given ID.
    """
    if ebay_category_id is None:
        return None

    result = await db.execute(
        text("""
            SELECT slug FROM categories
            WHERE :cat_id = ANY(ebay_category_ids)
              AND is_active = true
            LIMIT 1
        """),
        {"cat_id": ebay_category_id},
    )
    row = result.first()
    return row[0] if row else None
