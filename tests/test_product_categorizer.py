"""Tests para Product Categorizer engine."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.engines.product_categorizer import categorize_product, CategoryResult


def _mock_llm_response(content: str):
    """Crea un mock de respuesta de OpenAI."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestCategorizeProduct:
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

    @pytest.mark.asyncio
    async def test_extracts_phone(self):
        resp_json = json.dumps({
            "product_type": "phone",
            "category": "Smartphone",
            "confidence": 0.9,
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

    @pytest.mark.asyncio
    async def test_handles_markdown_fences(self):
        content = '```json\n{"product_type": "sneakers", "category": "Running Shoes", "confidence": 0.85}\n```'
        mock_client = AsyncMock()
        mock_client.chat.completions.create.return_value = _mock_llm_response(content)

        with patch(
            "app.services.engines.product_categorizer.get_llm_client",
            return_value=(mock_client, "gemini-2.5-flash"),
        ):
            result = await categorize_product("Nike Air Max 90")
            assert result is not None
            assert result.product_type == "sneakers"

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
