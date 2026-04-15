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
from app.services.engines.ai_explanation import generate_explanation
from app.services.engines.market_intelligence import compute_market_intelligence
from app.services.engines.comp_cleaner import clean_comps
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


def _clean_search_keyword(keyword: str) -> str:
    """Elimina frases de condición del keyword para evitar contaminar la búsqueda."""
    cleaned = _CONDITION_PHRASES.sub("", keyword)
    # Colapsar espacios, comas sueltas y limpiar
    cleaned = re.sub(r"[,\s]+", " ", cleaned).strip().strip(",").strip()
    return cleaned if cleaned else keyword


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
) -> _PipelineResult:
    """Ejecuta los motores A-K + decisión sobre un set de comps.

    Es síncrono — todos los motores son funciones puras sin I/O.
    """
    # Motor A: Limpiar comps
    cleaned = clean_comps(raw_comps, keyword=keyword, condition=condition)

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

    # Condition Analysis
    condition_analysis = _build_condition_analysis(cleaned)

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
    )

    # Warning: mercado dominado por un seller
    if competition.dominant_seller_share > 0.40:
        pct = round(competition.dominant_seller_share * 100)
        warnings.append(
            f"Un seller controla el {pct}% del mercado. "
            "Competir por el Buy Box será difícil."
        )

    # Warning de demand spike temporal
    if trend.demand_trend > 80 and trend.burstiness > 0.25:
        warnings.append(
            f"Spike de demanda detectado ({trend.demand_trend:+.0f}%). "
            "Ventas concentradas en pocos días — puede ser temporal. "
            "Monitorear antes de comprar grandes cantidades."
        )

    # Comps info
    has_valid_comps = cleaned.clean_total > 0 and pricing.market_list > 0
    comps_info, _ = _build_comps_info(cleaned, source=f"{marketplace_name}_cleaned")
    estimated_sale = pricing.market_list if has_valid_comps else None

    # Gate: sin comps → pass
    if not has_valid_comps and recommendation != "pass":
        recommendation = "pass"
        if not any("comps" in w.lower() or "condición" in w.lower() for w in warnings):
            if condition != "any":
                warnings.append(
                    f"No hay suficientes comps en condición '{condition}'. "
                    "No se puede estimar precio de venta."
                )
            else:
                warnings.append(
                    "No hay comps válidos. No se puede estimar precio de venta."
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

    comp_score = 100 if competition.category == "sano" else (60 if competition.category == "moderado" else 30)
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


def _build_condition_analysis(cleaned: CleanedComps) -> ConditionAnalysisOut:
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
) -> tuple[str, list[str]]:
    """Validador pre-BUY. Puede degradar 'buy' a 'buy_small' o 'watch' con warnings."""
    warnings: list[str] = []

    if max_buy is not None and max_buy.recommended_max > 0 and cost_price > max_buy.recommended_max:
        overpay = cost_price - max_buy.recommended_max
        warnings.append(
            f"Tu costo (${cost_price:.2f}) excede el máximo recomendado "
            f"(${max_buy.recommended_max:.2f}) por ${overpay:.2f}. "
            f"A ${max_buy.recommended_max:.2f} o menos, sería rentable."
        )
        if recommendation in ("buy", "buy_small"):
            recommendation = "watch"

    if confidence.score < 50:
        warnings.append(
            f"Confianza del análisis baja ({confidence.score}/100). "
            "Insuficientes datos para recomendar compra."
        )
        if recommendation in ("buy", "buy_small"):
            recommendation = "watch"

    if cleaned.requested_condition != "any":
        if cleaned.condition_filtered == 0 and cleaned.condition_match_rate < 0.5:
            warnings.append(
                f"Pocos comps en condición '{cleaned.requested_condition}'. "
                f"Solo {cleaned.condition_match_rate:.0%} coinciden. "
                "Precios pueden no ser representativos para esa condición."
            )
            if recommendation == "buy":
                recommendation = "watch"
        elif cleaned.condition_match_rate < 0.7:
            warnings.append(
                f"Comps mezclados: {cleaned.condition_match_rate:.0%} coinciden "
                f"con condición '{cleaned.requested_condition}'. "
                "Considerar revisar manualmente."
            )

    if title_risk.manual_review_required:
        warnings.append(
            f"Títulos ambiguos detectados ({title_risk.flagged_pct:.0%} de comps). "
            f"Flags: {', '.join(title_risk.top_flags)}. Revisar manualmente."
        )
        if recommendation == "buy" and title_risk.risk_score > 0.4:
            recommendation = "buy_small"

    if cleaned.clean_total < 5:
        warnings.append(
            f"Solo {cleaned.clean_total} comps después de limpieza. "
            "Resultados pueden no ser representativos."
        )
        if recommendation == "buy" and cleaned.clean_total < 3:
            recommendation = "buy_small"
        elif recommendation == "buy":
            recommendation = "buy_small"

    if distribution_shape == "bimodal":
        warnings.append(
            "Distribución de precios bimodal detectada. "
            "Hay dos grupos de precio distintos — la mediana puede no ser representativa."
        )

    if profit_market.profit <= 0 and recommendation in ("buy", "buy_small"):
        recommendation = "pass"
        warnings.append("Profit negativo. No se recomienda comprar.")

    if cleaned.cv > 0.50:
        warnings.append(
            f"Alta dispersión de precios (CV={cleaned.cv:.2f}). "
            "El mercado es volátil."
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
    search_keyword = _clean_search_keyword(keyword) if keyword else keyword
    if search_keyword and search_keyword != keyword:
        logger.info("Keyword limpiado: '%s' → '%s'", keyword, search_keyword)

    # -----------------------------------------------------------------------
    # 1. Fetch de comps: eBay + Amazon en PARALELO
    # -----------------------------------------------------------------------
    ebay = _get_ebay_client()
    ebay_coro = ebay.get_sold_comps(
        barcode=barcode, keyword=search_keyword, days=30, limit=50,
        condition="any",
    )

    amazon_raw: CompsResult | None = None
    if settings.keepa_api_key:
        amazon = _get_amazon_client()
        amazon_coro = amazon.get_sold_comps(
            barcode=barcode, keyword=search_keyword, days=30, limit=50,
        )
        ebay_raw, amazon_raw = await asyncio.gather(ebay_coro, amazon_coro)
    else:
        ebay_raw = await ebay_coro

    # 1b. Fallback eBay: si barcode no devolvió, reintentar con keyword
    if not ebay_raw.listings and barcode and search_keyword and search_keyword != barcode:
        logger.info("eBay barcode sin resultados, reintentando con keyword='%s'", search_keyword)
        ebay_raw = await ebay.get_sold_comps(
            keyword=search_keyword, days=30, limit=50, condition="any",
        )

    # -----------------------------------------------------------------------
    # 2. Enriquecer títulos eBay con LLM (Amazon/Keepa ya tiene datos struct.)
    # -----------------------------------------------------------------------
    ebay_enriched = False
    if ebay_raw.listings:
        ebay_raw = await enrich_listings(ebay_raw, keyword=keyword or barcode)
        ebay_enriched = True

    # -----------------------------------------------------------------------
    # 3. Ejecutar pipeline de motores en AMBOS marketplaces
    # -----------------------------------------------------------------------
    kw = keyword or barcode or ""
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
    )

    ebay_pipeline = _run_pipeline(
        ebay_raw, marketplace_name="ebay", enriched=ebay_enriched, **pipeline_kwargs,
    )

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
                risk = type(risk)(score=risk.score, category="alto", factors=risk.factors)
            elif risk.score < 60:
                risk = type(risk)(score=risk.score, category="medio", factors=risk.factors)

        if market_intel.product_lifecycle == "end_of_life":
            risk = type(risk)(
                score=max(0, risk.score - 10),
                category=risk.category,
                factors=risk.factors,
            )
            if risk.score < 30:
                risk = type(risk)(score=risk.score, category="alto", factors=risk.factors)
            elif risk.score < 60:
                risk = type(risk)(score=risk.score, category="medio", factors=risk.factors)

        seasonal_adj = round(market_intel.seasonal_factor * 10)
        opportunity = max(0, min(100, opportunity + seasonal_adj))

        for ev in market_intel.market_events:
            if ev.impact == "negativo" and ev.relevance == "alta":
                warnings.append(f"Evento de mercado: {ev.event}")

        if (
            market_intel.timing_recommendation == "wait"
            and recommendation in ("buy", "buy_small")
            and market_intel.confidence == "alta"
        ):
            recommendation = "watch"
            warnings.append(
                "Market intelligence recomienda esperar antes de comprar."
            )

    # -----------------------------------------------------------------------
    # 8. Producto + persistencia
    # -----------------------------------------------------------------------
    try:
        product = await _find_or_create_product(db, barcode, keyword, primary.raw_comps, upc_info)
    except Exception as e:
        logger.warning("DB unavailable, skipping persistence: %s", e)
        product = None

    has_valid_comps = primary.has_valid_comps

    if has_valid_comps:
        estimated_sale = primary.pricing.market_list
        channels = _calculate_all_channels(
            cost_price, estimated_sale,
            shipping_cost=shipping_cost, packaging_cost=packaging_cost,
            prep_cost=prep_cost, promo_cost=promo_cost,
            return_reserve_pct=return_reserve_pct,
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
                    )
                    break
            channels.sort(key=lambda c: c.profit, reverse=True)
    else:
        estimated_sale = None
        channels = None

    # Summary
    headroom = (primary.max_buy.recommended_max - cost_price) if has_valid_comps else 0.0
    summary = AnalysisSummary(
        recommendation=recommendation,
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
    product_title = keyword or (barcode or "Unknown")
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
            fallback_brand = _detect_brand(product_title)
        product_summary = ProductSummary(
            id=0,
            barcode=barcode,
            title=product_title,
            brand=fallback_brand,
            image_url=upc_info.get("image_url") if upc_info else None,
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
        f"\n\nCOMPARACIÓN ENTRE MARKETPLACES:\n"
        f"\n"
        f"eBay:\n"
        f"- Mediana: ${ebay_median:.2f} ({ebay.cleaned.clean_total} comps)\n"
        f"- Profit: ${ebay.profit_market.profit:.2f} (ROI: {ebay.profit_market.roi:.1%})\n"
        f"- Velocidad: {ebay.velocity.score}/100 ({ebay.velocity.category})\n"
        f"- Riesgo: {ebay.risk.score}/100 ({ebay.risk.category})\n"
        f"- Tendencia demanda: {ebay.trend.demand_trend:+.1f}%\n"
        f"- Opportunity Score: {ebay.opportunity}/100 → {ebay.recommendation}\n"
        f"\n"
        f"Amazon:\n"
        f"- Mediana: ${amz_median:.2f} ({amazon.cleaned.clean_total} comps)\n"
        f"- Profit: ${amazon.profit_market.profit:.2f} (ROI: {amazon.profit_market.roi:.1%})\n"
        f"- Velocidad: {amazon.velocity.score}/100 ({amazon.velocity.category})\n"
        f"- Riesgo: {amazon.risk.score}/100 ({amazon.risk.category})\n"
        f"- Tendencia demanda: {amazon.trend.demand_trend:+.1f}%\n"
        f"- Opportunity Score: {amazon.opportunity}/100 → {amazon.recommendation}\n"
        f"\n"
        f"Delta de precios: Amazon es {delta_pct:+.1f}% vs eBay\n"
        f"\n"
        f"Analiza las oportunidades y tendencias en AMBOS marketplaces. "
        f"Recomienda el mejor canal para este producto y por qué."
    )


def _detect_distribution_shape(prices: list[float]) -> str:
    """Detecta forma de la distribución de precios."""
    if len(prices) < 5:
        return "insufficient"

    sorted_prices = sorted(prices)
    total_range = sorted_prices[-1] - sorted_prices[0]

    if total_range == 0:
        return "normal"

    gaps = [sorted_prices[i + 1] - sorted_prices[i] for i in range(len(sorted_prices) - 1)]
    sorted_gaps = sorted(gaps)
    median_gap = sorted_gaps[len(gaps) // 2]
    max_gap = max(gaps)

    is_outlier = max_gap > median_gap * 2 if median_gap > 0 else max_gap > total_range * 0.3
    is_significant = max_gap > total_range * 0.20

    if is_outlier and is_significant:
        return "bimodal"

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


_KNOWN_BRANDS = [
    "Nike", "Adidas", "Apple", "Samsung", "Sony", "Nintendo", "Microsoft", "Google",
    "LG", "Bose", "JBL", "Canon", "Nikon", "Dyson", "Lego", "Funko",
    "Jordan", "New Balance", "Puma", "Asics", "Reebok", "Converse", "Vans",
    "Under Armour", "North Face", "Patagonia", "Columbia",
    "Dell", "HP", "Lenovo", "Asus", "Acer", "Razer", "Logitech", "Corsair",
    "KitchenAid", "Instant Pot", "Vitamix", "Cuisinart", "Ninja",
    "Beats", "AirPods", "Oakley", "Ray-Ban", "Crocs", "Birkenstock",
    "Pokemon", "Hoka", "On Running", "Brooks", "Saucony", "Salomon",
]
_BRAND_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(b) for b in _KNOWN_BRANDS) + r")\b",
    re.IGNORECASE,
)


def _detect_brand(text: str) -> str | None:
    """Detecta marca conocida en texto (keyword o título)."""
    if not text:
        return None
    match = _BRAND_PATTERN.search(text)
    if match:
        matched = match.group(1).lower()
        for brand in _KNOWN_BRANDS:
            if brand.lower() == matched:
                return brand
    return None


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
                product.brand = _detect_brand(product.title)
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
                product.brand = _detect_brand(keyword) or _detect_brand(product.title)
            return product

    title = keyword or barcode or "Producto sin título"
    if comps.listings:
        title = comps.listings[0].title or title
    if upc_info and upc_info.get("title"):
        title = upc_info["title"]

    brand = upc_info.get("brand") if upc_info else None
    if not brand:
        brand = _detect_brand(keyword or title)

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
) -> list[ChannelBreakdown]:
    """Calcula profit estimado en cada marketplace."""
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
            )
        )
    channels.sort(key=lambda c: c.profit, reverse=True)
    return channels
