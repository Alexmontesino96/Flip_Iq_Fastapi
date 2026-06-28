"""Tests de las señales del producto evaluado (multipack) — PR-M2.

Verifica _pick_main_product, _extract_package_quantity y que el CompsResult
lleve evaluated_title / evaluated_package_quantity para el guard de M3.
"""

import pytest

from app.services.marketplace.amazon import (
    AmazonClient,
    _extract_package_quantity,
    _pick_main_product,
)


def _product_with_offer(
    asin="B0X",
    title="Widget",
    package_quantity=None,
    number_of_items=None,
    rank=None,
):
    """Producto Keepa con UNA oferta válida (genera 1 listing)."""
    p = {
        "asin": asin,
        "title": title,
        "offers": [
            {"offerCSV": [1000, 1500, 0], "condition": 1,
             "sellerName": "Seller", "isFBA": True},
        ],
    }
    if package_quantity is not None:
        p["packageQuantity"] = package_quantity
    if number_of_items is not None:
        p["numberOfItems"] = number_of_items
    if rank is not None:
        # current[CSV_SALES_RANK] == current[3]
        p["stats"] = {"current": [None, None, None, rank]}
    return p


class TestPickMainProduct:
    def test_empty(self):
        assert _pick_main_product([]) is None

    def test_single(self):
        p = {"asin": "B1"}
        assert _pick_main_product([p]) is p

    def test_best_rank_wins(self):
        a = {"asin": "A", "stats": {"current": [None, None, None, 5000]}}
        b = {"asin": "B", "stats": {"current": [None, None, None, 1000]}}
        assert _pick_main_product([a, b])["asin"] == "B"

    def test_fallback_to_first_without_rank(self):
        a = {"asin": "A"}
        b = {"asin": "B"}
        assert _pick_main_product([a, b])["asin"] == "A"

    def test_ignores_sales_rank_reference(self):
        # Debe usar current[3], NO salesRankReference (id de categoría, Findings #9).
        a = {"asin": "A", "stats": {"salesRankReference": 1, "current": [None, None, None, 5000]}}
        b = {"asin": "B", "stats": {"current": [None, None, None, 3000]}}
        # Si usara salesRankReference, A (=1) ganaría; con current[3], gana B (3000<5000).
        assert _pick_main_product([a, b])["asin"] == "B"

    def test_rank_zero_or_negative_treated_as_missing(self):
        a = {"asin": "A", "stats": {"current": [None, None, None, 0]}}
        b = {"asin": "B", "stats": {"current": [None, None, None, -1]}}
        # Ninguno tiene rank válido → fallback al primero.
        assert _pick_main_product([a, b])["asin"] == "A"


class TestExtractPackageQuantity:
    def test_package_quantity(self):
        assert _extract_package_quantity({"packageQuantity": 12}) == 12

    def test_number_of_items_fallback(self):
        assert _extract_package_quantity({"numberOfItems": 6}) == 6

    def test_package_quantity_takes_priority(self):
        assert _extract_package_quantity({"packageQuantity": 12, "numberOfItems": 6}) == 12

    def test_missing_returns_none(self):
        assert _extract_package_quantity({"asin": "B1"}) is None

    def test_zero_not_returned(self):
        # 0 no es una cantidad válida → cae al siguiente o None (nunca 0).
        assert _extract_package_quantity({"packageQuantity": 0}) is None
        assert _extract_package_quantity({"packageQuantity": 0, "numberOfItems": 3}) == 3

    def test_string_coerced(self):
        assert _extract_package_quantity({"packageQuantity": "12"}) == 12

    def test_garbage_returns_none(self):
        assert _extract_package_quantity({"packageQuantity": "abc"}) is None
        assert _extract_package_quantity({"packageQuantity": None}) is None


class TestEvaluatedSignalsIntegration:
    def setup_method(self):
        self.client = AmazonClient()

    def test_signals_populated(self):
        p = _product_with_offer(
            asin="B0PACK", title="Soap (Pack of 12)", package_quantity=12,
        )
        result = self.client._build_comps_from_products([p], 30, 50, "test")
        assert result.evaluated_title == "Soap (Pack of 12)"
        assert result.evaluated_package_quantity == 12

    def test_signals_none_when_absent(self):
        p = _product_with_offer(asin="B0SINGLE", title="Widget")
        result = self.client._build_comps_from_products([p], 30, 50, "test")
        assert result.evaluated_title == "Widget"
        assert result.evaluated_package_quantity is None

    def test_main_product_chosen_by_rank(self):
        # Dos productos: el de mejor rank define las señales evaluadas.
        pack = _product_with_offer(
            asin="A", title="Item (Pack of 6)", package_quantity=6, rank=9000,
        )
        single = _product_with_offer(
            asin="B", title="Item Single", package_quantity=1, rank=100,
        )
        result = self.client._build_comps_from_products([pack, single], 30, 50, "test")
        # 'single' tiene mejor rank (100 < 9000) → es el evaluado.
        assert result.evaluated_title == "Item Single"
        assert result.evaluated_package_quantity == 1

    def test_empty_listings_no_crash(self):
        # Producto sin offers ni buybox → sin listings → no crashea.
        result = self.client._build_comps_from_products(
            [{"asin": "B0", "title": "No Offers"}], 30, 50, "test",
        )
        # Retorna temprano (CompsResult vacío); las señales quedan en default None.
        assert result.evaluated_package_quantity is None
