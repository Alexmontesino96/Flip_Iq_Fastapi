"""Motor D — Max Buy Price.

Calcula el precio máximo de compra basado en datos reales del mercado.
No impone un target de ganancia fijo — muestra breakeven y max por ROI
para que el usuario decida.
"""

from dataclasses import dataclass

from app.services.engines.profit_engine import ProfitResult


@dataclass
class MaxBuyResult:
    max_by_profit: float
    max_by_roi: float
    recommended_max: float
    breakeven: float


def compute_max_buy(
    profit_result: ProfitResult,
    target_profit: float = 10.0,
    target_roi: float = 0.35,
) -> MaxBuyResult:
    """Calcula precio máximo de compra.

    - breakeven: máximo sin perder dinero (profit = $0)
    - max_by_roi: máximo para lograr target_roi (default 35%)
    - recommended_max: el menor de breakeven y max_by_roi
    - max_by_profit: legacy, basado en target_profit (informativo)
    """
    # Neto real disponible después de fees, costos variables y reserva
    available = profit_result.risk_adjusted_net - profit_result.prep_cost

    # Breakeven: profit = 0, o sea cost = available
    breakeven = available

    # max_by_roi: cuánto puedo pagar y lograr target_roi
    # roi = profit / (cost + prep) => cost = available / (1 + roi)
    max_by_roi = available / (1 + target_roi) if target_roi > -1 else 0.0

    # max_by_profit: legacy, cuánto puedo pagar y aún ganar target_profit
    max_by_profit = available - target_profit

    # Recomendación: basada en breakeven y ROI (no en target_profit fijo)
    recommended_max = min(breakeven, max_by_roi)

    return MaxBuyResult(
        max_by_profit=round(max(0, max_by_profit), 2),
        max_by_roi=round(max(0, max_by_roi), 2),
        recommended_max=round(max(0, recommended_max), 2),
        breakeven=round(max(0, breakeven), 2),
    )
