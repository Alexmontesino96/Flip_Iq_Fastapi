"""Motor B — Pricing Engine.

Calcula 3 precios recomendados basados en comps limpios:
- quick_list: salida rápida
- market_list: precio de mercado (mediana)
- stretch_list: precio premium (si CV lo permite)
"""

from dataclasses import dataclass

from app.services.marketplace.base import CleanedComps


@dataclass
class PricingResult:
    quick_list: float
    market_list: float
    stretch_list: float
    stretch_allowed: bool


def compute_pricing(cleaned: CleanedComps) -> PricingResult:
    """Calcula precios recomendados a partir de comps limpios."""
    if cleaned.clean_total == 0:
        return PricingResult(
            quick_list=0.0,
            market_list=0.0,
            stretch_list=0.0,
            stretch_allowed=False,
        )

    median = cleaned.median_price
    p25 = cleaned.p25
    p75 = cleaned.p75
    iqr = cleaned.iqr
    cv = cleaned.cv

    # Cuando el rango natural es demasiado estrecho, usar 10% de la mediana
    # para que quick/stretch sean decisiones reales y no solo centavos alrededor
    # del mercado observado.
    min_spread = median * 0.10
    spread = 0.30 * iqr
    natural_range_pct = (p75 - p25) / median if median > 0 else 0.0
    use_min_spread = natural_range_pct < 0.10

    if use_min_spread:
        spread = min_spread
        # p25/p75 also collapse, so skip clamping
        quick_list = median - spread
        market_list = median
        stretch_allowed = True
        stretch_list = median + spread
    else:
        # quick_list = max(p25, median - spread)
        quick_list = max(p25, median - spread)
        market_list = median
        # stretch_list = min(p75, median + spread) si CV < 0.45, sino = market_list
        stretch_allowed = cv < 0.45
        if stretch_allowed:
            stretch_list = min(p75, median + spread)
        else:
            stretch_list = market_list

    return PricingResult(
        quick_list=round(quick_list, 2),
        market_list=round(market_list, 2),
        stretch_list=round(stretch_list, 2),
        stretch_allowed=stretch_allowed,
    )
