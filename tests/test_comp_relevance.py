"""Tests para Comp Relevance Filter (Cohere Rerank + LLM fallback)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.engines.comp_relevance import (
    _MIN_COMPS_AFTER_FILTER,
    _parse_response,
    filter_comps_by_relevance,
)
from app.services.marketplace.base import CompsResult, MarketplaceListing


def _make_listing(title: str, price: float = 100.0) -> MarketplaceListing:
    return MarketplaceListing(title=title, price=price, total_price=price)


def _make_comps(titles_prices: list[tuple[str, float]]) -> CompsResult:
    listings = [_make_listing(t, p) for t, p in titles_prices]
    return CompsResult(
        listings=listings,
        total_sold=len(listings),
        median_price=100.0,
        marketplace="ebay_sold",
    )


def _rerank_response(scores: list[float]) -> dict:
    """Construye una respuesta mock del API de OpenRouter Rerank."""
    return {
        "results": [
            {"index": i, "relevance_score": s, "document": {"text": f"title_{i}"}}
            for i, s in enumerate(scores)
        ]
    }


class TestFilterCompsByRelevanceRerank:
    """Tests con Cohere Rerank como método principal."""

    @pytest.mark.asyncio
    async def test_filters_wrong_variant(self):
        """GS keyword + Mens comps → Mens removidos via rerank scores."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 GS Pink", 90.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Grey", 75.0),
            ("Nike Vomero 5 GS Green", 82.0),
        ])

        # High scores for GS, low for Mens
        mock_scores = [0.95, 0.92, 0.90, 0.15, 0.12, 0.88, 0.91]

        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=[1 if s >= 0.5 else 0 for s in mock_scores],
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5
        for l in result.listings:
            assert "Mens" not in l.title
        assert result.reranked is True

    @pytest.mark.asyncio
    async def test_keeps_matching_comps(self):
        """GS keyword + all GS comps → todos sobreviven."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 GS Pink", 90.0),
            ("Nike Vomero 5 GS Grey", 75.0),
            ("Nike Vomero 5 GS Green", 82.0),
        ])

        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=[1, 1, 1, 1, 1],
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5
        assert result.reranked is True

    @pytest.mark.asyncio
    async def test_safety_net_keeps_all_if_too_few(self):
        """Si < MIN_COMPS sobreviven → no filtra."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 Mens White", 165.0),
            ("Nike Vomero 5 Mens Green", 155.0),
            ("Nike Vomero 5 Mens Grey", 162.0),
        ])

        # Solo 1 GS sobreviviría — menos que _MIN_COMPS_AFTER_FILTER
        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=[1, 0, 0, 0, 0, 0],
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        # Safety net: mantiene todos
        assert len(result.listings) == 6

    @pytest.mark.asyncio
    async def test_sets_reranked_flag(self):
        """Cuando rerank filtra exitosamente, comps.reranked = True."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 GS Pink", 90.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS Grey", 75.0),
            ("Nike Vomero 5 GS Green", 82.0),
        ])

        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=[1, 1, 1, 0, 1, 1],
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert result.reranked is True


class TestFilterCompsByRelevanceFallback:
    """Tests de fallback a LLM cuando rerank no está disponible."""

    @pytest.mark.asyncio
    async def test_fallback_to_llm_when_no_rerank_key(self):
        """Sin OpenRouter key → fallback a LLM."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 GS Pink", 90.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Grey", 75.0),
            ("Nike Vomero 5 GS Green", 82.0),
        ])

        mock_llm_verdicts = [1, 1, 1, 0, 0, 1, 1]

        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=None,  # rerank no disponible
        ), patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=mock_llm_verdicts,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5
        assert result.reranked is False  # Usó LLM, no rerank

    @pytest.mark.asyncio
    async def test_fallback_to_llm_when_rerank_fails(self):
        """Rerank falla → fallback a LLM."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 GS Pink", 90.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Grey", 75.0),
            ("Nike Vomero 5 GS Green", 82.0),
        ])

        mock_llm_verdicts = [1, 1, 1, 0, 0, 1, 1]

        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=mock_llm_verdicts,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5
        assert result.reranked is False

    @pytest.mark.asyncio
    async def test_graceful_degradation_both_fail(self):
        """Rerank + LLM fallan → retorna comps sin cambio."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Pink", 90.0),
        ])

        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5  # Sin cambios

    @pytest.mark.asyncio
    async def test_graceful_degradation_llm_exception(self):
        """Rerank None + LLM exception → retorna comps sin cambio."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Pink", 90.0),
        ])

        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5  # Sin cambios

    @pytest.mark.asyncio
    async def test_handles_wrong_length_response(self):
        """Rerank retorna array de tamaño incorrecto → no filtra."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Pink", 90.0),
        ])

        # Array más corto que listings
        with patch(
            "app.services.engines.comp_relevance._call_rerank",
            new_callable=AsyncMock,
            return_value=[1, 0, 1],
        ), patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5  # Sin cambios

    @pytest.mark.asyncio
    async def test_empty_comps_returns_unchanged(self):
        """CompsResult vacío → retorna sin cambio."""
        comps = CompsResult(listings=[], total_sold=0, median_price=0.0)
        result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")
        assert len(result.listings) == 0

    @pytest.mark.asyncio
    async def test_no_keyword_returns_unchanged(self):
        """Sin keyword → retorna sin cambio."""
        comps = _make_comps([("Nike Vomero 5 GS Black", 80.0)])
        result = await filter_comps_by_relevance(comps, "")
        assert len(result.listings) == 1


