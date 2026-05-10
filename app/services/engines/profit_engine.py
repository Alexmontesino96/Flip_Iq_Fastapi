"""Motor C — Profit Engine.

Calcula rentabilidad neta real con todos los costos del revendedor.
"""

from dataclasses import dataclass

from app.core.fees import MARKETPLACE_FEE_FIXED, MARKETPLACE_FEE_RATES

# Tiered return-reserve: % baja a medida que sube el precio.
# Evita que productos caros tengan una reserva desproporcionada.
_RETURN_RESERVE_TIERS = [
    (50,   0.05),   # $0–50:   5%
    (200,  0.03),   # $50–200: 3%
    (500,  0.02),   # $200–500: 2%
    (None, 0.01),   # $500+:   1%
]


def compute_return_reserve(sale_price: float) -> float:
    """Calcula reserva de devolución escalonada por precio."""
    reserve = 0.0
    prev = 0.0
    for ceiling, rate in _RETURN_RESERVE_TIERS:
        if ceiling is None:
            reserve += (sale_price - prev) * rate
            break
        if sale_price <= ceiling:
            reserve += (sale_price - prev) * rate
            break
        reserve += (ceiling - prev) * rate
        prev = ceiling
    return reserve


@dataclass
class ProfitResult:
    sale_price: float
    fee_rate: float
    fee_fixed: float
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
    fee_fixed_override: float | None = None,
) -> ProfitResult:
    """Calcula profit neto considerando todos los costos reales."""
    fee_rate = fee_rate_override if fee_rate_override is not None else MARKETPLACE_FEE_RATES.get(marketplace, 0.1325)
    fee_fixed = fee_fixed_override if fee_fixed_override is not None else MARKETPLACE_FEE_FIXED.get(marketplace, 0.0)
    marketplace_fees = sale_price * fee_rate + fee_fixed
    return_reserve = compute_return_reserve(sale_price)

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
        fee_fixed=round(fee_fixed, 2),
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
