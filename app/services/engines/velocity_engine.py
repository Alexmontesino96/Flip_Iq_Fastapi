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
    category: str  # very_fast|healthy|moderate|slow|very_slow
    market_sale_interval_days: float | None  # intervalo entre ventas en el mercado
    estimated_days_to_sell: str | None        # "~2d", "~7-30d", etc.


def _format_days_to_sell(spd: float) -> str:
    """Formatea estimación de días para vender.

    Con ventas frecuentes (>= 0.5/día), muestra un número específico.
    Con ventas escasas (< 0.5/día), muestra un rango que refleja la
    incertidumbre real — ej. 3 ventas en 30 días ≠ "vende en 10 días".
    """
    point = min(90.0, max(1.0, 1.0 / spd))

    if spd >= 0.5:
        # Suficiente volumen para un estimado puntual
        return f"~{round(point)}d"

    # Baja frecuencia: rango con piso en el punto estimado y techo
    # en el doble del intervalo (cap 90 días)
    lo = max(1, round(point * 0.7))
    hi = min(90, round(point * 2.0))
    if lo == hi:
        return f"~{lo}d"
    return f"~{lo}-{hi}d"


def compute_velocity(cleaned: CleanedComps, config=None) -> VelocityResult:
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

    # Category-tunable thresholds
    coeff = config.velocity_coefficient if config else 25
    scaling = config.velocity_scaling if config else 30
    ceiling = config.velocity_ceiling if config else 100
    thresh_fast = config.velocity_very_fast if config else 1.0
    thresh_healthy = config.velocity_healthy if config else 0.5
    thresh_moderate = config.velocity_moderate if config else 0.1

    score = min(ceiling, round(coeff * math.log(1 + scaling * spd)))

    if spd >= thresh_fast:
        category = "very_fast"
    elif spd >= thresh_healthy:
        category = "healthy"
    elif spd >= thresh_moderate:
        category = "moderate"
    else:
        category = "slow"

    # Intervalo entre ventas del mercado (no es promesa de venta individual)
    interval = round(1 / spd, 1) if spd > 0 else None

    return VelocityResult(
        score=score,
        sales_per_day=round(spd, 4),
        category=category,
        market_sale_interval_days=interval,
        estimated_days_to_sell=_format_days_to_sell(spd),
    )
