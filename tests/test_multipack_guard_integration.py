"""Integración del guard de multipack en _run_pipeline — PR-M3.

Verifica el caso Trojan end-to-end: detecta el pack, degrada la recomendación,
calcula el ROI corregido y NO toca el profit nominal (invariante AC-3).
"""

from datetime import datetime, timezone

from app.services.analysis_service import _run_pipeline
from app.services.marketplace.base import CompsResult, MarketplaceListing


def _amazon_comps(title, price, n=12, fba_fee=None, package_quantity=None):
    listings = [
        MarketplaceListing(
            title=title,
            price=price + i * 0.1,
            condition="new",
            marketplace="amazon",
            item_id="B0PACK",
            total_price=price + i * 0.1,
            ended_at=datetime.now(timezone.utc),
        )
        for i in range(n)
    ]
    raw = CompsResult.from_listings(listings, marketplace="amazon", days=30)
    raw.evaluated_title = title
    raw.evaluated_package_quantity = package_quantity
    raw.fba_fulfillment_fee = fba_fee
    return raw


class TestMultipackGuardIntegration:
    def test_trojan_pack_detected_and_corrected(self):
        raw = _amazon_comps(
            "Trojan Condoms (Pack of 12)", 28.80, fba_fee=4.20, package_quantity=1,
        )
        result = _run_pipeline(
            raw, keyword="trojan condoms", condition="any", cost_price=1.30,
            marketplace_name="amazon_fba",
        )
        assert result.is_likely_multipack is True
        assert result.bundle_factor == 12
        assert result.multipack_reason == "title_bundle"
        assert result.corrected_roi_pct is not None
        # El warning explica el pack.
        assert any("pack" in w.lower() for w in result.warnings)

    def test_ac3_nominal_profit_untouched(self):
        # AC-3: el profit nominal (sobre cost de 1 unidad) NO se toca; el corregido
        # (sobre cost*12) es un dato aparte y es MENOR.
        raw = _amazon_comps(
            "Trojan Condoms (Pack of 12)", 28.80, fba_fee=4.20, package_quantity=1,
        )
        result = _run_pipeline(
            raw, keyword="trojan condoms", condition="any", cost_price=1.30,
            marketplace_name="amazon_fba",
        )
        assert result.profit_market.profit > 0           # el nominal fantasma sigue ahí
        assert result.corrected_profit < result.profit_market.profit
        # La recomendación SÍ se degrada (no queda en 'buy' puro).
        assert result.recommendation in ("buy_small", "watch", "pass")

    def test_legit_single_no_guard(self):
        raw = _amazon_comps(
            "Trojan Condoms Single Unit", 15.0, fba_fee=3.50, package_quantity=1,
        )
        result = _run_pipeline(
            raw, keyword="trojan condoms", condition="any", cost_price=8.0,
            marketplace_name="amazon_fba",
        )
        assert result.is_likely_multipack is False
        assert result.bundle_factor is None
        assert result.corrected_roi_pct is None

    def test_fee_ratio_gate_without_title(self):
        # Sin "Pack of N" en el título, pero el fee delata el pack (gate 1).
        raw = _amazon_comps(
            "Generic Item", 40.0, fba_fee=9.0, package_quantity=1,
        )
        result = _run_pipeline(
            raw, keyword="generic item", condition="any", cost_price=1.0,
            marketplace_name="amazon_fba",
        )
        assert result.is_likely_multipack is True
        assert result.multipack_reason == "fee_ratio"
        assert result.bundle_factor is None  # sin título inequívoco
        assert result.corrected_roi_pct is None  # no se puede corregir sin factor

    def test_ebay_skips_guard(self):
        raw = _amazon_comps("Item (Pack of 12)", 28.80, fba_fee=4.20)
        result = _run_pipeline(
            raw, keyword="item", condition="any", cost_price=1.30,
            marketplace_name="ebay",
        )
        # El guard solo corre para amazon_fba.
        assert result.is_likely_multipack is False
        assert result.bundle_factor is None
