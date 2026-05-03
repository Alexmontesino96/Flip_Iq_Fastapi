"""Tests para UPC-first optimizations en analysis_service."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.marketplace.base import CompsResult, MarketplaceListing
from app.services.analysis_service import _simplify_upc_title


def _make_listings(n: int = 50) -> list[MarketplaceListing]:
    """Genera n listings dummy con fechas."""
    now = datetime.now(timezone.utc)
    return [
        MarketplaceListing(
            title=f"Nintendo Switch OLED #{i}",
            price=280.0 + i * 0.5,
            total_price=285.0 + i * 0.5,
            condition="New",
            ended_at=now - timedelta(days=30 * i / max(n - 1, 1)),
            marketplace="ebay",
        )
        for i in range(n)
    ]


def _make_comps(n: int = 50) -> CompsResult:
    listings = _make_listings(n)
    return CompsResult.from_listings(listings, marketplace="ebay", days=30)


def _mock_db():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _mock_session_factory():
    """Returns a mock async_session that yields a mock db on async with."""
    mock_db = _mock_db()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=mock_db)
    cm.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=cm)
    return factory


# Patch settings.keepa_api_key=None para que Amazon no entre en el flujo
_KEEPA_PATCH = patch("app.services.analysis_service.settings.keepa_api_key", None)
_DB_PATCH = patch("app.database.async_session", new_callable=_mock_session_factory)


class TestUpcTitleSimplification:
    def test_shoe_retail_title_removes_gender_category_and_colorway(self):
        title = "Mens adidas Adizero EVO SL Athletic Shoe - Core Black / White / Core Black"
        assert _simplify_upc_title(title) == "adidas Adizero EVO SL"

    def test_existing_specific_running_shoe_title_stays_searchable(self):
        title = "ASICS GEL-Nimbus(r) 28 Men's Running Shoes Black/Feather Grey : 7 D - Medium, Synthetic"
        assert _simplify_upc_title(title) == "ASICS GEL-Nimbus 28"


class TestUpcFirstLimits:
    """Barcode searches use limit=240, keyword searches use limit=50."""

    @pytest.mark.asyncio
    async def test_barcode_uses_limit_240(self):
        """When barcode is provided, eBay fetch uses limit=240."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(100))

        with (
            _KEEPA_PATCH, _DB_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay",
            )

            first_call = mock_ebay.get_sold_comps.call_args_list[0]
            assert first_call.kwargs.get("limit") == 240

    @pytest.mark.asyncio
    async def test_keyword_uses_limit_50(self):
        """When no barcode, eBay fetch uses limit=50."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(30))

        with (
            _KEEPA_PATCH, _DB_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", new_callable=AsyncMock, side_effect=lambda comps, **kw: comps),
            patch("app.services.analysis_service.filter_comps_by_relevance", new_callable=AsyncMock, side_effect=lambda comps, kw: comps),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                barcode=None, keyword="Nintendo Switch OLED",
                cost_price=200.0, marketplace="ebay",
            )

            first_call = mock_ebay.get_sold_comps.call_args_list[0]
            assert first_call.kwargs.get("limit") == 50


class TestUpcSkipsEnricher:
    """UPC hit skips enricher and relevance filter (clean data)."""

    @pytest.mark.asyncio
    async def test_upc_hit_skips_enricher(self):
        """When barcode returns results, enricher is NOT called."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(100))
        mock_enrich = AsyncMock(side_effect=lambda comps, **kw: comps)
        mock_filter = AsyncMock(side_effect=lambda comps, kw: comps)

        with (
            _KEEPA_PATCH, _DB_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", mock_enrich),
            patch("app.services.analysis_service.filter_comps_by_relevance", mock_filter),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay",
            )

            mock_enrich.assert_not_called()
            mock_filter.assert_not_called()
            # Only 1 eBay call — no supplement when UPC has results
            assert mock_ebay.get_sold_comps.call_count == 1

    @pytest.mark.asyncio
    async def test_keyword_calls_enricher(self):
        """When no barcode, enricher IS called."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(30))
        mock_enrich = AsyncMock(side_effect=lambda comps, **kw: comps)
        mock_filter = AsyncMock(side_effect=lambda comps, kw: comps)

        with (
            _KEEPA_PATCH, _DB_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", mock_enrich),
            patch("app.services.analysis_service.filter_comps_by_relevance", mock_filter),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                barcode=None, keyword="Nintendo Switch OLED",
                cost_price=200.0, marketplace="ebay",
            )

            mock_enrich.assert_called_once()
            mock_filter.assert_called_once()


class TestUpcFallback:
    """UPC fallback to keyword only when barcode returns 0 results."""

    @pytest.mark.asyncio
    async def test_upc_fallback_when_no_results(self):
        """When barcode returns 0 results, falls back to keyword search."""
        empty_comps = CompsResult(marketplace="ebay")
        kw_comps = _make_comps(100)
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(side_effect=[empty_comps, kw_comps])

        with (
            _KEEPA_PATCH, _DB_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", new_callable=AsyncMock, side_effect=lambda comps, **kw: comps),
            patch("app.services.analysis_service.filter_comps_by_relevance", new_callable=AsyncMock, side_effect=lambda comps, kw: comps),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay",
            )

            calls = mock_ebay.get_sold_comps.call_args_list
            assert len(calls) == 2
            # Second call is keyword fallback
            assert calls[1].kwargs.get("keyword") == "Nintendo Switch OLED"

    @pytest.mark.asyncio
    async def test_upc_fallback_when_raw_comps_cleanup_to_zero(self):
        """When UPC raw comps are unusable after cleanup, falls back to keyword."""
        now = datetime.now(timezone.utc)
        wrong_condition_comps = CompsResult.from_listings(
            [
                MarketplaceListing(
                    title="Nintendo Switch OLED used",
                    price=250.0,
                    total_price=250.0,
                    condition="Pre-Owned",
                    ended_at=now,
                    marketplace="ebay",
                )
            ],
            marketplace="ebay",
            days=30,
        )
        kw_comps = _make_comps(100)
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(side_effect=[wrong_condition_comps, kw_comps])

        with (
            _KEEPA_PATCH, _DB_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", new_callable=AsyncMock, side_effect=lambda comps, **kw: comps),
            patch("app.services.analysis_service.filter_comps_by_relevance", new_callable=AsyncMock, side_effect=lambda comps, kw: comps),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay", condition="new",
            )

            calls = mock_ebay.get_sold_comps.call_args_list
            assert len(calls) == 2
            assert calls[0].kwargs.get("barcode") == "045496596439"
            assert calls[1].kwargs.get("barcode") is None
            assert calls[1].kwargs.get("keyword") == "Nintendo Switch OLED"

    @pytest.mark.asyncio
    async def test_upc_no_fallback_when_results(self):
        """When barcode returns results, no keyword fallback."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(100))

        with (
            _KEEPA_PATCH, _DB_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay",
            )

            assert mock_ebay.get_sold_comps.call_count == 1
