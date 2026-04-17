"""Tests para los 3 fixes: channel labels, BuyBox best max_buy, warnings dedup."""

import pytest

from app.schemas.analysis import ChannelBreakdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_channel(marketplace: str, profit: float, roi_pct: float = 0.0) -> ChannelBreakdown:
    return ChannelBreakdown(
        marketplace=marketplace,
        estimated_sale_price=100.0,
        net_proceeds=80.0,
        profit=profit,
        roi_pct=roi_pct,
        margin_pct=profit,
    )


# ---------------------------------------------------------------------------
# Bug #2: BEST PROFIT → ONLY PROFITABLE when single profitable channel
# ---------------------------------------------------------------------------

class TestAssignChannelLabels:
    """_assign_channel_labels logic."""

    def test_only_profitable_label(self):
        """When only 1 channel is profitable, label should be ONLY PROFITABLE."""
        from app.services.analysis_service import _assign_channel_labels

        channels = [
            _make_channel("amazon_fba", profit=20.0, roi_pct=30.0),
            _make_channel("ebay", profit=-5.0, roi_pct=-10.0),
            _make_channel("mercadolibre", profit=-15.0, roi_pct=-20.0),
        ]
        _assign_channel_labels(channels)
        assert channels[0].label == "ONLY PROFITABLE"
        assert channels[1].label is None
        assert channels[2].label is None

    def test_best_profit_and_roi_labels(self):
        """When multiple channels are profitable, assign BEST PROFIT and BEST ROI."""
        from app.services.analysis_service import _assign_channel_labels

        channels = [
            _make_channel("amazon_fba", profit=20.0, roi_pct=25.0),
            _make_channel("ebay", profit=10.0, roi_pct=40.0),
            _make_channel("mercadolibre", profit=-5.0, roi_pct=-10.0),
        ]
        _assign_channel_labels(channels)
        assert channels[0].label == "BEST PROFIT"
        assert channels[1].label == "BEST ROI"

    def test_same_best_profit_and_roi(self):
        """When same channel has best profit AND best ROI, only BEST PROFIT."""
        from app.services.analysis_service import _assign_channel_labels

        channels = [
            _make_channel("amazon_fba", profit=20.0, roi_pct=40.0),
            _make_channel("ebay", profit=10.0, roi_pct=20.0),
        ]
        _assign_channel_labels(channels)
        assert channels[0].label == "BEST PROFIT"
        assert channels[1].label is None

    def test_no_labels_when_all_negative(self):
        """When no channel is profitable, no labels assigned."""
        from app.services.analysis_service import _assign_channel_labels

        channels = [
            _make_channel("ebay", profit=-5.0),
            _make_channel("amazon_fba", profit=-10.0),
        ]
        _assign_channel_labels(channels)
        assert all(ch.label is None for ch in channels)

    def test_empty_channels(self):
        """Empty list does not crash."""
        from app.services.analysis_service import _assign_channel_labels

        _assign_channel_labels([])
