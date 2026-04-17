"""Tests para ChannelBreakdown labels, is_estimated, y warnings."""

from app.schemas.analysis import AnalysisSummary, BuyBox, ChannelBreakdown, Returns, SalePlan
from app.services.analysis_service import _calculate_all_channels


class TestChannelBreakdownSchema:
    def test_defaults(self):
        ch = ChannelBreakdown(
            marketplace="ebay",
            estimated_sale_price=50.0,
            net_proceeds=40.0,
            profit=10.0,
            roi_pct=20.0,
            margin_pct=20.0,
        )
        assert ch.label is None
        assert ch.is_estimated is False

    def test_custom_values(self):
        ch = ChannelBreakdown(
            marketplace="ebay",
            estimated_sale_price=50.0,
            net_proceeds=40.0,
            profit=10.0,
            roi_pct=20.0,
            margin_pct=20.0,
            label="BEST PROFIT",
            is_estimated=True,
        )
        assert ch.label == "BEST PROFIT"
        assert ch.is_estimated is True

    def test_model_dump_includes_new_fields(self):
        ch = ChannelBreakdown(
            marketplace="ebay",
            estimated_sale_price=50.0,
            net_proceeds=40.0,
            profit=10.0,
            roi_pct=20.0,
            margin_pct=20.0,
            label="BEST ROI",
            is_estimated=True,
        )
        data = ch.model_dump()
        assert "label" in data
        assert "is_estimated" in data
        assert data["label"] == "BEST ROI"
        assert data["is_estimated"] is True

    def test_model_dump_defaults(self):
        ch = ChannelBreakdown(
            marketplace="ebay",
            estimated_sale_price=50.0,
            net_proceeds=40.0,
            profit=10.0,
            roi_pct=20.0,
            margin_pct=20.0,
        )
        data = ch.model_dump()
        assert data["label"] is None
        assert data["is_estimated"] is False


class TestCalculateAllChannels:
    def test_best_profit_label_assigned(self):
        channels = _calculate_all_channels(10.0, 50.0)
        assert channels[0].label == "BEST PROFIT"

    def test_best_roi_label_when_different(self):
        channels = _calculate_all_channels(10.0, 50.0)
        labels = [ch.label for ch in channels if ch.label is not None]
        # Al menos BEST PROFIT existe
        assert "BEST PROFIT" in labels
        # Si hay un canal con mejor ROI diferente, tendrá BEST ROI
        best_profit_idx = 0
        best_roi_idx = max(range(len(channels)), key=lambda i: channels[i].roi_pct)
        if best_profit_idx != best_roi_idx:
            assert channels[best_roi_idx].label == "BEST ROI"

    def test_others_have_none_label(self):
        channels = _calculate_all_channels(10.0, 50.0)
        labeled = [ch for ch in channels if ch.label is not None]
        unlabeled = [ch for ch in channels if ch.label is None]
        # Máximo 2 labels (BEST PROFIT + BEST ROI)
        assert len(labeled) <= 2
        # El resto es None
        for ch in unlabeled:
            assert ch.label is None

    def test_ebay_not_estimated(self):
        channels = _calculate_all_channels(
            10.0, 50.0, has_own_data={"ebay"},
        )
        ebay = next(ch for ch in channels if ch.marketplace == "ebay")
        assert ebay.is_estimated is False

    def test_amazon_with_own_data_not_estimated(self):
        channels = _calculate_all_channels(
            10.0, 50.0, has_own_data={"ebay", "amazon_fba"},
        )
        amz = next(ch for ch in channels if ch.marketplace == "amazon_fba")
        assert amz.is_estimated is False

    def test_amazon_without_own_data_is_estimated(self):
        channels = _calculate_all_channels(
            10.0, 50.0, has_own_data={"ebay"},
        )
        amz = next(ch for ch in channels if ch.marketplace == "amazon_fba")
        assert amz.is_estimated is True


class TestAnalysisSummaryWarnings:
    def test_analysis_summary_serializes_warnings(self):
        """Bug #4: Verificar que warnings se serializan correctamente."""
        summary = AnalysisSummary(
            recommendation="buy_small",
            buy_box=BuyBox(recommended_max_buy=50.0, your_cost=30.0, headroom=20.0),
            sale_plan=SalePlan(recommended_list_price=50.0, quick_sale_price=40.0, stretch_price=60.0),
            returns=Returns(profit=15.0, roi_pct=50.0, margin_pct=30.0),
            risk="medium",
            confidence="medium_high",
            warnings=[
                "Low confidence (45/100). Insufficient data.",
                "Only 4 comps after cleanup.",
            ],
        )
        data = summary.model_dump()
        assert len(data["warnings"]) == 2
        assert "Low confidence" in data["warnings"][0]
        assert "4 comps" in data["warnings"][1]
