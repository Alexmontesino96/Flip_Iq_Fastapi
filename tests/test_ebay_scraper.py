"""Tests para el scraper directo de eBay sold listings."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from curl_cffi.requests.exceptions import HTTPError, Timeout

from app.services.marketplace.ebay_scraper import (
    _build_search_query,
    _EBAY_CONDITION_IDS,
    _extract_item_id,
    _extract_seller,
    _parse_bids,
    _parse_price,
    _parse_shipping,
    _parse_sold_date,
    parse_sold_listings,
    scrape_sold_listings,
)


# ── HTML fixtures ──

# Layout actual (2025-2026): li.s-card con su-styled-text
EBAY_SCARD_HTML_FIXTURE = """
<html>
<body>
<div class="srp-river-results">
  <ul class="srp-results srp-list clearfix">

    <!-- Placeholder "Shop on eBay" — debe ser ignorado -->
    <li class="s-card s-card--horizontal" data-listingid="000000000">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <div class="s-card__caption">
              <span class="su-styled-text positive default">Sold  Apr 14, 2026</span>
            </div>
            <a class="s-card__link image-treatment" href="https://www.ebay.com/itm/000000000"></a>
            <a class="s-card__link" href="https://www.ebay.com/itm/000000000">
              <span class="su-styled-text primary default">Shop on eBay</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text secondary default">Brand New</span>
            <span class="su-styled-text positive bold large-1 s-card__price">$20.00</span>
          </div>
        </div>
      </div>
    </li>

    <!-- Item 1: compra directa con envío gratis y seller -->
    <li class="s-card s-card--horizontal" data-listingid="123456789">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <div class="s-card__caption">
              <span class="su-styled-text positive default">Sold  Apr 10, 2026</span>
            </div>
            <a class="s-card__link image-treatment" href="https://www.ebay.com/itm/123456789"></a>
            <a class="s-card__link" href="https://www.ebay.com/itm/123456789?hash=item1">
              <span class="su-styled-text primary default">Apple iPhone 15 Pro 256GB Natural Titanium Unlocked</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text secondary default">Pre-Owned</span>
            <span class="su-styled-text positive bold large-1 s-card__price">$899.99</span>
            <span class="su-styled-text secondary large">Free delivery</span>
            <span class="su-styled-text primary large">tech_deals_99</span>
            <span class="su-styled-text primary large">99.8% positive (2345)</span>
          </div>
        </div>
      </div>
    </li>

    <!-- Item 2: subasta con envío y bids -->
    <li class="s-card s-card--horizontal" data-listingid="987654321">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <div class="s-card__caption">
              <span class="su-styled-text positive default">Sold  Apr 8, 2026</span>
            </div>
            <a class="s-card__link image-treatment" href="https://www.ebay.com/itm/987654321"></a>
            <a class="s-card__link" href="https://www.ebay.com/itm/987654321?hash=item2">
              <span class="su-styled-text primary default">iPhone 15 Pro Max 512GB Blue Titanium - Excellent</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text secondary default">Brand New</span>
            <span class="su-styled-text positive strikethrough large-1 s-card__price">$1,149.00</span>
            <span class="su-styled-text secondary large">+$12.50 delivery</span>
            <span class="su-styled-text secondary large">23 bids</span>
            <span class="su-styled-text primary large">auction_king</span>
            <span class="su-styled-text primary large">98.5% positive (1.2K)</span>
          </div>
        </div>
      </div>
    </li>

    <!-- Item 3: minimal — sin seller, sin fecha, sin bids, "with coupon" trap -->
    <li class="s-card s-card--horizontal" data-listingid="555555555">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <a class="s-card__link image-treatment" href="https://www.ebay.com/itm/555555555"></a>
            <a class="s-card__link" href="https://www.ebay.com/itm/555555555?hash=item3">
              <span class="su-styled-text primary default">iPhone 15 Pro Case Clear</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text positive bold large-1 s-card__price">$12.99</span>
            <span class="su-styled-text secondary large">+$3.49 delivery</span>
            <span class="su-styled-text primary large">with coupon</span>
          </div>
        </div>
      </div>
    </li>

    <!-- Item 4: sin precio (debe ser ignorado) -->
    <li class="s-card s-card--horizontal" data-listingid="000000001">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <a class="s-card__link" href="https://www.ebay.com/itm/000000001">
              <span class="su-styled-text primary default">Some Listing Without Price</span>
            </a>
          </div>
        </div>
      </div>
    </li>

    <!-- Item 5: seller solo en .default con feedback -->
    <li class="s-card s-card--horizontal" data-listingid="666666666">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <div class="s-card__caption">
              <span class="su-styled-text positive default">Sold  Apr 12, 2026</span>
            </div>
            <a class="s-card__link image-treatment" href="https://www.ebay.com/itm/666666666"></a>
            <a class="s-card__link" href="https://www.ebay.com/itm/666666666">
              <span class="su-styled-text primary default">Nike Air Max 90 Size 10</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text secondary default">Pre-Owned</span>
            <span class="su-styled-text positive bold large-1 s-card__price">$75.00</span>
            <span class="su-styled-text secondary large">Free delivery</span>
            <span class="su-styled-text default">kicks_hub  99.1% positive (500)</span>
          </div>
        </div>
      </div>
    </li>

  </ul>
