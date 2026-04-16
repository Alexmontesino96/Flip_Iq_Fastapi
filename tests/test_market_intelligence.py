"""Tests para Motor M — Market Intelligence."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.engines.market_intelligence import (
    MarketEvent,
    MarketIntelligenceResult,
    _brave_search,
    _parse_intelligence_result,
    compute_market_intelligence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_llm_response() -> dict:
    return {
        "product_lifecycle": "mature",
        "depreciation_risk": 45,
        "seasonal_factor": 0.3,
        "market_events": [
            {"event": "New model announced", "impact": "negative", "relevance": "high"},
            {"event": "Black Friday upcoming", "impact": "positive", "relevance": "medium"},
        ],
        "timing_recommendation": "buy_now",
        "intelligence_summary": "Stable product with consistent demand. Good time to buy.",
        "confidence": "high",
    }


# ===========================================================================
# TestParseIntelligenceResult
# ===========================================================================

class TestParseIntelligenceResult:
    """7 tests para _parse_intelligence_result."""

    def test_valid_complete_response(self):
        data = _valid_llm_response()
        result = _parse_intelligence_result(data, "brave_search")

        assert result.product_lifecycle == "mature"
        assert result.depreciation_risk == 45
        assert result.seasonal_factor == 0.3
        assert len(result.market_events) == 2
        assert result.market_events[0].event == "New model announced"
        assert result.market_events[0].impact == "negative"
        assert result.timing_recommendation == "buy_now"
        assert result.confidence == "high"
        assert result.search_source == "brave_search"

    def test_invalid_lifecycle_defaults_to_mature(self):
        data = _valid_llm_response()
        data["product_lifecycle"] = "unknown_phase"
        result = _parse_intelligence_result(data, "llm_knowledge")
        assert result.product_lifecycle == "mature"

    def test_depreciation_risk_clamps_to_0_100(self):
        data = _valid_llm_response()

        data["depreciation_risk"] = 150
        result = _parse_intelligence_result(data, "llm_knowledge")
        assert result.depreciation_risk == 100

        data["depreciation_risk"] = -20
        result = _parse_intelligence_result(data, "llm_knowledge")
        assert result.depreciation_risk == 0

    def test_seasonal_factor_clamps(self):
        data = _valid_llm_response()

        data["seasonal_factor"] = 5.0
        result = _parse_intelligence_result(data, "llm_knowledge")
        assert result.seasonal_factor == 1.0

        data["seasonal_factor"] = -3.0
        result = _parse_intelligence_result(data, "llm_knowledge")
        assert result.seasonal_factor == -1.0

    def test_max_3_events(self):
        data = _valid_llm_response()
        data["market_events"] = [
            {"event": f"Event {i}", "impact": "neutral", "relevance": "low"}
            for i in range(6)
        ]
        result = _parse_intelligence_result(data, "llm_knowledge")
        assert len(result.market_events) == 3

    def test_invalid_timing_defaults_to_hold(self):
        data = _valid_llm_response()
        data["timing_recommendation"] = "panic_sell"
        result = _parse_intelligence_result(data, "llm_knowledge")
        assert result.timing_recommendation == "hold"

    def test_empty_data_returns_safe_defaults(self):
        result = _parse_intelligence_result({}, "llm_knowledge")
        assert result.product_lifecycle == "mature"
        assert result.depreciation_risk == 50
        assert result.seasonal_factor == 0.0
        assert result.market_events == []
        assert result.timing_recommendation == "hold"
        assert result.intelligence_summary == ""
        assert result.confidence == "medium"
        assert result.search_source == "llm_knowledge"


# ===========================================================================
# TestBraveSearch
# ===========================================================================

class TestBraveSearch:
    """3 tests para _brave_search."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self):
        with patch("app.services.engines.market_intelligence.settings") as mock_settings:
            mock_settings.brave_search_api_key = ""
            result = await _brave_search("test query")
            assert result is None

    @pytest.mark.asyncio
    async def test_successful_search_returns_results(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "web": {
                "results": [
                    {"title": "Result 1", "description": "Desc 1"},
                    {"title": "Result 2", "description": "Desc 2"},
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.engines.market_intelligence.settings") as mock_settings:
            mock_settings.brave_search_api_key = "test-key"
            with patch("app.services.engines.market_intelligence.httpx.AsyncClient", return_value=mock_client):
                result = await _brave_search("test query")

        assert result is not None
        assert len(result) == 2
        assert result[0]["title"] == "Result 1"

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.engines.market_intelligence.settings") as mock_settings:
            mock_settings.brave_search_api_key = "test-key"
            with patch("app.services.engines.market_intelligence.httpx.AsyncClient", return_value=mock_client):
                result = await _brave_search("test query")

        assert result is None


# ===========================================================================
# TestComputeMarketIntelligence
# ===========================================================================

class TestComputeMarketIntelligence:
    """4 tests para compute_market_intelligence."""

    _BASE_KWARGS = {
        "keyword": "iPhone 14 Pro",
        "marketplace": "ebay",
        "cleaned_total": 25,
        "median_price": 650.0,
        "min_price": 500.0,
        "max_price": 800.0,
        "sales_per_day": 2.5,
        "demand_trend": 5.0,
        "price_trend": -2.0,
    }

    @pytest.mark.asyncio
    async def test_no_llm_key_returns_none(self):
        with patch("app.core.llm.settings") as mock_settings:
            mock_settings.gemini_api_key = ""
            mock_settings.openai_api_key = ""
            result = await compute_market_intelligence(**self._BASE_KWARGS)
            assert result is None

    @pytest.mark.asyncio
    async def test_full_pipeline_with_web_search(self):
        llm_response = _valid_llm_response()

        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(llm_response)
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        with patch("app.core.llm.settings") as mock_llm_settings, \
             patch("app.services.engines.market_intelligence.settings") as mock_settings:
            mock_llm_settings.gemini_api_key = ""
            mock_llm_settings.openai_api_key = "test-openai-key"
            mock_settings.brave_search_api_key = "test-brave-key"
            with patch("app.services.engines.market_intelligence._brave_search", return_value=[
                {"title": "iPhone 14 trend", "description": "Prices stabilizing"},
            ]):
                with patch("openai.AsyncOpenAI", return_value=mock_client):
                    result = await compute_market_intelligence(**self._BASE_KWARGS)

        assert result is not None
        assert result.product_lifecycle == "mature"
        assert result.search_source == "brave_search"
        assert result.depreciation_risk == 45

    @pytest.mark.asyncio
    async def test_fallback_to_llm_knowledge_without_brave_key(self):
        llm_response = _valid_llm_response()

        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(llm_response)
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)

        with patch("app.core.llm.settings") as mock_llm_settings, \
             patch("app.services.engines.market_intelligence.settings") as mock_settings:
            mock_llm_settings.gemini_api_key = ""
            mock_llm_settings.openai_api_key = "test-openai-key"
            mock_settings.brave_search_api_key = ""
            with patch("app.services.engines.market_intelligence._brave_search", return_value=None):
                with patch("openai.AsyncOpenAI", return_value=mock_client):
                    result = await compute_market_intelligence(**self._BASE_KWARGS)

        assert result is not None
        assert result.search_source == "llm_knowledge"

    @pytest.mark.asyncio
    async def test_llm_error_returns_none(self):
        with patch("app.core.llm.settings") as mock_llm_settings, \
             patch("app.services.engines.market_intelligence.settings") as mock_settings:
            mock_llm_settings.gemini_api_key = ""
            mock_llm_settings.openai_api_key = "test-openai-key"
            mock_settings.brave_search_api_key = ""
            with patch("app.services.engines.market_intelligence._brave_search", return_value=None):
                mock_client = AsyncMock()
                mock_client.chat.completions.create = AsyncMock(
                    side_effect=Exception("API error")
                )
                with patch("openai.AsyncOpenAI", return_value=mock_client):
                    result = await compute_market_intelligence(**self._BASE_KWARGS)

        assert result is None
