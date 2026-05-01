"""Tests para Sprint 1: validación cost=0, profit signal, ventana temporal."""

import math
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisRequest, CompsInfo
from app.services.engines.profit_engine import compute_profit
from app.services.engines.comp_cleaner import clean_comps
from app.services.marketplace.base import (
    CleanedComps,
    CompsResult,
    MarketplaceListing,
)


# ---------------------------------------------------------------------------
# P0-1: Validar cost_price > 0
# ---------------------------------------------------------------------------

class TestCostPriceValidation:
    """cost_price <= 0 debe ser rechazado en el request schema."""

    def test_cost_zero_rejected(self):
        with pytest.raises(ValidationError, match="cost_price must be greater than 0"):
            AnalysisRequest(keyword="test helmet", cost_price=0)

    def test_cost_negative_rejected(self):
        with pytest.raises(ValidationError, match="cost_price must be greater than 0"):
            AnalysisRequest(keyword="test helmet", cost_price=-10)

    def test_cost_positive_accepted(self):
        req = AnalysisRequest(keyword="test helmet", cost_price=0.01)
        assert req.cost_price == 0.01

    def test_cost_normal_accepted(self):
        req = AnalysisRequest(keyword="test helmet", cost_price=50.0)
        assert req.cost_price == 50.0


class TestProfitEngineZeroCost:
    """profit_engine debe manejar cost=0 defensivamente (inf ROI)."""

    def test_zero_cost_positive_profit_returns_inf_roi(self):
        result = compute_profit(sale_price=100.0, cost_price=0, marketplace="ebay")
        assert result.profit > 0
        assert result.roi == float("inf")

    def test_zero_cost_negative_profit_returns_neg_inf_roi(self):
        # sale_price=0 → profit negativo (fee_fixed=$0.30 makes it -0.30)
        result = compute_profit(sale_price=0, cost_price=0, marketplace="ebay")
        # Con sale=0 + fee_fixed=0.30: profit=-0.30, cost=0 → -inf
        assert result.roi == float("-inf")

    def test_zero_cost_zero_profit(self):
        """Si profit es exactamente 0 y cost es 0, ROI = 0."""
        # With fee_fixed=0.30, sale=0 gives profit=-0.30, not 0.
        # Use fee_fixed_override=0 to test the zero-profit case.
        result = compute_profit(sale_price=0, cost_price=0, marketplace="ebay",
                                fee_fixed_override=0.0)
        assert result.roi == 0.0

    def test_normal_cost_roi_unchanged(self):
        """Verify normal cost still calculates ROI correctly."""
        result = compute_profit(sale_price=100.0, cost_price=50.0, marketplace="ebay")
        assert math.isfinite(result.roi)
        assert result.roi == 0.629  # 31.45 / 50 (includes $0.30 per-order fee)


# ---------------------------------------------------------------------------
# P0-5: Ventana temporal expandida
# ---------------------------------------------------------------------------

def _make_listings(n: int, days_back: int = 10) -> list[MarketplaceListing]:
    """Crea n listings con fechas distribuidas en los últimos days_back días."""
    now = datetime.now(timezone.utc)
    return [
        MarketplaceListing(
            title=f"Test Helmet Model {i}",
            price=170.0 + (i % 5),
            total_price=170.0 + (i % 5),
            condition="New",
            ended_at=now - timedelta(days=days_back * i / max(n, 1)),
            seller_username=f"seller_{i % 3}",
        )
        for i in range(n)
    ]


class TestTemporalWindowExpansion:
    """CleanedComps debe indicar cuando la ventana temporal fue expandida."""

    def test_no_expansion_when_enough_comps(self):
        """Con suficientes comps en 30 días, no se expande."""
        listings = _make_listings(10, days_back=20)
        raw = CompsResult.from_listings(listings, marketplace="ebay", days=30)
        cleaned = clean_comps(raw, keyword="test helmet")
        assert not cleaned.temporal_window_expanded
        assert cleaned.days_of_data <= 30

    def test_expansion_when_few_comps(self):
        """Con < 5 comps en 30 días, expande a 90 días."""
        now = datetime.now(timezone.utc)
        # 3 listings recientes + 8 listings de hace 60 días
        recent = [
            MarketplaceListing(
                title="Test Helmet", price=170.0, total_price=170.0,
                condition="New", ended_at=now - timedelta(days=i * 5),
                seller_username=f"seller_{i}",
            )
            for i in range(3)
        ]
        old = [
            MarketplaceListing(
                title="Test Helmet", price=172.0, total_price=172.0,
                condition="New", ended_at=now - timedelta(days=50 + i * 3),
                seller_username=f"seller_{i + 3}",
            )
            for i in range(8)
        ]
        all_listings = recent + old
        raw = CompsResult.from_listings(all_listings, marketplace="ebay", days=30)
        cleaned = clean_comps(raw, keyword="test helmet")
        assert cleaned.temporal_window_expanded
        assert cleaned.days_of_data == 90
        assert cleaned.initial_days_requested < 90

    def test_comps_info_has_expansion_fields(self):
        """CompsInfo schema acepta los nuevos campos."""
        info = CompsInfo(
            total_sold=15,
            avg_price=170.0,
            median_price=170.0,
            min_price=161.0,
            max_price=171.0,
            days_of_data=90,
            source="ebay_cleaned",
            temporal_window_expanded=True,
            initial_days_requested=30,
        )
        assert info.temporal_window_expanded is True
        assert info.initial_days_requested == 30

    def test_comps_info_defaults(self):
        """CompsInfo defaults: no expansion."""
        info = CompsInfo(
            total_sold=15,
            avg_price=170.0,
            median_price=170.0,
            min_price=161.0,
            max_price=171.0,
            days_of_data=30,
            source="ebay_cleaned",
        )
        assert info.temporal_window_expanded is False
        assert info.initial_days_requested is None
