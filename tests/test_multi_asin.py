"""Tests del flujo Multi-ASIN: consenso de marca + candidatos + anti-contaminación.

Migrado de batchflip_core (identity.choose_candidate) a FlipIQ.
"""

from unittest.mock import AsyncMock

import pytest

from app.services.marketplace.amazon import AmazonClient, _build_candidates
from app.services.marketplace.identity import choose_candidate


def _product(asin, brand, title="Generic Item", pkg=1):
    """Producto Keepa con una oferta válida (genera 1 listing)."""
    return {
        "asin": asin,
        "brand": brand,
        "title": title,
        "packageQuantity": pkg,
        "offers": [
            {"offerCSV": [1000, 1500, 0], "condition": 1,
             "sellerName": "S", "isFBA": True},
        ],
    }


class TestChooseCandidate:
    def test_no_candidates(self):
        r = choose_candidate("upc", [])
        assert r.asin is None and r.reason == "no_candidates"

    def test_single_candidate_no_brand_signal(self):
        r = choose_candidate("upc", [{"asin": "A", "brand": "X", "package_quantity": 1}])
        assert r.asin == "A" and r.reason == "no_brand_signal"
        assert r.needs_review is False

    def test_same_brand_default_ok(self):
        cands = [
            {"asin": "A", "brand": "Acme", "package_quantity": 1},
            {"asin": "B", "brand": "Acme", "package_quantity": 1},
        ]
        r = choose_candidate("upc", cands)
        assert r.reason == "default_ok"
        assert r.asin == "A"

    def test_corrected_to_majority_brand(self):
        # Caso Summer's Eve (10) vs Arrid (1): el default es el contaminante Arrid,
        # se corrige a la marca mayoritaria.
        cands = [{"asin": "ARRID", "brand": "Arrid", "package_quantity": 1}]
        cands += [{"asin": f"SE{i}", "brand": "Summers Eve", "package_quantity": 1} for i in range(10)]
        r = choose_candidate("upc", cands)
        assert r.reason == "corrected_to_majority_brand"
        assert r.chosen_brand == "summers eve"
        assert r.asin.startswith("SE")
        assert r.needs_review is False

    def test_fifty_fifty_stays_legacy(self):
        # 2 marcas 50/50 → zona intermedia documentada → default_ok (no degrada).
        cands = [
            {"asin": "A", "brand": "BrandA", "package_quantity": 1},
            {"asin": "B", "brand": "BrandB", "package_quantity": 1},
        ]
        r = choose_candidate("upc", cands)
        assert r.reason == "default_ok"
        assert r.needs_review is False

    def test_default_without_brand_not_corrected(self):
        # FIX (bug conocido de BatchFlip): un default SIN marca NO se corrige a la
        # marca mayoritaria — Keepa a veces no pobla 'brand' en la unidad legítima.
        cands = [
            {"asin": "LEGIT", "brand": None, "package_quantity": 1},   # default sin marca
            {"asin": "B", "brand": "Acme", "package_quantity": 6},
            {"asin": "C", "brand": "Acme", "package_quantity": 12},
        ]
        r = choose_candidate("upc", cands)
        assert r.reason == "no_brand_signal"
        assert r.asin == "LEGIT"           # se conserva el ASIN legítimo
        assert r.needs_review is False

    def test_dominant_share_populated(self):
        cands = [{"asin": "ARRID", "brand": "Arrid", "package_quantity": 1}]
        cands += [{"asin": f"SE{i}", "brand": "Summers Eve", "package_quantity": 1} for i in range(10)]
        r = choose_candidate("upc", cands)
        assert r.dominant_share == pytest.approx(10 / 11, abs=0.01)

    def test_ambiguous_conflict_needs_review(self):
        # default outlier (≤30%) sin mayoría clara (≥60%) → needs_review.
        cands = [
            {"asin": "A", "brand": "BrandA", "package_quantity": 1},   # 0.25 outlier
            {"asin": "B", "brand": "BrandB", "package_quantity": 1},   # 0.50 dom, <0.60
            {"asin": "C", "brand": "BrandB", "package_quantity": 1},
            {"asin": "D", "brand": "BrandC", "package_quantity": 1},   # 0.25
        ]
        r = choose_candidate("upc", cands)
        assert r.reason == "ambiguous_brand_conflict"
        assert r.needs_review is True