</div>
</body>
</html>
"""

# Layout legacy (pre-2025): li.s-item
EBAY_SITEM_HTML_FIXTURE = """
<html>
<body>
<div class="srp-results">
  <ul class="srp-results srp-list clearfix">

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/000000000">
            <h3 class="s-item__title">Shop on eBay</h3>
          </a>
        </div>
      </div>
    </li>

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/123456789?hash=item1">
            <h3 class="s-item__title">Apple iPhone 15 Pro 256GB Natural Titanium Unlocked</h3>
          </a>
          <div class="s-item__subtitle">
            <span class="SECONDARY_INFO">Pre-Owned</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__price">$899.99</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__logisticsCost">Free shipping</span>
          </div>
          <div class="s-item__detail">
            <span class="POSITIVE">Sold  Apr 10, 2026</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__seller-info-text">tech_deals_99 (2345) 99.8%</span>
          </div>
        </div>
      </div>
    </li>

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/987654321?hash=item2">
            <h3 class="s-item__title">iPhone 15 Pro Max 512GB Blue Titanium - Excellent</h3>
          </a>
          <div class="s-item__subtitle">
            <span class="SECONDARY_INFO">Brand New</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__price">$1,149.00</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__logisticsCost">+$12.50 shipping</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__bidCount">23 bids</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__ended-date">Sold  Apr 8, 2026</span>
          </div>
        </div>
      </div>
    </li>

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/555555555?hash=item3">
            <h3 class="s-item__title">iPhone 15 Pro Case Clear</h3>
          </a>
          <div class="s-item__detail">
            <span class="s-item__price">$12.99</span>
          </div>
          <div class="s-item__detail">
            <span class="s-item__logisticsCost">+$3.49 shipping</span>
          </div>
        </div>
      </div>
    </li>

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/000000001">
            <h3 class="s-item__title">Some Listing Without Price</h3>
          </a>
        </div>
      </div>
    </li>

  </ul>
