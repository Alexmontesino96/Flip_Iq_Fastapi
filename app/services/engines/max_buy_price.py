"""Motor D — Max Buy Price.

Responde: "No pagues más de esto" calculando el precio máximo de compra
para lograr los targets de profit y ROI del usuario.
"""

from dataclasses import dataclass

from app.services.engines.profit_engine import ProfitResult


@dataclass
class MaxBuyResult:
    max_by_profit: float
    max_by_roi: float
    recommended_max: float


def compute_max_buy(
    profit_result: ProfitResult,
    target_profit: float = 10.0,
    target_roi: float = 0.35,
) -> MaxBuyResult:
    """Calcula precio máximo de compra para cumplir targets."""
    # Neto real disponible después de fees, costos variables y reserva
    available = profit_result.risk_adjusted_net - profit_result.prep_cost

    # max_by_profit: cuánto puedo pagar y aún ganar target_profit
    max_by_profit = available - target_profit

    # max_by_roi: cuánto puedo pagar y lograr target_roi
    # roi = profit / (cost + prep) => cost = available / (1 + roi)
    max_by_roi = available / (1 + target_roi) if target_roi > -1 else 0.0

    recommended_max = min(max_by_profit, max_by_roi)

    return MaxBuyResult(
        max_by_profit=round(max(0, max_by_profit), 2),
        max_by_roi=round(max(0, max_by_roi), 2),
        recommended_max=round(max(0, recommended_max), 2),
    )
