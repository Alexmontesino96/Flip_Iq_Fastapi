"""Motor de análisis de FlipIQ.

Orquesta 13 motores especializados en AMBOS marketplaces (eBay + Amazon)
en paralelo, y la IA compara tendencias y oportunidades entre los dos.

A. Comp Cleaner → B. Pricing → C. Profit → D. Max Buy Price
E. Velocity → F. Risk → G. Confidence
H. Seller Premium → I. Competition → J. Trend → K. Listing Strategy
L. AI Explanation → M. Market Intelligence (Premium)
"""

import asyncio
import math
import re
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.fees import MARKETPLACE_CALCULATORS, calculate_margin
from app.models.analysis import Analysis
from app.models.product import Product
from app.schemas.analysis import (
    AICompleteEvent,
    AnalysisResponse,
    AnalysisSummary,
    BuyBox,
    ChannelBreakdown,
    CompetitionOut,
    CompsInfo,
    ConditionAnalysisOut,
    ConfidenceOut,
    ExecutionAnalysisOut,
    ExecutionPenaltyOut,
    ListingStrategyOut,
    MarketEventOut,
    MarketIntelligenceOut,
    MarketplaceAnalysis,
    MaxBuyOut,
    PriceBucketOut,
    PricingOut,
    ProductSummary,
    ProfitOut,
    Returns,
    RiskOut,
    SalesByDateOut,
    SalePlan,
    ScoreBreakdown,
    SellerPremiumOut,
    TitleRiskOut,
    TrendOut,
    SampleComp,
    VelocityOut,
)
from app.core.brands import detect_brand
from app.services.engines.ai_explanation import generate_explanation
from app.services.engines.market_intelligence import compute_market_intelligence
from app.services.engines.comp_cleaner import clean_comps
from app.services.engines.comp_relevance import filter_comps_by_relevance
from app.services.engines.product_categorizer import categorize_product
from app.services.category_config import ResolvedConfig, resolve_config, map_to_category_slug
from app.services.engines.title_enricher import enrich_listings
from app.services.engines.competition_engine import compute_competition
from app.services.engines.confidence_engine import compute_confidence
from app.services.engines.execution_engine import (
    ExecutionResult,
    cap_recommendation,
    compute_execution,
)
from app.services.engines.listing_strategy import compute_listing_strategy
from app.services.engines.max_buy_price import compute_max_buy
from app.services.engines.pricing_engine import compute_pricing
from app.services.engines.profit_engine import compute_profit
from app.services.engines.risk_engine import compute_risk
from app.services.engines.seller_premium import compute_seller_premium
from app.services.engines.title_risk import compute_title_risk
from app.services.engines.trend_engine import compute_trend
from app.services.engines.velocity_engine import compute_velocity
from app.services.marketplace.base import (
    CleanedComps,
    CompsResult,
    build_price_buckets,
    build_sales_by_date,
)
from app.services.marketplace.amazon import AmazonClient
from app.services.marketplace.ebay import EbayClient, lookup_upc

logger = logging.getLogger(__name__)

# Singletons de clientes de marketplace
_ebay_client: EbayClient | None = None
_amazon_client: AmazonClient | None = None


def _get_ebay_client() -> EbayClient:
    global _ebay_client
    if _ebay_client is None:
        _ebay_client = EbayClient()
    return _ebay_client


def _get_amazon_client() -> AmazonClient:
    global _amazon_client
    if _amazon_client is None:
        _amazon_client = AmazonClient()
    return _amazon_client


# Semáforo global: limita análisis concurrentes para no saturar DB/scraper/CPU
_analysis_semaphore = asyncio.Semaphore(8)


# Frases de condición que el usuario puede incluir en el keyword
_CONDITION_PHRASES = re.compile(
    r"\b(lightly used|gently used|barely used|heavily used|slightly used|well used"
    r"|brand new|like new|mint condition|near mint|excellent condition"
    r"|good condition|fair condition|poor condition"
    r"|new in box|new with tags|new without tags|new with box|new no box"
    r"|open box|sealed|unopened|nib|nwt|nwob|nib)\b",
    re.IGNORECASE,
)

# Mapeo de frases de condición → condición normalizada para auto-detección
_CONDITION_MAP: dict[str, str] = {
    "lightly used": "used", "gently used": "used", "barely used": "used",
    "heavily used": "used", "slightly used": "used", "well used": "used",
    "good condition": "used", "fair condition": "used", "poor condition": "used",
    "like new": "used", "excellent condition": "used", "near mint": "used",
    "brand new": "new", "mint condition": "new",
    "new in box": "new", "new with tags": "new", "new without tags": "new",
    "new with box": "new", "new no box": "new",
    "sealed": "new", "unopened": "new", "nib": "new", "nwt": "new",
    "nwob": "new",
    "open box": "open_box",
}


_CONDITION_NOISE = re.compile(
    r"\b(good condition|fair condition|poor condition|excellent condition"
    r"|like new|near mint|mint condition|pre-owned|pre owned|preowned"
    r"|used|refurbished|renewed|open box|for parts|not working"
    r"|as is|damaged|broken|cracked)\b",
    re.IGNORECASE,
)


def _has_condition_noise(title: str) -> bool:
    """True si el título tiene frases de condición típicas de eBay."""
    return bool(_CONDITION_NOISE.search(title))


_COLOR_WORDS = {
    "black", "white", "red", "blue", "green", "grey", "gray", "pink", "purple",
    "orange", "yellow", "brown", "silver", "gold", "navy", "teal", "beige",
    "coral", "ivory", "cream", "arctic", "feather", "pearl", "midnight",
    "velvet", "morganite", "charcoal", "rose", "aqua", "indigo", "olive",
    "platinum", "bronze", "copper", "magenta", "crimson", "slate", "onyx",
    "lime", "volt",
}

_COLOR_MODIFIERS = {
    "core", "cloud", "collegiate", "ftwr", "footwear", "team", "solar",
    "wonder", "carbon",
}

_COLOR_TRAILING_RE = (
    r"(?:" + "|".join(sorted(_COLOR_WORDS | _COLOR_MODIFIERS, key=len, reverse=True)) + r")"
    r"(?:[/\s-]+(?:"
    + "|".join(sorted(_COLOR_WORDS | _COLOR_MODIFIERS, key=len, reverse=True))
    + r"))*"
)