class TestBuildCandidates:
    def test_projects_fields(self):
        products = [
            {"asin": "B1", "brand": "Acme", "title": "Soap (Pack of 12)",
             "packageQuantity": 1, "imagesCSV": "abc123.jpg"},
        ]
        cands = _build_candidates(products)
        assert len(cands) == 1
        c = cands[0]
        assert c["asin"] == "B1"
        assert c["brand"] == "Acme"
        assert c["is_multipack"] is True
        assert c["image_url"].endswith("abc123.jpg")

    def test_skips_no_asin(self):
        cands = _build_candidates([{"brand": "Acme"}, {"asin": "B2", "brand": "Acme"}])
        assert [c["asin"] for c in cands] == ["B2"]


class TestGetSoldCompsMultiAsin:
    def _client(self, products):
        client = AmazonClient()
        client._api_key = "fake"
        client._keepa_product_by_code = AsyncMock(return_value=products)
        return client

    async def test_multi_asin_exposed(self):
        client = self._client([_product("B1", "BrandX"), _product("B2", "BrandX")])
        result = await client.get_sold_comps(barcode="012345678905")
        assert result.candidate_asins is not None
        assert len(result.candidate_asins) == 2
        assert result.identity_needs_review is False

    async def test_single_product_no_candidates(self):
        client = self._client([_product("B1", "BrandX")])
        result = await client.get_sold_comps(barcode="012345678905")
        # 1 solo product → sin badge.
        assert result.candidate_asins is None

    async def test_anti_contamination_excludes_other_brand_from_comps(self):
        products = [_product("BX", "Arrid")] + [
            _product(f"SE{i}", "Summers Eve") for i in range(3)
        ]
        client = self._client(products)
        result = await client.get_sold_comps(barcode="012345678905")
        assert result.identity_reason == "corrected_to_majority_brand"
        # El contaminante (Arrid) NO contamina los comps...
        asins_in_comps = {l.item_id for l in result.listings}
        assert "BX" not in asins_in_comps
        # ...pero sigue listado como candidato para el badge.
        assert any(c["asin"] == "BX" for c in result.candidate_asins)

    async def test_default_ok_majority_also_filters(self):
        # FIX MEDIO: con marca dominante clara (≥60%) que YA es la elegida
        # (default_ok), los contaminantes minoritarios también se filtran.
        products = [_product(f"SE{i}", "Summers Eve") for i in range(3)]
        products += [_product("CONTAM", "Other")]  # 1/4 = 25% contaminante
        client = self._client(products)
        result = await client.get_sold_comps(barcode="012345678905")
        asins_in_comps = {l.item_id for l in result.listings}
        assert "CONTAM" not in asins_in_comps          # contaminante fuera de comps
        assert any(c["asin"] == "CONTAM" for c in result.candidate_asins)  # sigue en badge

    async def test_keyword_path_no_consensus(self):
        # El path por keyword NO aplica consenso (marcas distintas son legítimas).
        client = AmazonClient()
        client._api_key = "fake"
        client._keepa_product_by_code = AsyncMock(return_value=[])
        client._keepa_search = AsyncMock(return_value=["K1", "K2"])
        client._keepa_product = AsyncMock(
            return_value=[_product("K1", "BrandA"), _product("K2", "BrandB")]
        )
        result = await client.get_sold_comps(keyword="generic item")
        assert result.candidate_asins is None
        assert result.identity_needs_review is False


