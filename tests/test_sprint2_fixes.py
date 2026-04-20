"""Tests para Sprint 2: truncación AI brief, confidence con ventana expandida, score breakdown."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.analysis import AnalysisSummary, BuyBox, Returns, SalePlan, ScoreBreakdown
from app.services.engines.ai_explanation import generate_explanation, _strip_markdown, _align_decision_line
from app.services.engines.confidence_engine import compute_confidence
from app.services.marketplace.base import (
    CleanedComps,
    CompsResult,
    MarketplaceListing,
)


# ---------------------------------------------------------------------------
# P0-3: AI Explanation truncation handling
# ---------------------------------------------------------------------------

class TestAIExplanationTruncation:
    """max_tokens dinámico y retry cuando se trunca."""

    @pytest.mark.asyncio
    async def test_higher_tokens_with_comparison_text(self):
        """Cuando hay comparison_text, se usan más tokens."""
        with patch("app.core.llm.get_llm_client") as mock_llm:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(
                message=MagicMock(content="Decision: NOT YET, low confidence\nWhy: test\nRisk: test\nAction: test"),
                finish_reason="stop",
            )]
            mock_client.chat.completions.create.return_value = mock_response
            mock_llm.return_value = (mock_client, "test-model")

            engine = _FakeEngine()
            result = await generate_explanation(
                keyword="test helmet",
                cost_price=50.0,
                marketplace="ebay",
                pricing=engine, profit_market=engine, max_buy=engine,
                velocity=engine, risk=engine, confidence=engine,
                competition=engine, trend=engine, listing=engine,
                opportunity_score=54, recommendation="watch",
                cleaned_total=15, raw_total=20,
                comparison_text="\n\nMARKETPLACE COMPARISON:\neBay vs Amazon...",
            )

            assert result is not None
            # Verificar que se usaron más tokens (500 base con comparación)
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["max_tokens"] == 500  # 300 base + 200 comparison

    @pytest.mark.asyncio
    async def test_base_tokens_without_comparison(self):
        """Sin comparison_text, se usa el base de 300 tokens."""
        with patch("app.core.llm.get_llm_client") as mock_llm:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(
                message=MagicMock(content="Decision: YES, good profit\nWhy: test\nRisk: test\nAction: test"),
                finish_reason="stop",
            )]
            mock_client.chat.completions.create.return_value = mock_response
            mock_llm.return_value = (mock_client, "test-model")

            engine = _FakeEngine()
            result = await generate_explanation(
                keyword="test helmet",
                cost_price=50.0,
                marketplace="ebay",
                pricing=engine, profit_market=engine, max_buy=engine,
                velocity=engine, risk=engine, confidence=engine,
                competition=engine, trend=engine, listing=engine,
                opportunity_score=70, recommendation="buy",
                cleaned_total=15, raw_total=20,
            )

            assert result is not None
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["max_tokens"] == 300

    @pytest.mark.asyncio
    async def test_retry_on_truncation(self):
        """Cuando finish_reason='length', reintenta con más tokens."""
        with patch("app.core.llm.get_llm_client") as mock_llm:
            mock_client = AsyncMock()
            # Primera llamada: truncada
            truncated_response = MagicMock()
            truncated_response.choices = [MagicMock(
                message=MagicMock(content="Decision: NOT YET, if"),
                finish_reason="length",
            )]
            # Segunda llamada (retry): completa
            full_response = MagicMock()
            full_response.choices = [MagicMock(
                message=MagicMock(content="Decision: NOT YET, low confidence\nWhy: test\nRisk: test\nAction: test"),
                finish_reason="stop",
            )]
            mock_client.chat.completions.create.side_effect = [truncated_response, full_response]
            mock_llm.return_value = (mock_client, "test-model")

            engine = _FakeEngine()
            result = await generate_explanation(
                keyword="test helmet",
                cost_price=50.0,
                marketplace="ebay",
                pricing=engine, profit_market=engine, max_buy=engine,
                velocity=engine, risk=engine, confidence=engine,
                competition=engine, trend=engine, listing=engine,
                opportunity_score=54, recommendation="watch",
                cleaned_total=15, raw_total=20,
            )

            # Debe haber 2 llamadas: original + retry
            assert mock_client.chat.completions.create.call_count == 2
            # El retry usa 1200 tokens
            retry_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
            assert retry_kwargs["max_tokens"] == 1200
            # Resultado es el completo (del retry)
            assert "low confidence" in result


class TestAlignDecisionLine:
    """La línea Decision debe coincidir con la recomendación del engine."""

    def test_buy_maps_to_yes(self):
        text = "Decision: YES, good deal"
        result = _align_decision_line(text, "buy")
        assert result.startswith("Decision: YES")

    def test_watch_maps_to_not_yet(self):
        text = "Decision: YES, great deal"
        result = _align_decision_line(text, "watch")
        assert "NOT YET" in result

    def test_pass_maps_to_no(self):
        text = "Decision: YES, go ahead"
        result = _align_decision_line(text, "pass")
        assert "NO" in result


# ---------------------------------------------------------------------------
# P0-4: Confidence con ventana temporal expandida
# ---------------------------------------------------------------------------

def _make_cleaned_comps(
    n: int,
    days_of_data: float = 30,
    temporal_window_expanded: bool = False,
    initial_days_requested: float = 30,
    days_spread: int = 10,
) -> CleanedComps:
    """Crea CleanedComps con n listings distribuidos en days_spread días."""
    now = datetime.now(timezone.utc)
    listings = [
        MarketplaceListing(
            title=f"Test Product {i}",
            price=100.0 + i,
            total_price=100.0 + i,
            condition="New",
            ended_at=now - timedelta(days=days_spread * i / max(n, 1)),
            seller_username=f"seller_{i % 5}",
        )
        for i in range(n)
    ]
    return CleanedComps(
        listings=listings,
        raw_total=n + 5,
        clean_total=n,
        outliers_removed=3,
        days_of_data=days_of_data,
        temporal_window_expanded=temporal_window_expanded,
        initial_days_requested=initial_days_requested,
    )


class TestConfidenceTemporalWindow:
    """timeline_score no debe ser contaminado por ventana expandida."""

    def test_confidence_normal_window(self):
        """Con ventana normal (30 días), confidence razonable."""
        cleaned = _make_cleaned_comps(15, days_of_data=30, days_spread=25)
        raw = CompsResult(listings=[], total_sold=20, days_of_data=30)
        result = compute_confidence(cleaned, raw, enriched=True)
        # 15 comps con buena distribución → confidence >= 50
        assert result.score >= 50

    def test_confidence_expanded_window_not_destroyed(self):
        """Con ventana expandida 30→90, timeline_score usa initial_days (30)."""
        cleaned = _make_cleaned_comps(
            15, days_of_data=90,
            temporal_window_expanded=True,
            initial_days_requested=30,
            days_spread=25,
        )
        raw = CompsResult(listings=[], total_sold=20, days_of_data=90)
        result = compute_confidence(cleaned, raw, enriched=True)
        # Con el fix, timeline usa 30 como base → score no se destruye
        assert result.score >= 40, f"Confidence too low: {result.score}"
        # Debe haber penalización por expansión
        assert result.factors["window_expansion_penalty"] == 10.0

    def test_confidence_expanded_vs_normal_difference(self):
        """La diferencia entre expandida y normal debe ser moderada (no >30 puntos)."""
        # Normal: 30 días
        cleaned_normal = _make_cleaned_comps(15, days_of_data=30, days_spread=25)
        raw = CompsResult(listings=[], total_sold=20, days_of_data=30)
        result_normal = compute_confidence(cleaned_normal, raw, enriched=True)

        # Expandida: 30→90 días (mismos listings)
        cleaned_expanded = _make_cleaned_comps(
            15, days_of_data=90,
            temporal_window_expanded=True,
            initial_days_requested=30,
            days_spread=25,
        )
        result_expanded = compute_confidence(cleaned_expanded, raw, enriched=True)

        diff = result_normal.score - result_expanded.score
        # La diferencia debe ser <= 15 puntos (10 de penalización + algo de timeline)
        assert diff <= 15, f"Too much difference: {diff} (normal={result_normal.score}, expanded={result_expanded.score})"

    def test_window_expansion_penalty_in_factors(self):
        """El factor de penalización por expansión aparece en los factors."""
        cleaned = _make_cleaned_comps(
            10, days_of_data=90,
            temporal_window_expanded=True,
            initial_days_requested=30,
        )
        raw = CompsResult(listings=[], total_sold=15, days_of_data=90)
        result = compute_confidence(cleaned, raw)
        assert "window_expansion_penalty" in result.factors
        assert result.factors["window_expansion_penalty"] == 10.0

    def test_no_penalty_without_expansion(self):
        """Sin expansión, no hay penalización."""
        cleaned = _make_cleaned_comps(15, days_of_data=30)
        raw = CompsResult(listings=[], total_sold=20, days_of_data=30)
        result = compute_confidence(cleaned, raw)
        assert result.factors["window_expansion_penalty"] == 0.0


# ---------------------------------------------------------------------------
# P1-6: ScoreBreakdown schema
# ---------------------------------------------------------------------------

class TestScoreBreakdown:
    """El nuevo ScoreBreakdown organiza scores por categoría."""

    def test_score_breakdown_creation(self):
        scores = ScoreBreakdown(
            flip_score=54,
            velocity=69,
            risk=96,
            risk_label="low",
            confidence=45,
            confidence_label="low",
            temporal_window_expanded=True,
            execution_score=60,
            win_probability=0.6,
            final_score=56,
        )
        assert scores.flip_score == 54
        assert scores.risk == 96
        assert scores.confidence == 45
        assert scores.temporal_window_expanded is True
        assert scores.execution_score == 60

    def test_score_breakdown_optional_fields(self):
        """execution_score y win_probability son opcionales."""
        scores = ScoreBreakdown(
            flip_score=54,
            velocity=69,
            risk=96,
            risk_label="low",
            confidence=45,
            confidence_label="low",
        )
        assert scores.execution_score is None
        assert scores.win_probability is None
        assert scores.final_score is None

    def test_summary_includes_scores(self):
        """AnalysisSummary acepta el campo scores."""
        scores = ScoreBreakdown(
            flip_score=54,
            velocity=69,
            risk=96,
            risk_label="low",
            confidence=45,
            confidence_label="low",
        )
        summary = AnalysisSummary(
            recommendation="watch",
            buy_box=BuyBox(recommended_max_buy=100, your_cost=50, headroom=50),
            sale_plan=SalePlan(recommended_list_price=170, quick_sale_price=160, stretch_price=None),
            returns=Returns(profit=81.60, roi_pct=163.2, margin_pct=48.0),
            risk="low",
            confidence="low",
            scores=scores,
        )
        assert summary.scores is not None
        assert summary.scores.flip_score == 54

    def test_summary_scores_optional(self):
        """scores puede ser None (backward compatible)."""
        summary = AnalysisSummary(
            recommendation="pass",
            buy_box=BuyBox(recommended_max_buy=0, your_cost=50, headroom=-50),
            sale_plan=SalePlan(recommended_list_price=0, quick_sale_price=0, stretch_price=None),
            returns=Returns(profit=0, roi_pct=0, margin_pct=0),
            risk="high",
            confidence="low",
        )
        assert summary.scores is None


# ---------------------------------------------------------------------------
# Helper fake engine
# ---------------------------------------------------------------------------

class _FakeEngine:
    """Fake engine results para tests de AI explanation."""
    quick_list = 160.0
    market_list = 170.0
    stretch_list = 180.0
    stretch_allowed = True
    profit = 81.60
    roi = 1.632
    margin = 0.48
    recommended_max = 100.74
    score = 69
    category = "moderate"
    demand_trend = 15.0
    price_trend = 5.0
    burstiness = 0.1
    confidence_str = "medium"
    hhi = 0.15
    recommended_format = "fixed_price"
    estimated_days_to_sell = "2-3 days"
