"""Tests para Motor A — Comp Cleaner."""

from datetime import datetime, timedelta, timezone

from app.services.engines.comp_cleaner import (
    clean_comps,
    normalize_condition,
    _compute_relevance,
    _compute_stats,
    _extract_model_numbers,
    _matches_product_type,
    _filter_by_danger,
)
from app.services.marketplace.base import CompsResult, MarketplaceListing


def _make_listing(price: float, shipping: float = 0.0, **kwargs) -> MarketplaceListing:
    return MarketplaceListing(
        title=kwargs.get("title", "Test Product"),
        price=price,
        shipping_price=shipping,
        total_price=kwargs.get("total_price"),
        seller_username=kwargs.get("seller_username", "seller1"),
        seller_feedback_pct=kwargs.get("seller_feedback_pct"),
        ended_at=kwargs.get("ended_at"),
        brand=kwargs.get("brand"),
        model=kwargs.get("model"),
        item_specifics=kwargs.get("item_specifics"),
        condition=kwargs.get("condition"),
    )


def _make_comps(prices: list[float], days: int = 30) -> CompsResult:
    listings = [_make_listing(p) for p in prices]
    return CompsResult.from_listings(listings, marketplace="ebay", days=days)


class TestCompCleaner:
    def test_empty_comps(self):
        raw = CompsResult()
        result = clean_comps(raw)
        assert result.clean_total == 0
        assert result.raw_total == 0

    def test_no_outliers_small_set(self):
        raw = _make_comps([10.0, 12.0, 11.0])
        result = clean_comps(raw)
        assert result.clean_total == 3
        assert result.outliers_removed == 0

    def test_removes_outliers(self):
        # 10 precios normales + 1 outlier extremo
        prices = [50.0, 52.0, 48.0, 51.0, 49.0, 53.0, 47.0, 50.0, 51.0, 48.0, 200.0]
        raw = _make_comps(prices)
        result = clean_comps(raw)
        assert result.outliers_removed >= 1
        assert result.clean_total < len(prices)
        # El outlier de 200 debe haber sido removido
        clean_prices = [l.total_price for l in result.listings]
        assert 200.0 not in clean_prices

    def test_normalizes_prices(self):
        """Verifica que price + shipping = total_price."""
        listings = [
            _make_listing(10.0, shipping=5.0),
            _make_listing(12.0, shipping=3.0),
            _make_listing(11.0, shipping=4.0),
        ]
        raw = CompsResult.from_listings(listings, days=30)
        result = clean_comps(raw)
        # Todos deben tener total_price = price + shipping
        for l in result.listings:
            assert l.total_price == l.price + (l.shipping_price or 0)

    def test_statistics_recalculated(self):
        prices = [10.0, 20.0, 30.0, 40.0, 50.0]
        raw = _make_comps(prices)
        result = clean_comps(raw)
        assert result.median_price > 0
        assert result.avg_price > 0
        assert result.std_dev > 0
        assert result.cv > 0
        assert result.p25 <= result.median_price <= result.p75

    def test_sales_per_day_calculated(self):
        prices = [10.0] * 15
        raw = _make_comps(prices, days=30)
        result = clean_comps(raw)
        assert result.sales_per_day == 0.5  # 15 / 30

    def test_cv_calculation(self):
        """CV = std_dev / avg. Con precios iguales, CV = 0."""
        prices = [50.0] * 10
        raw = _make_comps(prices)
        result = clean_comps(raw)
        assert result.cv == 0.0


class TestComputeStats:
    def test_empty(self):
        stats = _compute_stats([])
        assert stats["median"] == 0.0

    def test_single_value(self):
        stats = _compute_stats([100.0])
        assert stats["median"] == 100.0
        assert stats["avg"] == 100.0
        assert stats["std_dev"] == 0.0

    def test_known_values(self):
        stats = _compute_stats([10.0, 20.0, 30.0, 40.0, 50.0])
        assert stats["median"] == 30.0
        assert stats["avg"] == 30.0


class TestComputeRelevance:
    def test_exact_match_high_relevance(self):
        listing = _make_listing(
            100.0,
            title="iPhone 15 Pro Max 256GB",
            brand="Apple",
            condition="Used",
            item_specifics={"Model": "iPhone 15 Pro Max", "Storage": "256GB"},
        )
        score = _compute_relevance(listing, "iPhone 15 Pro Max 256GB")
        assert score > 0.65

    def test_unrelated_low_relevance(self):
        listing = _make_listing(
            100.0,
            title="Samsung Galaxy S24 Ultra",
            brand="Samsung",
        )
        score = _compute_relevance(listing, "iPhone 15 Pro Max")
        assert score < 0.7


