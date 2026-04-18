"""Tests para Motor B — Pricing Engine."""

from app.services.engines.pricing_engine import compute_pricing, PricingResult
from app.services.marketplace.base import CleanedComps


def _make_cleaned(median=100.0, p25=80.0, p75=120.0, iqr=40.0, cv=0.20, n=20) -> CleanedComps:
    return CleanedComps(
        clean_total=n,
        raw_total=n,
        median_price=median,
        avg_price=median,
        p25=p25,
        p75=p75,
        iqr=iqr,
        cv=cv,
        std_dev=median * cv,
        min_price=p25 - 10,
        max_price=p75 + 10,
        sales_per_day=n / 30,
        days_of_data=30,
    )


class TestPricingEngine:
    def test_empty_comps(self):
        cleaned = CleanedComps()
        result = compute_pricing(cleaned)
        assert result.quick_list == 0.0
        assert result.market_list == 0.0
        assert result.stretch_list == 0.0
        assert result.stretch_allowed is False

    def test_market_list_equals_median(self):
        cleaned = _make_cleaned(median=100.0)
        result = compute_pricing(cleaned)
        assert result.market_list == 100.0

    def test_quick_list_formula(self):
        """quick_list = max(p25, median - 0.30 * IQR)"""
        cleaned = _make_cleaned(median=100.0, p25=80.0, iqr=40.0)
        result = compute_pricing(cleaned)
        expected = max(80.0, 100.0 - 0.30 * 40.0)  # max(80, 88) = 88
        assert result.quick_list == expected

    def test_stretch_allowed_when_low_cv(self):
        cleaned = _make_cleaned(cv=0.20)
        result = compute_pricing(cleaned)
        assert result.stretch_allowed is True
        assert result.stretch_list >= result.market_list

    def test_stretch_not_allowed_when_high_cv(self):
        cleaned = _make_cleaned(cv=0.50)
        result = compute_pricing(cleaned)
        assert result.stretch_allowed is False
        assert result.stretch_list == result.market_list

    def test_stretch_formula(self):
        """stretch = min(p75, median + 0.30 * IQR) si CV < 0.45"""
        cleaned = _make_cleaned(median=100.0, p75=120.0, iqr=40.0, cv=0.20)
        result = compute_pricing(cleaned)
        expected = min(120.0, 100.0 + 0.30 * 40.0)  # min(120, 112) = 112
        assert result.stretch_list == expected

    def test_price_ordering(self):
        """quick <= market <= stretch"""
        cleaned = _make_cleaned(cv=0.20)
        result = compute_pricing(cleaned)
        assert result.quick_list <= result.market_list
        assert result.market_list <= result.stretch_list

    def test_zero_iqr_uses_minimum_spread(self):
        """When IQR=0 (all comps same price), prices should NOT collapse."""
        cleaned = _make_cleaned(
            median=105.20, p25=105.20, p75=105.20, iqr=0.0, cv=0.0, n=3,
        )
        result = compute_pricing(cleaned)
        assert result.market_list == 105.20
        # quick and stretch should differ from market
        assert result.quick_list < result.market_list
        assert result.stretch_list > result.market_list
        # spread = 10% of median
        assert result.quick_list == round(105.20 - 105.20 * 0.10, 2)
        assert result.stretch_list == round(105.20 + 105.20 * 0.10, 2)

    def test_narrow_market_range_expands_strategy_to_ten_percent(self):
        """When observed spread is <10%, quick/stretch should still be useful."""
        cleaned = _make_cleaned(
            median=129.20, p25=124.0, p75=132.0, iqr=8.0, cv=0.03, n=24,
        )
        result = compute_pricing(cleaned)
        assert result.market_list == 129.20
        assert result.quick_list == round(129.20 * 0.90, 2)
        assert result.stretch_list == round(129.20 * 1.10, 2)
