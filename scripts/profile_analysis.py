"""Profiler del pipeline de análisis.

Mide el tiempo de cada fase sin pasar por HTTP ni DB.
Uso: python -m scripts.profile_analysis
"""

import asyncio
import time
from contextlib import contextmanager

from app.services.marketplace.ebay import EbayClient, lookup_upc
from app.services.engines.title_enricher import enrich_listings
from app.services.engines.comp_cleaner import clean_comps
from app.services.engines.pricing_engine import compute_pricing
from app.services.engines.profit_engine import compute_profit
from app.services.engines.max_buy_price import compute_max_buy
from app.services.engines.velocity_engine import compute_velocity
from app.services.engines.risk_engine import compute_risk
from app.services.engines.title_risk import compute_title_risk
from app.services.engines.confidence_engine import compute_confidence
from app.services.engines.seller_premium import compute_seller_premium
from app.services.engines.competition_engine import compute_competition
from app.services.engines.trend_engine import compute_trend
from app.services.engines.listing_strategy import compute_listing_strategy
from app.services.engines.ai_explanation import generate_explanation
from app.services.analysis_service import (
    _compute_opportunity_score,
    _decide,
    _validate_buy,
    _build_condition_analysis,
    _build_comps_info,
    _detect_distribution_shape,
)


class Timer:
    def __init__(self):
        self.timings: list[tuple[str, float]] = []

    @contextmanager
    def measure(self, label: str):
        t0 = time.perf_counter()
        yield
        elapsed = time.perf_counter() - t0
        self.timings.append((label, elapsed))

    def report(self):
        total = sum(t for _, t in self.timings)
        print(f"\n{'='*60}")
        print(f"  PROFILING — Pipeline de Análisis")
        print(f"{'='*60}")
        print(f"{'Fase':<40} {'Tiempo':>8} {'%':>6}")
        print(f"{'-'*40} {'-'*8} {'-'*6}")
        for label, elapsed in self.timings:
            pct = (elapsed / total * 100) if total > 0 else 0
            ms = elapsed * 1000
            if ms >= 1000:
                time_str = f"{ms/1000:.2f}s"
            elif ms >= 1:
                time_str = f"{ms:.1f}ms"
            else:
                time_str = f"{ms*1000:.0f}µs"
            bar = "█" * int(pct / 2)
            print(f"  {label:<38} {time_str:>8} {pct:>5.1f}% {bar}")
        print(f"{'-'*40} {'-'*8} {'-'*6}")
        total_ms = total * 1000
        if total_ms >= 1000:
            total_str = f"{total_ms/1000:.2f}s"
        else:
            total_str = f"{total_ms:.1f}ms"
        print(f"  {'TOTAL':<38} {total_str:>8} 100.0%")
        print()


# --- Parámetros del request ---
PARAMS = {
    "keyword": "Nike Vomero 5",
    "barcode": None,
    "cost_price": 70,
    "marketplace": "ebay",
    "shipping_cost": 12,
    "packaging_cost": 0,
    "prep_cost": 0,
    "promo_cost": 0,
    "return_reserve_pct": 0.05,
    "target_profit": 25,
    "target_roi": 0.35,
    "condition": "new",
}


