"""Motor E — Velocity Engine.

Calcula velocidad de venta con fórmula logarítmica.
score = min(100, round(25 * ln(1 + 30 * sales_per_day)))
"""

import math
from dataclasses import dataclass

from app.services.marketplace.base import CleanedComps


@dataclass
class VelocityResult:
    score: int
    sales_per_day: float
    category: str  # very_fast|healthy|moderate|slow
    market_sale_interval_days: float | None  # intervalo entre ventas en el mercado
    estimated_days_to_sell: float | None      # None sin datos de listings activos


def compute_velocity(cleaned: CleanedComps) -> VelocityResult:
    """Calcula score de velocidad de venta basado en sales_per_day."""
    spd = cleaned.sales_per_day

    if spd <= 0:
        return VelocityResult(
            score=0,
            sales_per_day=0.0,
            category="very_slow",
            market_sale_interval_days=None,
            estimated_days_to_sell=None,
        )

    score = min(100, round(25 * math.log(1 + 30 * spd)))

    if spd >= 1.0:
        category = "very_fast"
    elif spd >= 0.5:
        category = "healthy"
    elif spd >= 0.1:
        category = "moderate"
    else:
        category = "slow"

    # Intervalo entre ventas del mercado (no es promesa de venta individual)
    interval = round(1 / spd, 1) if spd > 0 else None

    # Estimación de días para vender basada en velocidad del mercado
    estimated = round(min(90.0, max(1.0, 1.0 / spd)), 1) if spd > 0 else None

    return VelocityResult(
        score=score,
        sales_per_day=round(spd, 4),
        category=category,
        market_sale_interval_days=interval,
        estimated_days_to_sell=estimated,
    )
