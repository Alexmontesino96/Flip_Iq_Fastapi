"""Tests para Motor C — Profit Engine y Motor D — Max Buy Price."""

from app.services.engines.profit_engine import compute_profit
from app.services.engines.max_buy_price import compute_max_buy


class TestProfitEngine:
    def test_basic_profit(self):
        result = compute_profit(
            sale_price=100.0,
            cost_price=50.0,
            marketplace="ebay",
        )
        # eBay fee = 13.25%
        assert result.fee_rate == 0.1325
        assert result.marketplace_fees == 13.25
        # gross_proceeds = 100 - 13.25 - 0 - 0 - 0 = 86.75
        assert result.gross_proceeds == 86.75
        # return_reserve = 100 * 0.05 = 5.0
        assert result.return_reserve == 5.0
        # risk_adjusted_net = 86.75 - 5.0 = 81.75
        assert result.risk_adjusted_net == 81.75
        # profit = 81.75 - 50 - 0 = 31.75
        assert result.profit == 31.75
        # roi = 31.75 / 50 = 0.635
        assert result.roi == 0.635

    def test_profit_with_all_costs(self):
        result = compute_profit(
            sale_price=100.0,
            cost_price=40.0,
            marketplace="ebay",
            shipping_cost=8.0,
            packaging_cost=2.0,
            prep_cost=5.0,
            promo_cost=3.0,
            return_reserve_pct=0.05,
        )
        # gross_proceeds = 100 - 13.25 - 8 - 2 - 3 = 73.75
        assert result.gross_proceeds == 73.75
        # risk_adjusted_net = 73.75 - 5.0 = 68.75
        assert result.risk_adjusted_net == 68.75
        # profit = 68.75 - 40 - 5 = 23.75
        assert result.profit == 23.75
        # roi = 23.75 / (40 + 5) = 0.5278
        assert abs(result.roi - 0.5278) < 0.001

    def test_negative_profit(self):
        result = compute_profit(
            sale_price=50.0,
            cost_price=60.0,
            marketplace="ebay",
        )
        assert result.profit < 0
        assert result.roi < 0

    def test_amazon_fba_fee_rate(self):
        result = compute_profit(
            sale_price=100.0,
            cost_price=50.0,
            marketplace="amazon_fba",
        )
        assert result.fee_rate == 0.15

    def test_facebook_fee_rate(self):
        result = compute_profit(
            sale_price=100.0,
            cost_price=50.0,
            marketplace="facebook_marketplace",
        )
        assert result.fee_rate == 0.05

    def test_unknown_marketplace_defaults_ebay(self):
        result = compute_profit(
            sale_price=100.0,
            cost_price=50.0,
            marketplace="unknown",
        )
        assert result.fee_rate == 0.1325

    def test_margin_calculation(self):
        result = compute_profit(
            sale_price=100.0,
            cost_price=50.0,
            marketplace="ebay",
        )
        # margin = profit / sale_price
        assert result.margin == result.profit / 100.0

    def test_gross_vs_risk_adjusted(self):
        """gross_proceeds - return_reserve = risk_adjusted_net."""
        result = compute_profit(
            sale_price=200.0,
            cost_price=100.0,
            marketplace="ebay",
        )
        assert result.risk_adjusted_net == result.gross_proceeds - result.return_reserve


class TestMaxBuyPrice:
    def test_basic_max_buy(self):
        profit = compute_profit(
            sale_price=100.0,
            cost_price=50.0,
            marketplace="ebay",
        )
        result = compute_max_buy(profit, target_profit=10.0, target_roi=0.35)

        # available = risk_adjusted_net - prep_cost = 81.75 - 0 = 81.75
        # max_by_profit = 81.75 - 10 = 71.75
        assert result.max_by_profit == 71.75
        # max_by_roi = 81.75 / 1.35 = 60.56
        assert abs(result.max_by_roi - 60.56) < 0.01
        # recommended = min(71.75, 60.56) = 60.56
        assert result.recommended_max == result.max_by_roi

    def test_recommended_is_minimum(self):
        profit = compute_profit(sale_price=200.0, cost_price=50.0, marketplace="ebay")
        result = compute_max_buy(profit, target_profit=10.0, target_roi=0.35)
        assert result.recommended_max == min(result.max_by_profit, result.max_by_roi)

    def test_max_buy_never_negative(self):
        profit = compute_profit(sale_price=10.0, cost_price=50.0, marketplace="ebay")
        result = compute_max_buy(profit, target_profit=50.0, target_roi=0.35)
        assert result.recommended_max >= 0
        assert result.max_by_profit >= 0
        assert result.max_by_roi >= 0
