"""Tests for eBay Browse API client."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.marketplace.ebay_browse import (
    SearchSuggestion,
    _token_cache,
    search_keywords,
)


@pytest.fixture(autouse=True)
def _reset_token_cache():
    """Reset token cache between tests."""
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0
    yield
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


FAKE_TOKEN_RESPONSE = {
    "access_token": "v^1.1#fake-token",
    "expires_in": 7200,
    "token_type": "Application Access Token",
}

FAKE_SEARCH_RESPONSE = {
    "itemSummaries": [
        {
            "title": "Apple iPhone 14 Pro Max 256GB",
            "price": {"value": "899.99", "currency": "USD"},
            "image": {"imageUrl": "https://i.ebayimg.com/images/g/1.jpg"},
            "condition": "New",
            "itemId": "v1|123456|0",
            "itemWebUrl": "https://www.ebay.com/itm/123456",
            "categories": [{"categoryId": "9355", "categoryName": "Cell Phones"}],
            "itemAspects": [{"name": "Brand", "value": "Apple"}],
            "epid": "27060148964",
        },
        {
            "title": "iPhone 14 Pro Case Clear",
            "price": {"value": "12.99", "currency": "USD"},
            "image": {"imageUrl": "https://i.ebayimg.com/images/g/2.jpg"},
            "condition": "New",
            "itemId": "v1|789012|0",
            "categories": [],
            "itemAspects": [],
        },
    ]
}


def _make_response(status_code: int, json_data: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.sandbox.ebay.com/test"),
    )


@pytest.mark.asyncio
@patch("app.services.marketplace.ebay_browse.settings")
async def test_search_keywords_returns_suggestions(mock_settings):
    mock_settings.ebay_app_id = "test-app-id"
    mock_settings.ebay_cert_id = "test-cert-id"
    mock_settings.ebay_sandbox = True

    token_resp = _make_response(200, FAKE_TOKEN_RESPONSE)
    search_resp = _make_response(200, FAKE_SEARCH_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=token_resp)
        client_instance.get = AsyncMock(return_value=search_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await search_keywords("iphone 14 pro", limit=10)

    assert len(results) == 2
    assert results[0].title == "Apple iPhone 14 Pro Max 256GB"
    assert results[0].price == 899.99
    assert results[0].brand == "Apple"
    assert results[0].epid == "27060148964"
    assert results[0].category == "Cell Phones"
    assert results[1].brand is None
    assert results[1].category is None


@pytest.mark.asyncio
@patch("app.services.marketplace.ebay_browse.settings")
async def test_search_keywords_no_credentials(mock_settings):
    mock_settings.ebay_app_id = ""
    mock_settings.ebay_cert_id = ""

    results = await search_keywords("test query")
    assert results == []


@pytest.mark.asyncio
@patch("app.services.marketplace.ebay_browse.settings")
async def test_search_keywords_empty_results(mock_settings):
    mock_settings.ebay_app_id = "test-app-id"
    mock_settings.ebay_cert_id = "test-cert-id"
    mock_settings.ebay_sandbox = True

    token_resp = _make_response(200, FAKE_TOKEN_RESPONSE)
    empty_resp = _make_response(200, {"total": 0})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.post = AsyncMock(return_value=token_resp)
        client_instance.get = AsyncMock(return_value=empty_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await search_keywords("xyznonexistent", limit=5)

    assert results == []