</div>
</body>
</html>
"""

# Default fixture uses the current s-card layout
EBAY_HTML_FIXTURE = EBAY_SCARD_HTML_FIXTURE


# ── Tests de funciones de parsing individuales ──


class TestParsePrice:
    def test_normal_price(self):
        assert _parse_price("$208.48") == 208.48

    def test_price_with_commas(self):
        assert _parse_price("$1,299.99") == 1299.99

    def test_price_no_dollar(self):
        assert _parse_price("208.48") == 208.48

    def test_price_none(self):
        assert _parse_price(None) == 0.0

    def test_price_empty(self):
        assert _parse_price("") == 0.0

    def test_price_no_match(self):
        assert _parse_price("no price here") == 0.0

    def test_price_integer(self):
        assert _parse_price("$50") == 50.0


class TestParseShipping:
    def test_free_shipping(self):
        assert _parse_shipping("Free shipping") == 0.0

    def test_free_uppercase(self):
        assert _parse_shipping("FREE SHIPPING") == 0.0

    def test_paid_shipping(self):
        assert _parse_shipping("+$12.50 shipping") == 12.50

    def test_none(self):
        assert _parse_shipping(None) == 0.0

    def test_empty(self):
        assert _parse_shipping("") == 0.0


class TestParseBids:
    def test_bids(self):
        assert _parse_bids("23 bids") == 23

    def test_one_bid(self):
        assert _parse_bids("1 bid") == 1

    def test_none(self):
        assert _parse_bids(None) is None

    def test_no_match(self):
        assert _parse_bids("Buy It Now") is None


class TestParseSoldDate:
    def test_standard_format(self):
        result = _parse_sold_date("Sold  Apr 10, 2026")
        assert result is not None
        assert "2026-04-10" in result

    def test_no_comma(self):
        result = _parse_sold_date("Sold  Apr 10 2026")
        assert result is not None
        assert "2026-04-10" in result

    def test_none(self):
        assert _parse_sold_date(None) is None

    def test_empty(self):
        assert _parse_sold_date("") is None

    def test_no_sold_prefix(self):
        result = _parse_sold_date("Apr 10, 2026")
        assert result is not None
        assert "2026-04-10" in result


class TestExtractItemId:
    def test_normal_url(self):
        assert _extract_item_id("https://www.ebay.com/itm/123456789?hash=item1") == "123456789"

    def test_none(self):
        assert _extract_item_id(None) is None

    def test_no_match(self):
        assert _extract_item_id("https://www.ebay.com/sch/i.html") is None


class TestExtractSeller:
    def test_seller_with_feedback(self):
        assert _extract_seller("tech_deals_99 (2345) 99.8%") == "tech_deals_99"

    def test_simple_seller(self):
        assert _extract_seller("my-seller.name") == "my-seller.name"

    def test_none(self):
        assert _extract_seller(None) is None


# ── Tests de parsing completo de HTML ──


class TestParseSoldListingsSCard:
    """Tests para el layout actual s-card (2025-2026)."""

    def test_parses_correct_number_of_items(self):
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        # 6 items: placeholder(ignorado) + 4 válidos + 1 sin precio(ignorado)
        assert len(results) == 4

    def test_item_1_fields(self):
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        item = results[0]
        assert item["title"] == "Apple iPhone 15 Pro 256GB Natural Titanium Unlocked"
        assert item["soldPrice"] == "899.99"
        assert item["shippingPrice"] == "0.0"
        assert item["totalPrice"] == "899.99"
        assert item["condition"] == "Pre-Owned"
        assert "2026-04-10" in item["endedAt"]
        assert item["sellerUsername"] == "tech_deals_99"
        assert item["itemId"] == "123456789"
        assert item["bids"] is None

    def test_item_2_with_bids_and_shipping(self):
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        item = results[1]
        assert item["title"] == "iPhone 15 Pro Max 512GB Blue Titanium - Excellent"
        assert item["soldPrice"] == "1149.0"
        assert item["shippingPrice"] == "12.5"
        assert item["totalPrice"] == "1161.5"
        assert item["condition"] == "Brand New"
        assert item["bids"] == 23
        assert item["itemId"] == "987654321"
        assert "2026-04-08" in item["endedAt"]
        assert item["sellerUsername"] == "auction_king"

    def test_item_3_minimal_with_coupon_trap(self):
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        item = results[2]
        assert item["title"] == "iPhone 15 Pro Case Clear"
        assert item["soldPrice"] == "12.99"
        assert item["shippingPrice"] == "3.49"
        assert item["totalPrice"] == "16.48"
        assert item["condition"] is None
        assert item["endedAt"] is None
        # "with coupon" debe NO ser extraído como seller
        assert item["sellerUsername"] is None
        assert item["bids"] is None

    def test_item_seller_from_default_span(self):
        """Seller extraído del formato 'username  99.1% positive (500)' en .default."""
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        item = results[3]
        assert item["title"] == "Nike Air Max 90 Size 10"
        assert item["sellerUsername"] == "kicks_hub"

    def test_skips_placeholder(self):
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        titles = [r["title"] for r in results]
        assert not any("Shop on eBay" in t for t in titles)

    def test_skips_item_without_price(self):
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        titles = [r["title"] for r in results]
        assert "Some Listing Without Price" not in titles

    def test_data_listingid_used_as_item_id(self):
        results = parse_sold_listings(EBAY_SCARD_HTML_FIXTURE)
        assert results[0]["itemId"] == "123456789"


class TestParseSoldListingsSItem:
    """Tests para el layout legacy s-item (pre-2025)."""

    def test_parses_correct_number_of_items(self):
        results = parse_sold_listings(EBAY_SITEM_HTML_FIXTURE)
        assert len(results) == 3

    def test_item_1_fields(self):
        results = parse_sold_listings(EBAY_SITEM_HTML_FIXTURE)
        item = results[0]
        assert item["title"] == "Apple iPhone 15 Pro 256GB Natural Titanium Unlocked"
        assert item["soldPrice"] == "899.99"
        assert item["condition"] == "Pre-Owned"
        assert "2026-04-10" in item["endedAt"]
        assert item["sellerUsername"] == "tech_deals_99"

    def test_skips_placeholder(self):
        results = parse_sold_listings(EBAY_SITEM_HTML_FIXTURE)
        titles = [r["title"] for r in results]
        assert not any("Shop on eBay" in t for t in titles)


class TestParseSoldListingsGeneral:
    def test_empty_html(self):
        assert parse_sold_listings("") == []
        assert parse_sold_listings("<html><body></body></html>") == []


# ── Tests de scrape_sold_listings (con mock de httpx) ──


class TestScrapeSoldListings:
    @pytest.mark.asyncio
    async def test_successful_scrape(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await scrape_sold_listings("iPhone 15 Pro", limit=50)

        assert len(results) == 4
        assert results[0]["title"] == "Apple iPhone 15 Pro 256GB Natural Titanium Unlocked"

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await scrape_sold_listings("iPhone 15 Pro", limit=2)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status = MagicMock(
            side_effect=HTTPError("429 Too Many Requests")
        )

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(HTTPError):
                await scrape_sold_listings("iPhone 15 Pro")

    @pytest.mark.asyncio
    async def test_timeout_propagates(self):
        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Timeout("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(Timeout):
                await scrape_sold_listings("iPhone 15 Pro")

    @pytest.mark.asyncio
    async def test_empty_results_no_pagination(self):
        """Si la primera página no tiene resultados, no pagina."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><ul class='srp-results'></ul></body></html>"
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            results = await scrape_sold_listings("nonexistent product xyz")

        assert results == []
        # 2 llamadas: warmup (homepage) + 1 página de búsqueda (no paginó más)
        assert mock_client.get.call_count == 2


