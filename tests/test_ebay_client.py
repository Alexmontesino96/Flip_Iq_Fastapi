"""Tests del cliente eBay con Apify (caffein.dev~ebay-sold-listings).

Tests unitarios de mapping + mocks de httpx + test de integración.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.marketplace.ebay import (
    EbayClient,
    _map_listing,
    _parse_datetime,
    _parse_float,
)


# ── Tests de utilidades (sin red) ────────────────────────────────────


class TestParseFloat:
    def test_normal(self):
        assert _parse_float("123.45") == 123.45

    def test_string_with_commas(self):
        assert _parse_float("1,234.56") == 1234.56

    def test_integer(self):
        assert _parse_float(100) == 100.0

    def test_none(self):
        assert _parse_float(None) == 0.0

    def test_empty_string(self):
        assert _parse_float("") == 0.0

    def test_invalid(self):
        assert _parse_float("abc") == 0.0


class TestParseDatetime:
    def test_iso_format(self):
        result = _parse_datetime("2026-04-12T00:00:00.000Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 12

    def test_none(self):
        assert _parse_datetime(None) is None

    def test_empty(self):
        assert _parse_datetime("") is None

    def test_invalid(self):
        assert _parse_datetime("not a date") is None


class TestMapListing:
    def test_valid_item(self):
        item = {
            "itemId": "123456789",
            "title": "iPhone 15 Pro 256GB",
            "soldPrice": "899.99",
            "shippingPrice": "12.50",
            "totalPrice": "912.49",
            "url": "https://www.ebay.com/itm/123456789",
            "endedAt": "2026-04-10T00:00:00.000Z",
            "sellerUsername": "top_seller",
            "sellerFeedbackPercent": 99.5,
            "condition": "Pre-Owned",
            "bids": 7,
        }
        listing = _map_listing(item)
        assert listing is not None
        assert listing.title == "iPhone 15 Pro 256GB"
        assert listing.price == 899.99
        assert listing.shipping_price == 12.50
        assert listing.total_price == 912.49
        assert listing.item_id == "123456789"
        assert listing.seller_username == "top_seller"
        assert listing.seller_feedback_pct == 99.5
        assert listing.bids == 7
        assert listing.sold is True
        assert listing.marketplace == "ebay"

    def test_missing_title_returns_none(self):
        assert _map_listing({"soldPrice": "100"}) is None
        assert _map_listing({"title": "", "soldPrice": "100"}) is None

    def test_zero_price_returns_none(self):
        assert _map_listing({"title": "Test", "soldPrice": "0"}) is None
        assert _map_listing({"title": "Test"}) is None

    def test_free_shipping(self):
        item = {
            "title": "AirPods Pro",
            "soldPrice": "149.00",
            "shippingPrice": "0",
            "totalPrice": "149.00",
        }
        listing = _map_listing(item)
        assert listing is not None
        assert listing.shipping_price == 0.0
        assert listing.total_price == 149.0

    def test_total_price_fallback(self):
        """Si totalPrice es 0 o falta, recalcula como soldPrice + shipping."""
        item = {
            "title": "Test Product",
            "soldPrice": "100.00",
            "shippingPrice": "10.00",
            "totalPrice": "0",
        }
        listing = _map_listing(item)
        assert listing is not None
        assert listing.total_price == 110.0


# ── Tests con mock de httpx (sin red) ──────────────────────────────


SAMPLE_APIFY_RESPONSE = [
    {
        "itemId": "123456789",
        "title": "iPhone 15 Pro 256GB Unlocked",
        "soldPrice": "899.99",
        "shippingPrice": "12.50",
        "totalPrice": "912.49",
        "url": "https://www.ebay.com/itm/123456789",
        "endedAt": "2026-04-10T00:00:00.000Z",
        "sellerUsername": "top_seller",
        "sellerFeedbackPercent": 99.5,
    },
    {
        "itemId": "987654321",
        "title": "AirPods Pro 2nd Gen",
        "soldPrice": "149.00",
        "shippingPrice": "0",
        "totalPrice": "149.00",
        "url": "https://www.ebay.com/itm/987654321",
        "endedAt": "2026-03-25T00:00:00.000Z",
        "sellerUsername": "audio_shop",
        "bids": 7,
    },
]


def _mock_httpx_response(data, status_code=200):
    """Crea un mock de httpx.Response (métodos sync como en httpx real)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = str(data)
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def ebay_client():
    with patch("app.services.marketplace.ebay.settings") as mock_settings:
        mock_settings.apify_token = "test_token"
        client = EbayClient()
    return client


