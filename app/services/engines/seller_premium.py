"""Motor H — Seller Premium.

Compara precios de sellers top (>=99.5% feedback) vs el resto
para detectar si hay un premium por reputación.
"""

from dataclasses import dataclass

from app.services.marketplace.base import CleanedComps


@dataclass
class SellerPremiumResult:
    premium_median: float | None
    overall_median: float
    premium_delta: float
    premium_pct: float
    top_seller_count: int


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2


def compute_seller_premium(cleaned: CleanedComps) -> SellerPremiumResult:
    """Calcula diferencia de precio entre sellers top y el resto."""
    if cleaned.clean_total == 0:
        return SellerPremiumResult(
            premium_median=None,
            overall_median=0.0,
            premium_delta=0.0,
            premium_pct=0.0,
            top_seller_count=0,
        )

    overall_median = cleaned.median_price

    # Filtrar sellers con >=99.5% feedback
    top_prices = [
        l.total_price
        for l in cleaned.listings
        if l.seller_feedback_pct is not None and l.seller_feedback_pct >= 99.5
        and l.total_price is not None
    ]

    if not top_prices:
        return SellerPremiumResult(
            premium_median=None,
            overall_median=overall_median,
            premium_delta=0.0,
            premium_pct=0.0,
            top_seller_count=0,
        )

    premium_median = _median(top_prices)
    delta = premium_median - overall_median
    pct = (delta / overall_median * 100) if overall_median > 0 else 0.0

    return SellerPremiumResult(
        premium_median=round(premium_median, 2),
        overall_median=round(overall_median, 2),
        premium_delta=round(delta, 2),
        premium_pct=round(pct, 2),
        top_seller_count=len(top_prices),
    )