# ── Tests del fallback en EbayClient ──


class TestEbayClientFallback:
    @pytest.mark.asyncio
    async def test_scraper_failure_falls_back_to_apify(self):
        """Si el scraper falla, debe usar Apify como fallback."""
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "scraper"
        client._token = "fake-token"

        apify_data = [
            {
                "title": "iPhone 15 Pro from Apify",
                "soldPrice": "800.00",
                "shippingPrice": "0",
                "totalPrice": "800.00",
                "endedAt": "2026-04-10T00:00:00.000Z",
                "condition": "Pre-Owned",
                "url": "https://www.ebay.com/itm/111111111",
                "itemId": "111111111",
            }
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            side_effect=HTTPError("429 Too Many Requests"),
        ), patch.object(
            client, "_fetch_via_apify", new_callable=AsyncMock, return_value=apify_data,
        ):
            result = await client.get_sold_comps(keyword="iPhone 15 Pro")

        assert result.total_sold == 1
        assert result.listings[0].title == "iPhone 15 Pro from Apify"
        assert result.scrape_source == "apify"
        assert result.scrape_status == "ok"
        assert result.fallback_used is True
        assert result.query_used == "iPhone 15 Pro"

    @pytest.mark.asyncio
    async def test_apify_mode_skips_scraper(self):
        """Con data_source=apify, debe ir directo a Apify sin intentar scraper."""
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "apify"
        client._token = "fake-token"

        apify_data = [
            {
                "title": "iPhone from Apify",
                "soldPrice": "750.00",
                "totalPrice": "750.00",
            }
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
        ) as mock_scraper, patch.object(
            client, "_fetch_via_apify", new_callable=AsyncMock, return_value=apify_data,
        ):
            result = await client.get_sold_comps(keyword="iPhone")

        mock_scraper.assert_not_called()
        assert result.total_sold == 1

    @pytest.mark.asyncio
    async def test_scraper_success_no_apify_call(self):
        """Si el scraper funciona, no debe llamar a Apify."""
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "scraper"
        client._token = "fake-token"

        scraper_data = [
            {
                "title": "iPhone from Scraper",
                "soldPrice": "800.00",
                "shippingPrice": "0",
                "totalPrice": "800.00",
                "endedAt": "2026-04-10T00:00:00.000Z",
                "condition": "Pre-Owned",
            }
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            new_callable=AsyncMock,
            return_value=scraper_data,
        ), patch.object(
            client, "_fetch_via_apify", new_callable=AsyncMock,
        ) as mock_apify:
            result = await client.get_sold_comps(keyword="iPhone")

        mock_apify.assert_not_called()
        assert result.total_sold == 1
        assert result.listings[0].title == "iPhone from Scraper"

    @pytest.mark.asyncio
    async def test_no_query_returns_empty(self):
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        result = await client.get_sold_comps()
        assert result.total_sold == 0
        assert result.listings == []
        assert result.scrape_status == "empty"
        assert result.error_reason == "missing_query"

    @pytest.mark.asyncio
    async def test_scraper_empty_result_has_metadata(self):
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "scraper"
        client._token = None

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await client.get_sold_comps(keyword="missing product")

        assert result.total_sold == 0
        assert result.scrape_source == "scraper"
        assert result.scrape_status == "empty"
        assert result.diagnostics["attempts"][0]["source"] == "scraper"
        assert result.diagnostics["attempts"][0]["status"] == "empty"

    @pytest.mark.asyncio
    async def test_scraper_blocked_fallback_metadata(self):
        from app.services.marketplace.ebay import EbayClient
        from app.services.marketplace.ebay_scraper import EbayScraperBlocked

        client = EbayClient()
        client._data_source = "scraper"
        client._token = "fake-token"

        apify_data = [
            {"title": "Fallback item", "soldPrice": "50.00", "totalPrice": "50.00"}
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            side_effect=EbayScraperBlocked("challenge"),
        ), patch.object(
            client, "_fetch_via_apify", new_callable=AsyncMock, return_value=apify_data,
        ):
            result = await client.get_sold_comps(keyword="blocked query")

        assert result.scrape_source == "apify"
        assert result.fallback_used is True
        assert result.diagnostics["attempts"][0]["status"] == "blocked"
        assert any("fallback" in warning.lower() for warning in result.warnings)

    @pytest.mark.asyncio
    async def test_condition_propagated_to_scraper(self):
        """get_sold_comps(condition=X) pasa condition a scrape_sold_listings."""
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "scraper"
        client._token = None

        scraper_data = [
            {"title": f"iPhone {i}", "soldPrice": "800.00", "shippingPrice": "0", "totalPrice": "800.00"}
            for i in range(10)
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            new_callable=AsyncMock,
            return_value=scraper_data,
        ) as mock_scraper:
            await client.get_sold_comps(keyword="iPhone", condition="new")

        mock_scraper.assert_called_once()
        call_kwargs = mock_scraper.call_args
        assert call_kwargs.kwargs.get("condition") == "new"

    @pytest.mark.asyncio
    async def test_condition_any_not_propagated(self):
        """condition='any' no se pasa al scraper (queda None)."""
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "scraper"
        client._token = None

        scraper_data = [
            {"title": f"iPhone {i}", "soldPrice": "800.00", "totalPrice": "800.00"}
            for i in range(10)
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            new_callable=AsyncMock,
            return_value=scraper_data,
        ) as mock_scraper:
            await client.get_sold_comps(keyword="iPhone", condition="any")

        call_kwargs = mock_scraper.call_args
        assert call_kwargs.kwargs.get("condition") is None

    @pytest.mark.asyncio
    async def test_category_id_propagated_to_scraper(self):
        """get_sold_comps(category_id=X) pasa category_id a scrape_sold_listings."""
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "scraper"
        client._token = None

        scraper_data = [
            {"title": f"Switch {i}", "soldPrice": "200.00", "shippingPrice": "0", "totalPrice": "200.00"}
            for i in range(10)
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            new_callable=AsyncMock,
            return_value=scraper_data,
        ) as mock_scraper:
            await client.get_sold_comps(keyword="Nintendo Switch OLED", category_id=139971)

        mock_scraper.assert_called_once()
        call_kwargs = mock_scraper.call_args
        assert call_kwargs.kwargs.get("category_id") == 139971

    @pytest.mark.asyncio
    async def test_no_category_id_not_propagated(self):
        """Sin category_id, no se pasa al scraper."""
        from app.services.marketplace.ebay import EbayClient

        client = EbayClient()
        client._data_source = "scraper"
        client._token = None

        scraper_data = [
            {"title": f"Switch {i}", "soldPrice": "200.00", "shippingPrice": "0", "totalPrice": "200.00"}
            for i in range(10)
        ]

        with patch(
            "app.services.marketplace.ebay.scrape_sold_listings",
            new_callable=AsyncMock,
            return_value=scraper_data,
        ) as mock_scraper:
            await client.get_sold_comps(keyword="Nintendo Switch OLED")

        call_kwargs = mock_scraper.call_args
        assert call_kwargs.kwargs.get("category_id") is None


