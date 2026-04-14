"""Tests para Title Risk Detector."""

from app.services.engines.title_risk import (
    compute_title_risk,
    _build_suppressed_flags,
    _scan_title,
)
from app.services.marketplace.base import CleanedComps, MarketplaceListing


def _make_cleaned(titles: list[str]) -> CleanedComps:
    listings = [
        MarketplaceListing(title=t, price=100.0, total_price=100.0)
        for t in titles
    ]
    return CleanedComps(listings=listings, clean_total=len(listings))


class TestDangerPatterns:
    def test_detects_box_only(self):
        result = compute_title_risk(_make_cleaned(["iPhone 15 BOX ONLY"]))
        assert "box_only" in result.semantic_flags
        assert result.risk_score > 0

    def test_detects_variant(self):
        result = compute_title_risk(_make_cleaned(["Nintendo Switch OLED Variant"]))
        assert "variant" in result.semantic_flags

    def test_detects_custom(self):
        result = compute_title_risk(_make_cleaned(["Custom PS5 Controller"]))
        assert "custom" in result.semantic_flags

    def test_detects_prototype(self):
        result = compute_title_risk(_make_cleaned(["Prototype Game Cartridge"]))
        assert "prototype" in result.semantic_flags

    def test_detects_sealed(self):
        result = compute_title_risk(_make_cleaned(["Sealed Pokemon Card Box"]))
        assert "sealed" in result.semantic_flags

    def test_detects_limited_edition(self):
        result = compute_title_risk(_make_cleaned(["Nintendo Switch OLED Limited Edition"]))
        assert "limited_edition" in result.semantic_flags

    def test_no_false_positive_in_box(self):
        """'in box' no debe triggerear box_standalone."""
        result = compute_title_risk(_make_cleaned(["iPhone 15 Pro New in Box"]))
        assert "box_standalone" not in result.semantic_flags

    def test_no_false_positive_with_box(self):
        """'with box' no debe triggerear box_standalone."""
        result = compute_title_risk(_make_cleaned(["PS5 with box and cables"]))
        assert "box_standalone" not in result.semantic_flags

    def test_no_false_positive_open_box(self):
        """'open box' no debe triggerear box_standalone."""
        result = compute_title_risk(_make_cleaned(["MacBook Air M2 Open Box"]))
        assert "box_standalone" not in result.semantic_flags


class TestSuppression:
    def test_limited_edition_not_suppressed_by_edition_alone(self):
        """'edition' en keyword NO debe suprimir limited_edition."""
        suppressed = _build_suppressed_flags("Nintendo Switch OLED Edition")
        assert "limited_edition" not in suppressed
        assert "special_edition" not in suppressed

    def test_limited_edition_suppressed_by_full_phrase(self):
        """'limited edition' en keyword SÍ suprime limited_edition."""
        suppressed = _build_suppressed_flags("Nintendo Switch OLED Limited Edition")
        assert "limited_edition" in suppressed

    def test_special_edition_suppressed_by_full_phrase(self):
        suppressed = _build_suppressed_flags("PS5 Special Edition")
        assert "special_edition" in suppressed

    def test_box_standalone_suppressed_by_sealed(self):
        suppressed = _build_suppressed_flags("Pokemon Cards Sealed")
        assert "box_standalone" in suppressed

    def test_box_standalone_suppressed_by_nib(self):
        suppressed = _build_suppressed_flags("iPhone 15 NIB")
        assert "box_standalone" in suppressed


class TestComputeTitleRisk:
    def test_empty_comps(self):
        result = compute_title_risk(CleanedComps())
        assert result.risk_score == 0.0
        assert not result.manual_review_required

    def test_clean_titles_no_risk(self):
        result = compute_title_risk(
            _make_cleaned(["iPhone 15 Pro Max 256GB Used", "iPhone 15 Pro 128GB"])
        )
        assert result.risk_score == 0.0

    def test_mixed_titles_moderate_risk(self):
        titles = [
            "iPhone 15 Pro Max 256GB",
            "iPhone 15 Pro Max 256GB",
            "iPhone 15 BOX ONLY",
            "iPhone 15 Case Only",
        ]
        result = compute_title_risk(_make_cleaned(titles))
        assert result.risk_score > 0
        assert result.flagged_listings == 2
