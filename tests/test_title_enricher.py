"""Tests para Title Enricher — extracción de metadata de títulos."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.engines.title_enricher import (
    TitleEnrichment,
    _regex_bundle,
    _regex_condition,
    _regex_fallback,
    enrich_listings,
)
from app.services.marketplace.base import CompsResult, MarketplaceListing


# --- Regex Condition Tests ---

class TestRegexCondition:
    def test_new_sealed(self):
        assert _regex_condition("iPhone 15 NEW SEALED 256GB") == "new"

    def test_new_nib(self):
        assert _regex_condition("Nike Air Max NIB Size 10") == "new"

    def test_new_factory_sealed(self):
        assert _regex_condition("PS5 Console FACTORY SEALED") == "new"

    def test_used(self):
        assert _regex_condition("MacBook Pro 2023 Used Good") == "used"

    def test_pre_owned(self):
        assert _regex_condition("Rolex Submariner PRE-OWNED") == "used"

    def test_preowned_no_dash(self):
        assert _regex_condition("Rolex Submariner PREOWNED") == "used"

    def test_refurbished(self):
        assert _regex_condition("Dell XPS 15 REFURBISHED") == "refurbished"

    def test_open_box(self):
        assert _regex_condition("Sony WH-1000XM5 OPEN BOX") == "open_box"

    def test_for_parts(self):
        assert _regex_condition("iPhone 12 FOR PARTS screen cracked") == "for_parts"

    def test_as_is(self):
        assert _regex_condition("laptop AS-IS no power") == "for_parts"

    def test_no_condition(self):
        assert _regex_condition("Samsung Galaxy S24 Ultra 256GB") is None

    def test_brand_new(self):
        assert _regex_condition("BRAND NEW Nintendo Switch OLED") == "new"


# --- Regex Bundle Tests ---

class TestRegexBundle:
    def test_lot_of_3(self):
        is_b, size = _regex_bundle("LOT OF 3 iPhone Cases")
        assert is_b is True
        assert size == 3

    def test_bundle_of_5(self):
        is_b, size = _regex_bundle("Bundle of 5 USB Cables")
        assert is_b is True
        assert size == 5

    def test_set_of_2(self):
        is_b, size = _regex_bundle("Set of 2 Controllers")
        assert is_b is True
        assert size == 2

    def test_4_pack(self):
        is_b, size = _regex_bundle("4 Pack LED Bulbs")
        assert is_b is True
        assert size == 4

    def test_bulk_keyword(self):
        is_b, size = _regex_bundle("Wholesale Electronics Lot")
        assert is_b is True

    def test_not_bundle(self):
        is_b, size = _regex_bundle("iPhone 15 Pro Max 256GB")
        assert is_b is False
        assert size == 1

    def test_3x_pattern(self):
        is_b, size = _regex_bundle("3x Apple Lightning Cables")
        assert is_b is True
        assert size == 3


# --- Regex Fallback Tests ---

class TestRegexFallback:
    def test_multiple_titles(self):
        titles = [
            "iPhone 15 NEW SEALED 256GB",
            "MacBook Pro Used Good Condition",
            "LOT OF 3 Switch Controllers",
            "Samsung Galaxy S24 Ultra",
        ]
        results = _regex_fallback(titles)
        assert len(results) == 4
        assert results[0].condition == "new"
        assert results[1].condition == "used"
        assert results[2].is_bundle is True
        assert results[2].lot_size == 3
        assert results[3].condition is None
        assert results[3].is_bundle is False


# --- Enrich Listings Tests ---

class TestEnrichListings:
    @pytest.mark.asyncio
    async def test_empty_comps_returns_unchanged(self):
        comps = CompsResult()
        result = await enrich_listings(comps, keyword="test")
        assert result.listings == []

    @pytest.mark.asyncio
    async def test_fallback_regex_when_no_api_key(self):
        """Sin API key, usa regex fallback."""
        listings = [
            MarketplaceListing(
                title="iPhone 15 NEW SEALED 256GB", price=100, sold=True,
            ),
            MarketplaceListing(
                title="LOT OF 3 AirPods Pro Used", price=300, sold=True,
            ),
        ]
        comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

        with patch("app.core.llm.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            mock_settings.openai_api_key = ""
            result = await enrich_listings(comps, keyword="iPhone")

        assert result.listings[0].condition == "new"
        assert result.listings[1].condition == "used"
        assert result.listings[1].is_bundle is True
        assert result.listings[1].lot_size == 3

    @pytest.mark.asyncio
    async def test_preserves_existing_condition(self):
        """No sobreescribe condition que ya viene de Apify."""
        listings = [
            MarketplaceListing(
                title="iPhone 15 NEW SEALED", price=100, sold=True,
                condition="Used",  # Ya viene de Apify
            ),
        ]
        comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

        with patch("app.core.llm.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            mock_settings.openai_api_key = ""
            result = await enrich_listings(comps, keyword="iPhone")

        # No debe sobreescribir condition existente
        assert result.listings[0].condition == "Used"

    @pytest.mark.asyncio
    async def test_llm_extract_success(self):
        """Verifica que LLM enrichment funciona con mock."""
        listings = [
            MarketplaceListing(title="Apple iPhone 15 Pro Max 256GB New", price=1000, sold=True),
            MarketplaceListing(title="Samsung Galaxy S24 Ultra Used", price=800, sold=True),
        ]
        comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

        llm_response = json.dumps([
            {"condition": "new", "brand": "Apple", "model": "iPhone 15 Pro Max", "is_bundle": False, "lot_size": 1},
            {"condition": "used", "brand": "Samsung", "model": "Galaxy S24 Ultra", "is_bundle": False, "lot_size": 1},
        ])

        mock_message = MagicMock()
        mock_message.content = llm_response
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("app.core.llm.settings") as mock_settings, \
             patch("openai.AsyncOpenAI", return_value=mock_client):
            mock_settings.gemini_api_key = ""
            mock_settings.openai_api_key = "test-key"
            result = await enrich_listings(comps, keyword="iPhone 15")

        assert result.listings[0].condition == "new"
        assert result.listings[0].brand == "Apple"
        assert result.listings[0].model == "iPhone 15 Pro Max"
        assert result.listings[1].condition == "used"
        assert result.listings[1].brand == "Samsung"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_regex(self):
        """Si LLM falla, cae a regex."""
        listings = [
            MarketplaceListing(title="iPhone 15 NEW SEALED 256GB", price=100, sold=True),
        ]
        comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

        with patch("app.core.llm.settings") as mock_settings, \
             patch("app.services.engines.title_enricher._llm_extract", side_effect=Exception("API error")):
            mock_settings.gemini_api_key = ""
            mock_settings.openai_api_key = "test-key"
            result = await enrich_listings(comps, keyword="iPhone")

        # Debe usar regex fallback
        assert result.listings[0].condition == "new"