# ── Tests de filtrado de ubicación internacional ──


# Fixture s-card con items internacionales y US
EBAY_SCARD_LOCATION_HTML = """
<html>
<body>
<div class="srp-river-results">
  <ul class="srp-results srp-list clearfix">

    <!-- US item: debe pasar -->
    <li class="s-card s-card--horizontal" data-listingid="111111111">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <a class="s-card__link" href="https://www.ebay.com/itm/111111111">
              <span class="su-styled-text primary default">ASICS Gel-Nimbus 28 US Seller</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text positive bold large-1 s-card__price">$99.99</span>
            <span class="su-styled-text secondary large">Free delivery</span>
            <span class="su-styled-text secondary default">Located in United States</span>
          </div>
        </div>
      </div>
    </li>

    <!-- Japan item: debe ser filtrado -->
    <li class="s-card s-card--horizontal" data-listingid="222222222">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <a class="s-card__link" href="https://www.ebay.com/itm/222222222">
              <span class="su-styled-text primary default">ASICS Gel-Nimbus 28 Japan Import</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text positive bold large-1 s-card__price">$167.00</span>
            <span class="su-styled-text secondary large">+$40.00 delivery</span>
            <span class="su-styled-text secondary default">Located in Japan</span>
          </div>
        </div>
      </div>
    </li>

    <!-- No location: debe pasar (desconocido = permitido) -->
    <li class="s-card s-card--horizontal" data-listingid="333333333">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <a class="s-card__link" href="https://www.ebay.com/itm/333333333">
              <span class="su-styled-text primary default">ASICS Gel-Nimbus 28 No Location</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text positive bold large-1 s-card__price">$109.00</span>
            <span class="su-styled-text secondary large">Free delivery</span>
          </div>
        </div>
      </div>
    </li>

    <!-- Brazil item: debe ser filtrado -->
    <li class="s-card s-card--horizontal" data-listingid="444444444">
      <div class="su-card-container su-card-container--horizontal">
        <div class="su-card-container__content">
          <div class="su-card-container__header">
            <a class="s-card__link" href="https://www.ebay.com/itm/444444444">
              <span class="su-styled-text primary default">ASICS Gel-Nimbus 28 Brazil</span>
            </a>
          </div>
          <div class="su-card-container__body">
            <span class="su-styled-text positive bold large-1 s-card__price">$189.00</span>
            <span class="su-styled-text secondary large">+$36.00 delivery</span>
            <span class="su-styled-text secondary default">Located in Brazil</span>
          </div>
        </div>
      </div>
    </li>

  </ul>
</div>
</body>
</html>
"""

