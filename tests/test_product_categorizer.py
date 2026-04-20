"""Tests para Product Categorizer engine."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.engines.product_categorizer import (
    categorize_product,
    CategoryResult,
    _extract_product_type,
)
from app.services.marketplace.ebay_taxonomy import CategorySuggestion


def _mock_llm_response(content: str):
    """Crea un mock de respuesta de OpenAI."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestTaxonomyStrategy:
    """Tests for eBay Taxonomy API as primary categorization strategy."""

    @pytest.mark.asyncio
    async def test_uses_taxonomy_api_first(self):
        suggestions = [
            CategorySuggestion(
                category_id=9355,
                category_name="Cell Phones & Smartphones",
                parent_path=["Root", "Cell Phones & Accessories"],
            ),
        ]
        with patch(
            "app.services.engines.product_categorizer.get_category_suggestions",
            return_value=suggestions,
        ):
            result = await categorize_product("iPhone 15 Pro Max")

        assert result is not None
        assert result.ebay_category_id == 9355
        assert result.confidence == 0.9  # single suggestion = high confidence
        assert "Cell Phones & Smartphones" in result.category

    @pytest.mark.asyncio
    async def test_taxonomy_multiple_suggestions_lower_confidence(self):
        suggestions = [
            CategorySuggestion(category_id=9355, category_name="Smartphones", parent_path=[]),
            CategorySuggestion(category_id=42428, category_name="Cases", parent_path=[]),
        ]
        with patch(
            "app.services.engines.product_categorizer.get_category_suggestions",
            return_value=suggestions,
        ):
            result = await categorize_product("iPhone 15")

        assert result is not None
        assert result.ebay_category_id == 9355
        assert result.confidence == 0.8  # multiple = slightly lower

    @pytest.mark.asyncio
    async def test_falls_back_to_llm_when_taxonomy_fails(self):
        resp_json = json.dumps({
            "product_type": "helmet",
            "category": "Cycling Helmet",
            "confidence": 0.95,
            "ebay_category_id": None,
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(resp_json)

        with patch(
            "app.services.engines.product_categorizer.get_category_suggestions",
            return_value=[],  # Taxonomy returns nothing
        ), patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Oakley Aro3 Helmet")

        assert result is not None
        assert result.product_type == "helmet"

    @pytest.mark.asyncio
    async def test_falls_back_to_llm_when_taxonomy_errors(self):
        resp_json = json.dumps({
            "product_type": "console",
            "category": "Video Game Console",
            "confidence": 0.9,
            "ebay_category_id": 139971,
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(resp_json)

        with patch(
            "app.services.engines.product_categorizer.get_category_suggestions",
            side_effect=RuntimeError("network error"),
        ), patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Nintendo Switch")

        assert result is not None
        assert result.ebay_category_id == 139971


class TestExtractProductType:
    def test_basic_keyword(self):
        # "90" is a number, "max" is in skip list, "air" is valid
        assert _extract_product_type("Nike Air Max 90") == "air"

    def test_skips_colors(self):
        assert _extract_product_type("iPhone 15 Pro Black") != "black"

    def test_single_word(self):
        assert _extract_product_type("headphones") == "headphones"

    def test_skips_size(self):
        assert _extract_product_type("Nike Shirt XL") != "xl"


class TestCategorizeProductLLM:
    """Tests for LLM fallback path (taxonomy disabled)."""

    @pytest.fixture(autouse=True)
    def _disable_taxonomy(self):
        """Disable Taxonomy API so tests hit the LLM path."""
        with patch(
            "app.services.engines.product_categorizer.get_category_suggestions",
            return_value=[],
        ):
            yield

    @pytest.mark.asyncio
    async def test_returns_none_when_no_llm(self):
        with patch("app.services.engines.product_categorizer.get_llm_client", return_value=(None, None)):
            result = await categorize_product("Oakley Aro3 Helmet")
            assert result is None

    @pytest.mark.asyncio
    async def test_extracts_helmet(self):
        resp_json = json.dumps({
            "product_type": "helmet",
            "category": "Cycling Helmet",
            "confidence": 0.95,
            "ebay_category_id": None,
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(resp_json)

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Oakley Aro3 MIPS Helmet")
            assert result is not None
            assert result.product_type == "helmet"
            assert result.category == "Cycling Helmet"
            assert result.confidence == 0.95
            assert result.ebay_category_id is None

    @pytest.mark.asyncio
    async def test_extracts_phone_with_ebay_category(self):
        resp_json = json.dumps({
            "product_type": "phone",
            "category": "Smartphone",
            "confidence": 0.9,
            "ebay_category_id": 9355,
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(resp_json)

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("iPhone 15 Pro Max")
            assert result is not None
            assert result.product_type == "phone"
            assert result.ebay_category_id == 9355

    @pytest.mark.asyncio
    async def test_extracts_console_category(self):
        resp_json = json.dumps({
            "product_type": "console",
            "category": "Video Game Console",
            "confidence": 0.95,
            "ebay_category_id": 139971,
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(resp_json)

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Nintendo Switch OLED")
            assert result is not None
            assert result.product_type == "console"
            assert result.ebay_category_id == 139971

    @pytest.mark.asyncio
    async def test_invalid_ebay_category_id_ignored(self):
        resp_json = json.dumps({
            "product_type": "phone",
            "category": "Smartphone",
            "confidence": 0.9,
            "ebay_category_id": 999999,
        })
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(resp_json)

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("iPhone 15 Pro Max")
            assert result is not None
            assert result.ebay_category_id is None

    @pytest.mark.asyncio
    async def test_handles_markdown_fences(self):
        content = '```json\n{"product_type": "sneakers", "category": "Running Shoes", "confidence": 0.85, "ebay_category_id": 15709}\n```'
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(content)

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Nike Air Max 90")
            assert result is not None
            assert result.product_type == "sneakers"
            assert result.ebay_category_id == 15709

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response("not json")

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Something")
            assert result is None

    @pytest.mark.asyncio
    async def test_handles_empty_product_type(self):
        resp_json = json.dumps({"product_type": "", "category": "", "confidence": 0.0})
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(resp_json)

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("???")
            assert result is None

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("timeout")

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Test")
            assert result is None
