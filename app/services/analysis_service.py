"""Motor de análisis de FlipIQ.

Orquesta 13 motores especializados en AMBOS marketplaces (eBay + Amazon)
en paralelo, y la IA compara tendencias y oportunidades entre los dos.

A. Comp Cleaner → B. Pricing → C. Profit → D. Max Buy Price
E. Velocity → F. Risk → G. Confidence
H. Seller Premium → I. Competition → J. Trend → K. Listing Strategy
L. AI Explanation → M. Market Intelligence (Premium)
"""

import asyncio
import re
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.fees import MARKETPLACE_CALCULATORS, calculate_margin
from app.models.analysis import Analysis
from app.models.product import Product
from app.schemas.analysis import (
    AnalysisResponse,
    AnalysisSummary,
    BuyBox,
    ChannelBreakdown,
    CompetitionOut,
    CompsInfo,
    ConditionAnalysisOut,
    ConfidenceOut,
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
    SellerPremiumOut,
    TitleRiskOut,
    TrendOut,
    VelocityOut,
)
from app.core.brands import detect_brand
from app.services.engines.ai_explanation import generate_explanation
from app.services.engines.market_intelligence import compute_market_intelligence
from app.services.engines.comp_cleaner import clean_comps
from app.services.engines.comp_relevance import filter_comps_by_relevance
from app.services.engines.product_categorizer import categorize_product
from app.services.engines.title_enricher import enrich_listings
from app.services.engines.competition_engine import compute_competition
from app.services.engines.confidence_engine import compute_confidence
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
) -> _PipelineResult:
    """Ejecuta los motores A-K + decisión sobre un set de comps.

    Es síncrono — todos los motores son funciones puras sin I/O.
    """
    # Motor A: Limpiar comps
    cleaned = clean_comps(raw_comps, keyword=keyword, condition=condition, product_type=product_type)

    # Motor B: Precios recomendados
    pricing = compute_pricing(cleaned)

    # Motor C: Profit
    profit_market = compute_profit(
        pricing.market_list, cost_price, marketplace_name,
        shipping_cost, packaging_cost, prep_cost, promo_cost, return_reserve_pct,
    )
    profit_quick = compute_profit(
        pricing.quick_list, cost_price, marketplace_name,
        shipping_cost, packaging_cost, prep_cost, promo_cost, return_reserve_pct,
    )

    # Motor D: Max buy price
    max_buy = compute_max_buy(profit_market, target_profit, target_roi)

    # Motor E: Velocity
    velocity = compute_velocity(cleaned)

    # Motor F: Risk
    risk = compute_risk(cleaned, raw_comps)

    # Title Risk
    title_risk = compute_title_risk(cleaned, keyword=keyword)

    # Motor H: Seller Premium
    seller = compute_seller_premium(cleaned)

    # Motor I: Competition
    competition = compute_competition(cleaned)

    # Motor J: Trend (antes de confidence para pasar burstiness)
    trend = compute_trend(cleaned)

    # Motor G: Confidence (con burstiness del trend)
    confidence = compute_confidence(
        cleaned, raw_comps, enriched, title_risk.risk_score,
        burstiness=trend.burstiness,
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
        subset_profit = compute_profit(
            cleaned.condition_subset_median, cost_price, marketplace_name,
            shipping_cost, packaging_cost, prep_cost, promo_cost, return_reserve_pct,
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

    # Comps info
    has_valid_comps = cleaned.clean_total > 0 and pricing.market_list > 0
    comps_info, _ = _build_comps_info(cleaned, source=f"{marketplace_name}_cleaned")
    estimated_sale = pricing.market_list if has_valid_comps else None

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
    )


def _pipeline_to_marketplace_analysis(p: _PipelineResult) -> MarketplaceAnalysis:
    """Convierte un _PipelineResult a un MarketplaceAnalysis schema."""
    v = p.has_valid_comps
    return MarketplaceAnalysis(
        marketplace=p.marketplace_name,
        estimated_sale_price=p.estimated_sale,
        net_profit=p.profit_market.profit if v else None,
        roi_pct=round(p.profit_market.roi * 100, 2) if v else None,
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
        competition=CompetitionOut(**asdict(p.competition)) if v else None,
        trend=TrendOut(**asdict(p.trend)) if v else None,
        listing_strategy=ListingStrategyOut(**asdict(p.listing)) if v else None,
        title_risk=TitleRiskOut(**asdict(p.title_risk)),
        condition_analysis=p.condition_analysis,
        warnings=p.warnings,
    )


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
                    f"(median ${cleaned.median_price:.2f})."
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
                    f"Cannot estimate '{cleaned.requested_condition}' market value."
                )
            if recommendation == "buy":
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
# Función principal: run_analysis
# ---------------------------------------------------------------------------