# Fixture s-item (legacy) con items internacionales y US
EBAY_SITEM_LOCATION_HTML = """
<html>
<body>
<div class="srp-results">
  <ul class="srp-results srp-list clearfix">

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/111111111">
            <h3 class="s-item__title">ASICS Gel-Nimbus 28 US Seller</h3>
          </a>
          <span class="s-item__price">$99.99</span>
          <span class="s-item__logisticsCost">Free shipping</span>
          <span class="s-item__location">from United States</span>
        </div>
      </div>
    </li>

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/222222222">
            <h3 class="s-item__title">ASICS Gel-Nimbus 28 Japan Import</h3>
          </a>
          <span class="s-item__price">$167.00</span>
          <span class="s-item__logisticsCost">+$40.00 shipping</span>
          <span class="s-item__location">from Japan</span>
        </div>
      </div>
    </li>

    <li class="s-item">
      <div class="s-item__wrapper">
        <div class="s-item__info">
          <a class="s-item__link" href="https://www.ebay.com/itm/333333333">
            <h3 class="s-item__title">ASICS Gel-Nimbus 28 No Location</h3>
          </a>
          <span class="s-item__price">$109.00</span>
          <span class="s-item__logisticsCost">Free shipping</span>
        </div>
      </div>
    </li>

  </ul>
</div>
</body>
</html>
"""


