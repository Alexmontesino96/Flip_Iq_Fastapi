"""Tests para Motor L — AI Explanation."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.engines.ai_explanation import SYSTEM_PROMPT, generate_explanation


# --- Fake dataclasses para simular inputs ---

@dataclass
class FakePricing:
    quick_list: float = 40.0
    market_list: float = 50.0
    stretch_list: float = 60.0


@dataclass
class FakeProfit:
    profit: float = 15.0
    roi: float = 0.30
    margin: float = 0.23
    sale_price: float = 50.0
    fee_rate: float = 0.1325
    marketplace_fees: float = 6.63
    shipping_cost: float = 0.0
    packaging_cost: float = 0.0
    prep_cost: float = 0.0
    promo_cost: float = 0.0
    return_reserve: float = 2.50
    gross_proceeds: float = 43.37
    risk_adjusted_net: float = 40.87


@dataclass
class FakeMaxBuy:
    recommended_max: float = 35.0
    max_by_profit: float = 30.0
    max_by_roi: float = 35.0


@dataclass
class FakeVelocity:
    score: int = 65
    category: str = "moderate"
    sales_per_day: float = 0.5
    market_sale_interval_days: float = 2.0
    estimated_days_to_sell: float = 7.0


@dataclass
class FakeRisk:
    score: int = 72
    category: str = "low"
    factors: dict = None
    def __post_init__(self):
        if self.factors is None:
            self.factors = {"cv": 0.85, "sample_size": 0.7}


@dataclass
class FakeConfidence:
    score: int = 68
    category: str = "medium_high"
    factors: dict = None
    def __post_init__(self):
        if self.factors is None:
            self.factors = {"sample": 0.7, "enrichment": 0.8}


@dataclass
class FakeCompetition:
    hhi: float = 0.08
    category: str = "healthy"
    dominant_seller_share: float = 0.15
    unique_sellers: int = 12


@dataclass
class FakeTrend:
    demand_trend: float = 5.2
    price_trend: float = -1.3
    coverage_ratio: float = 0.8
    burstiness: float = 0.1
    confidence: str = "medium"
    category: str = "stable"


@dataclass
class FakeListing:
    recommended_format: str = "fixed_price"
    reasoning: str = "test"
    auction_signal: float = 0.2
    fixed_price_signal: float = 0.8
    suggested_min_offer: float = None


class TestSystemPrompt:
    def test_prompt_mentions_4_paragraphs(self):
        assert "4 paragraphs" in SYSTEM_PROMPT

    def test_prompt_has_structure_guidance(self):
        assert "Market Overview" in SYSTEM_PROMPT
        assert "Profit Analysis" in SYSTEM_PROMPT
        assert "Risk Factors" in SYSTEM_PROMPT
        assert "Recommendation" in SYSTEM_PROMPT


class TestMaxTokens:
    @pytest.mark.asyncio
    async def test_max_tokens_at_least_1200(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Test explanation"))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("app.core.llm.get_llm_client", return_value=(mock_client, "test-model")):
            await generate_explanation(
                keyword="Test Product",
                cost_price=35.0,
                marketplace="ebay",
                pricing=FakePricing(),
                profit_market=FakeProfit(),
                max_buy=FakeMaxBuy(),
                velocity=FakeVelocity(),
                risk=FakeRisk(),
                confidence=FakeConfidence(),
                competition=FakeCompetition(),
                trend=FakeTrend(),
                listing=FakeListing(),
                opportunity_score=65,
                recommendation="buy",
                cleaned_total=25,
                raw_total=40,
            )

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs["max_tokens"] >= 2000


class TestGenerateExplanation:
    @pytest.mark.asyncio
    async def test_returns_none_without_api_key(self):
        with patch("app.core.llm.get_llm_client", return_value=(None, None)):
            result = await generate_explanation(
                keyword="Test",
                cost_price=10.0,
                marketplace="ebay",
                pricing=FakePricing(),
                profit_market=FakeProfit(),
                max_buy=FakeMaxBuy(),
                velocity=FakeVelocity(),
                risk=FakeRisk(),
                confidence=FakeConfidence(),
                competition=FakeCompetition(),
                trend=FakeTrend(),
                listing=FakeListing(),
                opportunity_score=50,
                recommendation="watch",
                cleaned_total=10,
                raw_total=20,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_includes_comparison_text(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="With comparison"))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("app.core.llm.get_llm_client", return_value=(mock_client, "test-model")):
            result = await generate_explanation(
                keyword="Test Product",
                cost_price=35.0,
                marketplace="ebay",
                pricing=FakePricing(),
                profit_market=FakeProfit(),
                max_buy=FakeMaxBuy(),
                velocity=FakeVelocity(),
                risk=FakeRisk(),
                confidence=FakeConfidence(),
                competition=FakeCompetition(),
                trend=FakeTrend(),
                listing=FakeListing(),
                opportunity_score=65,
                recommendation="buy",
                cleaned_total=25,
                raw_total=40,
                comparison_text="\n\neBay vs Amazon comparison data here",
            )

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            user_msg = call_kwargs["messages"][1]["content"]
            assert "eBay vs Amazon comparison data here" in user_msg
            assert result == "With comparison"
