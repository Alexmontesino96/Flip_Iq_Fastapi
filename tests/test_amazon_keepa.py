"""Tests para el cliente Amazon via Keepa API y análisis dual."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.marketplace.amazon import (
    AmazonClient,
    keepa_time_to_datetime,
    estimate_sales_per_day,
    _map_keepa_offers,
    _map_buybox_history,
    KEEPA_EPOCH,
)
from app.services.marketplace.base import CompsResult, MarketplaceListing


# --- Helpers ---

def _make_product(
    asin="B0TEST123",
    title="Test Product",
    offers=None,
    csv=None,
    stats=None,
):
    """Crea un producto Keepa mock."""
    p = {"asin": asin, "title": title}
    if offers is not None:
        p["offers"] = offers
    if csv is not None:
        p["csv"] = csv
    if stats is not None:
        p["stats"] = stats
    return p


def _keepa_minutes(dt: datetime) -> int:
    """Convierte datetime a Keepa minutes."""
    return int((dt - KEEPA_EPOCH).total_seconds() / 60)


def _make_listing(price, marketplace="ebay", days_ago=5):
    """Crea un MarketplaceListing de prueba."""
    return MarketplaceListing(
        title=f"Test {marketplace} item",
        price=price,
        total_price=price,
        sold=True,
        marketplace=marketplace,
        ended_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


# --- keepa_time_to_datetime ---

class TestKeepaTimeConversion:
    def test_epoch(self):
        assert keepa_time_to_datetime(0) == KEEPA_EPOCH

    def test_one_day(self):
        result = keepa_time_to_datetime(1440)  # 24 * 60
        expected = KEEPA_EPOCH + timedelta(days=1)
        assert result == expected

    def test_known_date(self):
        minutes = int((datetime(2024, 1, 1, tzinfo=timezone.utc) - KEEPA_EPOCH).total_seconds() / 60)
        result = keepa_time_to_datetime(minutes)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1


# --- estimate_sales_per_day ---

class TestEstimateSalesPerDay:
    def test_top_rank(self):
        assert estimate_sales_per_day(1000) == 10.0

    def test_mid_rank(self):
        assert estimate_sales_per_day(25_000) == 3.5

    def test_low_rank(self):
        assert estimate_sales_per_day(100_000) == 0.75

    def test_very_low_rank(self):
        assert estimate_sales_per_day(500_000) == 0.15

    def test_none_rank(self):
        assert estimate_sales_per_day(None) == 0.0

    def test_zero_rank(self):
        assert estimate_sales_per_day(0) == 0.0

    def test_negative_rank(self):
        assert estimate_sales_per_day(-5) == 0.0


# --- _map_keepa_offers ---

class TestMapKeepaOffers:
    def test_basic_offer(self):
        """offerCSV usa triples: [time, price_cents, shipping_cents]."""
        now_minutes = _keepa_minutes(datetime.now(timezone.utc))
        product = _make_product(
            offers=[{
                "offerCSV": [now_minutes, 2999, 0],
                "condition": 1,
                "sellerName": "TestSeller",
                "isFBA": True,
            }],
        )
        listings = _map_keepa_offers(product)
        assert len(listings) == 1
        assert listings[0].price == 29.99
        assert listings[0].condition == "new"
        assert listings[0].seller_username == "TestSeller"
        assert listings[0].marketplace == "amazon"
        assert listings[0].shipping_price == 0.0  # FBA → shipping 0

    def test_used_offer_with_shipping(self):
        now_minutes = _keepa_minutes(datetime.now(timezone.utc))
        product = _make_product(
            offers=[{
                "offerCSV": [now_minutes, 1500, 499],
                "condition": 3,
                "sellerId": "SELLER123",
                "isFBA": False,
            }],
        )
        listings = _map_keepa_offers(product)
        assert len(listings) == 1
        assert listings[0].price == 15.0
        assert listings[0].condition == "used"
        assert listings[0].shipping_price == 4.99
        assert listings[0].total_price == 19.99

    def test_fba_ignores_shipping_in_csv(self):
        """FBA: shipping_price siempre 0 aunque haya valor en offerCSV."""
        now_minutes = _keepa_minutes(datetime.now(timezone.utc))
        product = _make_product(
            offers=[{
                "offerCSV": [now_minutes, 5000, 599],
                "condition": 1,
                "sellerName": "FBASeller",
                "isFBA": True,
            }],
        )
        listings = _map_keepa_offers(product)
        assert len(listings) == 1
        assert listings[0].shipping_price == 0.0
        assert listings[0].total_price == 50.0

    def test_multiple_price_history_takes_latest(self):
        t1 = _keepa_minutes(datetime.now(timezone.utc) - timedelta(days=5))
        t2 = _keepa_minutes(datetime.now(timezone.utc))
        product = _make_product(
            offers=[{
                "offerCSV": [t1, 2000, 0, t2, 2500, 0],
                "condition": 1,
                "sellerName": "Seller",
                "isFBA": True,
            }],
        )
        listings = _map_keepa_offers(product)
        assert len(listings) == 1
        assert listings[0].price == 25.0

    def test_negative_price_skipped(self):
        now_minutes = _keepa_minutes(datetime.now(timezone.utc))
        product = _make_product(
            offers=[{
                "offerCSV": [now_minutes, -1, 0],
                "condition": 1,
                "sellerName": "Seller",
                "isFBA": True,
            }],
        )
        listings = _map_keepa_offers(product)
        assert len(listings) == 0

    def test_unreasonable_price_filtered(self):
        """Precios > $5,000 se filtran como sanity check."""
        now_minutes = _keepa_minutes(datetime.now(timezone.utc))
        product = _make_product(
            offers=[{
                "offerCSV": [now_minutes, 999999, 0],  # $9,999.99
                "condition": 1,
                "sellerName": "Seller",
                "isFBA": True,
            }],
        )
        listings = _map_keepa_offers(product)
        assert len(listings) == 0

    def test_empty_offers(self):
        product = _make_product(offers=[])
        assert _map_keepa_offers(product) == []

    def test_no_offers_key(self):
        product = _make_product()
        assert _map_keepa_offers(product) == []


# --- _map_buybox_history ---

class TestMapBuyboxHistory:
    def test_recent_buybox_entries(self):
        """csv[18] usa triples: [time, price_cents, shipping_cents]."""
        now = datetime.now(timezone.utc)
        t1 = _keepa_minutes(now - timedelta(days=5))
        t2 = _keepa_minutes(now - timedelta(days=2))

        csv = [None] * 19
        csv[18] = [t1, 3499, 0, t2, 3299, 0]

        product = _make_product(csv=csv)
        listings = _map_buybox_history(product, days=30)
        assert len(listings) == 2
        assert listings[0].price == 34.99
        assert listings[1].price == 32.99
        assert listings[0].seller_username == "Amazon Buy Box"

    def test_buybox_with_shipping(self):
        now = datetime.now(timezone.utc)
        t = _keepa_minutes(now - timedelta(days=3))

        csv = [None] * 19
        csv[18] = [t, 2500, 499]

        product = _make_product(csv=csv)
        listings = _map_buybox_history(product, days=30)
        assert len(listings) == 1
        assert listings[0].price == 25.0
        assert listings[0].shipping_price == 4.99
        assert listings[0].total_price == 29.99

    def test_old_entries_filtered(self):
        now = datetime.now(timezone.utc)
        old = _keepa_minutes(now - timedelta(days=60))
        recent = _keepa_minutes(now - timedelta(days=5))

        csv = [None] * 19
        csv[18] = [old, 3000, 0, recent, 3500, 0]

        product = _make_product(csv=csv)
        listings = _map_buybox_history(product, days=30)
        assert len(listings) == 1
        assert listings[0].price == 35.0

    def test_negative_price_skipped(self):
        now = datetime.now(timezone.utc)
        t = _keepa_minutes(now - timedelta(days=1))

        csv = [None] * 19
        csv[18] = [t, -1, 0]

        product = _make_product(csv=csv)
        listings = _map_buybox_history(product, days=30)
        assert len(listings) == 0

    def test_no_csv(self):
        product = _make_product(csv=None)
        assert _map_buybox_history(product) == []

    def test_csv_too_short(self):
        product = _make_product(csv=[None] * 5)
        assert _map_buybox_history(product) == []


# --- AmazonClient.get_sold_comps ---

class TestGetSoldComps:
    @pytest.mark.asyncio
    async def test_no_api_key(self):
        """Sin API key retorna CompsResult vacío."""
        with patch("app.services.marketplace.amazon.settings") as mock_settings:
            mock_settings.keepa_api_key = ""
            client = AmazonClient()
            client._api_key = ""
            result = await client.get_sold_comps(keyword="test")
            assert isinstance(result, CompsResult)
            assert result.marketplace == "amazon"
            assert len(result.listings) == 0

    @pytest.mark.asyncio
    async def test_keyword_search(self):
        """Busca por keyword → search + product → comps."""
        now = datetime.now(timezone.utc)
        t = _keepa_minutes(now - timedelta(days=3))

        search_response = {"products": [{"asin": "B0ASIN001"}, {"asin": "B0ASIN002"}]}
        product_response = {
            "products": [
                _make_product(
                    asin="B0ASIN001",
                    title="Product One",
                    offers=[{
                        "offerCSV": [t, 4999, 0],
                        "condition": 1,
                        "sellerName": "SellerA",
                        "isFBA": True,
                    }],
                    stats={"salesRankReference": 15_000},
                ),
                _make_product(
                    asin="B0ASIN002",
                    title="Product Two",
                    offers=[{
                        "offerCSV": [t, 3999, 0],
                        "condition": 1,
                        "sellerName": "SellerB",
                        "isFBA": True,
                    }],
                    stats={"salesRankReference": 80_000},
                ),
            ]
        }

        client = AmazonClient()
        client._api_key = "test-key"

        with patch.object(client, "_keepa_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [search_response, product_response]

            result = await client.get_sold_comps(keyword="test product")

            assert isinstance(result, CompsResult)
            assert result.marketplace == "amazon"
            assert len(result.listings) >= 2
            assert result.sales_per_day == 3.5

    @pytest.mark.asyncio
    async def test_barcode_search(self):
        """Busca por UPC → product by code → comps."""
        now = datetime.now(timezone.utc)
        t = _keepa_minutes(now - timedelta(days=1))

        product_response = {
            "products": [
                _make_product(
                    asin="B0UPC001",
                    title="UPC Product",
                    offers=[{
                        "offerCSV": [t, 2499, 0],
                        "condition": 1,
                        "sellerName": "Seller",
                        "isFBA": True,
                    }],
                    stats={"salesRankReference": 3_000},
                ),
            ]
        }

        client = AmazonClient()
        client._api_key = "test-key"

        with patch.object(client, "_keepa_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = product_response

            result = await client.get_sold_comps(barcode="012345678901")

            assert result.marketplace == "amazon"
            assert len(result.listings) >= 1
            assert result.sales_per_day == 10.0

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self):
        """Error HTTP retorna CompsResult vacío sin crash."""
        client = AmazonClient()
        client._api_key = "test-key"

        with patch.object(client, "_keepa_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None

            result = await client.get_sold_comps(keyword="test")

            assert isinstance(result, CompsResult)
            assert result.marketplace == "amazon"
            assert len(result.listings) == 0

    @pytest.mark.asyncio
    async def test_barcode_fallback_to_keyword(self):
        """Si barcode no da resultados, intenta keyword."""
        now = datetime.now(timezone.utc)
        t = _keepa_minutes(now - timedelta(days=1))

        empty_response = {"products": []}
        search_response = {"products": [{"asin": "B0FALL01"}]}
        product_response = {
            "products": [
                _make_product(
                    asin="B0FALL01",
                    title="Fallback Product",
                    offers=[{
                        "offerCSV": [t, 1999, 0],
                        "condition": 1,
                        "sellerName": "S",
                        "isFBA": True,
                    }],
                ),
            ]
        }

        client = AmazonClient()
        client._api_key = "test-key"

        with patch.object(client, "_keepa_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [empty_response, search_response, product_response]

            result = await client.get_sold_comps(barcode="000", keyword="fallback")

            assert len(result.listings) >= 1


# --- Pipeline extraction ---

class TestRunPipeline:
    """Verifica que _run_pipeline produce resultados válidos."""

    def test_pipeline_with_comps(self):
        from app.services.analysis_service import _run_pipeline

        listings = [_make_listing(p, "ebay", d) for p, d in
                     [(50, 1), (55, 2), (45, 3), (60, 4), (52, 5),
                      (48, 6), (53, 7), (57, 8), (51, 9), (54, 10)]]
        comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

        result = _run_pipeline(
            comps, keyword="test product", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )

        assert result.has_valid_comps
        assert result.cleaned.clean_total > 0
        assert result.pricing.market_list > 0
        assert result.profit_market.profit > 0
        assert result.opportunity > 0
        assert result.recommendation in ("buy", "buy_small", "watch", "pass")
        assert result.comps_info is not None

    def test_pipeline_empty_comps(self):
        from app.services.analysis_service import _run_pipeline

        comps = CompsResult(marketplace="amazon")

        result = _run_pipeline(
            comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="amazon_fba",
        )

        assert not result.has_valid_comps
        assert result.recommendation == "pass"
        assert result.comps_info is None

    def test_pipeline_different_marketplaces_independent(self):
        """Dos pipelines sobre datos distintos producen resultados independientes."""
        from app.services.analysis_service import _run_pipeline

        ebay_listings = [_make_listing(50, "ebay", d) for d in range(1, 11)]
        amazon_listings = [_make_listing(80, "amazon", d) for d in range(1, 11)]

        ebay_comps = CompsResult.from_listings(ebay_listings, marketplace="ebay", days=30)
        amazon_comps = CompsResult.from_listings(amazon_listings, marketplace="amazon", days=30)

        ebay_result = _run_pipeline(
            ebay_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )
        amazon_result = _run_pipeline(
            amazon_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="amazon_fba",
        )

        # Precios distintos → profits distintos
        assert ebay_result.pricing.market_list != amazon_result.pricing.market_list
        assert ebay_result.profit_market.profit != amazon_result.profit_market.profit


# --- MarketplaceAnalysis conversion ---

class TestPipelineToMarketplaceAnalysis:
    def test_conversion_with_valid_comps(self):
        from app.services.analysis_service import _run_pipeline, _pipeline_to_marketplace_analysis

        listings = [_make_listing(p, "ebay", d) for p, d in
                     [(50, 1), (55, 2), (45, 3), (60, 4), (52, 5),
                      (48, 6), (53, 7), (57, 8), (51, 9), (54, 10)]]
        comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

        pipeline = _run_pipeline(
            comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )
        ma = _pipeline_to_marketplace_analysis(pipeline)

        assert ma.marketplace == "ebay"
        assert ma.estimated_sale_price is not None
        assert ma.pricing is not None
        assert ma.velocity is not None
        assert ma.risk is not None
        assert ma.recommendation in ("buy", "buy_small", "watch", "pass")

    def test_conversion_empty_comps(self):
        from app.services.analysis_service import _run_pipeline, _pipeline_to_marketplace_analysis

        comps = CompsResult(marketplace="amazon")
        pipeline = _run_pipeline(
            comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="amazon_fba",
        )
        ma = _pipeline_to_marketplace_analysis(pipeline)

        assert ma.marketplace == "amazon_fba"
        assert ma.estimated_sale_price is None
        assert ma.pricing is None
        assert ma.recommendation == "pass"


# --- Comparison text ---

class TestBuildComparisonText:
    def test_both_valid(self):
        from app.services.analysis_service import _run_pipeline, _build_comparison_text

        ebay_listings = [_make_listing(50, "ebay", d) for d in range(1, 11)]
        amazon_listings = [_make_listing(70, "amazon", d) for d in range(1, 11)]

        ebay_comps = CompsResult.from_listings(ebay_listings, marketplace="ebay", days=30)
        amazon_comps = CompsResult.from_listings(amazon_listings, marketplace="amazon", days=30)

        ebay_p = _run_pipeline(
            ebay_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )
        amazon_p = _run_pipeline(
            amazon_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="amazon_fba",
        )

        text = _build_comparison_text(ebay_p, amazon_p)

        assert text is not None
        assert "eBay" in text
        assert "Amazon" in text
        assert "COMPARACIÓN" in text
        assert "Delta" in text

    def test_only_ebay(self):
        from app.services.analysis_service import _run_pipeline, _build_comparison_text

        listings = [_make_listing(50, "ebay", d) for d in range(1, 11)]
        comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

        ebay_p = _run_pipeline(
            comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )

        text = _build_comparison_text(ebay_p, None)
        assert text is None

    def test_amazon_empty(self):
        from app.services.analysis_service import _run_pipeline, _build_comparison_text

        ebay_listings = [_make_listing(50, "ebay", d) for d in range(1, 11)]
        ebay_comps = CompsResult.from_listings(ebay_listings, marketplace="ebay", days=30)

        empty_amazon = CompsResult(marketplace="amazon")

        ebay_p = _run_pipeline(
            ebay_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )
        amazon_p = _run_pipeline(
            empty_amazon, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="amazon_fba",
        )

        text = _build_comparison_text(ebay_p, amazon_p)
        assert text is None  # Amazon sin comps válidos


# --- Best marketplace selection ---

class TestBestMarketplace:
    def test_amazon_higher_opportunity(self):
        from app.services.analysis_service import _run_pipeline

        ebay_listings = [_make_listing(50, "ebay", d) for d in range(1, 11)]
        amazon_listings = [_make_listing(120, "amazon", d) for d in range(1, 11)]

        ebay_comps = CompsResult.from_listings(ebay_listings, marketplace="ebay", days=30)
        amazon_comps = CompsResult.from_listings(amazon_listings, marketplace="amazon", days=30)

        ebay_p = _run_pipeline(
            ebay_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )
        amazon_p = _run_pipeline(
            amazon_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="amazon_fba",
        )

        candidates = [ebay_p, amazon_p]
        valid = [c for c in candidates if c.has_valid_comps]
        best = max(valid, key=lambda c: c.opportunity)

        # Amazon a $120 vs eBay a $50 con cost $30 → Amazon más profitable
        assert best.marketplace_name == "amazon_fba"

    def test_only_ebay_valid(self):
        from app.services.analysis_service import _run_pipeline

        ebay_listings = [_make_listing(50, "ebay", d) for d in range(1, 11)]
        ebay_comps = CompsResult.from_listings(ebay_listings, marketplace="ebay", days=30)
        empty_amazon = CompsResult(marketplace="amazon")

        ebay_p = _run_pipeline(
            ebay_comps, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="ebay",
        )
        amazon_p = _run_pipeline(
            empty_amazon, keyword="test", condition="any",
            cost_price=30.0, marketplace_name="amazon_fba",
        )

        valid = [c for c in [ebay_p, amazon_p] if c.has_valid_comps]
        best = max(valid, key=lambda c: c.opportunity)

        assert best.marketplace_name == "ebay"


# --- AI explanation with comparison ---

class TestAIExplanationComparison:
    @pytest.mark.asyncio
    async def test_comparison_text_passed_to_llm(self):
        """Verifica que generate_explanation acepta comparison_text sin crashear."""
        from app.services.engines.ai_explanation import generate_explanation
        from dataclasses import dataclass

        @dataclass
        class FakeEngine:
            quick_list: float = 50.0
            market_list: float = 55.0
            stretch_list: float = 60.0
            stretch_allowed: bool = True
            profit: float = 10.0
            roi: float = 0.3
            margin: float = 0.2
            sale_price: float = 55.0
            recommended_max: float = 40.0
            max_by_profit: float = 40.0
            max_by_roi: float = 38.0
            score: int = 70
            category: str = "medio"
            sales_per_day: float = 1.0
            estimated_days_to_sell: float = 5.0
            market_sale_interval_days: float = 1.0
            factors: dict = None
            hhi: float = 0.1
            dominant_seller_share: float = 0.3
            unique_sellers: int = 5
            demand_trend: float = 10.0
            price_trend: float = 5.0
            coverage_ratio: float = 0.8
            burstiness: float = 0.1
            confidence: str = "media"
            recommended_format: str = "fixed_price"
            reasoning: str = "test"
            auction_signal: float = 0.2
            fixed_price_signal: float = 0.8
            suggested_min_offer: float = 45.0

            def __post_init__(self):
                if self.factors is None:
                    self.factors = {}

        engine = FakeEngine()
        comp_text = "COMPARACIÓN: eBay $50 vs Amazon $70"

        # Mock del LLM client para no depender de API key
        with patch("app.core.llm.get_llm_client") as mock_llm:
            mock_client = AsyncMock()
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="Análisis comparativo OK"))]
            )
            mock_llm.return_value = (mock_client, "test-model")

            result = await generate_explanation(
                keyword="test",
                cost_price=30.0,
                marketplace="ebay",
                pricing=engine,
                profit_market=engine,
                max_buy=engine,
                velocity=engine,
                risk=engine,
                confidence=engine,
                competition=engine,
                trend=engine,
                listing=engine,
                opportunity_score=70,
                recommendation="buy",
                cleaned_total=10,
                raw_total=15,
                comparison_text=comp_text,
            )

            assert result == "Análisis comparativo OK"
            # Verificar que el comparison_text se incluyó en el prompt
            call_args = mock_client.chat.completions.create.call_args
            messages = call_args.kwargs["messages"]
            user_msg = messages[1]["content"]
            assert "COMPARACIÓN" in user_msg
