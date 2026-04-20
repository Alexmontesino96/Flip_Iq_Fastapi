"""Motor C — Profit Engine.

Calcula rentabilidad neta real con todos los costos del revendedor.
"""

from dataclasses import dataclass

from app.core.fees import MARKETPLACE_FEE_RATES


@dataclass
class ProfitResult:
    sale_price: float
    fee_rate: float
    marketplace_fees: float
    shipping_cost: float
    packaging_cost: float
    prep_cost: float
    promo_cost: float
    return_reserve: float
    gross_proceeds: float        # sale - fees - shipping - packaging - promo
    risk_adjusted_net: float     # gross_proceeds - return_reserve
    profit: float                # risk_adjusted_net - cost - prep
    roi: float                   # profit / (cost + prep)
    margin: float                # profit / sale_price


def compute_profit(
    sale_price: float,
    cost_price: float,
    marketplace: str,
    shipping_cost: float = 0.0,
    packaging_cost: float = 0.0,
    prep_cost: float = 0.0,
    promo_cost: float = 0.0,
    return_reserve_pct: float = 0.05,
    fee_rate_override: float | None = None,
) -> ProfitResult:
    """Calcula profit neto considerando todos los costos reales."""
    fee_rate = fee_rate_override if fee_rate_override is not None else MARKETPLACE_FEE_RATES.get(marketplace, 0.1325)
    marketplace_fees = sale_price * fee_rate
    return_reserve = sale_price * return_reserve_pct

    gross_proceeds = (
        sale_price - marketplace_fees - shipping_cost - packaging_cost - promo_cost
    )
    risk_adjusted_net = gross_proceeds - return_reserve
    profit = risk_adjusted_net - cost_price - prep_cost

    total_invested = cost_price + prep_cost
    if total_invested <= 0:
        roi = float("inf") if profit > 0 else (float("-inf") if profit < 0 else 0.0)
    else:
        roi = profit / total_invested
    margin = profit / sale_price if sale_price > 0 else 0.0

    return ProfitResult(
        sale_price=round(sale_price, 2),
        fee_rate=fee_rate,
        marketplace_fees=round(marketplace_fees, 2),
        shipping_cost=round(shipping_cost, 2),
        packaging_cost=round(packaging_cost, 2),
        prep_cost=round(prep_cost, 2),
        promo_cost=round(promo_cost, 2),
        return_reserve=round(return_reserve, 2),
        gross_proceeds=round(gross_proceeds, 2),
        risk_adjusted_net=round(risk_adjusted_net, 2),
        profit=round(profit, 2),
        roi=round(roi, 4),
        margin=round(margin, 4),
    )