class TestNormalizeCondition:
    def test_new(self):
        assert normalize_condition("New") == "new"
        assert normalize_condition("Brand New") == "new"
        assert normalize_condition("New with tags") == "new"
        assert normalize_condition("New without box") == "new"
        assert normalize_condition("New other (see details)") == "new"

    def test_used(self):
        assert normalize_condition("Pre-Owned") == "used"
        assert normalize_condition("Used") == "used"
        assert normalize_condition("Very Good") == "used"
        assert normalize_condition("Acceptable") == "used"

    def test_refurbished(self):
        assert normalize_condition("Seller refurbished") == "refurbished"
        assert normalize_condition("Certified - Refurbished") == "refurbished"

    def test_open_box(self):
        assert normalize_condition("Open box") == "open_box"

    def test_for_parts(self):
        assert normalize_condition("For parts or not working") == "for_parts"

    def test_unknown(self):
        assert normalize_condition(None) == "unknown"
        assert normalize_condition("") == "unknown"


class TestConditionFiltering:
    def _make_comps_with_conditions(self, conditions: list[str]) -> CompsResult:
        """Crea comps con condiciones específicas, precios en rango normal."""
        listings = []
        for i, cond in enumerate(conditions):
            listings.append(_make_listing(
                50.0 + i,
                condition=cond,
            ))
        return CompsResult.from_listings(listings, marketplace="ebay", days=30)

    def test_any_keeps_all(self):
        raw = self._make_comps_with_conditions(
            ["New", "Used", "Pre-Owned", "New", "Used"]
        )
        result = clean_comps(raw, condition="any")
        assert result.clean_total == 5
        assert result.condition_filtered == 0
        assert result.requested_condition == "any"

    def test_new_filters_used(self):
        raw = self._make_comps_with_conditions(
            ["New", "New", "New", "Used", "Pre-Owned", "New"]
        )
        result = clean_comps(raw, condition="new")
        assert result.condition_filtered > 0
        assert result.clean_total == 4  # solo los New
        assert result.condition_match_rate == 1.0  # todos los finales son new

    def test_used_filters_new(self):
        raw = self._make_comps_with_conditions(
            ["Used", "Pre-Owned", "Used", "New", "New", "Used"]
        )
        result = clean_comps(raw, condition="used")
        assert result.condition_filtered > 0
        assert result.clean_total == 4  # los 3 Used + 1 Pre-Owned (ambos normalizan a "used")

    def test_insufficient_condition_keeps_all(self):
        """Si <3 comps coinciden, no filtra para no perder datos."""
        raw = self._make_comps_with_conditions(
            ["New", "Used", "Used", "Used", "Used"]
        )
        result = clean_comps(raw, condition="new")
        # Solo 1 New, no suficiente para filtrar → mantiene todos
        assert result.condition_filtered == 0
        assert result.clean_total == 5

    def test_condition_counts_populated(self):
        raw = self._make_comps_with_conditions(
            ["New", "New", "Used", "Open box", "Pre-Owned"]
        )
        result = clean_comps(raw, condition="any")
        assert "new" in result.condition_counts
        assert "used" in result.condition_counts
        assert result.condition_counts["new"] == 2

    def test_condition_match_rate_when_mixed(self):
        """Cuando se pide new pero no se puede filtrar, match_rate refleja la mezcla."""
        raw = self._make_comps_with_conditions(
            ["New", "Used", "Used", "Used", "Used"]
        )
        result = clean_comps(raw, condition="new")
        # No se filtró (solo 1 New), match_rate = 1/5 = 0.2
        assert result.condition_match_rate < 0.5