class TestLocationFilteringSCard:
    """Tests de filtrado de ubicación internacional en layout s-card."""

    def test_filters_international_items(self):
        results = parse_sold_listings(EBAY_SCARD_LOCATION_HTML)
        titles = [r["title"] for r in results]
        # US y sin location pasan, Japan y Brazil filtrados
        assert len(results) == 2
        assert "ASICS Gel-Nimbus 28 US Seller" in titles
        assert "ASICS Gel-Nimbus 28 No Location" in titles
        assert "ASICS Gel-Nimbus 28 Japan Import" not in titles
        assert "ASICS Gel-Nimbus 28 Brazil" not in titles

    def test_us_item_has_location(self):
        results = parse_sold_listings(EBAY_SCARD_LOCATION_HTML)
        us_item = [r for r in results if "US Seller" in r["title"]][0]
        assert us_item["itemLocation"] == "United States"

    def test_no_location_item_has_none(self):
        results = parse_sold_listings(EBAY_SCARD_LOCATION_HTML)
        no_loc = [r for r in results if "No Location" in r["title"]][0]
        assert no_loc["itemLocation"] is None


class TestLocationFilteringSItem:
    """Tests de filtrado de ubicación internacional en layout legacy s-item."""

    def test_filters_international_items(self):
        results = parse_sold_listings(EBAY_SITEM_LOCATION_HTML)
        titles = [r["title"] for r in results]
        # US y sin location pasan, Japan filtrado
        assert len(results) == 2
        assert "ASICS Gel-Nimbus 28 US Seller" in titles
        assert "ASICS Gel-Nimbus 28 No Location" in titles
        assert "ASICS Gel-Nimbus 28 Japan Import" not in titles

    def test_us_item_has_location(self):
        results = parse_sold_listings(EBAY_SITEM_LOCATION_HTML)
        us_item = [r for r in results if "US Seller" in r["title"]][0]
        assert us_item["itemLocation"] == "United States"

    def test_no_location_item_has_none(self):
        results = parse_sold_listings(EBAY_SITEM_LOCATION_HTML)
        no_loc = [r for r in results if "No Location" in r["title"]][0]
        assert no_loc["itemLocation"] is None