async def main():
    timer = Timer()
    p = PARAMS

    ebay = EbayClient()

    # Fase 1: Apify — obtener comps
    with timer.measure("Apify: get_sold_comps"):
        raw_comps = await ebay.get_sold_comps(
            barcode=p["barcode"], keyword=p["keyword"],
            days=30, limit=50, condition="any",
        )
    print(f"  → {raw_comps.total_sold} comps crudos")

    # Fase 2: LLM Title Enrichment
    enriched = False
    if raw_comps.listings:
        with timer.measure("LLM: enrich_listings"):
            raw_comps = await enrich_listings(raw_comps, keyword=p["keyword"])
            enriched = True

    # Fase 3: Motor A — Comp Cleaner
    with timer.measure("Motor A: clean_comps"):
        cleaned = clean_comps(raw_comps, keyword=p["keyword"], condition=p["condition"])
    print(f"  → {cleaned.clean_total} comps limpios (de {cleaned.raw_total})")

    # Fase 4: Motor B — Pricing
    with timer.measure("Motor B: pricing"):
        pricing = compute_pricing(cleaned)

    # Fase 5: Motor C — Profit
    with timer.measure("Motor C: profit"):
        profit_market = compute_profit(
            pricing.market_list, p["cost_price"], p["marketplace"],
            p["shipping_cost"], p["packaging_cost"], p["prep_cost"],
            p["promo_cost"], p["return_reserve_pct"],
        )
        profit_quick = compute_profit(
            pricing.quick_list, p["cost_price"], p["marketplace"],
            p["shipping_cost"], p["packaging_cost"], p["prep_cost"],
            p["promo_cost"], p["return_reserve_pct"],
        )

    # Fase 6: Motor D — Max Buy
    with timer.measure("Motor D: max_buy"):
        max_buy = compute_max_buy(profit_market, p["target_profit"], p["target_roi"])

    # Fase 7: Motor E — Velocity
    with timer.measure("Motor E: velocity"):
        velocity = compute_velocity(cleaned)

    # Fase 8: Motor F — Risk
    with timer.measure("Motor F: risk"):
        risk = compute_risk(cleaned, raw_comps)

    # Fase 9: Title Risk
    with timer.measure("Motor L2: title_risk"):
        title_risk = compute_title_risk(cleaned, keyword=p["keyword"])

    # Fase 10: Motor G — Confidence
    with timer.measure("Motor G: confidence"):
        confidence = compute_confidence(cleaned, raw_comps, enriched, title_risk.risk_score)

    # Fase 11: Motor H — Seller Premium
    with timer.measure("Motor H: seller_premium"):
        seller = compute_seller_premium(cleaned)

    # Fase 12: Motor I — Competition
    with timer.measure("Motor I: competition"):
        competition = compute_competition(cleaned)

    # Fase 13: Motor J — Trend
    with timer.measure("Motor J: trend"):
        trend = compute_trend(cleaned)

    # Fase 14: Motor K — Listing Strategy
    with timer.measure("Motor K: listing_strategy"):
        listing = compute_listing_strategy(cleaned, velocity, risk, quick_price=pricing.quick_list)

    # Fase 15: Condition Analysis
    with timer.measure("Condition analysis"):
        condition_analysis = _build_condition_analysis(cleaned)

    # Fase 16: Opportunity Score + Decision
    with timer.measure("Opportunity + Decision + Validate"):
        clean_prices = sorted(l.total_price for l in cleaned.listings if l.total_price)
        dist_shape = _detect_distribution_shape(clean_prices) if cleaned.clean_total > 0 else "unknown"

        opportunity = _compute_opportunity_score(
            profit_market, velocity, risk, confidence, competition, trend,
        ) if cleaned.clean_total > 0 else 0

        recommendation = _decide(opportunity, profit_market, risk, confidence)
        recommendation, warnings = _validate_buy(
            recommendation, confidence, title_risk, cleaned, profit_market,
            max_buy=max_buy, cost_price=p["cost_price"],
            distribution_shape=dist_shape,
        )

    # Fase 17: AI Explanation
    with timer.measure("Motor L: ai_explanation"):
        ai_explanation = await generate_explanation(
            keyword=p["keyword"],
            cost_price=p["cost_price"],
            marketplace=p["marketplace"],
            pricing=pricing,
            profit_market=profit_market,
            max_buy=max_buy,
            velocity=velocity,
            risk=risk,
            confidence=confidence,
            competition=competition,
            trend=trend,
            listing=listing,
            opportunity_score=opportunity,
            recommendation=recommendation,
            cleaned_total=cleaned.clean_total,
            raw_total=cleaned.raw_total,
        )

    # Fase 18: Build comps info
    with timer.measure("Build comps info"):
        comps_info, _ = _build_comps_info(cleaned)

    # Report
    timer.report()

    # Resumen del resultado
    print(f"  Resultado: {recommendation}")
    print(f"  Flip score: {opportunity}")
    print(f"  Precio mercado: ${pricing.market_list:.2f}")
    print(f"  Profit: ${profit_market.profit:.2f}")
    print(f"  ROI: {profit_market.roi*100:.1f}%")
    print(f"  Velocity: {velocity.score}/100 ({velocity.category})")
    print(f"  Est. días venta: {velocity.estimated_days_to_sell}")
    print(f"  Risk: {risk.score}/100 ({risk.category})")
    print(f"  Confidence: {confidence.score}/100 ({confidence.category})")
    print(f"  Distribution: {dist_shape}")
    print(f"  Listing: {listing.recommended_format}")
    if listing.suggested_min_offer:
        print(f"  Min offer: ${listing.suggested_min_offer:.2f}")
    if warnings:
        print(f"  Warnings: {warnings}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
