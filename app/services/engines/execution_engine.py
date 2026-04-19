"""Execution Engine.

Estima si el reseller puede capturar la oportunidad, no solo si el mercado
existe. El objetivo es convertir riesgos operativos (Buy Box, fees genericas,
bimodalidad, tendencia negativa, muestra baja) en caps reales de decision.
"""

from dataclasses import dataclass, field

from app.services.marketplace.base import CleanedComps, CompsResult


_RECOMMENDATION_RANK = {
    "pass": 0,
    "watch": 1,
    "buy_small": 2,
    "buy": 3,
}


@dataclass
class ExecutionPenalty:
    code: str
    severity: str
    points: int
    message: str


@dataclass
class ExecutionResult:
    score: int
    category: str
    win_probability: float
    expected_profit: float
    max_recommendation: str
    quantity_guidance: str
    channel_role: str = "candidate"
    penalties: list[ExecutionPenalty] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def cap_recommendation(recommendation: str, cap: str) -> str:
    """Reduce recommendation si excede el cap permitido por execution risk."""
    if _RECOMMENDATION_RANK.get(recommendation, 0) <= _RECOMMENDATION_RANK.get(cap, 0):
        return recommendation
    return cap


def compute_execution(
    *,
    marketplace_name: str,
    profit_market,
    risk,
    confidence,
    competition,
    trend,
    cleaned: CleanedComps,
    raw_comps: CompsResult,
    distribution_shape: str,
    product_type: str | None = None,
) -> ExecutionResult:
    """Calcula execution score y probabilidad práctica de venta."""
    penalties: list[ExecutionPenalty] = []

    def add(code: str, severity: str, points: int, message: str) -> None:
        penalties.append(
            ExecutionPenalty(
                code=code,
                severity=severity,
                points=max(0, points),
                message=message,
            )
        )

    if cleaned.clean_total == 0 or profit_market.profit <= 0:
        add("no_profitable_execution", "critical", 100, "No profitable execution path with valid comps.")
        return ExecutionResult(
            score=0,
            category="blocked",
            win_probability=0.0,
            expected_profit=0.0,
            max_recommendation="pass",
            quantity_guidance="Do not buy",
            penalties=penalties,
            warnings=[p.message for p in penalties],
        )

    # Confidence y muestra: si la data no es confiable, no se compra profundo.
    if confidence.score < 50:
        add("low_confidence", "high", 22, f"Low confidence ({confidence.score}/100) makes execution uncertain.")
    elif confidence.score < 60:
        add("moderate_confidence", "medium", 10, f"Moderate confidence ({confidence.score}/100); test quantities first.")

    if cleaned.clean_total < 5:
        add("very_small_sample", "high", 24, f"Only {cleaned.clean_total} clean comps after filtering.")
    elif cleaned.clean_total < 10:
        add("small_sample", "medium", 12, f"Only {cleaned.clean_total} clean comps; execution confidence is limited.")

    # Seller concentration / Buy Box accessibility.
    dominant = competition.dominant_seller_share or 0.0
    if dominant >= 0.90:
        add("seller_dominance_extreme", "critical", 30, "One seller controls 90%+ of the market.")
    elif dominant >= 0.80:
        add("seller_dominance_high", "high", 22, f"One seller controls {dominant:.0%} of the market.")
    elif dominant >= 0.65:
        add("seller_dominance_elevated", "medium", 18, f"Top seller controls {dominant:.0%} of the market.")
    elif dominant >= 0.50:
        add("seller_dominance_moderate", "medium", 10, f"Top seller controls {dominant:.0%} of the market.")

    # Amazon/FBA-specific execution risk.
    if marketplace_name == "amazon_fba":
        add("generic_fba_fees", "medium", 8, "Amazon FBA fees are generic estimates.")
        if dominant >= 0.80:
            add("low_buy_box_access", "high", 8, "Buy Box access is likely difficult for new inventory.")

    # Price realism.
    if distribution_shape == "bimodal":
        add("bimodal_pricing", "medium", 10, "Bimodal pricing means the raw median may not be your executable price.")
    elif distribution_shape == "dispersed":
        add("dispersed_pricing", "medium", 10, "Prices are highly dispersed; positioning risk is elevated.")

    if cleaned.pricing_basis == "mixed_conditions":
        add("mixed_condition_pricing", "high", 22, "Pricing is based on mixed conditions, not a reliable primary estimate.")

    if cleaned.cv > 0.50:
        add("high_price_volatility", "medium", 10, f"High price dispersion (CV={cleaned.cv:.2f}).")

    # Demand trend y market stability.
    if trend.demand_trend < -50 and trend.confidence != "low":
        add("demand_declining_fast", "high", 18, f"Demand trend is sharply negative ({trend.demand_trend:+.1f}%).")
    elif trend.demand_trend < -30 and trend.confidence != "low":
        add("demand_declining", "medium", 12, f"Demand trend is negative ({trend.demand_trend:+.1f}%).")

    if risk.score < 50:
        add("market_stability_low", "medium", 12, f"Market stability is weak ({risk.score}/100).")
    elif risk.score < 65:
        add("market_stability_moderate", "low", 6, f"Market stability is moderate ({risk.score}/100).")

    # High-ticket/electronics need a larger safety buffer because defects,
    # shipping damage, returns and cash tie-up hurt execution.
    pt = (product_type or "").lower()
    high_ticket = profit_market.sale_price >= 300 or profit_market.gross_proceeds >= 300
    electronics_like = any(
        token in pt
        for token in ("console", "electronics", "phone", "laptop", "tablet", "camera", "gaming")
    )
    if high_ticket and (electronics_like or marketplace_name == "amazon_fba"):
        add("high_ticket_execution", "medium", 7, "High-ticket electronics require stronger execution margin and return buffer.")

    if raw_comps.fallback_used:
        add("fallback_source", "medium", 8, "Fallback marketplace data source was used.")
    if raw_comps.scrape_status in ("blocked", "partial"):
        add("source_limited", "medium", 10, f"Marketplace source status is '{raw_comps.scrape_status}'.")

    total_penalty = sum(p.points for p in penalties)
    score = max(0, min(100, round(100 - total_penalty)))

    # Probability is intentionally conservative for concentrated Amazon markets.
    win_probability = score / 100
    if marketplace_name == "amazon_fba" and dominant > 0.40:
        win_probability *= max(0.20, 1 - dominant * 0.55)
    elif dominant > 0.65:
        win_probability *= max(0.35, 1 - dominant * 0.35)
    win_probability = round(max(0.02, min(0.95, win_probability)), 2)

    expected_profit = round(profit_market.profit * win_probability, 2)

    if score >= 75:
        category = "strong"
        max_recommendation = "buy"
        quantity_guidance = "Standard buy"
    elif score >= 55:
        category = "moderate"
        max_recommendation = "buy_small"
        quantity_guidance = "Buy small"
    elif score >= 35:
        category = "difficult"
        max_recommendation = "buy_small"
        quantity_guidance = "Test 1-2 units"
    elif score >= 20:
        category = "very_difficult"
        max_recommendation = "watch"
        quantity_guidance = "Watch or test only with a confirmed exit"
    else:
        category = "blocked"
        max_recommendation = "pass"
        quantity_guidance = "Do not buy"

    if any(p.code in {"seller_dominance_high", "seller_dominance_extreme", "low_buy_box_access"} for p in penalties):
        if marketplace_name == "amazon_fba":
            max_recommendation = cap_recommendation(max_recommendation, "buy_small")
            quantity_guidance = "Test only"

    if any(p.code in {"low_confidence", "mixed_condition_pricing", "demand_declining_fast"} for p in penalties):
        max_recommendation = cap_recommendation(max_recommendation, "buy_small")

    warnings = [p.message for p in penalties if p.severity in ("high", "critical")]

    return ExecutionResult(
        score=score,
        category=category,
        win_probability=win_probability,
        expected_profit=expected_profit,
        max_recommendation=max_recommendation,
        quantity_guidance=quantity_guidance,
        penalties=penalties,
        warnings=warnings,
    )