# ── Tests de Smart Query Building ──


class TestBuildSearchQuery:
    def test_default_exclusions_added(self):
        result = _build_search_query("iPhone 15 Pro")
        assert "iPhone 15 Pro" in result
        assert "-lot" in result
        assert "-bundle" in result
        assert "-broken" in result

    def test_keyword_term_not_excluded(self):
        """Si el keyword contiene un término de exclusión, no se excluye."""
        result = _build_search_query("iPhone lot of 5")
        assert "-lot" not in result
        # Otros sí
        assert "-bundle" in result

    def test_custom_exclusions(self):
        result = _build_search_query("PS5", exclude_terms=["broken", "parts"])
        assert result == "PS5 -broken -parts"

    def test_all_exclusions_in_keyword(self):
        """Si todos los términos de exclusión están en el keyword, no se excluyen."""
        from app.services.marketplace.ebay_scraper import _DEFAULT_EXCLUSIONS
        kw = " ".join(_DEFAULT_EXCLUSIONS)
        result = _build_search_query(kw)
        assert result == kw

    def test_case_insensitive(self):
        result = _build_search_query("BULK wholesale items")
        assert "-bulk" not in result
        assert "-wholesale" not in result
        assert "-lot" in result

    def test_empty_custom_exclusions(self):
        result = _build_search_query("iPhone", exclude_terms=[])
        assert result == "iPhone"


class TestScrapeSoldListingsCategoryId:
    @pytest.mark.asyncio
    async def test_category_id_adds_sacat_param(self):
        """category_id agrega _sacat a la URL."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("Nintendo Switch OLED", category_id=139971)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["_sacat"] == "139971"

    @pytest.mark.asyncio
    async def test_no_category_id_no_sacat_param(self):
        """Sin category_id, no agrega _sacat."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("Nintendo Switch OLED")

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert "_sacat" not in params

    @pytest.mark.asyncio
    async def test_category_id_with_condition(self):
        """category_id y condition pueden coexistir."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("Nintendo Switch OLED", condition="new", category_id=139971)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["_sacat"] == "139971"
        assert params["LH_ItemCondition"] == _EBAY_CONDITION_IDS["new"]


class TestScrapeSoldListingsConditionParams:
    @pytest.mark.asyncio
    async def test_condition_new_adds_item_condition_param(self):
        """condition='new' agrega LH_ItemCondition a la URL."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("iPhone 15 Pro", condition="new")

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["LH_ItemCondition"] == _EBAY_CONDITION_IDS["new"]
        assert params["_sop"] == "13"
        assert params["rt"] == "nc"

    @pytest.mark.asyncio
    async def test_condition_used_adds_item_condition_param(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("iPhone 15 Pro", condition="used")

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["LH_ItemCondition"] == _EBAY_CONDITION_IDS["used"]

    @pytest.mark.asyncio
    async def test_no_condition_no_item_condition_param(self):
        """Sin condition, no agrega LH_ItemCondition."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("iPhone 15 Pro")

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert "LH_ItemCondition" not in params

    @pytest.mark.asyncio
    async def test_invalid_condition_ignored(self):
        """Condición inválida no agrega LH_ItemCondition."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("iPhone 15 Pro", condition="mint")

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert "LH_ItemCondition" not in params

    @pytest.mark.asyncio
    async def test_query_has_exclusions(self):
        """La query enviada a eBay incluye exclusiones por defecto."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = EBAY_SCARD_HTML_FIXTURE
        mock_response.raise_for_status = MagicMock()

        with patch("app.services.marketplace.ebay_scraper.AsyncSession") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await scrape_sold_listings("iPhone 15 Pro")

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        nkw = params["_nkw"]
        assert "-lot" in nkw
        assert "-bundle" in nkw
