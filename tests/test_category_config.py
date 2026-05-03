"""Tests for category-based configuration system."""

import pytest

from app.services.category_config import (
    GLOBAL_DEFAULTS,
    ResolvedConfig,
    _build_config,
)


class TestGlobalDefaults:
    def test_has_all_required_keys(self):
        required = [
            "fee_rate", "fee_fixed", "return_reserve_pct",
            "risk_cv_threshold", "risk_dispersion_threshold",
            "velocity_coefficient", "velocity_scaling",
            "confidence_sample_size", "confidence_burstiness_threshold",
            "trend_demand_delta", "pricing_min_spread",
            "competition_hhi_concentrated", "execution_high_ticket_threshold",
        ]
        for key in required:
            assert key in GLOBAL_DEFAULTS, f"Missing key: {key}"

    def test_defaults_match_engine_hardcoded_values(self):
        """Global defaults must match the original hardcoded engine values."""
        assert GLOBAL_DEFAULTS["fee_rate"] == 0.136
        assert GLOBAL_DEFAULTS["return_reserve_pct"] == 0.05
        assert GLOBAL_DEFAULTS["risk_cv_threshold"] == 0.60
        assert GLOBAL_DEFAULTS["risk_dispersion_threshold"] == 0.60
        assert GLOBAL_DEFAULTS["velocity_coefficient"] == 25
        assert GLOBAL_DEFAULTS["velocity_scaling"] == 30
        assert GLOBAL_DEFAULTS["confidence_sample_size"] == 20
        assert GLOBAL_DEFAULTS["confidence_burstiness_threshold"] == 0.3
        assert GLOBAL_DEFAULTS["trend_demand_delta"] == 15
        assert GLOBAL_DEFAULTS["pricing_min_spread"] == 0.10
        assert GLOBAL_DEFAULTS["pricing_spread_factor"] == 0.30
        assert GLOBAL_DEFAULTS["pricing_cv_threshold"] == 0.45
        assert GLOBAL_DEFAULTS["competition_hhi_concentrated"] == 0.25
        assert GLOBAL_DEFAULTS["competition_hhi_moderate"] == 0.15
        assert GLOBAL_DEFAULTS["execution_high_ticket_threshold"] == 300


class TestResolvedConfig:
    def test_default_config_matches_global(self):
        """ResolvedConfig() with no args should match GLOBAL_DEFAULTS."""
        cfg = ResolvedConfig()
        assert cfg.fee_rate == 0.136
        assert cfg.risk_cv_threshold == 0.60
        assert cfg.velocity_coefficient == 25
        assert cfg.config_source == "global"
        assert cfg.observation_mode is False

    def test_build_config_from_merged_dict(self):
        merged = dict(GLOBAL_DEFAULTS)
        merged["fee_rate"] = 0.0
        merged["category_slug"] = "sneakers"
        merged["config_source"] = "category"
        merged["observation_mode"] = False

        cfg = _build_config(merged)
        assert cfg.fee_rate == 0.0
        assert cfg.category_slug == "sneakers"
        assert cfg.risk_cv_threshold == 0.60  # unchanged

    def test_build_config_ignores_unknown_keys(self):
        merged = dict(GLOBAL_DEFAULTS)
        merged["unknown_future_param"] = 42
        merged["category_slug"] = None
        merged["config_source"] = "global"

        cfg = _build_config(merged)
        assert not hasattr(cfg, "unknown_future_param")

    def test_category_overrides_merge(self):
        """Simulate level-2 merge: category overrides on top of global."""
        merged = dict(GLOBAL_DEFAULTS)
        # Sneakers overrides
        category_overrides = {
            "return_reserve_pct": 0.08,
            "shipping_cost": 15.00,
            "risk_cv_threshold": 0.55,
        }
        merged.update(category_overrides)
        merged["category_slug"] = "sneakers"
        merged["config_source"] = "category"

        cfg = _build_config(merged)
        assert cfg.return_reserve_pct == 0.08
        assert cfg.shipping_cost == 15.00
        assert cfg.risk_cv_threshold == 0.55
        # Global defaults preserved for un-overridden keys
        assert cfg.fee_rate == 0.136
        assert cfg.velocity_coefficient == 25

    def test_channel_overrides_merge(self):
        """Simulate level-3 merge: channel overrides on top of category."""
        merged = dict(GLOBAL_DEFAULTS)
        # Category level
        merged.update({"return_reserve_pct": 0.08, "shipping_cost": 15.00})
        # Channel level (e.g., eBay sneakers >$150 = 0% fee)
        merged.update({"fee_rate": 0.0})
        merged["category_slug"] = "sneakers"
        merged["channel"] = "ebay"
        merged["config_source"] = "channel"

        cfg = _build_config(merged)
        assert cfg.fee_rate == 0.0  # channel override
        assert cfg.return_reserve_pct == 0.08  # category override
        assert cfg.shipping_cost == 15.00  # category override
        assert cfg.velocity_coefficient == 25  # global default


