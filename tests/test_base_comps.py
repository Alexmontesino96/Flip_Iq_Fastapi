"""Tests para CompsResult.from_listings() — days_of_data dinámico."""

from datetime import datetime, timedelta, timezone

from app.services.marketplace.base import CompsResult, MarketplaceListing


def _make_listing(price: float, ended_at: datetime | None = None) -> MarketplaceListing:
    return MarketplaceListing(
        title="Test Product",
        price=price,
        total_price=price,
        ended_at=ended_at,
    )


class TestDaysOfDataDynamic:
    """days_of_data se calcula del rango real de fechas de los listings."""

    def test_days_from_date_span(self):
        """Con listings que cubren 15 días, days_of_data = 15."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(100.0, ended_at=now - timedelta(days=15)),
            _make_listing(110.0, ended_at=now - timedelta(days=10)),
            _make_listing(105.0, ended_at=now - timedelta(days=5)),
            _make_listing(108.0, ended_at=now),
        ]
        result = CompsResult.from_listings(listings, days=30)
        assert result.days_of_data == 15.0
        assert result.total_sold == 4
        # sales_per_day = 4 / 15.0
        assert result.sales_per_day == round(4 / 15.0, 2)

    def test_days_fallback_no_dates(self):
        """Sin fechas en listings, usa el parámetro days (30)."""
        listings = [
            _make_listing(100.0),
            _make_listing(110.0),
            _make_listing(105.0),
        ]
        result = CompsResult.from_listings(listings, days=30)
        assert result.days_of_data == 30
        assert result.sales_per_day == round(3 / 30, 2)

    def test_days_fallback_single_listing(self):
        """Con 1 solo listing con fecha, usa days (no se puede calcular span)."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(100.0, ended_at=now),
        ]
        result = CompsResult.from_listings(listings, days=30)
        assert result.days_of_data == 30
        assert result.sales_per_day == round(1 / 30, 2)

    def test_days_span_less_than_one(self):
        """Span < 1 día → usa days default."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(100.0, ended_at=now - timedelta(hours=12)),
            _make_listing(110.0, ended_at=now),
        ]
        result = CompsResult.from_listings(listings, days=30)
        assert result.days_of_data == 30

    def test_sales_per_day_high_volume(self):
        """240 items en 15 días → sales_per_day = 16."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(
                100.0 + i * 0.1,
                ended_at=now - timedelta(days=15 * i / 239),
            )
            for i in range(240)
        ]
        result = CompsResult.from_listings(listings, days=30)
        assert result.days_of_data == 15.0
        assert result.sales_per_day == 16.0  # 240 / 15.0

    def test_empty_listings(self):
        """Lista vacía → days_of_data = days default."""
        result = CompsResult.from_listings([], days=30)
        assert result.days_of_data == 30
        assert result.total_sold == 0
        assert result.sales_per_day == 0.0

    def test_mixed_dates_and_none(self):
        """Solo listings con fecha se usan para calcular span."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(100.0, ended_at=now - timedelta(days=10)),
            _make_listing(110.0, ended_at=None),  # sin fecha
            _make_listing(105.0, ended_at=None),  # sin fecha
            _make_listing(108.0, ended_at=now),
        ]
        result = CompsResult.from_listings(listings, days=30)
        assert result.days_of_data == 10.0
        # total_sold cuenta todos los listings (4), no solo los con fecha
        assert result.total_sold == 4
        assert result.sales_per_day == round(4 / 10.0, 2)

    def test_precise_span_calculation(self):
        """Span de 7.5 días se redondea a 7.5."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(100.0, ended_at=now - timedelta(days=7, hours=12)),
            _make_listing(110.0, ended_at=now),
        ]
        result = CompsResult.from_listings(listings, days=30)
        assert result.days_of_data == 7.5