async def run_analysis(
    db: AsyncSession,
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
) -> AnalysisResponse:
    from app.core.llm import reset_gemini
    reset_gemini()

    # 0. Si solo hay barcode, intentar UPC lookup para obtener keyword
    upc_info: dict | None = None
    if barcode and not keyword:
        upc_info = await lookup_upc(barcode)
        if upc_info and upc_info.get("title"):
            keyword = upc_info["title"]
            logger.info("UPC lookup: %s → '%s'", barcode, keyword)

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
    if product_type:
        # Override manual del usuario — usar directo
        logger.info("product_type manual: '%s'", product_type)
    elif search_keyword:
        category_result = await categorize_product(search_keyword)
        if category_result:
            product_type = category_result.product_type
            logger.info(
                "Categorizado: '%s' → product_type='%s' (confidence=%.2f)",
                search_keyword, product_type, category_result.confidence,
            )

    # -----------------------------------------------------------------------
    # 1. Fetch de comps: eBay + Amazon en PARALELO
    # -----------------------------------------------------------------------
    ebay = _get_ebay_client()
    ebay_coro = ebay.get_sold_comps(
        barcode=barcode, keyword=search_keyword, days=30, limit=50,
        condition=condition,
    )

    amazon_raw: CompsResult | None = None
    if settings.keepa_api_key:
        amazon = _get_amazon_client()
        amazon_coro = amazon.get_sold_comps(
            barcode=barcode, keyword=search_keyword, days=30, limit=50,
            product_type=product_type,
        )
        results = await asyncio.gather(ebay_coro, amazon_coro, return_exceptions=True)
        ebay_raw = results[0] if not isinstance(results[0], Exception) else CompsResult(listings=[], total_sold=0, median_price=0.0, source="ebay_sold")
        if isinstance(results[0], Exception):
            logger.warning("eBay fetch failed, continuing with empty comps: %s", results[0])
        if isinstance(results[1], Exception):
            logger.warning("Amazon fetch failed, continuing with eBay only: %s", results[1])
        else:
            amazon_raw = results[1]
    else:
        ebay_raw = await ebay_coro

    # 1b. Fallback eBay: si barcode no devolvió, reintentar con keyword
    if not ebay_raw.listings and barcode and search_keyword and search_keyword != barcode:
        logger.info("eBay barcode sin resultados, reintentando con keyword='%s'", search_keyword)
        try:
            ebay_raw = await ebay.get_sold_comps(
                keyword=search_keyword, days=30, limit=50, condition=condition,
            )
        except Exception as e:
            logger.warning("eBay keyword fallback failed: %s", e)

    # -----------------------------------------------------------------------
    # 2. Enriquecer títulos eBay con LLM (Amazon/Keepa ya tiene datos struct.)
    # -----------------------------------------------------------------------
    ebay_enriched = False
    if ebay_raw.listings:
        ebay_raw = await enrich_listings(ebay_raw, keyword=search_keyword or barcode)
        ebay_enriched = True

    # -----------------------------------------------------------------------
    # 2b. LLM relevance filter (después de enrich, antes de pipeline)
    # -----------------------------------------------------------------------
    if ebay_raw.listings and search_keyword:
        ebay_raw = await filter_comps_by_relevance(ebay_raw, search_keyword)
    if amazon_raw and amazon_raw.listings and search_keyword:
        amazon_raw = await filter_comps_by_relevance(amazon_raw, search_keyword)

    # -----------------------------------------------------------------------
    # 3. Ejecutar pipeline de motores en AMBOS marketplaces
    # -----------------------------------------------------------------------
    kw = search_keyword or barcode or ""
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
        ebay_raw, marketplace_name="ebay", enriched=ebay_enriched, **pipeline_kwargs,
    )

    # -----------------------------------------------------------------------
    # 3b. Re-fetch eBay si pocos comps limpios (buscar más páginas)
    # -----------------------------------------------------------------------
    _MIN_CLEAN_COMPS = 15
    _REFETCH_LIMIT = 150  # 150 items → scraper pide página 1 completa (240)
    if (
        ebay_pipeline.cleaned.clean_total < _MIN_CLEAN_COMPS
        and search_keyword
        and ebay_pipeline.cleaned.clean_total > 0  # al menos algo encontró
    ):
        logger.info(
            "Solo %d comps limpios eBay (<%d), re-fetching con limit=%d",
            ebay_pipeline.cleaned.clean_total, _MIN_CLEAN_COMPS, _REFETCH_LIMIT,
        )
        try:
            ebay_raw2 = await ebay.get_sold_comps(
                barcode=barcode, keyword=search_keyword, days=30,
                limit=_REFETCH_LIMIT, condition=condition,
            )
            if ebay_raw2.listings:
                ebay_raw2 = await enrich_listings(
                    ebay_raw2, keyword=search_keyword or barcode,
                )
                if search_keyword:
                    ebay_raw2 = await filter_comps_by_relevance(
                        ebay_raw2, search_keyword,
                    )
                ebay_pipeline2 = _run_pipeline(
                    ebay_raw2, marketplace_name="ebay",
                    enriched=True, **pipeline_kwargs,
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
            amazon_raw, marketplace_name="amazon_fba", enriched=False, **pipeline_kwargs,
        )

    # -----------------------------------------------------------------------
    # 4. Seleccionar pipeline primario (el del marketplace solicitado)
    # -----------------------------------------------------------------------
    if marketplace == "amazon_fba" and amazon_pipeline and amazon_pipeline.has_valid_comps:
        primary = amazon_pipeline
    else:
        primary = ebay_pipeline

    # -----------------------------------------------------------------------
    # 5. Determinar best_marketplace comparando profit real de cada pipeline
    # -----------------------------------------------------------------------
    candidates = [ebay_pipeline]
    if amazon_pipeline:
        candidates.append(amazon_pipeline)
    valid_candidates = [c for c in candidates if c.has_valid_comps]
    if valid_candidates:
        best_by_profit = max(valid_candidates, key=lambda c: c.profit_market.profit)
        best_by_opportunity = max(valid_candidates, key=lambda c: c.opportunity)

        # Si el mismo marketplace gana en ambas métricas, es claro
        if best_by_profit.marketplace_name == best_by_opportunity.marketplace_name:
            best_marketplace = best_by_profit.marketplace_name
            best_marketplace_reason = "best_profit"
        else:
            # Diferente ganador: priorizar profit (es lo que importa al seller)
            best_marketplace = best_by_profit.marketplace_name
            best_marketplace_reason = "best_profit"
    else:
        best_marketplace = primary.marketplace_name
        best_marketplace_reason = "only_available"

    # -----------------------------------------------------------------------
    # 6. Motor L: AI Explanation (con datos de AMBOS marketplaces)
    # -----------------------------------------------------------------------
    comparison_text = _build_comparison_text(ebay_pipeline, amazon_pipeline)

    explanation_coro = generate_explanation(
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
        comparison_text=comparison_text,
    )

    # Motor M: Market Intelligence (premium)
    if mode == "premium" and primary.cleaned.clean_total > 0:
        intel_coro = compute_market_intelligence(
            keyword=kw,
            marketplace=marketplace,
            cleaned_total=primary.cleaned.clean_total,
            median_price=primary.cleaned.median_price,
            min_price=primary.cleaned.min_price,
            max_price=primary.cleaned.max_price,
            sales_per_day=primary.cleaned.sales_per_day,
            demand_trend=primary.trend.demand_trend,
            price_trend=primary.trend.price_trend,
        )
        ai_explanation, market_intel = await asyncio.gather(
            explanation_coro, intel_coro
        )
    else:
        ai_explanation = await explanation_coro
        market_intel = None

    # -----------------------------------------------------------------------
    # 7. Ajustes post-intelligence en el pipeline primario
    # -----------------------------------------------------------------------
    risk = primary.risk
    opportunity = primary.opportunity
    recommendation = primary.recommendation
    warnings = list(primary.warnings)

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

    # -----------------------------------------------------------------------
    # 8. Producto + persistencia
    # -----------------------------------------------------------------------
    try:
        product = await _find_or_create_product(db, barcode, keyword, primary.raw_comps, upc_info)
        # B16: Enrich image_url from Amazon if missing
        if product and not product.image_url and amazon_raw and amazon_raw.image_url:
            product.image_url = amazon_raw.image_url
    except Exception as e:
        logger.warning("DB unavailable, skipping persistence: %s", e)
        product = None

    has_valid_comps = primary.has_valid_comps

    if has_valid_comps:
        estimated_sale = primary.pricing.market_list
        own_data_markets = {"ebay"}
        if amazon_pipeline and amazon_pipeline.has_valid_comps:
            own_data_markets.add("amazon_fba")
        channels = _calculate_all_channels(
            cost_price, estimated_sale,
            shipping_cost=shipping_cost, packaging_cost=packaging_cost,
            prep_cost=prep_cost, promo_cost=promo_cost,
            return_reserve_pct=return_reserve_pct,
            has_own_data=own_data_markets,
        )
        # Override amazon_fba channel con datos reales si tenemos pipeline Amazon
        if channels and amazon_pipeline and amazon_pipeline.has_valid_comps:
            amz_sale = amazon_pipeline.pricing.market_list
            for i, ch in enumerate(channels):
                if ch.marketplace == "amazon_fba":
                    fees = MARKETPLACE_CALCULATORS["amazon_fba"](Decimal(str(amz_sale)))
                    gross = fees["net_proceeds"]
                    net = gross - shipping_cost - packaging_cost - promo_cost
                    ret_reserve = amz_sale * return_reserve_pct
                    profit = net - ret_reserve - cost_price - prep_cost
                    invested = cost_price + prep_cost
                    channels[i] = ChannelBreakdown(
                        marketplace="amazon_fba",
                        estimated_sale_price=amz_sale,
                        net_proceeds=round(net - ret_reserve, 2),
                        profit=round(profit, 2),
                        roi_pct=round(profit / invested * 100, 2) if invested > 0 else 0,
                        margin_pct=round(profit / amz_sale * 100, 2) if amz_sale > 0 else 0,
                        is_estimated=False,
                    )
                    break
            channels.sort(key=lambda c: c.profit, reverse=True)
            _assign_channel_labels(channels)
    else:
        estimated_sale = None
        channels = None

    # Summary
    headroom = (primary.max_buy.recommended_max - cost_price) if has_valid_comps else 0.0
    signal_map = {"buy": "positive", "buy_small": "positive", "watch": "caution", "pass": "negative"}
    signal = signal_map.get(recommendation, "neutral")
    summary = AnalysisSummary(
        recommendation=recommendation,
        signal=signal,
        buy_box=BuyBox(
            recommended_max_buy=primary.max_buy.recommended_max if has_valid_comps else 0.0,
            your_cost=cost_price,
            headroom=round(headroom, 2),
        ),
        sale_plan=SalePlan(
            recommended_list_price=primary.pricing.market_list if has_valid_comps else 0.0,
            quick_sale_price=primary.pricing.quick_list if has_valid_comps else 0.0,
            stretch_price=(primary.pricing.stretch_list if primary.pricing.stretch_allowed else None) if has_valid_comps else None,
        ),
        returns=Returns(
            profit=primary.profit_market.profit if has_valid_comps else 0.0,
            roi_pct=round(primary.profit_market.roi * 100, 2) if has_valid_comps else 0.0,
            margin_pct=round(primary.profit_market.margin * 100, 2) if has_valid_comps else 0.0,
        ),
        risk=risk.category,
        confidence=primary.confidence.category,
        warnings=warnings,
    )

    # Engines data blob
    engines_data = {
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
        "market_intelligence": asdict(market_intel) if market_intel else None,
        "cleaned_comps": {
            "raw_total": primary.cleaned.raw_total,
            "clean_total": primary.cleaned.clean_total,
            "outliers_removed": primary.cleaned.outliers_removed,
            "relevance_filtered": primary.cleaned.relevance_filtered,
            "cv": primary.cleaned.cv,
        },
    }

    top_net_profit = primary.profit_market.profit if has_valid_comps else None
    top_margin = round(primary.profit_market.margin * 100, 2) if has_valid_comps else None
    top_roi = round(primary.profit_market.roi * 100, 2) if has_valid_comps else None

    # Persistir
    analysis_id = None
    if product is not None:
        try:
            analysis = Analysis(
                product_id=product.id,
                cost_price=cost_price,
                marketplace=marketplace,
                estimated_sale_price=estimated_sale,
                net_profit=top_net_profit,
                margin_pct=top_margin,
                roi_pct=top_roi,
                flip_score=opportunity if has_valid_comps else None,
                risk_score=risk.score if has_valid_comps else None,
                velocity_score=primary.velocity.score if has_valid_comps else None,
                confidence_score=primary.confidence.score,
                opportunity_score=opportunity,
                recommendation=recommendation,
                channels=[c.model_dump() for c in channels] if channels else None,
                engines_data=engines_data,
                ai_explanation=ai_explanation,
                shipping_cost=shipping_cost,
                prep_cost=prep_cost,
            )
            db.add(analysis)
            await db.commit()
            await db.refresh(analysis)
            analysis_id = analysis.id
        except Exception as e:
            logger.warning("DB persist failed, returning result without ID: %s", e)

    # -----------------------------------------------------------------------
    # 9. Construir MarketplaceAnalysis para cada marketplace
    # -----------------------------------------------------------------------
    ebay_analysis = _pipeline_to_marketplace_analysis(ebay_pipeline)
    amazon_analysis = _pipeline_to_marketplace_analysis(amazon_pipeline) if amazon_pipeline else None

    # Product summary
    product_title = search_keyword or (barcode or "Unknown")
    if product is not None:
        product_summary = ProductSummary(
            id=product.id,
            barcode=product.barcode,
            title=product.title,
            brand=product.brand,
            image_url=product.image_url,
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

    return AnalysisResponse(
        id=analysis_id,
        product=product_summary,
        cost_price=cost_price,
        marketplace=marketplace,
        estimated_sale_price=estimated_sale,
        net_profit=top_net_profit,
        margin_pct=top_margin,
        roi_pct=top_roi,
        flip_score=opportunity if has_valid_comps else None,
        risk_score=risk.score if has_valid_comps else None,
        velocity_score=primary.velocity.score if has_valid_comps else None,
        recommendation=recommendation,
        channels=channels,
        summary=summary,
        ai_explanation=ai_explanation if has_valid_comps else None,
        market_intelligence=MarketIntelligenceOut(
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
        ) if market_intel and has_valid_comps else None,
        detected_category=category_result.category if category_result else None,
        category_confidence=category_result.confidence if category_result else None,
        ebay_analysis=ebay_analysis,
        amazon_analysis=amazon_analysis,
        best_marketplace=best_marketplace,
        best_marketplace_reason=best_marketplace_reason,
        created_at=analysis.created_at if analysis_id else datetime.now(timezone.utc),
    )


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
        net = gross_after_fees - shipping_cost - packaging_cost - promo_cost
        return_reserve = sale_price * return_reserve_pct
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
    """Asigna labels BEST PROFIT y BEST ROI a los canales."""
    if not channels:
        return
    # Reset labels
    for ch in channels:
        ch.label = None
    # BEST PROFIT: ya está sorted por profit desc → channels[0]
    channels[0].label = "BEST PROFIT"
    # BEST ROI: si es diferente al de mejor profit
    best_roi_idx = max(range(len(channels)), key=lambda i: channels[i].roi_pct)
    if best_roi_idx != 0:
        channels[best_roi_idx].label = "BEST ROI"