class TestIdentityReviewDegradation:
    def test_ambiguous_identity_warns_and_degrades(self):
        from datetime import datetime, timezone

        from app.services.analysis_service import _run_pipeline
        from app.services.marketplace.base import CompsResult, MarketplaceListing

        listings = [
            MarketplaceListing(
                title="Item", price=20 + i, condition="new", marketplace="amazon",
                item_id="B1", total_price=20 + i, ended_at=datetime.now(timezone.utc),
            )
            for i in range(12)
        ]
        raw = CompsResult.from_listings(listings, marketplace="amazon", days=30)
        raw.identity_needs_review = True
        result = _run_pipeline(
            raw, keyword="item", condition="any", cost_price=5.0,
            marketplace_name="amazon_fba",
        )
        assert any("variant" in w.lower() for w in result.warnings)
        # Con identidad ambigua nunca se recomienda comprar.
        assert result.recommendation not in ("buy", "buy_small")


class TestMultiAsinPersistence:
    def test_reconstruction_preserves_identity_and_multipack_fields(self):
        from datetime import datetime, timezone

        from app.api.v1.analysis import _reconstruct_marketplace_analysis
        from app.services.analysis_service import _pipeline_to_engines_dict, _run_pipeline
        from app.services.marketplace.base import CompsResult, MarketplaceListing

        candidate_asins = [
            {
                "asin": "B1",
                "title": "Soap Single",
                "brand": "BrandA",
                "package_quantity": 1,
                "is_multipack": False,
                "image_url": None,
            },
            {
                "asin": "B2",
                "title": "Soap Pack of 12",
                "brand": "BrandB",
                "package_quantity": 12,
                "is_multipack": True,
                "image_url": None,
            },
        ]
        listings = [
            MarketplaceListing(
                title="Soap Pack of 12",
                price=24 + i,
                condition="new",
                marketplace="amazon",
                item_id="B2",
                total_price=24 + i,
                ended_at=datetime.now(timezone.utc),
            )
            for i in range(12)
        ]
        raw = CompsResult.from_listings(listings, marketplace="amazon", days=30)
        raw.evaluated_title = "Soap (Pack of 12)"
        raw.candidate_asins = candidate_asins
        raw.identity_needs_review = True
        raw.identity_reason = "ambiguous_brand_conflict"

        result = _run_pipeline(
            raw, keyword="soap", condition="any", cost_price=2.0,
            marketplace_name="amazon_fba",
        )
        engines = _pipeline_to_engines_dict(result)
        reconstructed = _reconstruct_marketplace_analysis(engines, "amazon")

        assert engines["identity"]["candidate_asins"] == candidate_asins
        assert reconstructed["candidate_asins"] == candidate_asins
        assert reconstructed["identity_review"] is True
        assert reconstructed["identity_reason"] == "ambiguous_brand_conflict"
        assert reconstructed["is_likely_multipack"] is True
        assert reconstructed["bundle_factor"] == 12
        assert reconstructed["corrected_roi_pct"] is not None


class TestVariantPrices:
    def test_extract_buy_box_price(self):
        from app.services.marketplace.amazon import _extract_buy_box_price
        assert _extract_buy_box_price({"stats": {"current": [None] * 18 + [2500]}}) == 25.0
        # fallback a New (current[1]) si el buy box no tiene dato (-1)
        assert _extract_buy_box_price(
            {"stats": {"current": [None, 1800] + [None] * 16 + [-1]}}
        ) == 18.0
        assert _extract_buy_box_price({}) is None

    async def test_get_variant_prices(self):
        client = AmazonClient()
        client._api_key = "fake"
        products = [{
            "asin": "B1", "brand": "Acme", "title": "Item A",
            "stats": {"current": [None] * 18 + [2500]},
            "offers": [{"offerCSV": [1000, 2400, 0], "condition": 1,
                        "sellerName": "S", "isFBA": True}],
        }]
        client._keepa_product = AsyncMock(return_value=products)
        result = await client.get_variant_prices(["B1"])
        assert len(result) == 1
        assert result[0]["asin"] == "B1"
        assert result[0]["brand"] == "Acme"
        assert result[0]["buy_box_price"] == 25.0
        assert result[0]["median_price"] == 24.0

    async def test_empty_and_no_key(self):
        client = AmazonClient()
        client._api_key = "fake"
        assert await client.get_variant_prices([]) == []
        client._api_key = ""
        assert await client.get_variant_prices(["B1"]) == []
