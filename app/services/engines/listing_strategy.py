"""Motor K — Listing Strategy.

Recomienda formato de listing (fixed_price, auction, best_offer)
basado en velocidad, riesgo y datos de comps.
"""

from dataclasses import dataclass

from app.services.engines.risk_engine import RiskResult
from app.services.engines.velocity_engine import VelocityResult
from app.services.marketplace.base import CleanedComps


@dataclass
class ListingStrategyResult:
    recommended_format: str     # fixed_price|auction|best_offer
    reasoning: str
    auction_signal: float       # 0-1
    fixed_price_signal: float   # 0-1
    suggested_min_offer: float | None = None  # mínimo a aceptar en best_offer


def compute_listing_strategy(
    cleaned: CleanedComps,
    velocity: VelocityResult,
    risk: RiskResult,
    quick_price: float | None = None,
) -> ListingStrategyResult:
    """Recomienda formato de listing óptimo."""
    if cleaned.clean_total == 0:
        return ListingStrategyResult(
            recommended_format="best_offer",
            reasoning="Sin datos suficientes. Best offer permite negociar.",
            auction_signal=0.3,
            fixed_price_signal=0.3,
        )

    # Señales para auction
    auction_signal = 0.0

    # Alta demanda favorece auction
    if velocity.score >= 70:
        auction_signal += 0.35
    elif velocity.score >= 50:
        auction_signal += 0.15

    # Mercado estable favorece auction (los bidders compiten)
    if risk.score >= 70:
        auction_signal += 0.25

    # CV alto (precios dispersos) favorece auction
    if cleaned.cv > 0.35:
        auction_signal += 0.20

    # Bids en comps sugieren demanda de auction
    bids_count = sum(1 for l in cleaned.listings if l.bids and l.bids > 0)
    if bids_count > cleaned.clean_total * 0.3:
        auction_signal += 0.20

    auction_signal = min(1.0, auction_signal)

    # Señales para fixed price
    fixed_price_signal = 0.0

    # Mercado estable con buen precio = fixed price
    if risk.score >= 60:
        fixed_price_signal += 0.30
    if cleaned.cv < 0.30:
        fixed_price_signal += 0.30

    # Velocidad moderada favorece fixed price
    if 30 <= velocity.score <= 70:
        fixed_price_signal += 0.20

    # Muchos comps = mercado predecible
    if cleaned.clean_total >= 15:
        fixed_price_signal += 0.20

    fixed_price_signal = min(1.0, fixed_price_signal)

    # Matiz de variación para el reasoning
    cv = cleaned.cv
    if cv < 0.20:
        variation_desc = "precios muy consistentes"
    elif cv < 0.35:
        variation_desc = "variación moderada por diferencias de condición o presentación"
    else:
        variation_desc = "variación amplia en precios, posiblemente por bundles o condiciones mixtas"

    # Descripción de velocidad para reasoning
    if velocity.score >= 70:
        velocity_desc = "alta velocidad de venta"
    elif velocity.score >= 40:
        velocity_desc = "velocidad de venta moderada"
    else:
        velocity_desc = "baja velocidad de venta"

    # Decisión
    if auction_signal >= 0.60 and auction_signal > fixed_price_signal:
        recommended = "auction"
        reasoning = (
            f"Mercado activo con {velocity_desc} y {variation_desc}. "
            "La demanda alta favorece subasta donde los compradores compiten."
        )
    elif fixed_price_signal >= 0.50:
        recommended = "fixed_price"
        reasoning = (
            f"Mercado con {velocity_desc} y {variation_desc}. "
            "Suficiente consistencia para fixed price, aunque conviene monitorear comps."
        )
    else:
        recommended = "best_offer"
        if velocity.score >= 60:
            reasoning = (
                f"Mercado activo con {velocity_desc} pero {variation_desc}. "
                "Best offer permite capturar compradores dispuestos a pagar más, "
                "mientras el volumen garantiza rotación rápida."
            )
        elif velocity.score >= 30:
            reasoning = (
                f"Mercado con {velocity_desc} y {variation_desc}. "
                "Best offer permite negociar y atraer compradores."
            )
        else:
            reasoning = (
                f"Mercado lento con {velocity_desc} y {variation_desc}. "
                "Best offer permite negociar y atraer compradores."
            )

    if cleaned.clean_total < 10:
        reasoning += f" (basado en solo {cleaned.clean_total} comps — muestra limitada)"

    # suggested_min_offer: mínimo a aceptar cuando formato es best_offer
    min_offer = quick_price if recommended == "best_offer" and quick_price else None

    return ListingStrategyResult(
        recommended_format=recommended,
        reasoning=reasoning,
        auction_signal=round(auction_signal, 2),
        fixed_price_signal=round(fixed_price_signal, 2),
        suggested_min_offer=round(min_offer, 2) if min_offer else None,
    )