def _simplify_upc_title(title: str) -> str:
    """Simplifica un título de UPC database para usarlo como keyword de eBay.

    UPC databases devuelven títulos muy específicos con talla, color, material, etc:
      "ASICS GEL-Nimbus(r) 28 Men's Running Shoes Black/Feather Grey : 7 D - Medium, Synthetic"
    que no matchean nada en eBay. Cortamos specs innecesarias.
    """
    # Quitar trademark symbols
    t = re.sub(r"[®™©]|\(r\)|\(tm\)", "", title, flags=re.IGNORECASE)
    # Quitar todo después de ":" o "|" (suele ser talla/color/specs)
    t = re.split(r"[:|]", t)[0]
    # Quitar sufijos retail de color/specs después de guion:
    # "Adizero EVO SL Athletic Shoe - Core Black / White / Core Black"
    t = re.sub(r"\s+-\s+" + _COLOR_TRAILING_RE + r"\s*$", "", t, flags=re.IGNORECASE)
    # Quitar patrones de talla: "Size 7", "Sz 11", "7 D - Medium", etc.
    t = re.sub(r"\b(?:size|sz)\s*\d+[.\d]*\s*\w*\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\b\d+\s*D\s*-\s*\w+\b", "", t)
    # Quitar material suelto
    t = re.sub(r",?\s*\b(Synthetic|Leather|Mesh|Canvas|Suede|Rubber|Nylon|Polyester)\b", "", t, flags=re.IGNORECASE)
    # Quitar genero y nouns retail que no ayudan en eBay.
    t = re.sub(
        r"\b(?:men(?:['’?]?s)?|women(?:['’?]?s)?|mens|womens|boys|girls|youth|kids|unisex)\b",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:(?:athletic|running|training|walking|casual)\s+)?"
        r"(?:shoe|shoes|sneaker|sneakers|trainer|trainers|footwear)\b",
        "",
        t,
        flags=re.IGNORECASE,
    )
    # Quitar trailing color phrase (e.g. "Black/Feather Grey")
    # Match a final run of color words separated by spaces or /
    t = re.sub(r"\s+" + _COLOR_TRAILING_RE + r"\s*$", "", t, flags=re.IGNORECASE)
    # Limpiar puntuación trailing y espacios
    t = re.sub(r"[,\-/]+\s*$", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t if len(t) >= 8 and len(t.split()) >= 2 else title


def _clean_search_keyword(keyword: str) -> tuple[str, str | None]:
    """Elimina frases de condición del keyword y detecta condición implícita.

    Returns:
        (cleaned_keyword, detected_condition) — condition es None si no se detectó.
    """
    detected_condition: str | None = None
    matches = _CONDITION_PHRASES.findall(keyword)
    if matches:
        # Tomar la primera frase de condición encontrada
        phrase = matches[0].lower()
        detected_condition = _CONDITION_MAP.get(phrase)

    cleaned = _CONDITION_PHRASES.sub("", keyword)
    # Colapsar espacios, comas sueltas y limpiar
    cleaned = re.sub(r"[,\s]+", " ", cleaned).strip().strip(",").strip()
    return (cleaned if cleaned else keyword), detected_condition


# ---------------------------------------------------------------------------
# Dataclass interna para resultados del pipeline por marketplace
# ---------------------------------------------------------------------------

@dataclass
class _PipelineResult:
    """Resultado completo del pipeline de motores para un marketplace."""
    marketplace_name: str
    raw_comps: CompsResult
    cleaned: CleanedComps
    enriched: bool
    pricing: Any
    profit_market: Any
    profit_quick: Any
    max_buy: Any
    velocity: Any
    risk: Any
    title_risk: Any
    confidence: Any
    seller: Any
    competition: Any
    trend: Any
    listing: Any
    condition_analysis: ConditionAnalysisOut
    condition_subset_pricing: dict | None
    opportunity: int
    recommendation: str
    warnings: list[str]
    distribution_shape: str
    has_valid_comps: bool
    comps_info: CompsInfo | None
    estimated_sale: float | None
    execution: ExecutionResult | None = None


# ---------------------------------------------------------------------------
# Pipeline de motores reutilizable
# ---------------------------------------------------------------------------

def _run_pipeline(
    raw_comps: CompsResult,
    keyword: str,
    condition: str,
    cost_price: float,
    marketplace_name: str,
    shipping_cost: float = 0.0,
    packaging_cost: float = 0.0,
    prep_cost: float = 0.0,
    promo_cost: float = 0.0,
    return_reserve_pct: float = 0.05,
    target_profit: float = 10.0,
    target_roi: float = 0.35,
    enriched: bool = False,
    product_type: str | None = None,
    config: ResolvedConfig | None = None,
) -> _PipelineResult:
    """Ejecuta los motores A-K + decisión sobre un set de comps.

    Es síncrono — todos los motores son funciones puras sin I/O.
    config: ResolvedConfig with category-specific overrides (optional).
    """
    # Apply config defaults for costs when user didn't provide explicit values
    if config:
        if shipping_cost == 0.0 and config.shipping_cost > 0:
            shipping_cost = config.shipping_cost
        if packaging_cost == 0.0 and config.packaging_cost > 0:
            packaging_cost = config.packaging_cost
        if return_reserve_pct == 0.05 and config.return_reserve_pct != 0.05:
            return_reserve_pct = config.return_reserve_pct

    # Motor A: Limpiar comps
    cleaned = clean_comps(raw_comps, keyword=keyword, condition=condition, product_type=product_type)

    # Motor B: Precios recomendados
    pricing = compute_pricing(cleaned, config=config)

    # Fee resolution: Keepa real fees > config bracket (price-aware) > marketplace default
    # Resolve fee bracket based on actual sale price (after pricing is known)
    if raw_comps.fba_referral_pct is not None or raw_comps.fba_fulfillment_fee is not None:
        # Real Keepa fees take priority
        fee_override = raw_comps.fba_referral_pct if raw_comps.fba_referral_pct is not None else (config.fee_rate if config else None)
        fee_fixed_override = raw_comps.fba_fulfillment_fee if raw_comps.fba_fulfillment_fee is not None else (config.fee_fixed if config else None)
    elif config:
        # Resolve bracket by actual sale price (handles eBay price-dependent fees)
        fee_override, fee_fixed_override = config.resolve_fee_for_price(pricing.market_list)
    else:
        fee_override = None
        fee_fixed_override = None

    # Motor C: Profit
    profit_market = compute_profit(
        pricing.market_list, cost_price, marketplace_name,
        shipping_cost, packaging_cost, prep_cost, promo_cost, return_reserve_pct,
        fee_rate_override=fee_override, fee_fixed_override=fee_fixed_override,
    )
    # Quick price may fall in a different bracket
    if config and not (raw_comps.fba_referral_pct is not None or raw_comps.fba_fulfillment_fee is not None):
        fee_quick, fee_fixed_quick = config.resolve_fee_for_price(pricing.quick_list)
    else:
        fee_quick, fee_fixed_quick = fee_override, fee_fixed_override
    profit_quick = compute_profit(
        pricing.quick_list, cost_price, marketplace_name,
        shipping_cost, packaging_cost, prep_cost, promo_cost, return_reserve_pct,
        fee_rate_override=fee_quick, fee_fixed_override=fee_fixed_quick,
    )

    # Motor D: Max buy price
    max_buy = compute_max_buy(profit_market, target_profit, target_roi)

    # Motor E: Velocity
    velocity = compute_velocity(cleaned, config=config)

    # Motor F: Risk
    risk = compute_risk(cleaned, raw_comps, config=config)

    # Title Risk
    title_risk = compute_title_risk(cleaned, keyword=keyword)

    # Motor H: Seller Premium
    seller = compute_seller_premium(cleaned)

    # Motor I: Competition
    competition = compute_competition(cleaned, config=config)

    # Motor J: Trend (antes de confidence para pasar burstiness)
    trend = compute_trend(cleaned, config=config)

    # Motor G: Confidence (con burstiness del trend)
    confidence = compute_confidence(
        cleaned, raw_comps, enriched, title_risk.risk_score,
        burstiness=trend.burstiness, config=config,
    )

    # Motor K: Listing Strategy
    listing = compute_listing_strategy(cleaned, velocity, risk, quick_price=pricing.quick_list)

    # Condition Analysis + Mini-pipeline sobre condition subset
    condition_subset_pricing = None
    if (
        cleaned.condition_subset_count > 0
        and cleaned.condition_subset_median is not None
        and cleaned.condition_subset_median > 0
    ):
        sub_fee, sub_fee_fixed = (fee_override, fee_fixed_override)
        if config and not (raw_comps.fba_referral_pct is not None or raw_comps.fba_fulfillment_fee is not None):
            sub_fee, sub_fee_fixed = config.resolve_fee_for_price(cleaned.condition_subset_median)
        subset_profit = compute_profit(
            cleaned.condition_subset_median, cost_price, marketplace_name,
            shipping_cost, packaging_cost, prep_cost, promo_cost, return_reserve_pct,
            fee_rate_override=sub_fee, fee_fixed_override=sub_fee_fixed,
        )
        subset_max_buy = compute_max_buy(subset_profit, target_profit, target_roi)
        condition_subset_pricing = {
            "count": cleaned.condition_subset_count,
            "median": cleaned.condition_subset_median,
            "profit": subset_profit.profit,
            "roi_pct": round(subset_profit.roi * 100, 2),
            "margin_pct": round(subset_profit.margin * 100, 2),
            "max_buy": subset_max_buy.recommended_max,
        }

    condition_analysis = _build_condition_analysis(cleaned, condition_subset_pricing)

    # Opportunity Score
    if cleaned.clean_total > 0:
        opportunity = _compute_opportunity_score(
            profit_market, velocity, risk, confidence, competition, trend,
        )
    else:
        opportunity = 0

    if cleaned.pricing_basis == "mixed_conditions":
        opportunity = min(opportunity, 44)

    # Distribución de precios
    clean_prices = sorted(l.total_price for l in cleaned.listings if l.total_price)
    distribution_shape = _detect_distribution_shape(clean_prices) if cleaned.clean_total > 0 else "unknown"

    # Decisión + Validador
    recommendation = _decide(opportunity, profit_market, risk, confidence)
    recommendation, warnings = _validate_buy(
        recommendation, confidence, title_risk, cleaned, profit_market,
        max_buy=max_buy, cost_price=cost_price,
        distribution_shape=distribution_shape,
        condition_subset_pricing=condition_subset_pricing,
    )

    for warning in cleaned.data_quality_warnings:
        if warning not in warnings:
            warnings.append(warning)

    if raw_comps.fallback_used:
        warnings.append("Marketplace fallback data source was used; verify comps manually.")
    if raw_comps.scrape_status in ("blocked", "partial"):
        warnings.append(
            f"Marketplace data source returned status '{raw_comps.scrape_status}'. "
            "Pricing confidence is limited."
        )
    if marketplace_name == "amazon_fba" and cleaned.clean_total > 0:
        if raw_comps.fba_referral_pct is None and raw_comps.fba_fulfillment_fee is None:
            warnings.append(
                "Amazon FBA fees use a generic estimate; confirm category and fulfillment costs before buying."
            )

    # Warning: mercado dominado por un seller
    if competition.dominant_seller_share > 0.40:
        pct = round(competition.dominant_seller_share * 100)
        warnings.append(
            f"One seller controls {pct}% of the market. "
            "Competing for the Buy Box will be difficult."
        )

    # Warning de demand spike temporal
    if trend.demand_trend > 80 and trend.burstiness > 0.25 and trend.confidence != "low":
        warnings.append(
            f"Demand spike detected ({trend.demand_trend:+.0f}%). "
            "Sales concentrated in a few days — may be temporary. "
            "Monitor before buying large quantities."
        )

    # Warning: precio plano (varianza cero) — probable single-seller market
    if cleaned.clean_total >= 5 and cleaned.std_dev == 0 and cleaned.p25 == cleaned.p75:
        warnings.append(
            "All comps at the same price — likely a single-seller or fixed-price market. "
            "Pricing data may not reflect true market value."
        )

    # Comps info
    has_valid_comps = cleaned.clean_total > 0 and pricing.market_list > 0
    comps_info, _ = _build_comps_info(cleaned, source=f"{marketplace_name}_cleaned")
    estimated_sale = pricing.market_list if has_valid_comps else None

    execution = compute_execution(
        marketplace_name=marketplace_name,
        profit_market=profit_market,
        risk=risk,
        confidence=confidence,
        competition=competition,
        trend=trend,
        cleaned=cleaned,
        raw_comps=raw_comps,
        distribution_shape=distribution_shape,
        product_type=product_type,
        config=config,
    )
    capped_recommendation = cap_recommendation(
        recommendation,
        execution.max_recommendation,
    )
    if capped_recommendation != recommendation:
        warnings.append(
            f"Execution risk caps this marketplace at '{execution.max_recommendation}'. "
            f"{execution.quantity_guidance} recommended."
        )
        recommendation = capped_recommendation
    for warning in execution.warnings:
        if warning not in warnings:
            warnings.append(warning)

    # Gate: sin comps → pass
    if not has_valid_comps and recommendation != "pass":
        recommendation = "pass"
        if not any("comps" in w.lower() or "condition" in w.lower() for w in warnings):
            if condition != "any":
                warnings.append(
                    f"Not enough comps in '{condition}' condition. "
                    "Cannot estimate sale price."
                )
            else:
                warnings.append(
                    "No valid comps. Cannot estimate sale price."
                )

    warnings = _dedupe_warnings(warnings)

    return _PipelineResult(
        marketplace_name=marketplace_name,
        raw_comps=raw_comps,
        cleaned=cleaned,
        enriched=enriched,
        pricing=pricing,
        profit_market=profit_market,
        profit_quick=profit_quick,
        max_buy=max_buy,
        velocity=velocity,
        risk=risk,
        title_risk=title_risk,
        confidence=confidence,
        seller=seller,
        competition=competition,
        trend=trend,
        listing=listing,
        condition_analysis=condition_analysis,
        condition_subset_pricing=condition_subset_pricing,
        opportunity=opportunity,
        recommendation=recommendation,
        warnings=warnings,
        distribution_shape=distribution_shape,
        has_valid_comps=has_valid_comps,
        comps_info=comps_info,
        estimated_sale=estimated_sale,
        execution=execution,
    )


def _pipeline_to_marketplace_analysis(p: _PipelineResult) -> MarketplaceAnalysis:
    """Convierte un _PipelineResult a un MarketplaceAnalysis schema."""
    v = p.has_valid_comps
    return MarketplaceAnalysis(
        marketplace=p.marketplace_name,
        estimated_sale_price=p.estimated_sale,
        net_profit=p.profit_market.profit if v else None,
        roi_pct=round(p.profit_market.roi * 100, 2) if (v and math.isfinite(p.profit_market.roi)) else None,
        margin_pct=round(p.profit_market.margin * 100, 2) if v else None,
        flip_score=p.opportunity if v else None,
        recommendation=p.recommendation,
        comps=p.comps_info,
        pricing=PricingOut(**asdict(p.pricing)) if v else None,
        profit_detail=ProfitOut(**asdict(p.profit_market)) if v else None,
        max_buy_price=MaxBuyOut(**asdict(p.max_buy)) if v else None,
        velocity=VelocityOut(**asdict(p.velocity)) if v else None,
        risk=RiskOut(**asdict(p.risk)) if v else None,
        confidence=ConfidenceOut(**asdict(p.confidence)) if v else None,
        seller_premium=SellerPremiumOut(**asdict(p.seller)) if v else None,
        competition=CompetitionOut(**{**asdict(p.competition), "marketplace": p.marketplace_name}) if v else None,
        trend=TrendOut(**{**asdict(p.trend), "marketplace": p.marketplace_name}) if v else None,
        listing_strategy=ListingStrategyOut(**asdict(p.listing)) if v else None,
        title_risk=TitleRiskOut(**asdict(p.title_risk)),
        condition_analysis=p.condition_analysis,
        execution_analysis=_execution_to_schema(p.execution) if p.execution else None,
        warnings=p.warnings,
    )


def _execution_to_schema(execution: ExecutionResult) -> ExecutionAnalysisOut:
    """Convierte ExecutionResult dataclass a schema API."""
    return ExecutionAnalysisOut(
        score=execution.score,
        category=execution.category,
        win_probability=execution.win_probability,
        expected_profit=execution.expected_profit,
        max_recommendation=execution.max_recommendation,
        quantity_guidance=execution.quantity_guidance,
        channel_role=execution.channel_role,
        penalties=[
            ExecutionPenaltyOut(
                code=p.code,
                severity=p.severity,
                points=p.points,
                message=p.message,
            )
            for p in execution.penalties
        ],
        warnings=execution.warnings,
    )


def _pipeline_to_engines_dict(p: _PipelineResult) -> dict:
    """Extract engine outputs from a pipeline for DB persistence."""
    return {
        "marketplace": p.marketplace_name,
        "pricing": asdict(p.pricing),
        "profit_market": asdict(p.profit_market),
        "profit_quick": asdict(p.profit_quick),
        "max_buy": asdict(p.max_buy),
        "velocity": asdict(p.velocity),
        "risk": asdict(p.risk),
        "confidence": asdict(p.confidence),
        "seller_premium": asdict(p.seller),
        "competition": asdict(p.competition),
        "trend": asdict(p.trend),
        "listing_strategy": asdict(p.listing),
        "title_risk": asdict(p.title_risk),
        "condition_analysis": p.condition_analysis.model_dump(),
        "opportunity_score": p.opportunity,
        "recommendation": p.recommendation,
        "execution": asdict(p.execution) if p.execution else None,
        "has_valid_comps": p.has_valid_comps,
        "cleaned_comps": {
            "raw_total": p.cleaned.raw_total,
            "clean_total": p.cleaned.clean_total,
            "outliers_removed": p.cleaned.outliers_removed,
            "relevance_filtered": p.cleaned.relevance_filtered,
            "cv": p.cleaned.cv,
            "median_price": p.cleaned.median_price,
            "min_price": p.cleaned.min_price,
            "max_price": p.cleaned.max_price,
            "avg_price": p.cleaned.avg_price,
            "std_dev": p.cleaned.std_dev,
            "p25": p.cleaned.p25,
            "p75": p.cleaned.p75,
            "days_of_data": p.cleaned.days_of_data,
            "sales_per_day": p.cleaned.sales_per_day,
            "temporal_window_expanded": p.cleaned.temporal_window_expanded,
            "initial_days_requested": p.cleaned.initial_days_requested,
        },
        "warnings": p.warnings,
    }


def _build_marketplace_engines(
    ebay_pipeline: _PipelineResult,
    amazon_pipeline: _PipelineResult | None,
) -> dict:
    """Build per-marketplace engine data for DB persistence."""
    result = {"ebay": _pipeline_to_engines_dict(ebay_pipeline)}
    if amazon_pipeline:
        result["amazon"] = _pipeline_to_engines_dict(amazon_pipeline)
    return result


# ---------------------------------------------------------------------------
# Helpers de scores, decisión, validación (sin cambios)
# ---------------------------------------------------------------------------

def _compute_opportunity_score(
    profit_market,
    velocity,
    risk,
    confidence,
    competition,
    trend,
) -> int:
    """Score de oportunidad compuesto (0-100)."""
    roi = profit_market.roi
    if roi >= 0.50:
        profit_score = 100
    elif roi >= 0.30:
        profit_score = 80
    elif roi >= 0.15:
        profit_score = 60
    elif roi > 0:
        profit_score = 40
    else:
        profit_score = 10

    comp_score = 100 if competition.category == "healthy" else (60 if competition.category == "moderate" else 30)
    trend_bonus = min(20, max(-20, trend.demand_trend / 5))
    market_health = min(100, max(0, comp_score + trend_bonus))

    score = (
        0.30 * profit_score
        + 0.25 * velocity.score
        + 0.20 * risk.score
        + 0.15 * confidence.score
        + 0.10 * market_health
    )

    return min(100, max(0, round(score)))


def _compute_final_score(market_score: int, execution_score: int) -> int:
    """Score final: market opportunity con ajuste por execution risk."""
    return min(100, max(0, round(0.65 * market_score + 0.35 * execution_score)))


def _pipeline_expected_profit(p: _PipelineResult) -> float:
    if p.execution:
        return p.execution.expected_profit
    return p.profit_market.profit


def _is_actionable_channel_candidate(p: _PipelineResult) -> bool:
    """Can this marketplace lead the user-facing buy decision?"""
    if not p.has_valid_comps or not p.execution:
        return False
    if p.recommendation not in {"buy", "buy_small"}:
        return False
    if p.cleaned.clean_total < 5:
        return False
    if p.confidence.score < 40:
        return False
    if p.execution.score < 55:
        return False
    return p.execution.expected_profit > 0


def _select_primary_marketplace(
    valid_candidates: list[_PipelineResult],
) -> tuple[_PipelineResult, _PipelineResult, str | None, str]:
    """Choose primary channel without letting weak data dominate the decision."""
    best_by_profit = max(valid_candidates, key=lambda c: c.profit_market.profit)
    sort_key = lambda c: (
        _pipeline_expected_profit(c),
        c.execution.score if c.execution else 0,
        c.profit_market.profit,
    )

    actionable = [c for c in valid_candidates if _is_actionable_channel_candidate(c)]
    if actionable:
        primary = max(actionable, key=sort_key)
        recommended_marketplace: str | None = primary.marketplace_name
        reason = (
            "best_profit"
            if best_by_profit.marketplace_name == recommended_marketplace
            else "best_execution"
        )
    else:
        primary = max(valid_candidates, key=sort_key)
        recommended_marketplace = None
        reason = "best_available_untrusted"

    return primary, best_by_profit, recommended_marketplace, reason


def _warning_category(warning: str) -> str | None:
    """Categoria semantica para evitar warnings duplicados con distinto texto."""
    w = warning.lower()
    if "confidence" in w:
        return "confidence"
    if "only" in w and ("comp" in w or "clean" in w):
        return "small_sample"
    if "comps after" in w or "clean comps" in w:
        return "small_sample"
    if "fba fees" in w or "generic estimate" in w:
        return "fba_fees"
    if "bimodal" in w or "distribution" in w:
        return "price_distribution"
    if "seller" in w or "buy box" in w:
        return "seller_concentration"
    if "fallback" in w:
        return "fallback_source"
    if "blocked" in w or "partial" in w or "source status" in w:
        return "source_status"
    if "condition" in w or "mixed comps" in w:
        return "condition"
    if "demand" in w or "trend" in w:
        return "demand"
    if "execution risk caps" in w:
        return "execution_cap"
    return None


def _dedupe_warnings(warnings: list[str]) -> list[str]:
    """Deduplica warnings por texto exacto y por categoria semantica."""
    seen_text: set[str] = set()
    seen_categories: set[str] = set()
    result: list[str] = []

    for warning in warnings:
        normalized = " ".join(warning.strip().split())
        if not normalized:
            continue
        text_key = normalized.lower()
        if text_key in seen_text:
            continue

        category = _warning_category(normalized)
        if category and category in seen_categories:
            continue

        seen_text.add(text_key)
        if category:
            seen_categories.add(category)
        result.append(normalized)

    return result


def _build_execution_text(
    *,
    primary: _PipelineResult,
    best_profit_marketplace: str,
    recommended_marketplace: str | None,
    final_score: int,
) -> str:
    """Bloque compacto para que la IA explique execution risk accionable."""
    if not primary.execution:
        return ""
    e = primary.execution
    penalties = ", ".join(p.code for p in e.penalties[:5]) or "none"
    recommended_label = recommended_marketplace or "None yet; verify manually"
    return (
        "\n\nEXECUTION ANALYSIS:\n"
        f"- Market score: {primary.opportunity}/100\n"
        f"- Execution score: {e.score}/100 ({e.category})\n"
        f"- Final score: {final_score}/100\n"
        f"- Execution confidence: {e.win_probability:.0%}\n"
        f"- Expected execution-weighted profit: ${e.expected_profit:.2f}\n"
        f"- Recommended channel: {recommended_label}\n"
        f"- Best raw profit channel: {best_profit_marketplace}\n"
        f"- Quantity guidance: {e.quantity_guidance}\n"
        f"- Main execution penalties: {penalties}\n"
        "Lead the recommendation with the execution guidance, not only raw ROI."
    )


def _decide(opportunity_score: int, profit_market, risk, confidence) -> str:
    """Decisión basada en opportunity score y factores clave."""
    if (
        opportunity_score >= 60
        and profit_market.profit > 0
        and risk.score >= 40
        and confidence.score >= 30
    ):
        return "buy"
    elif (
        opportunity_score >= 45
        and profit_market.profit > 0
        and profit_market.roi > 0.20
        and risk.score >= 30
    ):
        return "buy_small"
    elif opportunity_score >= 35 or profit_market.roi > 0.10:
        return "watch"
    return "pass"


def _build_condition_analysis(
    cleaned: CleanedComps,
    condition_subset_pricing: dict | None = None,
) -> ConditionAnalysisOut:
    """Construye el bloque de análisis de condición."""
    counts = cleaned.condition_counts
    has_new = counts.get("new", 0) > 0
    has_used = counts.get("used", 0) > 0 or counts.get("for_parts", 0) > 0
    mixed = has_new and has_used

    return ConditionAnalysisOut(
        requested_condition=cleaned.requested_condition,
        filter_applied=cleaned.condition_filtered > 0,
        condition_counts=counts,
        condition_match_rate=cleaned.condition_match_rate,
        condition_filtered=cleaned.condition_filtered,
        mixed_conditions=mixed,
        raw_condition_total=cleaned.raw_total - cleaned.outliers_removed,
        condition_subset_count=cleaned.condition_subset_count,
        condition_subset_median=cleaned.condition_subset_median,
        condition_subset_pricing=condition_subset_pricing,
    )


def _validate_buy(
    recommendation: str,
    confidence,
    title_risk,
    cleaned: CleanedComps,
    profit_market,
    max_buy=None,
    cost_price: float = 0.0,
    distribution_shape: str = "unknown",
    condition_subset_pricing: dict | None = None,
) -> tuple[str, list[str]]:
    """Validador pre-BUY. Puede degradar 'buy' a 'buy_small' o 'watch' con warnings."""
    warnings: list[str] = []

    if max_buy is not None and max_buy.recommended_max > 0 and cost_price > max_buy.recommended_max:
        overpay = cost_price - max_buy.recommended_max
        warnings.append(
            f"Your cost (${cost_price:.2f}) exceeds the recommended max "
            f"(${max_buy.recommended_max:.2f}) by ${overpay:.2f}. "
            f"At ${max_buy.recommended_max:.2f} or less, it would be profitable."
        )
        if recommendation in ("buy", "buy_small"):
            recommendation = "watch"

    if confidence.score < 50:
        warnings.append(
            f"Low analysis confidence ({confidence.score}/100). "
            "Insufficient data to recommend purchase."
        )
        if recommendation in ("buy", "buy_small"):
            recommendation = "watch"
    elif confidence.score < 60:
        warnings.append(
            f"Moderate analysis confidence ({confidence.score}/100). "
            "Consider buying smaller quantities until more data is available."
        )
        if recommendation == "buy":
            recommendation = "buy_small"

    if cleaned.requested_condition != "any":
        if cleaned.condition_filtered == 0 and cleaned.condition_match_rate < 0.5:
            # Safety net activó: no pudo filtrar por condición
            if cleaned.condition_subset_median is not None and cleaned.condition_subset_count > 0:
                base_msg = (
                    f"Only {cleaned.condition_subset_count} of {cleaned.clean_total} comps "
                    f"match '{cleaned.requested_condition}' condition "
                    f"(subset median ${cleaned.condition_subset_median:.2f}). "
                    f"Prices shown are based on all {cleaned.clean_total} comps "
                    f"(median ${cleaned.median_price:.2f}) and are not a reliable "
                    "primary estimate for the requested condition."
                )
                if condition_subset_pricing:
                    base_msg += (
                        f" If selling as '{cleaned.requested_condition}': "
                        f"est. profit ${condition_subset_pricing['profit']:.2f}, "
                        f"ROI {condition_subset_pricing['roi_pct']:.1f}%, "
                        f"max buy ${condition_subset_pricing['max_buy']:.2f}."
                    )
                warnings.append(base_msg)
            else:
                warnings.append(
                    f"No comps found in '{cleaned.requested_condition}' condition. "
                    f"Prices shown are based on all {cleaned.clean_total} comps (all conditions). "
                    f"Cannot estimate '{cleaned.requested_condition}' market value reliably."
                )
            if recommendation in ("buy", "buy_small"):
                recommendation = "watch"
        elif cleaned.condition_match_rate < 0.7:
            warnings.append(
                f"Mixed comps: {cleaned.condition_match_rate:.0%} match "
                f"'{cleaned.requested_condition}' condition. "
                "Consider reviewing manually."
            )

    if title_risk.manual_review_required:
        warnings.append(
            f"Ambiguous titles detected ({title_risk.flagged_pct:.0%} of comps). "
            f"Flags: {', '.join(title_risk.top_flags)}. Review manually."
        )
        if recommendation == "buy" and title_risk.risk_score > 0.4:
            recommendation = "buy_small"

    if cleaned.clean_total < 5:
        warnings.append(
            f"Only {cleaned.clean_total} comps after cleanup. "
            "Results may not be representative."
        )
        if recommendation == "buy" and cleaned.clean_total < 3:
            recommendation = "buy_small"
        elif recommendation == "buy":
            recommendation = "buy_small"

    if distribution_shape == "bimodal":
        warnings.append(
            "Bimodal price distribution detected. "
            "There are two distinct price groups — the median may not be representative."
        )
    elif distribution_shape == "dispersed":
        warnings.append(
            "Highly dispersed prices — no clear market consensus. "
            "Pricing will require careful positioning."
        )

    if profit_market.profit <= 0 and recommendation in ("buy", "buy_small"):
        recommendation = "pass"
        warnings.append("Negative profit. Purchase not recommended.")

    if cleaned.cv > 0.50:
        warnings.append(
            f"High price dispersion (CV={cleaned.cv:.2f}). "
            "The market is volatile."
        )

    return recommendation, warnings


# ---------------------------------------------------------------------------
# Generador progresivo: run_analysis_progressive
# ---------------------------------------------------------------------------

async def run_analysis_progressive(
    barcode: str | None,
    keyword: str | None,
    cost_price: float,
    marketplace: str,
    # Parámetros opcionales
    shipping_cost: float = 0.0,
    packaging_cost: float = 0.0,
    prep_cost: float = 0.0,
    promo_cost: float = 0.0,
    return_reserve_pct: float = 0.05,
    target_profit: float = 10.0,
    target_roi: float = 0.35,
    detailed: bool = False,
    condition: str = "any",
    mode: str = "standard",
    product_type: str | None = None,
    user_id: int | None = None,
) -> AsyncGenerator[dict, None]:
    """Genera datos de análisis con progreso SSE y 2 chunks de resultado.

    Eventos opcionales ("progress"): estados de avance para la UI.
    Yield 1 ("analysis"): AnalysisResponse completo sin AI explanation.
    Yield 2 ("ai_complete"): AICompleteEvent con AI + campos que pueden cambiar.
    """
    from app.core.llm import reset_gemini
    reset_gemini()

    # Track analysis_started en Customer.io
    if user_id:
        from app.services import customerio
        asyncio.create_task(customerio.track(user_id, "analysis_started",
            query=barcode or keyword or "",
        ))

    _t_total = time.perf_counter()

    def _progress(
        stage: str,
        status: str,
        message: str,
        progress: int,
        details: dict | None = None,
    ) -> dict:
        return {
            "event": "progress",
            "data": {
                "stage": stage,
                "status": status,
                "message": message,
                "progress": max(0, min(100, progress)),
                "elapsed_ms": int((time.perf_counter() - _t_total) * 1000),
                "details": details or {},
            },
        }

    yield _progress(
        "start",
        "active",
        "Starting live market scan",
        3,
        {"barcode": barcode, "keyword": keyword, "condition": condition},
    )

    # 0. Normalizar barcode: strip leading zeros → formato UPC-12 / EAN-13 estándar
    if barcode:
        barcode = barcode.lstrip("0") or barcode  # no dejar vacío si era "000..."
        # Re-pad a UPC-12 o EAN-13 estándar si quedó más corto
        if len(barcode) < 12:
            barcode = barcode.zfill(12)
        elif len(barcode) == 12:
            pass  # UPC-A estándar
        elif len(barcode) < 13:
            barcode = barcode.zfill(13)
        logger.info("Barcode normalizado: '%s'", barcode)

    # 0b. Si solo hay barcode, intentar UPC lookup para obtener keyword
    upc_info: dict | None = None
    if barcode and not keyword:
        yield _progress(
            "identify",
            "active",
            "Looking up barcode title",
            8,
            {"barcode": barcode},
        )
        upc_info = await lookup_upc(barcode)
        if upc_info and upc_info.get("title"):
            raw_title = upc_info["title"]
            keyword = _simplify_upc_title(raw_title)
            logger.info("UPC lookup: %s → '%s' (raw: '%s')", barcode, keyword, raw_title)
        yield _progress(
            "identify",
            "complete",
            "Product identity resolved" if keyword else "Barcode lookup finished",
            12,
            {
                "barcode": barcode,
                "keyword": keyword,
                "title_found": bool(upc_info and upc_info.get("title")),
            },
        )

    # 0b. Limpiar keyword: quitar frases de condición que contaminan la búsqueda
    # ("Lightly Used", "Brand New", etc.) — el filtro de condición se aplica en comp_cleaner
    detected_condition: str | None = None
    if keyword:
        search_keyword, detected_condition = _clean_search_keyword(keyword)
    else:
        search_keyword = keyword
    if search_keyword and search_keyword != keyword:
        logger.info("Keyword limpiado: '%s' → '%s'", keyword, search_keyword)

    # Auto-set condition si el usuario no la especificó pero la incluía en el keyword
    if condition == "any" and detected_condition:
        condition = detected_condition
        logger.info("Condición auto-detectada del keyword: '%s'", condition)

    # -----------------------------------------------------------------------
    # 0c. Categorizar producto: LLM extrae product_type del keyword
    # -----------------------------------------------------------------------
    category_result = None
    category_slug: str | None = None
    if product_type:
        # Override manual del usuario — usar directo
        logger.info("product_type manual: '%s'", product_type)
    elif search_keyword:
        yield _progress(
            "category",
            "active",
            "Classifying product type",
            14,
            {"keyword": search_keyword},
        )
        category_result = await categorize_product(search_keyword)
        category_slug: str | None = None
        if category_result:
            product_type = category_result.product_type
            # Map eBay category ID to our internal category slug for config
            try:
                from app.database import async_session as _sf
                async with _sf() as _cat_db:
                    category_slug = await map_to_category_slug(category_result.ebay_category_id, _cat_db)
            except Exception:
                category_slug = None
            logger.info(
                "Categorizado: '%s' → product_type='%s' category_slug='%s' (confidence=%.2f)",
                search_keyword, product_type, category_slug, category_result.confidence,
            )
        yield _progress(
            "category",
            "complete",
            "Product type classified" if product_type else "Category check finished",
            18,
            {
                "keyword": search_keyword,
                "product_type": product_type,
                "confidence": category_result.confidence if category_result else None,
            },
        )

    # -----------------------------------------------------------------------
    # 1. Fetch de comps: eBay + Amazon en PARALELO
    # -----------------------------------------------------------------------
    ebay_category_id = category_result.ebay_category_id if category_result else None

    ebay = _get_ebay_client()
    ebay_limit = 240 if barcode else 50
    logger.info(
        "FETCH START: barcode='%s' keyword='%s' ebay_limit=%d",
        barcode, search_keyword, ebay_limit,
    )
    yield _progress(
        "fetch",
        "active",
        "Pulling live eBay and Amazon market data",
        25,
        {
            "barcode": barcode,
            "keyword": search_keyword,
            "ebay_limit": ebay_limit,
            "amazon_enabled": bool(settings.keepa_api_key),
        },
    )
    _t0 = time.perf_counter()
    ebay_coro = ebay.get_sold_comps(
        barcode=barcode, keyword=search_keyword, days=30, limit=ebay_limit,
        condition=condition, category_id=ebay_category_id,
    )

    amazon_raw: CompsResult | None = None
    if settings.keepa_api_key:
        amazon = _get_amazon_client()
        amazon_coro = amazon.get_sold_comps(
            barcode=barcode, keyword=search_keyword, days=30, limit=50,
            product_type=product_type,
        )
        results = await asyncio.gather(ebay_coro, amazon_coro, return_exceptions=True)
        ebay_raw = results[0] if not isinstance(results[0], Exception) else CompsResult(
            listings=[],
            total_sold=0,
            median_price=0.0,
            marketplace="ebay",
            scrape_source="ebay",
            scrape_status="blocked",
            error_reason=type(results[0]).__name__,
        )
        if isinstance(results[0], Exception):
            logger.warning("eBay fetch failed, continuing with empty comps: %s", results[0])
        if isinstance(results[1], Exception):
            logger.warning("Amazon fetch failed, continuing with eBay only: %s", results[1])
        else:
            amazon_raw = results[1]
    else:
        ebay_raw = await ebay_coro

    # 1b. UPC fallback: si barcode no devolvió nada, intentar keyword.
    # No suplementar UPC con keyword: el UPC devuelve el producto EXACTO
    # (condición correcta, sin ruido). Keyword trae items de otras condiciones
    # que contaminan la mediana de precio.
    upc_hit = bool(barcode and ebay_raw.listings)
    used_keyword_fallback = False
    if barcode and not ebay_raw.listings:
        # Fallback keyword: search_keyword (de UPC lookup) o título de Amazon/Keepa
        fallback_kw = search_keyword
        if (not fallback_kw or fallback_kw == barcode) and amazon_raw and amazon_raw.listings:
            # Usar título del primer listing de Amazon como keyword
            fallback_kw = amazon_raw.listings[0].title
            logger.info("Usando título de Amazon como fallback keyword: '%s'", fallback_kw)
        if fallback_kw and fallback_kw != barcode:
            logger.info("eBay barcode sin resultados, fallback keyword='%s'", fallback_kw)
            used_keyword_fallback = True
            yield _progress(
                "fetch",
                "active",
                "UPC search had no eBay matches; trying the product title",
                36,
                {"barcode": barcode, "fallback_keyword": fallback_kw},
            )
            try:
                ebay_raw = await ebay.get_sold_comps(
                    keyword=fallback_kw, days=30, limit=ebay_limit,
                    condition=condition, category_id=ebay_category_id,
                )
                upc_hit = False
                if not search_keyword:
                    search_keyword = fallback_kw
            except Exception as e:
                logger.warning("eBay keyword fallback failed: %s", e)

    logger.info("⏱ FETCH: %.1fs (ebay=%d, amazon=%d)",
                time.perf_counter() - _t0,
                len(ebay_raw.listings),
                len(amazon_raw.listings) if amazon_raw else 0)
    yield _progress(
        "fetch",
        "complete",
        "Marketplace data collected",
        46,
        {
            "ebay_raw_count": len(ebay_raw.listings),
            "amazon_raw_count": len(amazon_raw.listings) if amazon_raw else 0,
            "ebay_source": ebay_raw.scrape_source,
            "ebay_status": ebay_raw.scrape_status,
            "fallback_used": ebay_raw.fallback_used or used_keyword_fallback,
            "query_used": ebay_raw.query_used,
        },
    )

    # -----------------------------------------------------------------------
    # 2. Enriquecer títulos eBay con LLM (Amazon/Keepa ya tiene datos struct.)
    # -----------------------------------------------------------------------
    _t0 = time.perf_counter()
    ebay_enriched = False

    yield _progress(
        "matching",
        "active",
        "Reading listings and matching relevant comps",
        52,
        {
            "ebay_raw_count": len(ebay_raw.listings),
            "amazon_raw_count": len(amazon_raw.listings) if amazon_raw else 0,
            "keyword": search_keyword,
        },
    )
    if ebay_raw.listings and not upc_hit:
        ebay_raw = await enrich_listings(ebay_raw, keyword=search_keyword or barcode)
        ebay_enriched = True
        yield _progress(
            "matching",
            "active",
            "Listing titles enriched",
            58,
            {"ebay_count": len(ebay_raw.listings)},
        )

    # -----------------------------------------------------------------------
    # 2b. LLM relevance filter (después de enrich, antes de pipeline)
    # -----------------------------------------------------------------------
    if ebay_raw.listings and search_keyword and not upc_hit:
        ebay_raw = await filter_comps_by_relevance(ebay_raw, search_keyword)
    if amazon_raw and amazon_raw.listings and search_keyword:
        amazon_raw = await filter_comps_by_relevance(amazon_raw, search_keyword)

    logger.info("⏱ ENRICH+RELEVANCE: %.1fs", time.perf_counter() - _t0)
    yield _progress(
        "matching",
        "complete",
        "Relevant comps selected",
        68,
        {
            "ebay_count": len(ebay_raw.listings),
            "amazon_count": len(amazon_raw.listings) if amazon_raw else 0,
        },
    )

    # -----------------------------------------------------------------------
    # 3. Ejecutar pipeline de motores en AMBOS marketplaces
    # -----------------------------------------------------------------------
    _t0 = time.perf_counter()
    yield _progress(
        "scoring",
        "active",
        "Calculating profit, velocity, risk, and confidence",
        72,
        {"condition": condition, "cost_price": cost_price},
    )
    kw = search_keyword or barcode or ""

    # Resolve category-specific config (fees, thresholds, shipping defaults)
    ebay_config: ResolvedConfig | None = None
    amazon_config: ResolvedConfig | None = None
    try:
        from app.database import async_session as _sf
        async with _sf() as _cfg_db:
            ebay_config = await resolve_config(category_slug, channel="ebay", db=_cfg_db)
            amazon_config = await resolve_config(category_slug, channel="amazon_fba", db=_cfg_db)
    except Exception as e:
        logger.warning("Category config resolution failed, using global defaults: %s", e)

    pipeline_kwargs = dict(
        keyword=kw,
        condition=condition,
        cost_price=cost_price,
        shipping_cost=shipping_cost,
        packaging_cost=packaging_cost,
        prep_cost=prep_cost,
        promo_cost=promo_cost,
        return_reserve_pct=return_reserve_pct,
        target_profit=target_profit,
        target_roi=target_roi,
        product_type=product_type,
    )

    ebay_pipeline = _run_pipeline(
        ebay_raw, marketplace_name="ebay", enriched=ebay_enriched,
        config=ebay_config, **pipeline_kwargs,
    )

    # Si la busqueda UPC devolvio algo bruto pero nada util tras cleanup
    # (moneda no-USD, condicion incorrecta, placeholders, etc.), intentar el titulo UPC.
    if (
        barcode
        and search_keyword
        and upc_hit
        and not used_keyword_fallback
        and (
            not ebay_pipeline.has_valid_comps
            or ebay_pipeline.cleaned.pricing_basis == "mixed_conditions"
        )
    ):
        logger.info(
            "eBay UPC raw comps unusable after cleanup, fallback keyword='%s'",
            search_keyword,
        )
        yield _progress(
            "scoring",
            "active",
            "UPC comps were not reliable; checking title-based comps",
            76,
            {"barcode": barcode, "fallback_keyword": search_keyword},
        )
        try:
            ebay_raw_kw = await ebay.get_sold_comps(
                keyword=search_keyword, days=30, limit=ebay_limit,
                condition=condition, category_id=ebay_category_id,
            )
            ebay_enriched_kw = False
            if ebay_raw_kw.listings:
                ebay_raw_kw = await enrich_listings(
                    ebay_raw_kw, keyword=search_keyword,
                )
                ebay_enriched_kw = True
                ebay_raw_kw = await filter_comps_by_relevance(
                    ebay_raw_kw, search_keyword,
                )
            ebay_pipeline_kw = _run_pipeline(
                ebay_raw_kw, marketplace_name="ebay",
                enriched=ebay_enriched_kw, config=ebay_config, **pipeline_kwargs,
            )
            if ebay_pipeline_kw.cleaned.clean_total > ebay_pipeline.cleaned.clean_total:
                ebay_raw = ebay_raw_kw
                ebay_pipeline = ebay_pipeline_kw
                ebay_enriched = ebay_enriched_kw
                upc_hit = False
                used_keyword_fallback = True
        except Exception as e:
            logger.warning("eBay post-cleanup keyword fallback failed: %s", e)

    # -----------------------------------------------------------------------
    # 3b. Re-fetch eBay si pocos comps limpios (buscar más páginas)
    # -----------------------------------------------------------------------
    _MIN_CLEAN_COMPS = 15
    _REFETCH_LIMIT = 150  # 150 items → scraper pide página 1 completa (240)
    _REFETCH_LIMIT_UPC = 500  # UPC data es limpia, vale la pena pedir más
    if (
        ebay_pipeline.cleaned.clean_total < _MIN_CLEAN_COMPS
        and search_keyword
        and ebay_pipeline.cleaned.clean_total > 0  # al menos algo encontró
    ):
        refetch_limit = _REFETCH_LIMIT_UPC if barcode else _REFETCH_LIMIT
        logger.info(
            "Solo %d comps limpios eBay (<%d), re-fetching con limit=%d",
            ebay_pipeline.cleaned.clean_total, _MIN_CLEAN_COMPS, refetch_limit,
        )
        yield _progress(
            "scoring",
            "active",
            "Small comp sample; pulling a wider eBay sample",
            78,
            {
                "clean_count": ebay_pipeline.cleaned.clean_total,
                "refetch_limit": refetch_limit,
            },
        )
        try:
            refetch_barcode = barcode if upc_hit else None
            ebay_raw2 = await ebay.get_sold_comps(
                barcode=refetch_barcode, keyword=search_keyword, days=30,
                limit=refetch_limit, condition=condition,
                category_id=ebay_category_id,
            )
            if ebay_raw2.listings:
                if not upc_hit:
                    ebay_raw2 = await enrich_listings(
                        ebay_raw2, keyword=search_keyword or barcode,
                    )
                if search_keyword and not upc_hit:
                    ebay_raw2 = await filter_comps_by_relevance(
                        ebay_raw2, search_keyword,
                    )
                ebay_pipeline2 = _run_pipeline(
                    ebay_raw2, marketplace_name="ebay",
                    enriched=True, config=ebay_config, **pipeline_kwargs,
                )
                if ebay_pipeline2.cleaned.clean_total > ebay_pipeline.cleaned.clean_total:
                    logger.info(
                        "Re-fetch mejoró de %d a %d comps limpios",
                        ebay_pipeline.cleaned.clean_total,
                        ebay_pipeline2.cleaned.clean_total,
                    )
                    ebay_pipeline = ebay_pipeline2
        except Exception as e:
            logger.warning("eBay re-fetch failed: %s", e)

    amazon_pipeline: _PipelineResult | None = None
    if amazon_raw and amazon_raw.listings:
        amazon_pipeline = _run_pipeline(
            amazon_raw, marketplace_name="amazon_fba", enriched=True,
            config=amazon_config, **pipeline_kwargs,
        )

    logger.info("⏱ PIPELINE+REFETCH: %.1fs (ebay=%d, amazon=%d comps)",
                time.perf_counter() - _t0,
                ebay_pipeline.cleaned.clean_total,
                amazon_pipeline.cleaned.clean_total if amazon_pipeline else 0)
    yield _progress(
        "scoring",
        "complete",
        "Deal model ready",
        82,
        {
            "ebay_clean_count": ebay_pipeline.cleaned.clean_total,
            "amazon_clean_count": amazon_pipeline.cleaned.clean_total if amazon_pipeline else 0,
        },
    )

    # -----------------------------------------------------------------------
    # 4. Determinar canal recomendado con execution-aware expected profit.
    # -----------------------------------------------------------------------
    candidates = [ebay_pipeline]
    if amazon_pipeline:
        candidates.append(amazon_pipeline)
    valid_candidates = [c for c in candidates if c.has_valid_comps]

    if valid_candidates:
        (
            primary,
            best_by_profit,
            recommended_marketplace,
            best_marketplace_reason,
        ) = _select_primary_marketplace(valid_candidates)

        best_profit_marketplace = best_by_profit.marketplace_name
        best_marketplace = primary.marketplace_name

        if recommended_marketplace is None:
            primary.warnings.append(
                "No marketplace has enough execution confidence to be recommended automatically; "
                "verify comps manually before buying."
            )

        best_by_execution = (
            next(
                (c for c in valid_candidates if c.marketplace_name == recommended_marketplace),
                primary,
            )
            if recommended_marketplace
            else primary
        )

        for c in valid_candidates:
            if c.execution:
                c.execution.channel_role = "candidate"
        if best_by_profit.execution:
            best_by_profit.execution.channel_role = "best_profit"
        if recommended_marketplace and best_by_execution.execution:
            best_by_execution.execution.channel_role = "recommended"

        if (
            primary.recommendation == "buy"
            and best_by_profit.marketplace_name != primary.marketplace_name
            and best_by_profit.execution
            and best_by_profit.execution.score < 55
        ):
            primary.recommendation = "buy_small"
            primary.warnings.append(
                "Best-profit channel has low execution probability; "
                "overall buy quantity is capped."
            )
    else:
        # Fallback primario si ningún marketplace tiene comps útiles.
        if marketplace == "amazon_fba" and amazon_pipeline:
            primary = amazon_pipeline
        else:
            primary = ebay_pipeline
        best_marketplace = primary.marketplace_name
        best_marketplace_reason = "only_available"
        best_profit_marketplace = primary.marketplace_name
        recommended_marketplace = primary.marketplace_name

    market_score = primary.opportunity if primary.has_valid_comps else 0
    execution_score = primary.execution.score if primary.execution else 0
    final_score = _compute_final_score(market_score, execution_score)

    # -----------------------------------------------------------------------
    # 6a. Iniciar tareas AI (non-blocking, corren mientras construimos chunk 1)
    # -----------------------------------------------------------------------
    comparison_text = _build_comparison_text(ebay_pipeline, amazon_pipeline)
    execution_text = _build_execution_text(
        primary=primary,
        best_profit_marketplace=best_profit_marketplace,
        recommended_marketplace=recommended_marketplace,
        final_score=final_score,
    )

    # Determine user tier for AI gating
    _user_tier = "free"
    if user_id:
        try:
            from app.models.user import User as _UserModel
            from app.database import async_session as _sf
            async with _sf() as _tier_db:
                _user_obj = await _tier_db.get(_UserModel, user_id)
                if _user_obj:
                    _user_tier = _user_obj.tier
        except Exception:
            pass

    # AI explanation: available for all plans
    _ai_unlocked = True

    explanation_task = None
    if _ai_unlocked:
        explanation_task = asyncio.create_task(generate_explanation(
            keyword=kw,
            cost_price=cost_price,
            marketplace=primary.marketplace_name,
            pricing=primary.pricing,
            profit_market=primary.profit_market,
            max_buy=primary.max_buy,
            velocity=primary.velocity,
            risk=primary.risk,
            confidence=primary.confidence,
            competition=primary.competition,
            trend=primary.trend,
            listing=primary.listing,
            opportunity_score=primary.opportunity,
            recommendation=primary.recommendation,
            cleaned_total=primary.cleaned.clean_total,
            raw_total=primary.cleaned.raw_total,
            comparison_text=(comparison_text or "") + execution_text,
        ))

    intel_task = None
    if mode == "premium" and primary.cleaned.clean_total > 0:
        intel_task = asyncio.create_task(compute_market_intelligence(
            keyword=kw,
            marketplace=marketplace,
            cleaned_total=primary.cleaned.clean_total,
            median_price=primary.cleaned.median_price,
            min_price=primary.cleaned.min_price,
            max_price=primary.cleaned.max_price,
            sales_per_day=primary.cleaned.sales_per_day,
            demand_trend=primary.trend.demand_trend,
            price_trend=primary.trend.price_trend,
        ))

    # -----------------------------------------------------------------------
    # 6b. Construir datos para chunk 1 (mientras AI corre en background)
    # -----------------------------------------------------------------------

    # Producto — sesión corta solo para DB
    from app.database import async_session as _sf
    product = None
    _persist_db = None
    try:
        _persist_db = _sf()
        _db = await _persist_db.__aenter__()
        product = await _find_or_create_product(_db, barcode, keyword, primary.raw_comps, upc_info)
        if product and not product.image_url and amazon_raw and amazon_raw.image_url:
            product.image_url = amazon_raw.image_url
    except Exception as e:
        logger.warning("DB unavailable, skipping persistence: %s", e)
        product = None

    has_valid_comps = primary.has_valid_comps

    # Channels
    if has_valid_comps:
        estimated_sale = primary.pricing.market_list
        own_data_markets = set()
        if ebay_pipeline.has_valid_comps:
            own_data_markets.add("ebay")
        if amazon_pipeline and amazon_pipeline.has_valid_comps:
            own_data_markets.add("amazon_fba")
        channels = _calculate_all_channels(
            cost_price, estimated_sale,
            shipping_cost=shipping_cost, packaging_cost=packaging_cost,
            prep_cost=prep_cost, promo_cost=promo_cost,
            return_reserve_pct=return_reserve_pct,
            has_own_data=own_data_markets,
        )
        if channels:
            for pipeline in candidates:
                if not pipeline.has_valid_comps:
                    continue
                if pipeline.marketplace_name not in MARKETPLACE_CALCULATORS:
                    continue
                # Use the pipeline's own profit calculation (already has correct
                # category/bracket fees from _run_pipeline) instead of recalculating
                # with flat MARKETPLACE_CALCULATORS.
                p = pipeline.profit_market
                sale = pipeline.pricing.market_list
                for i, ch in enumerate(channels):
                    if ch.marketplace != pipeline.marketplace_name:
                        continue
                    channels[i] = ChannelBreakdown(
                        marketplace=pipeline.marketplace_name,
                        estimated_sale_price=sale,
                        net_proceeds=round(p.risk_adjusted_net, 2),
                        profit=round(p.profit, 2),
                        roi_pct=round(p.roi * 100, 2) if math.isfinite(p.roi) else 0,
                        margin_pct=round(p.margin * 100, 2) if math.isfinite(p.margin) else 0,
                        is_estimated=False,
                    )
                    break
            channels.sort(key=lambda c: c.profit, reverse=True)
            _assign_channel_labels(channels)
            _attach_execution_to_channels(
                channels,
                candidates,
                recommended_marketplace=recommended_marketplace,
                best_profit_marketplace=best_profit_marketplace,
            )
    else:
        estimated_sale = None
        channels = None

    # Summary con valores pre-intelligence
    signal_map = {"buy": "positive", "buy_small": "positive", "watch": "caution", "pass": "negative"}
    best_max_buy = primary.max_buy.recommended_max if has_valid_comps else 0.0
    headroom = (best_max_buy - cost_price) if has_valid_comps else 0.0
    signal = signal_map.get(primary.recommendation, "neutral")

    # Profit signal: usar el mejor profit real entre channels con datos propios,
    # para que el hero number coincida con lo que se muestra por canal.
    if has_valid_comps and channels:
        real_channels = [ch for ch in channels if not ch.is_estimated]
        if real_channels:
            best_channel = max(real_channels, key=lambda ch: ch.profit)
            summary_profit = best_channel.profit
            summary_roi = best_channel.roi_pct
            summary_margin = best_channel.margin_pct
        else:
            summary_profit = primary.profit_market.profit
            _roi = primary.profit_market.roi
            summary_roi = round(_roi * 100, 2) if math.isfinite(_roi) else 0.0
            summary_margin = round(primary.profit_market.margin * 100, 2)
    else:
        summary_profit = 0.0
        summary_roi = 0.0
        summary_margin = 0.0

    scores = ScoreBreakdown(
        flip_score=primary.opportunity if has_valid_comps else 0,
        velocity=primary.velocity.score if has_valid_comps else 0,
        risk=primary.risk.score if has_valid_comps else 0,
        risk_label=primary.risk.category,
        confidence=primary.confidence.score if has_valid_comps else 0,
        confidence_label=primary.confidence.category,
        temporal_window_expanded=primary.cleaned.temporal_window_expanded,
        execution_score=primary.execution.score if primary.execution else None,
        win_probability=primary.execution.win_probability if primary.execution else None,
        final_score=final_score,
    ) if has_valid_comps else None

    summary = AnalysisSummary(
        recommendation=primary.recommendation,
        signal=signal,
        buy_box=BuyBox(
            recommended_max_buy=best_max_buy,
            your_cost=cost_price,
            headroom=round(headroom, 2),
        ),
        sale_plan=SalePlan(
            recommended_list_price=primary.pricing.market_list if has_valid_comps else 0.0,
            quick_sale_price=primary.pricing.quick_list if has_valid_comps else 0.0,
            stretch_price=(primary.pricing.stretch_list if primary.pricing.stretch_allowed else None) if has_valid_comps else None,
        ),
        returns=Returns(
            profit=summary_profit,
            roi_pct=summary_roi,
            margin_pct=summary_margin,
        ),
        risk=primary.risk.category,
        confidence=primary.confidence.category,
        warnings=[],
        scores=scores,
    )

    # Marketplace analyses
    ebay_analysis = _pipeline_to_marketplace_analysis(ebay_pipeline)
    amazon_analysis = _pipeline_to_marketplace_analysis(amazon_pipeline) if amazon_pipeline else None

    # Product summary
    _product_id = product.id if product is not None else None
    _product_barcode = product.barcode if product is not None else None
    _product_title = product.title if product is not None else None
    _product_brand = product.brand if product is not None else None
    _product_image_url = product.image_url if product is not None else None

    product_title = search_keyword or (barcode or "Unknown")
    if _product_id is not None:
        product_summary = ProductSummary(
            id=_product_id,
            barcode=_product_barcode,
            title=_product_title,
            brand=_product_brand,
            image_url=_product_image_url,
        )
    else:
        fallback_brand = upc_info.get("brand") if upc_info else None
        if not fallback_brand:
            fallback_brand = detect_brand(product_title)
        product_summary = ProductSummary(
            id=0,
            barcode=barcode,
            title=product_title,
            brand=fallback_brand,
            image_url=(
                (upc_info.get("image_url") if upc_info else None)
                or (amazon_raw.image_url if amazon_raw else None)
            ),
        )

    # Top-level fields: consistentes con el summary profit signal
    top_net_profit = summary_profit if has_valid_comps else None
    top_margin = summary_margin if has_valid_comps else None
    top_roi = summary_roi if has_valid_comps else None

    # -----------------------------------------------------------------------
    # PERSIST: Save analysis BEFORE yielding so it's not lost if client disconnects
    # -----------------------------------------------------------------------
    analysis_id = None
    if product is not None:
        analysis_kwargs = dict(
            user_id=user_id,
            product_id=_product_id,
            cost_price=cost_price,
            marketplace=marketplace,
            estimated_sale_price=estimated_sale,
            net_profit=top_net_profit,
            margin_pct=top_margin,
            roi_pct=top_roi,
            flip_score=primary.opportunity if has_valid_comps else None,
            risk_score=primary.risk.score if has_valid_comps else None,
            velocity_score=primary.velocity.score if has_valid_comps else None,
            confidence_score=primary.confidence.score,
            opportunity_score=primary.opportunity,
            recommendation=primary.recommendation,
            channels=[c.model_dump() for c in channels] if channels else None,
            engines_data=None,  # filled later via UPDATE
            ai_explanation=None,  # filled later via UPDATE
            shipping_cost=shipping_cost,
            prep_cost=prep_cost,
            no_comps_found=not has_valid_comps,
        )
        try:
            analysis = Analysis(**analysis_kwargs)
            _db.add(analysis)
            await _db.commit()
            await _db.refresh(analysis)
            analysis_id = analysis.id
        except Exception as e:
            logger.warning("DB persist failed (attempt 1): %s", e)
            try:
                await _db.rollback()
            except Exception:
                pass
            try:
                async with _sf() as fresh_db:
                    analysis = Analysis(**analysis_kwargs)
                    fresh_db.add(analysis)
                    await fresh_db.commit()
                    await fresh_db.refresh(analysis)
                    analysis_id = analysis.id
                    logger.info("DB persist succeeded on retry (id=%s)", analysis_id)
            except Exception as e2:
                logger.error("DB persist failed (attempt 2): %s", e2)

    # -----------------------------------------------------------------------
    # AUTO: crear manual review request si no hay comps
    # -----------------------------------------------------------------------
    manual_review_id: int | None = None
    if not has_valid_comps and user_id:
        try:
            from app.models.manual_review import ManualReviewRequest
            review = ManualReviewRequest(
                user_id=user_id,
                analysis_id=analysis_id,
                query=barcode or keyword or "",
                barcode=barcode,
                cost_price=cost_price,
                marketplace=marketplace,
            )
            _db.add(review)
            await _db.commit()
            await _db.refresh(review)
            manual_review_id = review.id
        except Exception as e:
            logger.warning("Manual review request failed: %s", e)
            try:
                await _db.rollback()
            except Exception:
                pass

    # Cerrar sesión DB — ya no se necesita hasta el UPDATE final
    if _persist_db is not None:
        try:
            await _persist_db.__aexit__(None, None, None)
        except Exception:
            pass
        _persist_db = None

    # -----------------------------------------------------------------------
    # YIELD 1: Respuesta parcial (sin AI explanation)
    # -----------------------------------------------------------------------
    yield _progress(
        "analysis",
        "complete",
        "Decision ready",
        88,
        {
            "best_marketplace": best_marketplace,
            "best_profit_marketplace": best_profit_marketplace,
            "recommended_marketplace": recommended_marketplace,
            "recommendation": primary.recommendation,
            "opportunity": primary.opportunity if has_valid_comps else None,
            "execution_score": primary.execution.score if primary.execution else None,
            "final_score": final_score,
        },
    )
    partial_response = AnalysisResponse(
        id=analysis_id,
        product=product_summary,
        cost_price=cost_price,
        marketplace=marketplace,
        estimated_sale_price=estimated_sale,
        net_profit=top_net_profit,
        margin_pct=top_margin,
        roi_pct=top_roi,
        flip_score=primary.opportunity if has_valid_comps else None,
        risk_score=primary.risk.score if has_valid_comps else None,
        velocity_score=primary.velocity.score if has_valid_comps else None,
        recommendation=primary.recommendation,
        channels=channels,
        summary=summary,
        ai_explanation=None,
        ai_locked=not _ai_unlocked,
        market_intelligence=None,
        detected_category=category_result.category if category_result else None,
        category_confidence=category_result.confidence if category_result else None,
        category_slug=category_slug,
        no_comps_found=not has_valid_comps,
        manual_review_id=manual_review_id,
        sample_comps=_select_sample_comps(
            ebay_pipeline.cleaned if ebay_pipeline.has_valid_comps else primary.cleaned
        ),
        observation_mode=ebay_config.observation_mode if ebay_config else False,
        ebay_analysis=ebay_analysis,
        amazon_analysis=amazon_analysis,
        best_marketplace=best_marketplace,
        best_marketplace_reason=best_marketplace_reason,
        best_profit_marketplace=best_profit_marketplace,
        recommended_marketplace=recommended_marketplace,
        execution_analysis=_execution_to_schema(primary.execution) if primary.execution else None,
        market_score=market_score,
        final_score=final_score,
        created_at=datetime.now(timezone.utc),
    )
    yield {"event": "analysis", "data": partial_response}

    # -----------------------------------------------------------------------
    # 7. Await AI + Intelligence
    # -----------------------------------------------------------------------
    yield _progress(
        "ai",
        "active",
        "Writing the AI brief",
        92,
        {"mode": mode, "has_market_intelligence": bool(intel_task)},
    )
    _t0 = time.perf_counter()
    ai_explanation = None
    if explanation_task is not None:
        try:
            ai_explanation = await explanation_task
        except Exception as e:
            logger.warning("AI explanation failed: %s", e)

    market_intel = None
    if intel_task is not None:
        try:
            market_intel = await intel_task
        except Exception as e:
            logger.warning("Market intelligence failed: %s", e)

    logger.info("⏱ AI+INTELLIGENCE: %.1fs", time.perf_counter() - _t0)

    # -----------------------------------------------------------------------
    # 8. Ajustes post-intelligence en el pipeline primario
    # -----------------------------------------------------------------------
    risk = primary.risk
    opportunity = primary.opportunity
    recommendation = primary.recommendation
    warnings: list[str] = []

    if market_intel is not None:
        if market_intel.depreciation_risk > 70:
            penalty = min(15, (market_intel.depreciation_risk - 70) // 2)
            risk = type(risk)(
                score=max(0, risk.score - penalty),
                category=risk.category,
                factors=risk.factors,
            )
            if risk.score < 30:
                risk = type(risk)(score=risk.score, category="high", factors=risk.factors)
            elif risk.score < 60:
                risk = type(risk)(score=risk.score, category="medium", factors=risk.factors)

        if market_intel.product_lifecycle == "end_of_life":
            risk = type(risk)(
                score=max(0, risk.score - 10),
                category=risk.category,
                factors=risk.factors,
            )
            if risk.score < 30:
                risk = type(risk)(score=risk.score, category="high", factors=risk.factors)
            elif risk.score < 60:
                risk = type(risk)(score=risk.score, category="medium", factors=risk.factors)

        seasonal_adj = round(market_intel.seasonal_factor * 10)
        opportunity = max(0, min(100, opportunity + seasonal_adj))

        for ev in market_intel.market_events:
            if ev.impact == "negative" and ev.relevance == "high":
                warnings.append(f"Market event: {ev.event}")

        if (
            market_intel.timing_recommendation == "wait"
            and recommendation in ("buy", "buy_small")
            and market_intel.confidence == "high"
        ):
            recommendation = "watch"
            warnings.append(
                "Market intelligence recommends waiting before buying."
            )

    warnings = _dedupe_warnings(warnings)

    # Rebuild summary con valores finales
    signal = signal_map.get(recommendation, "neutral")
    # Actualizar scores con risk post-intelligence
    final_scores = None
    if scores is not None:
        final_scores = ScoreBreakdown(
            flip_score=opportunity,
            velocity=scores.velocity,
            risk=risk.score,
            risk_label=risk.category,
            confidence=scores.confidence,
            confidence_label=scores.confidence_label,
            temporal_window_expanded=scores.temporal_window_expanded,
            execution_score=scores.execution_score,
            win_probability=scores.win_probability,
            final_score=scores.final_score,
        )

    final_summary = AnalysisSummary(
        recommendation=recommendation,
        signal=signal,
        buy_box=summary.buy_box,
        sale_plan=summary.sale_plan,
        returns=summary.returns,
        risk=risk.category,
        confidence=primary.confidence.category,
        warnings=warnings,
        scores=final_scores,
    )

    # Market intelligence output
    market_intel_out = None
    if market_intel and has_valid_comps:
        market_intel_out = MarketIntelligenceOut(
            product_lifecycle=market_intel.product_lifecycle,
            depreciation_risk=market_intel.depreciation_risk,
            seasonal_factor=market_intel.seasonal_factor,
            market_events=[
                MarketEventOut(event=e.event, impact=e.impact, relevance=e.relevance)
                for e in market_intel.market_events
            ],
            timing_recommendation=market_intel.timing_recommendation,
            intelligence_summary=market_intel.intelligence_summary,
            confidence=market_intel.confidence,
            search_source=market_intel.search_source,
        )

    # -----------------------------------------------------------------------
    # 9. Engines data + persistencia
    # -----------------------------------------------------------------------
    # Sample comps para persistir en engines_data (reutilizable en GET)
    _sample_comps_data = [s.model_dump() for s in _select_sample_comps(
        ebay_pipeline.cleaned if ebay_pipeline.has_valid_comps else primary.cleaned
    )]

    engines_data = {
        "sample_comps": _sample_comps_data,
        "pricing": asdict(primary.pricing),
        "profit_market": asdict(primary.profit_market),
        "profit_quick": asdict(primary.profit_quick),
        "max_buy": asdict(primary.max_buy),
        "velocity": asdict(primary.velocity),
        "risk": asdict(risk),
        "confidence": asdict(primary.confidence),
        "seller_premium": asdict(primary.seller),
        "competition": asdict(primary.competition),
        "trend": asdict(primary.trend),
        "listing_strategy": asdict(primary.listing),
        "title_risk": asdict(primary.title_risk),
        "condition_analysis": primary.condition_analysis.model_dump(),
        "opportunity_score": opportunity,
        "market_score": market_score,
        "execution_score": execution_score,
        "final_score": final_score,
        "best_profit_marketplace": best_profit_marketplace,
        "recommended_marketplace": recommended_marketplace,
        "execution": asdict(primary.execution) if primary.execution else None,
        "marketplace_execution": {
            p.marketplace_name: asdict(p.execution)
            for p in candidates
            if p.execution is not None
        },
        "market_intelligence": asdict(market_intel) if market_intel else None,
        "cleaned_comps": {
            "raw_total": primary.cleaned.raw_total,
            "clean_total": primary.cleaned.clean_total,
            "outliers_removed": primary.cleaned.outliers_removed,
            "relevance_filtered": primary.cleaned.relevance_filtered,
            "cv": primary.cleaned.cv,
            "median_price": primary.cleaned.median_price,
            "p25": primary.cleaned.p25,
            "p75": primary.cleaned.p75,
            "days_of_data": primary.cleaned.days_of_data,
            "temporal_window_expanded": primary.cleaned.temporal_window_expanded,
            "initial_days_requested": primary.cleaned.initial_days_requested,
        },
        "data_quality": {
            "pricing_basis": primary.cleaned.pricing_basis,
            "warnings": primary.cleaned.data_quality_warnings,
            "filter_counts": primary.cleaned.filter_counts,
            "scraper": {
                "source": primary.raw_comps.scrape_source,
                "status": primary.raw_comps.scrape_status,
                "fallback_used": primary.raw_comps.fallback_used,
                "query_used": primary.raw_comps.query_used,
                "error_reason": primary.raw_comps.error_reason,
                "diagnostics": primary.raw_comps.diagnostics,
            },
        },
        # Per-marketplace engine outputs for accurate GET reconstruction
        "marketplace_engines": _build_marketplace_engines(
            ebay_pipeline, amazon_pipeline,
        ),
    }

    # UPDATE analysis with AI explanation + post-intelligence adjustments
    if analysis_id is not None:
        try:
            async with _sf() as _upd_db:
                from sqlalchemy import update
                await _upd_db.execute(
                    update(Analysis).where(Analysis.id == analysis_id).values(
                        ai_explanation=ai_explanation if has_valid_comps else None,
                        flip_score=opportunity if has_valid_comps else None,
                        risk_score=risk.score if has_valid_comps else None,
                        recommendation=recommendation,
                        engines_data=engines_data,
                    )
                )
                await _upd_db.commit()
        except Exception as e:
            logger.warning("DB update (AI fields) failed: %s", e)

    logger.info(
        "ANALYSIS product=%r cost=%.2f marketplace=%s sale=%.2f profit=%s roi=%s "
        "opportunity=%d recommendation=%s best=%s comps=%d/%d",
        search_keyword or barcode,
        cost_price,
        marketplace,
        estimated_sale or 0,
        f"${top_net_profit:.2f}" if top_net_profit is not None else "N/A",
        f"{top_roi:.1f}%" if top_roi is not None else "N/A",
        opportunity,
        recommendation,
        best_marketplace,
        primary.cleaned.clean_total,
        primary.cleaned.raw_total,
    )
    logger.info("⏱ TOTAL: %.1fs", time.perf_counter() - _t_total)

    # -----------------------------------------------------------------------
    # YIELD 2: AI complete + campos finales
    # -----------------------------------------------------------------------
    yield _progress(
        "ai",
        "complete",
        "AI brief ready",
        100,
        {"analysis_id": analysis_id},
    )
    yield {"event": "ai_complete", "data": AICompleteEvent(
        ai_explanation=ai_explanation if has_valid_comps else None,
        market_intelligence=market_intel_out,
        risk_score=risk.score if has_valid_comps else None,
        flip_score=opportunity if has_valid_comps else None,
        recommendation=recommendation,
        summary=final_summary,
        id=analysis_id,
    )}


# ---------------------------------------------------------------------------
# Funcion principal: run_analysis (wrapper sincrono)
# ---------------------------------------------------------------------------

async def run_analysis(
    barcode: str | None,
    keyword: str | None,
    cost_price: float,
    marketplace: str,
    shipping_cost: float = 0.0,
    packaging_cost: float = 0.0,
    prep_cost: float = 0.0,
    promo_cost: float = 0.0,
    return_reserve_pct: float = 0.05,
    target_profit: float = 10.0,
    target_roi: float = 0.35,
    detailed: bool = False,
    condition: str = "any",
    mode: str = "standard",
    product_type: str | None = None,
    user_id: int | None = None,
) -> AnalysisResponse:
    """Endpoint sincrono: espera todo y retorna la respuesta completa."""
    async with _analysis_semaphore:
        return await _run_analysis_inner(
            barcode=barcode, keyword=keyword, cost_price=cost_price,
            marketplace=marketplace, shipping_cost=shipping_cost,
            packaging_cost=packaging_cost, prep_cost=prep_cost,
            promo_cost=promo_cost, return_reserve_pct=return_reserve_pct,
            target_profit=target_profit, target_roi=target_roi,
            detailed=detailed, condition=condition, mode=mode,
            product_type=product_type, user_id=user_id,
        )


async def _run_analysis_inner(
    barcode: str | None,
    keyword: str | None,
    cost_price: float,
    marketplace: str,
    shipping_cost: float = 0.0,
    packaging_cost: float = 0.0,
    prep_cost: float = 0.0,
    promo_cost: float = 0.0,
    return_reserve_pct: float = 0.05,
    target_profit: float = 10.0,
    target_roi: float = 0.35,
    detailed: bool = False,
    condition: str = "any",
    mode: str = "standard",
    product_type: str | None = None,
    user_id: int | None = None,
) -> AnalysisResponse:
    result = None
    async for chunk in run_analysis_progressive(
        barcode=barcode, keyword=keyword, cost_price=cost_price,
        marketplace=marketplace, shipping_cost=shipping_cost,
        packaging_cost=packaging_cost, prep_cost=prep_cost,
        promo_cost=promo_cost, return_reserve_pct=return_reserve_pct,
        target_profit=target_profit, target_roi=target_roi,
        detailed=detailed, condition=condition, mode=mode,
        product_type=product_type, user_id=user_id,
    ):
        if chunk["event"] == "analysis":
            result = chunk["data"]
        elif chunk["event"] == "ai_complete":
            ai_data = chunk["data"]
            result.ai_explanation = ai_data.ai_explanation
            result.market_intelligence = ai_data.market_intelligence
            if ai_data.risk_score is not None:
                result.risk_score = ai_data.risk_score
            if ai_data.flip_score is not None:
                result.flip_score = ai_data.flip_score
            if ai_data.recommendation is not None:
                result.recommendation = ai_data.recommendation
            if ai_data.summary is not None:
                result.summary = ai_data.summary
            if ai_data.id is not None:
                result.id = ai_data.id
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_comparison_text(
    ebay: _PipelineResult,
    amazon: _PipelineResult | None,
) -> str | None:
    """Construye texto de comparación entre marketplaces para la IA."""
    if amazon is None or not amazon.has_valid_comps:
        if not ebay.has_valid_comps:
            return None
        return None  # Solo hay un marketplace, no hay comparación

    if not ebay.has_valid_comps:
        return None

    ebay_median = ebay.cleaned.median_price
    amz_median = amazon.cleaned.median_price
    delta_pct = ((amz_median - ebay_median) / ebay_median * 100) if ebay_median > 0 else 0

    return (
        f"\n\nMARKETPLACE COMPARISON:\n"
        f"\n"
        f"eBay:\n"
        f"- Median: ${ebay_median:.2f} ({ebay.cleaned.clean_total} comps)\n"
        f"- Profit: ${ebay.profit_market.profit:.2f} (ROI: {ebay.profit_market.roi:.1%})\n"
        f"- Velocity: {ebay.velocity.score}/100 ({ebay.velocity.category})\n"
        f"- Risk: {ebay.risk.score}/100 ({ebay.risk.category})\n"
        f"- Demand trend: {ebay.trend.demand_trend:+.1f}%\n"
        f"- Opportunity: {ebay.opportunity}/100 → {ebay.recommendation}\n"
        f"\n"
        f"Amazon:\n"
        f"- Median: ${amz_median:.2f} ({amazon.cleaned.clean_total} comps)\n"
        f"- Profit: ${amazon.profit_market.profit:.2f} (ROI: {amazon.profit_market.roi:.1%})\n"
        f"- Velocity: {amazon.velocity.score}/100 ({amazon.velocity.category})\n"
        f"- Risk: {amazon.risk.score}/100 ({amazon.risk.category})\n"
        f"- Demand trend: {amazon.trend.demand_trend:+.1f}%\n"
        f"- Opportunity: {amazon.opportunity}/100 → {amazon.recommendation}\n"
        f"\n"
        f"Price delta: Amazon is {delta_pct:+.1f}% vs eBay\n"
        f"\n"
        f"Analyze opportunities and trends across BOTH marketplaces. "
        f"Recommend the best channel for this product and why."
    )


def _detect_distribution_shape(prices: list[float]) -> str:
    """Detecta forma de la distribución de precios.

    Retorna: 'normal', 'bimodal', 'dispersed', 'insufficient'.
    Bimodal = dos clusters claros separados por un gap grande.
    Dispersed = precios muy esparcidos sin clusters claros (CV > 0.5).
    """
    if len(prices) < 5:
        return "insufficient"

    sorted_prices = sorted(prices)
    total_range = sorted_prices[-1] - sorted_prices[0]

    if total_range == 0:
        return "normal"

    gaps = [sorted_prices[i + 1] - sorted_prices[i] for i in range(len(sorted_prices) - 1)]
    median_gap = sorted(gaps)[len(gaps) // 2]

    # Check each gap for bimodal split: must have >=2 items on each side
    for idx, gap in enumerate(gaps):
        left_count = idx + 1
        right_count = len(sorted_prices) - left_count
        if left_count < 2 or right_count < 2:
            continue
        gap_ratio = gap / total_range
        is_outlier = gap > median_gap * 2 if median_gap > 0 else gap_ratio > 0.25
        is_significant = gap_ratio > 0.15
        if is_outlier and is_significant:
            return "bimodal"

    # Dispersed: many large gaps, no clear center
    mean = sum(sorted_prices) / len(sorted_prices)
    variance = sum((p - mean) ** 2 for p in sorted_prices) / len(sorted_prices)
    cv = (variance ** 0.5) / mean if mean > 0 else 0
    if cv > 0.50:
        return "dispersed"

    return "normal"


def _clean_listing_url(url: str | None) -> str | None:
    """Limpia URLs de eBay: solo /itm/ITEM_ID, sin query params del scraper."""
    if not url:
        return None
    # eBay: https://www.ebay.com/itm/123456?_skw=... → https://www.ebay.com/itm/123456
    if "ebay.com/itm/" in url:
        return url.split("?")[0]
    # Amazon: https://www.amazon.com/dp/B09... → ya está limpia
    return url.split("?")[0] if "amazon.com" in url else url


def _select_sample_comps(
    cleaned: CleanedComps,
    n: int = 3,
) -> list[SampleComp]:
    """Selecciona n listings representativos: los más recientes cerca de la mediana (±1 std)."""
    if not cleaned.listings or cleaned.clean_total == 0:
        return []

    median = cleaned.median_price
    std = cleaned.std_dev or (cleaned.iqr / 1.35 if cleaned.iqr else median * 0.2)
    low = median - std
    high = median + std

    # Filtrar los que están dentro de ±1 std de la mediana
    candidates = [
        l for l in cleaned.listings
        if low <= l.price <= high
    ]
    # Fallback: si hay pocos candidatos, usar todos
    if len(candidates) < n:
        candidates = list(cleaned.listings)

    # Ordenar por fecha más reciente
    candidates.sort(
        key=lambda l: l.ended_at or datetime(2000, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )

    return [
        SampleComp(
            title=l.title,
            sold_price=l.price,
            sold_date=l.ended_at.strftime("%Y-%m-%d") if l.ended_at else None,
            condition=l.condition,
            url=_clean_listing_url(l.url),
            image_url=l.image_url,
        )
        for l in candidates[:n]
    ]


def _build_comps_info(
    cleaned: CleanedComps,
    source: str = "ebay_sold_cleaned",
) -> tuple[CompsInfo | None, str]:
    """Construye CompsInfo usando SOLO el dataset limpio."""
    if cleaned.clean_total == 0:
        return None, "unknown"

    clean_prices = sorted(l.total_price for l in cleaned.listings if l.total_price)
    price_buckets = build_price_buckets(clean_prices)
    sales_timeline = build_sales_by_date(cleaned.listings)
    distribution_shape = _detect_distribution_shape(clean_prices)

    return CompsInfo(
        total_sold=cleaned.clean_total,
        avg_price=cleaned.avg_price,
        median_price=cleaned.median_price,
        min_price=cleaned.min_price,
        max_price=cleaned.max_price,
        std_dev=cleaned.std_dev,
        p25=cleaned.p25,
        p75=cleaned.p75,
        sales_per_day=cleaned.sales_per_day,
        days_of_data=cleaned.days_of_data,
        source=source,
        distribution_shape=distribution_shape,
        price_distribution=[
            PriceBucketOut(
                range_min=b.range_min, range_max=b.range_max,
                count=b.count, pct_of_total=b.pct_of_total,
            ) for b in price_buckets
        ],
        sales_timeline=[
            SalesByDateOut(
                date=s.date, count=s.count, avg_price=s.avg_price,
                min_price=s.min_price, max_price=s.max_price,
            ) for s in sales_timeline
        ],
        temporal_window_expanded=cleaned.temporal_window_expanded,
        initial_days_requested=cleaned.initial_days_requested if cleaned.temporal_window_expanded else None,
    ), distribution_shape


async def _find_or_create_product(
    db: AsyncSession,
    barcode: str | None,
    keyword: str | None,
    comps: CompsResult,
    upc_info: dict | None = None,
) -> Product:
    """Busca producto existente o crea uno enriquecido con datos de comps y UPC lookup."""
    if barcode:
        result = await db.execute(select(Product).where(Product.barcode == barcode))
        product = result.scalar_one_or_none()
        if product:
            if comps.total_sold > 0:
                product.avg_sell_price = comps.median_price
            if upc_info:
                if not product.brand and upc_info.get("brand"):
                    product.brand = upc_info["brand"]
                if not product.image_url and upc_info.get("image_url"):
                    product.image_url = upc_info["image_url"]
                if product.title == barcode and upc_info.get("title"):
                    product.title = upc_info["title"]
            if not product.brand:
                product.brand = detect_brand(product.title)
            return product

    if keyword:
        result = await db.execute(
            select(Product).where(Product.title.ilike(f"%{keyword}%")).limit(1)
        )
        product = result.scalar_one_or_none()
        if product:
            if comps.total_sold > 0:
                product.avg_sell_price = comps.median_price
            if not product.brand:
                product.brand = detect_brand(keyword) or detect_brand(product.title)
            # Fix: actualizar título si contiene frases de condición del eBay listing
            if keyword and _has_condition_noise(product.title):
                product.title = keyword
            return product

    title = keyword or barcode or "Untitled product"
    if not keyword and comps.listings:
        # Solo usar título del comp como fallback si no hay keyword,
        # porque los títulos de eBay incluyen condición/talla del seller
        # (e.g. "Nintendo Switch OLED - Good Condition") que confunde.
        title = comps.listings[0].title or title
    if upc_info and upc_info.get("title"):
        title = upc_info["title"]

    brand = upc_info.get("brand") if upc_info else None
    if not brand:
        brand = detect_brand(keyword or title)

    product = Product(
        barcode=barcode,
        title=title,
        brand=brand,
        image_url=upc_info.get("image_url") if upc_info else None,
        avg_sell_price=comps.median_price if comps.total_sold > 0 else None,
    )
    db.add(product)
    await db.flush()
    return product



def _calculate_all_channels(
    cost: float,
    sale_price: float,
    marketplace: str = "ebay",
    shipping_cost: float = 0.0,
    packaging_cost: float = 0.0,
    prep_cost: float = 0.0,
    promo_cost: float = 0.0,
    return_reserve_pct: float = 0.05,
    has_own_data: set[str] | None = None,
) -> list[ChannelBreakdown]:
    """Calcula profit estimado en cada marketplace."""
    if has_own_data is None:
        has_own_data = set()
    channels = []
    for name, calc_fn in MARKETPLACE_CALCULATORS.items():
        fees = calc_fn(Decimal(str(sale_price)))
        gross_after_fees = fees["net_proceeds"]
        from app.services.engines.profit_engine import compute_return_reserve
        net = gross_after_fees - shipping_cost - packaging_cost - promo_cost
        return_reserve = compute_return_reserve(sale_price)
        profit = net - return_reserve - cost - prep_cost
        total_invested = cost + prep_cost
        roi_pct = (profit / total_invested * 100) if total_invested > 0 else 0
        margin_pct = (profit / sale_price * 100) if sale_price > 0 else 0
        channels.append(
            ChannelBreakdown(
                marketplace=name,
                estimated_sale_price=sale_price,
                net_proceeds=round(net - return_reserve, 2),
                profit=round(profit, 2),
                roi_pct=round(roi_pct, 2),
                margin_pct=round(margin_pct, 2),
                is_estimated=name not in has_own_data,
            )
        )
    channels.sort(key=lambda c: c.profit, reverse=True)
    _assign_channel_labels(channels)
    return channels


def _assign_channel_labels(channels: list[ChannelBreakdown]) -> None:
    """Asigna labels BEST PROFIT y BEST ROI a los canales.

    Only channels with real comps data (is_estimated=False) qualify for labels.
    Estimated channels inherit prices from the primary marketplace and shouldn't
    be labeled as "BEST PROFIT" — their numbers are speculative.
    """
    if not channels:
        return
    # Reset labels
    for ch in channels:
        ch.label = None

    # Only channels with real data qualify for labels
    real_profitable = [ch for ch in channels if ch.profit > 0 and not ch.is_estimated]

    if len(real_profitable) == 1:
        real_profitable[0].label = "ONLY PROFITABLE"
    elif len(real_profitable) > 1:
        # Sorted by profit desc already
        real_profitable[0].label = "BEST PROFIT"
        best_roi = max(real_profitable, key=lambda c: c.roi_pct)
        if best_roi is not real_profitable[0]:
            best_roi.label = "BEST ROI"
    # Si ninguno real es rentable, no se asigna label


def _attach_execution_to_channels(
    channels: list[ChannelBreakdown],
    pipelines: list[_PipelineResult],
    *,
    recommended_marketplace: str | None,
    best_profit_marketplace: str,
) -> None:
    """Agrega execution metadata a channels sin reemplazar labels de profit."""
    by_marketplace = {p.marketplace_name: p for p in pipelines}
    for ch in channels:
        p = by_marketplace.get(ch.marketplace)
        if not p or not p.execution:
            ch.channel_role = "candidate"
            continue

        execution = p.execution
        role = "candidate"
        if ch.marketplace == recommended_marketplace:
            role = "recommended"
        elif ch.marketplace == best_profit_marketplace:
            role = "test_only" if execution.score < 55 else "best_profit"

        ch.execution_score = execution.score
        ch.win_probability = execution.win_probability
        ch.expected_profit = execution.expected_profit
        ch.channel_role = role
        ch.execution_note = execution.quantity_guidance
