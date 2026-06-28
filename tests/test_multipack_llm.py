"""Tests de la rama LLM del extractor de bundle factor — PR-M4."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.marketplace.multipack as mp
from app.services.marketplace.multipack import extract_bundle_factor


def _mock_client(content):
    """Cliente AsyncOpenAI falso que devuelve `content` como respuesta del chat."""
    client = MagicMock()
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )
    client.chat.completions.create = AsyncMock(return_value=resp)
    client.close = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def _clear_cache():
    mp._LLM_CACHE.clear()
    yield
    mp._LLM_CACHE.clear()


class TestExtractBundleFactor:
    async def test_no_signal_returns_one_without_llm(self):
        # Sin señal de pack no toca el LLM (client inyectado nunca se usa).
        client = _mock_client('{"bundle_factor": 99}')
        assert await extract_bundle_factor("Plain Widget", client=client) == 1
        client.chat.completions.create.assert_not_called()

    async def test_unambiguous_uses_regex_not_llm(self):
        client = _mock_client('{"bundle_factor": 99}')
        assert await extract_bundle_factor("Soap (Pack of 12)", client=client) == 12
        client.chat.completions.create.assert_not_called()

    async def test_ambiguous_count_resolved_by_llm(self):
        # "12 Count" es ambiguo → consulta al LLM, que dice 12 (rollos).
        client = _mock_client('{"bundle_factor": 12, "base_unit": "roll"}')
        assert await extract_bundle_factor("Paper Towels, 12 Count", client=client) == 12
        client.chat.completions.create.assert_called_once()

    async def test_ambiguous_count_is_base_unit(self):
        # "3 Count" describe la unidad base → factor 1.
        client = _mock_client('{"bundle_factor": 1, "base_unit": "box"}')
        assert await extract_bundle_factor("Condoms, 3 Count", client=client) == 1

    async def test_llm_failure_returns_none(self):
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=Exception("boom"))
        assert await extract_bundle_factor("K-Cups, 24 Count", client=client) is None

    async def test_malformed_json_returns_none(self):
        client = _mock_client("not json at all")
        assert await extract_bundle_factor("K-Cups, 24 Count", client=client) is None

    async def test_factor_out_of_range_returns_none(self):
        client = _mock_client('{"bundle_factor": 99999}')
        assert await extract_bundle_factor("K-Cups, 24 Count", client=client) is None

    async def test_cache_avoids_second_llm_call(self):
        client = _mock_client('{"bundle_factor": 12}')
        t = "Paper Towels, 12 Count"
        assert await extract_bundle_factor(t, client=client) == 12
        # Segunda llamada: sale del cache, no vuelve a llamar al LLM.
        client.chat.completions.create.reset_mock()
        assert await extract_bundle_factor(t, client=client) == 12
        client.chat.completions.create.assert_not_called()

    async def test_no_llm_available_returns_none(self):
        # client=None y get_llm_client devuelve (None, None) → None sin crash.
        with patch("app.core.llm.get_llm_client", return_value=(None, None)):
            assert await extract_bundle_factor("Towels, 12 Count") is None

    @pytest.mark.parametrize("bad", [None, "", 123])
    async def test_non_string_returns_none(self, bad):
        assert await extract_bundle_factor(bad) is None
