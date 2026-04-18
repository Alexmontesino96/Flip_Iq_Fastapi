"""Tests para LLM Comp Relevance Filter."""

from unittest.mock import AsyncMock, MagicMock, patch

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


class TestFilterCompsByRelevance:
    @pytest.mark.asyncio
    async def test_filters_wrong_variant(self):
        """GS keyword + Mens comps → Mens removidos."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 GS Pink", 90.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Grey", 75.0),
            ("Nike Vomero 5 GS Green", 82.0),
        ])

        # Mock LLM: GS=1, Mens=0
        mock_verdicts = [1, 1, 1, 0, 0, 1, 1]

        with patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=mock_verdicts,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5
        for l in result.listings:
            assert "Mens" not in l.title

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

        mock_verdicts = [1, 1, 1, 1, 1]

        with patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=mock_verdicts,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5

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
        mock_verdicts = [1, 0, 0, 0, 0, 0]

        with patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=mock_verdicts,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        # Safety net: mantiene todos
        assert len(result.listings) == 6

    @pytest.mark.asyncio
    async def test_low_sample_relevant_comps_are_used_with_warning(self):
        """Si sobreviven 2-4 comps relevantes, se usan aunque la muestra sea baja."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 GS Pink", 90.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 Mens Grey", 162.0),
        ])

        mock_verdicts = [1, 1, 1, 0, 0, 0]

        with patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=mock_verdicts,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 3
        assert result.diagnostics["relevance_filter"]["low_sample"] is True
        assert any("highly relevant comps" in warning for warning in result.warnings)

    @pytest.mark.asyncio
    async def test_graceful_degradation_no_llm(self):
        """Sin API key → retorna comps sin cambio."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Pink", 90.0),
        ])

        with patch("app.core.llm.has_llm", return_value=False):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5  # Sin cambios

    @pytest.mark.asyncio
    async def test_graceful_degradation_llm_error(self):
        """LLM falla → retorna comps sin cambio."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Pink", 90.0),
        ])

        with patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5  # Sin cambios

    @pytest.mark.asyncio
    async def test_handles_malformed_response(self):
        """LLM retorna basura → no filtra."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Pink", 90.0),
        ])

        # LLM returns None (malformed/unparseable)
        with patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await filter_comps_by_relevance(comps, "Nike Vomero 5 GS")

        assert len(result.listings) == 5  # Sin cambios

    @pytest.mark.asyncio
    async def test_handles_wrong_length_response(self):
        """LLM retorna array de tamaño incorrecto → no filtra."""
        comps = _make_comps([
            ("Nike Vomero 5 GS Black", 80.0),
            ("Nike Vomero 5 Mens Blue", 160.0),
            ("Nike Vomero 5 GS White", 85.0),
            ("Nike Vomero 5 Mens Red", 170.0),
            ("Nike Vomero 5 GS Pink", 90.0),
        ])

        # Array más corto que listings
        mock_verdicts = [1, 0, 1]

        with patch(
            "app.services.engines.comp_relevance._call_llm",
            new_callable=AsyncMock,
            return_value=mock_verdicts,
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