class TestEngineBackwardCompat:
    """Verify engines work correctly with config=None (backward compatible)."""

    def test_profit_engine_no_config(self):
        from app.services.engines.profit_engine import compute_profit

        result = compute_profit(100.0, 50.0, "ebay")
        assert result.fee_rate == 0.136
        assert result.profit > 0

    def test_profit_engine_with_fee_override(self):
        from app.services.engines.profit_engine import compute_profit

        result = compute_profit(200.0, 100.0, "ebay", fee_rate_override=0.0, fee_fixed_override=0.0)
        assert result.fee_rate == 0.0
        assert result.marketplace_fees == 0.0
        # profit = 200 - 0 (fees) - 10 (5% return reserve) - 100 (cost) = 90
        assert result.profit == 90.0

    def test_risk_engine_no_config(self):
        from app.services.engines.risk_engine import compute_risk
        from tests.helpers import make_cleaned_comps, make_comps_result

        cleaned = make_cleaned_comps(cv=0.3, clean_total=20)
        raw = make_comps_result()
        result = compute_risk(cleaned, raw)
        assert 0 <= result.score <= 100

    def test_risk_engine_with_config(self):
        from app.services.engines.risk_engine import compute_risk
        from tests.helpers import make_cleaned_comps, make_comps_result

        cfg = ResolvedConfig(risk_cv_threshold=0.30)  # tighter threshold
        cleaned = make_cleaned_comps(cv=0.3, clean_total=20)
        raw = make_comps_result()

        result_default = compute_risk(cleaned, raw)
        result_strict = compute_risk(cleaned, raw, config=cfg)

        # Stricter threshold = lower score (more risk)
        assert result_strict.score <= result_default.score

    def test_velocity_engine_no_config(self):
        from app.services.engines.velocity_engine import compute_velocity
        from tests.helpers import make_cleaned_comps

        cleaned = make_cleaned_comps(sales_per_day=0.5)
        result = compute_velocity(cleaned)
        assert result.score > 0
        assert result.category == "healthy"

    def test_pricing_engine_no_config(self):
        from app.services.engines.pricing_engine import compute_pricing
        from tests.helpers import make_cleaned_comps

        cleaned = make_cleaned_comps()
        result = compute_pricing(cleaned)
        assert result.market_list > 0

    def test_competition_engine_no_config(self):
        from app.services.engines.competition_engine import compute_competition
        from tests.helpers import make_cleaned_comps

        cleaned = make_cleaned_comps()
        result = compute_competition(cleaned)
        assert result.category in ("healthy", "moderate", "concentrated", "no_data")

    def test_trend_engine_no_config(self):
        from app.services.engines.trend_engine import compute_trend
        from tests.helpers import make_cleaned_comps

        cleaned = make_cleaned_comps()
        result = compute_trend(cleaned)
        assert result.category in ("rising", "stable", "declining", "no_data")