class TestCallRerank:
    """Tests directos para _call_rerank."""

    @pytest.mark.asyncio
    async def test_rerank_converts_scores_to_verdicts(self):
        """Scores >= 0.5 → 1, < 0.5 → 0."""
        from app.services.engines.comp_relevance import _call_rerank

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _rerank_response([0.95, 0.12, 0.80, 0.30])

        with patch("app.services.engines.comp_relevance.settings", openrouter_api_key="test-key"), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await _call_rerank(
                ["title1", "title2", "title3", "title4"],
                "keyword",
            )

        assert result == [1, 0, 1, 0]

    @pytest.mark.asyncio
    async def test_rerank_no_key_returns_none(self):
        """Sin API key → None."""
        from app.services.engines.comp_relevance import _call_rerank

        with patch("app.services.engines.comp_relevance.settings", openrouter_api_key=""):
            result = await _call_rerank(["title1"], "keyword")

        assert result is None

    @pytest.mark.asyncio
    async def test_rerank_api_error_returns_none(self):
        """Error de API → None."""
        from app.services.engines.comp_relevance import _call_rerank

        with patch("app.services.engines.comp_relevance.settings", openrouter_api_key="test-key"), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock(),
            ))
            mock_client_cls.return_value = mock_client

            result = await _call_rerank(["title1"], "keyword")

        assert result is None


class TestParseResponse:
    def test_valid_array(self):
        assert _parse_response("[1,0,1,0,1]", 5) == [1, 0, 1, 0, 1]

    def test_with_markdown_fences(self):
        assert _parse_response("```json\n[1,0,1]\n```", 3) == [1, 0, 1]

    def test_wrong_length_returns_none(self):
        assert _parse_response("[1,0]", 5) is None

    def test_invalid_json_returns_none(self):
        assert _parse_response("not json", 3) is None

    def test_not_a_list_returns_none(self):
        assert _parse_response('{"key": "value"}', 1) is None

    def test_unexpected_values_returns_none(self):
        assert _parse_response("[1, 2, 0]", 3) is None

    def test_string_values_accepted(self):
        """LLM might return "1" and "0" as strings."""
        assert _parse_response('["1","0","1"]', 3) == [1, 0, 1]

    def test_boolean_values_accepted(self):
        """LLM might return true/false."""
        assert _parse_response("[true, false, true]", 3) == [1, 0, 1]