class TestTemporalFiltering:
    def test_filters_old_listings(self):
        """Listings fuera de la ventana temporal se filtran."""
        now = datetime.now(timezone.utc)
        listings = [
            # 3 dentro de la ventana (últimos 30 días)
            _make_listing(50.0, ended_at=now - timedelta(days=5)),
            _make_listing(52.0, ended_at=now - timedelta(days=10)),
            _make_listing(48.0, ended_at=now - timedelta(days=20)),
            # 2 fuera de la ventana (> 30 días)
            _make_listing(55.0, ended_at=now - timedelta(days=60)),
            _make_listing(45.0, ended_at=now - timedelta(days=90)),
        ]
        raw = CompsResult(listings=listings, days_of_data=30, total_sold=5)
        result = clean_comps(raw)
        assert result.clean_total == 3

    def test_keeps_listings_without_ended_at(self):
        """Listings sin ended_at no se penalizan."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(50.0, ended_at=now - timedelta(days=5)),
            _make_listing(52.0, ended_at=None),  # sin fecha
            _make_listing(48.0, ended_at=now - timedelta(days=10)),
        ]
        raw = CompsResult(listings=listings, days_of_data=30, total_sold=3)
        result = clean_comps(raw)
        assert result.clean_total == 3

    def test_all_old_listings_returns_empty(self):
        """Si todos los listings son viejos, retorna vacío."""
        now = datetime.now(timezone.utc)
        listings = [
            _make_listing(50.0, ended_at=now - timedelta(days=60)),
            _make_listing(52.0, ended_at=now - timedelta(days=90)),
        ]
        raw = CompsResult(listings=listings, days_of_data=30, total_sold=2)
        result = clean_comps(raw)
        assert result.clean_total == 0


class TestMatchesProductType:
    def test_exact_match(self):
        assert _matches_product_type("Oakley Aro3 MIPS Helmet", "helmet") is True

    def test_plural_match(self):
        assert _matches_product_type("Nike Running Shoes Size 10", "shoe") is True
        assert _matches_product_type("Nike Running Shoe", "shoes") is True

    def test_no_match(self):
        assert _matches_product_type("Oakley ARO3 Replacement Visor", "helmet") is False

    def test_case_insensitive(self):
        assert _matches_product_type("CYCLING HELMET PRO", "helmet") is True

    def test_ies_plural(self):
        assert _matches_product_type("Rechargeable Batteries Pack", "battery") is True
        assert _matches_product_type("AA Battery NiMH", "batteries") is True


class TestDangerFilter:
    def test_filters_replacement(self):
        listings = [
            _make_listing(100.0, title="Oakley Aro3 MIPS Helmet"),
            _make_listing(20.0, title="Oakley Aro3 Replacement Visor"),
            _make_listing(95.0, title="Oakley Aro3 Helmet Black"),
            _make_listing(15.0, title="Oakley Shield for Parts"),
        ]
        kept, removed = _filter_by_danger(listings, keyword="Oakley Aro3 Helmet")
        assert removed >= 2  # replacement + for_parts
        assert len(kept) == 2

    def test_keeps_when_keyword_matches_flag(self):
        """Si buscamos 'replacement visor', no filtra 'replacement'."""
        listings = [
            _make_listing(20.0, title="Oakley ARO3 Replacement Visor"),
            _make_listing(25.0, title="Oakley Replacement Shield"),
        ]
        kept, removed = _filter_by_danger(listings, keyword="Oakley replacement visor")
        assert removed == 0
        assert len(kept) == 2

    def test_no_danger_keeps_all(self):
        listings = [
            _make_listing(100.0, title="Oakley Aro3 MIPS Helmet"),
            _make_listing(95.0, title="Oakley Aro3 Helmet Black"),
        ]
        kept, removed = _filter_by_danger(listings)
        assert removed == 0
        assert len(kept) == 2


class TestProductTypeFiltering:
    def _make_comps_with_titles(self, titles_prices: list[tuple[str, float]]) -> CompsResult:
        listings = [
            _make_listing(price, title=title) for title, price in titles_prices
        ]
        return CompsResult.from_listings(listings, marketplace="ebay", days=30)

    def test_filters_non_matching_titles(self):
        raw = self._make_comps_with_titles([
            ("Oakley Aro3 MIPS Helmet", 100.0),
            ("Oakley ARO3 Replacement Visor", 20.0),
            ("Oakley Aro3 Helmet Matte Black", 95.0),
            ("Oakley Shield Lens Clear", 15.0),
            ("Oakley Cycling Helmet Pro", 110.0),
        ])
        result = clean_comps(raw, keyword="Oakley Aro3 Helmet", product_type="helmet")
        assert result.product_type_filtered >= 2  # visor + lens
        # Helmets should remain
        for l in result.listings:
            assert "helmet" in l.title.lower()

    def test_no_filter_when_too_few_remain(self):
        """Si filtrar deja < 3 comps, no filtra."""
        raw = self._make_comps_with_titles([
            ("Oakley Aro3 MIPS Helmet", 100.0),
            ("Oakley ARO3 Replacement Visor", 20.0),
            ("Oakley Shield Lens", 15.0),
            ("Oakley Visor Cover", 18.0),
        ])
        result = clean_comps(raw, keyword="Oakley Aro3 Helmet", product_type="helmet")
        # Solo 1 helmet, filtrar dejaría < 3 → no filtra
        assert result.product_type_filtered == 0

    def test_no_filter_without_product_type(self):
        raw = self._make_comps_with_titles([
            ("Oakley Aro3 MIPS Helmet", 100.0),
            ("Oakley ARO3 Replacement Visor", 20.0),
            ("Oakley Helmet Black", 95.0),
        ])
        result = clean_comps(raw, keyword="Oakley Aro3 Helmet")
        assert result.product_type_filtered == 0

    def test_danger_filter_in_clean_comps(self):
        """Danger filter removes high-weight flagged listings."""
        raw = self._make_comps_with_titles([
            ("iPhone 15 Pro 256GB", 800.0),
            ("iPhone 15 Pro Box Only", 30.0),
            ("iPhone 15 Pro Max", 900.0),
            ("iPhone 15 Pro Broken Screen", 200.0),
            ("iPhone 15 Pro 128GB", 750.0),
        ])
        result = clean_comps(raw, keyword="iPhone 15 Pro")
        # "box only" and "broken" should be filtered by danger
        assert result.danger_filtered >= 1


class TestExtractModelNumbers:
    def test_simple_model(self):
        result = _extract_model_numbers("Nike Vomero 6")
        assert result == {"vomero": "6"}

    def test_multiple_models(self):
        result = _extract_model_numbers("iPhone 15 Pro 256GB")
        assert "iphone" in result
        assert result["iphone"] == "15"

    def test_ignores_size(self):
        result = _extract_model_numbers("Vomero 6 Size 10")
        assert "vomero" in result
        assert "size" not in result

    def test_ignores_pack(self):
        result = _extract_model_numbers("Battery Pack of 4")
        assert "pack" not in result

    def test_ps5(self):
        result = _extract_model_numbers("PS5 Console")
        assert result == {"ps": "5"}

    def test_empty_string(self):
        assert _extract_model_numbers("") == {}

    def test_no_numbers(self):
        assert _extract_model_numbers("Nike Running Shoes") == {}


class TestModelNumberPenalty:
    def test_vomero_6_vs_vomero_5(self):
        """Bug original: Vomero 5 no debe pasar como comp de Vomero 6."""
        listing = _make_listing(100.0, title="Nike Vomero 5", brand="Nike")
        score = _compute_relevance(listing, "Nike Vomero 6")
        assert score < 0.75

    def test_vomero_6_matches_vomero_6(self):
        listing = _make_listing(100.0, title="Nike Vomero 6", brand="Nike")
        score = _compute_relevance(listing, "Nike Vomero 6")
        assert score > 0.50

    def test_iphone_15_vs_iphone_14(self):
        listing = _make_listing(800.0, title="iPhone 14 Pro Max", brand="Apple")
        score = _compute_relevance(listing, "iPhone 15 Pro Max")
        assert score < 0.75

    def test_size_numbers_not_penalized(self):
        """Diferencia de talla no debe penalizar el modelo."""
        listing_10 = _make_listing(100.0, title="Nike Vomero 6 Size 10", brand="Nike")
        listing_11 = _make_listing(100.0, title="Nike Vomero 6 Size 11", brand="Nike")
        score_10 = _compute_relevance(listing_10, "Nike Vomero 6 Size 11")
        score_11 = _compute_relevance(listing_11, "Nike Vomero 6 Size 11")
        assert abs(score_10 - score_11) < 0.15

    def test_no_model_number_no_penalty(self):
        listing = _make_listing(100.0, title="Nike Running Shoes", brand="Nike")
        score = _compute_relevance(listing, "Nike Running Shoes")
        assert score > 0.40


class TestCleanCompsModelFiltering:
    def test_filters_wrong_model_numbers(self):
        """3 listings Vomero 6 + 2 Vomero 5 → solo Vomero 6 sobrevive."""
        specs = {"Brand": "Nike", "Model": "Vomero", "Color": "Black"}
        listings = [
            _make_listing(100.0, title="Nike Vomero 6 Black", brand="Nike", condition="New", item_specifics=specs),
            _make_listing(95.0, title="Nike Vomero 6 White", brand="Nike", condition="New", item_specifics=specs),
            _make_listing(105.0, title="Nike Vomero 6 Red", brand="Nike", condition="New", item_specifics=specs),
            _make_listing(80.0, title="Nike Vomero 5 Blue", brand="Nike", condition="New", item_specifics=specs),
            _make_listing(85.0, title="Nike Vomero 5 Green", brand="Nike", condition="New", item_specifics=specs),
        ]
        raw = CompsResult.from_listings(listings, marketplace="ebay", days=30)
        result = clean_comps(raw, keyword="Nike Vomero 6")
        assert result.relevance_filtered >= 2
        for l in result.listings:
            assert "vomero 5" not in l.title.lower()
