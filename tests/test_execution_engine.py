from types import SimpleNamespace

from app.services.engines.execution_engine import cap_recommendation, compute_execution
from app.services.marketplace.base import CleanedComps, CompsResult


def _profit(profit=216.49, sale_price=549.99, gross_proceeds=436.49):
    return SimpleNamespace(
        profit=profit,
        sale_price=sale_price,
        gross_proceeds=gross_proceeds,
    )


def _cleaned(clean_total=21, cv=0.12, pricing_basis="market"):
    return CleanedComps(
        clean_total=clean_total,
        raw_total=clean_total,
        cv=cv,
        pricing_basis=pricing_basis,
    )


def test_amazon_dominance_bimodal_and_decline_reduce_execution():
    result = compute_execution(
        marketplace_name="amazon_fba",
        profit_market=_profit(),
        risk=SimpleNamespace(score=96),
        confidence=SimpleNamespace(score=59),
        competition=SimpleNamespace(dominant_seller_share=0.81),
        trend=SimpleNamespace(demand_trend=-42.9, confidence="medium"),
        cleaned=_cleaned(),
        raw_comps=CompsResult(marketplace="amazon_fba"),
        distribution_shape="bimodal",
        product_type="console",
    )

    codes = {p.code for p in result.penalties}
    assert result.score < 55
    assert result.win_probability < 0.35
    assert result.max_recommendation in {"watch", "buy_small"}
    assert "seller_dominance_high" in codes
    assert "generic_fba_fees" in codes
    assert "bimodal_pricing" in codes
    assert "demand_declining" in codes


def test_clean_ebay_market_can_keep_buy_cap():
    result = compute_execution(
        marketplace_name="ebay",
        profit_market=_profit(profit=140.98, sale_price=441.56, gross_proceeds=360.0),
        risk=SimpleNamespace(score=82),
        confidence=SimpleNamespace(score=88),
        competition=SimpleNamespace(dominant_seller_share=0.18),
        trend=SimpleNamespace(demand_trend=66.7, confidence="medium"),
        cleaned=_cleaned(clean_total=64, cv=0.18),
        raw_comps=CompsResult(marketplace="ebay"),
        distribution_shape="normal",
        product_type="console",
    )

    assert result.score >= 75
    assert result.max_recommendation == "buy"
    assert result.win_probability >= 0.75


def test_cap_recommendation_only_degrades():
    assert cap_recommendation("buy", "buy_small") == "buy_small"
    assert cap_recommendation("watch", "buy_small") == "watch"
    assert cap_recommendation("pass", "watch") == "pass"
