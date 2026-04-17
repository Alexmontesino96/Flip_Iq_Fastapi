"""Tests para UPC-first optimizations en analysis_service."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.marketplace.base import CompsResult, MarketplaceListing


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


# Patch settings.keepa_api_key=None para que Amazon no entre en el flujo
_KEEPA_PATCH = patch("app.services.analysis_service.settings.keepa_api_key", None)


class TestUpcFirstLimits:
    """Barcode searches use limit=240, keyword searches use limit=50."""

    @pytest.mark.asyncio
    async def test_barcode_uses_limit_240(self):
        """When barcode is provided, eBay fetch uses limit=240."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(100))

        with (
            _KEEPA_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                db=_mock_db(), barcode="045496596439", keyword=None,
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
            _KEEPA_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", new_callable=AsyncMock, side_effect=lambda comps, **kw: comps),
            patch("app.services.analysis_service.filter_comps_by_relevance", new_callable=AsyncMock, side_effect=lambda comps, kw: comps),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                db=_mock_db(), barcode=None, keyword="Nintendo Switch OLED",
                cost_price=200.0, marketplace="ebay",
            )

            first_call = mock_ebay.get_sold_comps.call_args_list[0]
            assert first_call.kwargs.get("limit") == 50


class TestUpcSkipsEnricher:
    """UPC hit skips enricher and relevance filter."""

    @pytest.mark.asyncio
    async def test_upc_hit_skips_enricher(self):
        """When barcode returns results, enricher is NOT called."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(100))
        mock_enrich = AsyncMock(side_effect=lambda comps, **kw: comps)
        mock_filter = AsyncMock(side_effect=lambda comps, kw: comps)

        with (
            _KEEPA_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", mock_enrich),
            patch("app.services.analysis_service.filter_comps_by_relevance", mock_filter),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                db=_mock_db(), barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay",
            )

            mock_enrich.assert_not_called()
            mock_filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_keyword_calls_enricher(self):
        """When no barcode, enricher IS called."""
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=_make_comps(30))
        mock_enrich = AsyncMock(side_effect=lambda comps, **kw: comps)
        mock_filter = AsyncMock(side_effect=lambda comps, kw: comps)

        with (
            _KEEPA_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", mock_enrich),
            patch("app.services.analysis_service.filter_comps_by_relevance", mock_filter),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                db=_mock_db(), barcode=None, keyword="Nintendo Switch OLED",
                cost_price=200.0, marketplace="ebay",
            )

            mock_enrich.assert_called_once()
            mock_filter.assert_called_once()


class TestUpcSupplementAndRefetch:
    """UPC supplement with keyword when few results, refetch with limit=500."""

    @pytest.mark.asyncio
    async def test_upc_supplements_with_keyword(self):
        """When UPC returns <80 recent items, supplements with keyword search."""
        small_comps = _make_comps(5)
        big_comps = _make_comps(100)
        mock_ebay = AsyncMock()
        # Call 1: UPC → 5 items, Call 2: keyword supplement → 100
        mock_ebay.get_sold_comps = AsyncMock(side_effect=[small_comps, big_comps])

        with (
            _KEEPA_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.enrich_listings", new_callable=AsyncMock, side_effect=lambda comps, **kw: comps),
            patch("app.services.analysis_service.filter_comps_by_relevance", new_callable=AsyncMock, side_effect=lambda comps, kw: comps),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                db=_mock_db(), barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay",
            )

            calls = mock_ebay.get_sold_comps.call_args_list
            assert len(calls) >= 2
            # Second call should be keyword supplement with limit=240
            supplement_call = calls[1]
            assert supplement_call.kwargs.get("keyword") == "Nintendo Switch OLED"
            assert supplement_call.kwargs.get("limit") == 240

    @pytest.mark.asyncio
    async def test_no_supplement_when_enough_upc_results(self):
        """When UPC returns >=80 recent items, no keyword supplement needed."""
        big_comps = _make_comps(100)
        mock_ebay = AsyncMock()
        mock_ebay.get_sold_comps = AsyncMock(return_value=big_comps)

        with (
            _KEEPA_PATCH,
            patch("app.services.analysis_service._get_ebay_client", return_value=mock_ebay),
            patch("app.services.analysis_service.lookup_upc", new_callable=AsyncMock, return_value={"title": "Nintendo Switch OLED"}),
            patch("app.services.analysis_service.categorize_product", new_callable=AsyncMock, return_value=None),
            patch("app.services.analysis_service.generate_explanation", new_callable=AsyncMock, return_value="Test explanation"),
        ):
            from app.services.analysis_service import run_analysis
            await run_analysis(
                db=_mock_db(), barcode="045496596439", keyword=None,
                cost_price=200.0, marketplace="ebay",
            )

            # Only 1 call — no supplement needed
            assert mock_ebay.get_sold_comps.call_count == 1
