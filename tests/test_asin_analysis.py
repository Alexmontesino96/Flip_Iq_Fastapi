"""Tests para el endpoint de análisis Amazon por ASIN directo."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.schemas.analysis import AsinAnalysisRequest
from app.services.marketplace.amazon import AmazonClient, KEEPA_EPOCH
from app.services.marketplace.base import CompsResult, MarketplaceListing


# --- Helpers ---

def _keepa_minutes(dt: datetime) -> int:
    return int((dt - KEEPA_EPOCH).total_seconds() / 60)


def _make_product(asin="B08N5WRWNW", title="Test Widget Pro", price_cents=2999):
    """Producto Keepa mock con ofertas y Buy Box history."""
    now = datetime.now(timezone.utc)
    km = _keepa_minutes(now - timedelta(days=5))
    return {
        "asin": asin,
        "title": title,
        "brand": "TestBrand",
        "model": "TW-100",
        "offers": [
            {
                "condition": 1,
                "isFBA": True,
                "sellerName": "SellerA",
                "offerCSV": [km, price_cents, 0],
            },
            {
                "condition": 1,
                "isFBA": False,
                "sellerName": "SellerB",
                "offerCSV": [km, price_cents + 200, 499],
            },
        ],
        "csv": [None] * 18 + [
            [km, price_cents, 0, km + 60, price_cents - 100, 0],
        ],
        "stats": {"salesRankReference": 15000},
        "referralFeePercentage": 15,
        "fbaFees": {"pickAndPackFee": 322},
        "imagesCSV": "test-image-hash.jpg",
    }


# --- Schema validation ---

class TestAsinAnalysisRequest:
    def test_valid_asin(self):
        req = AsinAnalysisRequest(asin="B08N5WRWNW", cost_price=25.0)
        assert req.asin == "B08N5WRWNW"

    def test_lowercase_asin_uppercased(self):
        req = AsinAnalysisRequest(asin="b08n5wrwnw", cost_price=25.0)
        assert req.asin == "B08N5WRWNW"

    def test_invalid_asin_too_short(self):
        with pytest.raises(ValueError, match="10 alphanumeric"):
            AsinAnalysisRequest(asin="B08", cost_price=25.0)

    def test_invalid_asin_special_chars(self):
        with pytest.raises(ValueError, match="10 alphanumeric"):
            AsinAnalysisRequest(asin="B08-N5WRWN", cost_price=25.0)

    def test_cost_price_must_be_positive(self):
        with pytest.raises(ValueError, match="greater than 0"):
            AsinAnalysisRequest(asin="B08N5WRWNW", cost_price=0)


# --- get_sold_comps_by_asin ---

class TestGetSoldCompsByAsin:
    @pytest.mark.asyncio
    async def test_returns_comps_for_valid_asin(self):
        product = _make_product()
        client = AmazonClient()
        client._api_key = "test-key"

        with patch.object(client, "_keepa_product", new_callable=AsyncMock) as mock:
            mock.return_value = [product]
            result = await client.get_sold_comps_by_asin("B08N5WRWNW")

        assert isinstance(result, CompsResult)
        assert result.marketplace == "amazon"
        assert result.total_sold > 0
        assert len(result.listings) > 0
        # Verify FBA fees extracted
        assert result.fba_referral_pct is not None
        assert result.fba_fulfillment_fee is not None
        # Verify BSR-based velocity
        assert result.sales_per_day > 0
        # Verify image
        assert result.image_url is not None

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_key(self):
        client = AmazonClient()
        client._api_key = None
        result = await client.get_sold_comps_by_asin("B08N5WRWNW")
        assert result.total_sold == 0
        assert result.marketplace == "amazon"

    @pytest.mark.asyncio
    async def test_returns_empty_when_asin_not_found(self):
        client = AmazonClient()
        client._api_key = "test-key"
        with patch.object(client, "_keepa_product", new_callable=AsyncMock) as mock:
            mock.return_value = []
            result = await client.get_sold_comps_by_asin("B0NOTFOUND")
        assert result.total_sold == 0

    @pytest.mark.asyncio
    async def test_calls_keepa_product_with_asin(self):
        """Verifica que usa _keepa_product directo (no _keepa_search)."""
        client = AmazonClient()
        client._api_key = "test-key"
        with patch.object(client, "_keepa_product", new_callable=AsyncMock) as mock_product, \
             patch.object(client, "_keepa_search", new_callable=AsyncMock) as mock_search:
            mock_product.return_value = [_make_product()]
            await client.get_sold_comps_by_asin("B08N5WRWNW")

        mock_product.assert_called_once_with(["B08N5WRWNW"])
        mock_search.assert_not_called()


# --- run_analysis_asin ---

class TestRunAnalysisAsin:
    @pytest.mark.asyncio
    async def test_returns_analysis_response(self):
        product = _make_product(price_cents=5000)  # $50 product

        with patch(
            "app.services.analysis_service._get_amazon_client"
        ) as mock_get_client, \
             patch(
            "app.services.analysis_service.categorize_product",
            new_callable=AsyncMock,
            return_value=None,
        ), \
             patch(
            "app.services.analysis_service.generate_explanation",
            new_callable=AsyncMock,
            return_value="Test explanation",
        ), \
             patch(
            "app.services.analysis_service._find_or_create_product",
            new_callable=AsyncMock,
            return_value=None,
        ), \
             patch("app.database.async_session") as mock_sf:
            # Mock Amazon client
            mock_client = AsyncMock()
            mock_client.get_sold_comps_by_asin = AsyncMock()

            # Build real CompsResult from product
            real_client = AmazonClient()
            real_client._api_key = "test"
            with patch.object(real_client, "_keepa_product", new_callable=AsyncMock, return_value=[product]):
                real_comps = await real_client.get_sold_comps_by_asin("B08N5WRWNW")

            mock_client.get_sold_comps_by_asin.return_value = real_comps
            mock_get_client.return_value = mock_client

            # Mock DB session
            mock_db = AsyncMock()
            mock_session_ctx = AsyncMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_sf.return_value = mock_session_ctx

            from app.services.analysis_service import run_analysis_asin
            result = await run_analysis_asin(
                asin="B08N5WRWNW",
                cost_price=25.0,
            )

        assert result is not None
        assert result.marketplace == "amazon_fba"
        assert result.ebay_analysis is None
        assert result.amazon_analysis is not None
        assert result.best_marketplace == "amazon"
        assert result.recommendation in ("buy", "buy_small", "watch", "pass")

    @pytest.mark.asyncio
    async def test_no_comps_returns_pass(self):
        """Sin datos de Keepa debe retornar pass."""
        empty_comps = CompsResult(marketplace="amazon")

        with patch(
            "app.services.analysis_service._get_amazon_client"
        ) as mock_get_client, \
             patch(
            "app.services.analysis_service.categorize_product",
            new_callable=AsyncMock,
            return_value=None,
        ), \
             patch(
            "app.services.analysis_service.generate_explanation",
            new_callable=AsyncMock,
            return_value=None,
        ), \
             patch(
            "app.services.analysis_service._find_or_create_product",
            new_callable=AsyncMock,
            return_value=None,
        ), \
             patch("app.database.async_session") as mock_sf:
            mock_client = AsyncMock()
            mock_client.get_sold_comps_by_asin = AsyncMock(return_value=empty_comps)
            mock_get_client.return_value = mock_client

            mock_db = AsyncMock()
            mock_session_ctx = AsyncMock()
            mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_sf.return_value = mock_session_ctx

            from app.services.analysis_service import run_analysis_asin
            result = await run_analysis_asin(
                asin="B0NOTFOUND",
                cost_price=25.0,
            )

        assert result.recommendation == "pass"
        assert result.no_comps_found is True