@pytest.mark.asyncio
async def test_get_sold_comps_with_mock(ebay_client):
    mock_resp = _mock_httpx_response(SAMPLE_APIFY_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        comps = await ebay_client.get_sold_comps(keyword="iphone 15 pro", days=30, limit=50)

    assert comps.total_sold == 2
    assert comps.marketplace == "ebay"
    assert comps.avg_price > 0
    assert comps.listings[0].title == "iPhone 15 Pro 256GB Unlocked"
    assert comps.listings[0].shipping_price == 12.50
    assert comps.listings[1].title == "AirPods Pro 2nd Gen"
    assert comps.listings[1].bids == 7


@pytest.mark.asyncio
async def test_get_sold_comps_empty_keyword(ebay_client):
    comps = await ebay_client.get_sold_comps(keyword=None, barcode=None)
    assert comps.total_sold == 0
    assert comps.marketplace == "ebay"


@pytest.mark.asyncio
async def test_get_sold_comps_apify_error(ebay_client):
    """Apify devuelve error HTTP → retorna CompsResult vacío."""
    import httpx as _httpx

    mock_request = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
        "500", request=mock_request, response=mock_resp
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        comps = await ebay_client.get_sold_comps(keyword="test product")

    assert comps.total_sold == 0
    assert comps.marketplace == "ebay"


@pytest.mark.asyncio
async def test_get_sold_comps_timeout(ebay_client):
    """Timeout de Apify → retorna CompsResult vacío."""
    import httpx as _httpx

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_httpx.TimeoutException("timeout"))
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        comps = await ebay_client.get_sold_comps(keyword="test")

    assert comps.total_sold == 0


@pytest.mark.asyncio
async def test_get_sold_comps_no_token():
    """Sin APIFY_TOKEN → retorna CompsResult vacío."""
    with patch("app.services.marketplace.ebay.settings") as mock_settings:
        mock_settings.apify_token = ""
        client = EbayClient()

    comps = await client.get_sold_comps(keyword="test")
    assert comps.total_sold == 0


@pytest.mark.asyncio
async def test_get_sold_comps_passes_condition_kwarg(ebay_client):
    """Verifica que condition='any' no causa error (compatibilidad)."""
    mock_resp = _mock_httpx_response(SAMPLE_APIFY_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        comps = await ebay_client.get_sold_comps(keyword="test", condition="any")

    assert comps.total_sold == 2


@pytest.mark.asyncio
async def test_apify_request_uses_current_actor_input_schema(ebay_client):
    """El actor actual usa count/daysToScrape/itemCondition, no maxItems/condition."""
    ebay_client._data_source = "apify"
    ebay_client._token = "test_token"
    mock_resp = _mock_httpx_response(SAMPLE_APIFY_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        comps = await ebay_client.get_sold_comps(
            keyword="roller blades",
            days=45,
            limit=20,
            condition="new",
            category_id=888,
        )

    assert comps.total_sold == 2
    body = mock_client.post.call_args.kwargs["json"]
    assert body["keyword"] == "roller blades"
    assert body["count"] == 20
    assert body["daysToScrape"] == 45
    assert body["categoryId"] == "888"
    assert body["itemLocation"] == "domestic"
    assert body["itemCondition"] == "new"
    assert body["currencyMode"] == "USD"
    assert "maxItems" not in body
    assert "condition" not in body
    params = mock_client.post.call_args.kwargs["params"]
    assert params["maxItems"] == "20"
    assert "maxTotalChargeUsd" not in params


@pytest.mark.asyncio
async def test_apify_billing_limit_has_specific_error_reason(ebay_client):
    """HTTP 402 de Apify no debe verse como CAPTCHA genérico."""
    import httpx as _httpx

    ebay_client._data_source = "apify"
    ebay_client._token = "test_token"
    mock_request = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 402
    mock_resp.text = "not-enough-usage-to-run-paid-actor"
    mock_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
        "402", request=mock_request, response=mock_resp
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        comps = await ebay_client.get_sold_comps(keyword="roller blades", limit=20)

    assert comps.total_sold == 0
    assert comps.scrape_source == "apify"
    assert comps.scrape_status == "blocked"
    assert comps.error_reason == "billing_limit"
    assert any("usage credits" in warning for warning in comps.warnings)


# ── Test de integración (requiere APIFY_TOKEN en .env) ──────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integration_real_apify():
    """Validar integración real con Apify.

    Ejecutar manualmente: APIFY_TOKEN=... pytest tests/test_ebay_client.py -v -m integration
    """
    client = EbayClient()
    comps = await client.get_sold_comps(keyword="iphone 15 pro", days=30, limit=5)
    if comps.error_reason in ("billing_limit", "missing_token"):
        pytest.skip(f"Apify integration unavailable: {comps.error_reason}")
    assert comps.total_sold > 0
    assert comps.avg_price > 0
    for listing in comps.listings:
        assert listing.price > 0
        assert listing.sold is True
        assert listing.title
