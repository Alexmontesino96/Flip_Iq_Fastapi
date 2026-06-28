"""Tests del guard de integridad de coste (multipack + size) — PR-M3."""

import pytest

from app.services.engines.cost_integrity import (
    corrected_metrics,
    detect_multipack_mismatch,
    multipack_mismatch_reason,
)
from app.services.engines.size_match import detect_size_mismatch, extract_sizes


class TestMultipackMismatchReason:
    def test_gate1_fee_ratio(self):
        # fee 8.0 >= cost 1.0 * 6 → delata el pack sin mirar el título.
        assert multipack_mismatch_reason(
            cost_unit=1.0, keepa_fba_fee=8.0, package_quantity=1, bundle_factor=None,
        ) == "fee_ratio"

    def test_gate2_package_quantity(self):
        assert multipack_mismatch_reason(
            cost_unit=2.0, keepa_fba_fee=3.0, package_quantity=6, bundle_factor=None,
        ) == "package_quantity"

    def test_gate3_title_bundle(self):
        # Caso Trojan: fee solo ~3x el coste, packageQuantity=1, "Pack of 12".
        assert multipack_mismatch_reason(
            cost_unit=1.30, keepa_fba_fee=4.20, package_quantity=1, bundle_factor=12,
        ) == "title_bundle"

    def test_no_mismatch_legit_single(self):
        assert multipack_mismatch_reason(
            cost_unit=5.0, keepa_fba_fee=3.50, package_quantity=1, bundle_factor=None,
        ) is None

    def test_precedence_fee_ratio_first(self):
        assert multipack_mismatch_reason(
            cost_unit=1.0, keepa_fba_fee=8.0, package_quantity=6, bundle_factor=12,
        ) == "fee_ratio"

    @pytest.mark.parametrize("cost", [0, -1, None])
    def test_invalid_cost_returns_none(self, cost):
        assert multipack_mismatch_reason(
            cost_unit=cost, keepa_fba_fee=8.0, package_quantity=6, bundle_factor=12,
        ) is None

    def test_zero_fee_does_not_trigger(self):
        # fee 0.0/None no debe disparar el gate 1 (null-safety).
        assert multipack_mismatch_reason(
            cost_unit=1.0, keepa_fba_fee=0.0, package_quantity=1, bundle_factor=None,
        ) is None

    def test_detect_bool_wrapper(self):
        assert detect_multipack_mismatch(
            cost_unit=1.30, keepa_fba_fee=4.20, package_quantity=1, bundle_factor=12,
        ) is True
        assert detect_multipack_mismatch(
            cost_unit=5.0, keepa_fba_fee=3.50, package_quantity=1, bundle_factor=None,
        ) is False


class TestCorrectedMetrics:
    def test_trojan_case(self):
        # nominal profit $22 sobre cost $1.30, pack de 12.
        # corrected_cost = 1.30*12 = 15.60
        # corrected_profit = 22 - 1.30*11 = 7.70
        # corrected_roi = 7.70/15.60*100 ≈ 49.36
        profit, roi = corrected_metrics(
            nominal_profit=22.0, cost_unit=1.30, bundle_factor=12,
        )
        assert profit == 7.70
        assert roi == pytest.approx(49.36, abs=0.05)

    def test_negative_when_pack_kills_it(self):
        # cost alto * N supera el profit → corrected negativo.
        profit, roi = corrected_metrics(
            nominal_profit=5.0, cost_unit=3.0, bundle_factor=6,
        )
        # corrected_profit = 5 - 3*5 = -10
        assert profit == -10.0
        assert roi < 0

    @pytest.mark.parametrize("bf", [None, 1, 0])
    def test_no_multipack_returns_none(self, bf):
        assert corrected_metrics(nominal_profit=22.0, cost_unit=1.30, bundle_factor=bf) == (None, None)

    @pytest.mark.parametrize("cost", [0, -1, None])
    def test_invalid_cost_returns_none(self, cost):
        assert corrected_metrics(nominal_profit=22.0, cost_unit=cost, bundle_factor=12) == (None, None)

    def test_missing_profit_returns_none(self):
        assert corrected_metrics(nominal_profit=None, cost_unit=1.30, bundle_factor=12) == (None, None)


class TestSizeMatch:
    def test_extract_mass(self):
        assert extract_sizes("Coffee 12 oz")["mass"] == pytest.approx(340.19, abs=0.1)

    def test_extract_volume(self):
        assert extract_sizes("Juice 16 fl oz")["vol"] == pytest.approx(473.18, abs=0.1)

    def test_fl_oz_not_recaptured_as_mass(self):
        # "16 fl oz" es volumen, no debe contar como masa (16 oz).
        sizes = extract_sizes("Juice 16 fl oz")
        assert "mass" not in sizes

    def test_empty(self):
        assert extract_sizes("Book about coffee") == {}
        assert extract_sizes(None) == {}

    def test_mismatch_mass(self):
        # 150 g vs 45 g → ratio 3.33 >= 1.5 → mismatch.
        assert detect_size_mismatch("Snack 150 g", "Snack 45 g") is True

    def test_no_mismatch_within_tolerance(self):
        # 150 g vs 120 g → ratio 1.25 < 1.5 → sin mismatch.
        assert detect_size_mismatch("Snack 150 g", "Snack 120 g") is False

    def test_abstains_cross_dimension(self):
        # masa vs volumen → no comparable → se abstiene (False).
        assert detect_size_mismatch("Juice 16 fl oz", "Juice 5 oz") is False

    def test_abstains_when_size_missing(self):
        assert detect_size_mismatch("Generic Book", "Generic Book Large") is False
