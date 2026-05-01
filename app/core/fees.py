"""Calculadoras de fees por marketplace.

Cada marketplace cobra distintos fees (referral, FBA, insertion, final value, etc.).
Estas funciones estiman el costo neto para el reseller.
"""

from decimal import Decimal


def ebay_fees(
    sale_price: Decimal,
    category_rate: Decimal = Decimal("0.1325"),
    per_order_fee: Decimal = Decimal("0.30"),
) -> dict:
    """Estima fees de eBay (final value fee + per-order fee).

    eBay cobra ~13.25% en la mayoría de categorías (incluye payment processing)
    más $0.30 por transacción.
    """
    final_value = sale_price * category_rate + per_order_fee
    net = sale_price - final_value
    return {
        "marketplace": "ebay",
        "sale_price": float(sale_price),
        "final_value_fee": float(round(final_value, 2)),
        "net_proceeds": float(round(net, 2)),
    }


def amazon_fba_fees(
    sale_price: Decimal,
    referral_rate: Decimal = Decimal("0.15"),
    fba_fee: Decimal = Decimal("3.50"),
) -> dict:
    """Estima fees de Amazon FBA (referral + fulfillment).

    Referral fee ~15% en la mayoría de categorías.
    FBA fee depende del tamaño/peso; US$3.50 es un promedio para items estándar pequeños.
    """
    referral = sale_price * referral_rate
    total_fees = referral + fba_fee
    net = sale_price - total_fees
    return {
        "marketplace": "amazon_fba",
        "sale_price": float(sale_price),
        "referral_fee": float(round(referral, 2)),
        "fba_fee": float(fba_fee),
        "total_fees": float(round(total_fees, 2)),
        "net_proceeds": float(round(net, 2)),
    }


def mercadolibre_fees(
    sale_price: Decimal, category_rate: Decimal = Decimal("0.16")
) -> dict:
    """Estima fees de MercadoLibre (~16% comisión estándar)."""
    commission = sale_price * category_rate
    net = sale_price - commission
    return {
        "marketplace": "mercadolibre",
        "sale_price": float(sale_price),
        "commission": float(round(commission, 2)),
        "net_proceeds": float(round(net, 2)),
    }


def facebook_marketplace_fees(
    sale_price: Decimal, fee_rate: Decimal = Decimal("0.05")
) -> dict:
    """Facebook Marketplace cobra ~5% por ventas con envío (selling fee)."""
    selling_fee = sale_price * fee_rate
    net = sale_price - selling_fee
    return {
        "marketplace": "facebook_marketplace",
        "sale_price": float(sale_price),
        "selling_fee": float(round(selling_fee, 2)),
        "net_proceeds": float(round(net, 2)),
    }


MARKETPLACE_FEE_RATES = {
    "ebay": 0.1325,
    "amazon_fba": 0.15,
}

MARKETPLACE_FEE_FIXED = {
    "ebay": 0.30,       # per-order fee
    "amazon_fba": 3.50,  # FBA fulfillment (default, overridden by Keepa)
}


MARKETPLACE_CALCULATORS = {
    "ebay": ebay_fees,
    "amazon_fba": amazon_fba_fees,
}


def calculate_margin(
    cost: Decimal, sale_price: Decimal, marketplace: str = "ebay"
) -> dict:
    """Calcula margen neto después de fees del marketplace."""
    calculator = MARKETPLACE_CALCULATORS.get(marketplace, ebay_fees)
    fees_result = calculator(sale_price)
    net_proceeds = Decimal(str(fees_result["net_proceeds"]))
    profit = net_proceeds - cost
    margin_pct = (profit / cost * 100) if cost > 0 else Decimal("0")
    roi = (profit / cost * 100) if cost > 0 else Decimal("0")

    return {
        **fees_result,
        "cost": float(cost),
        "profit": float(round(profit, 2)),
        "margin_pct": float(round(margin_pct, 2)),
        "roi_pct": float(round(roi, 2)),
    }
