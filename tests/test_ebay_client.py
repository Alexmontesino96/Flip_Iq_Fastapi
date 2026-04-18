"""Tests del cliente eBay (scraper directo + RPi proxies).

Tests unitarios de mapping + mocks del scraper.
"""

from unittest.mock import AsyncMock, patch

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


# ── Tests del EbayClient (mocked scraper) ──────────────────────────

SAMPLE_SCRAPER_DATA = [
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


@pytest.fixture
def ebay_client():
    with patch("app.services.marketplace.ebay.settings") as mock_settings:
        mock_settings.ebay_data_source = "scraper"
        mock_settings.rpi_scraper_urls = ""
        mock_settings.rpi_scraper_api_key = ""
        mock_settings.residential_proxy_url = ""
        client = EbayClient()
    return client


@pytest.mark.asyncio
async def test_get_sold_comps_with_mock(ebay_client):
    with patch(
        "app.services.marketplace.ebay.scrape_sold_listings",
        new_callable=AsyncMock,
        return_value=SAMPLE_SCRAPER_DATA,
    ):
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
async def test_get_sold_comps_scraper_error(ebay_client):
    """Scraper falla → retorna CompsResult vacío."""
    with patch(
        "app.services.marketplace.ebay.scrape_sold_listings",
        new_callable=AsyncMock,
        return_value=None,
    ):
        comps = await ebay_client.get_sold_comps(keyword="test product")

    assert comps.total_sold == 0
    assert comps.marketplace == "ebay"


@pytest.mark.asyncio
async def test_get_sold_comps_scraper_empty(ebay_client):
    """Scraper retorna lista vacía → CompsResult vacío."""
    with patch(
        "app.services.marketplace.ebay.scrape_sold_listings",
        new_callable=AsyncMock,
        return_value=[],
    ):
        comps = await ebay_client.get_sold_comps(keyword="test")

    assert comps.total_sold == 0


@pytest.mark.asyncio
async def test_get_sold_comps_passes_condition_kwarg(ebay_client):
    """Verifica que condition='any' no causa error."""
    with patch(
        "app.services.marketplace.ebay.scrape_sold_listings",
        new_callable=AsyncMock,
        return_value=SAMPLE_SCRAPER_DATA,
    ):
        comps = await ebay_client.get_sold_comps(keyword="test", condition="any")

    assert comps.total_sold == 2
