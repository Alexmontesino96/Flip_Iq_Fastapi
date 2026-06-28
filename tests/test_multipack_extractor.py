"""Tests del extractor de bundle factor (multipack) — PR-M1.

Cubre regex_bundle_factor / is_multipack_title / has_pack_signal y verifica el
fix del bug "N count" en _filter_multipacks (antes descartaba unidades sueltas
válidas como "Vitamin C, 100 Count").
"""

import pytest

from app.services.marketplace.amazon import _filter_multipacks, _is_multipack
from app.services.marketplace.multipack import (
    has_pack_signal,
    is_multipack_title,
    regex_bundle_factor,
)


class TestRegexBundleFactor:
    @pytest.mark.parametrize("title,expected", [
        ("Soap (Pack of 12)", 12),
        ("Trojan Condoms, 3 Count (Pack of 12)", 12),  # ignora el "3 Count"
        ("AA Batteries 24-Pack", 24),
        ("Paper Towels 6-pk", 6),
        ("Dish Soap, Case of 8", 8),
        ("Storage Box of 50 Gloves", 50),
        ("Set of 4 Mugs", 4),
        ("Lot of 10 Pens", 10),
        ("Bundle of 3 Notebooks", 3),
        ("Razors Twin Pack", 2),
        ("Gum Triple Pack", 3),
    ])
    def test_unambiguous_bundles(self, title, expected):
        assert regex_bundle_factor(title) == expected

    @pytest.mark.parametrize("title", [
        "Vitamin C, 100 Count",       # "N count" ambiguo → None (NO se filtra)
        "Trojan Condoms, 3 Count",    # descriptor de la unidad base
        "Paper Towels, 12 Count",     # ambiguo (podría ser 12 rollos → LLM)
        "K-Cups 24 ct",               # ambiguo
        "Plain Widget",               # sin señal
        "Shampoo 16 fl oz",           # tamaño, no pack
        "",                           # vacío
    ])
    def test_ambiguous_or_none(self, title):
        assert regex_bundle_factor(title) is None

    def test_takes_largest_factor(self):
        # Si conviven varios patrones, toma el mayor.
        assert regex_bundle_factor("Pack of 6 plus 12-pack bonus") == 12

    def test_caps_unreasonable_number(self):
        # Un número absurdo adyacente a "pack of" se descarta por el cap.
        assert regex_bundle_factor("Pack of 9999") is None

    @pytest.mark.parametrize("value", [None, 123, [], {}])
    def test_non_string_input(self, value):
        assert regex_bundle_factor(value) is None


class TestIsMultipackTitle:
    @pytest.mark.parametrize("title,expected", [
        ("Soap (Pack of 12)", True),
        ("AA Batteries 24-Pack", True),
        ("Razors Twin Pack", True),
        ("Pack of 1 Widget", False),       # "Pack of 1" no es multipack
        ("Vitamin C, 100 Count", False),   # FIX del bug: "N count" NO es multipack
        ("Condoms, 3 Count", False),
        ("Plain Widget", False),
    ])
    def test_is_multipack(self, title, expected):
        assert is_multipack_title(title) is expected

    def test_delegation_from_amazon_client(self):
        # _is_multipack de amazon.py delega en la fuente única de verdad.
        assert _is_multipack("Soap (Pack of 12)") is True
        assert _is_multipack("Vitamin C, 100 Count") is False


class TestHasPackSignal:
    @pytest.mark.parametrize("title", [
        "Soap Pack of 12",
        "Towels 12ct",     # conteo pegado
        "Batteries 36CT",  # conteo pegado, mayúsculas
        "Gum 12pk",
        "Set of 4",
        "Item (6)",
        "Cereal 2 x 18",
    ])
    def test_detects_signal(self, title):
        assert has_pack_signal(title) is True

    @pytest.mark.parametrize("title", [
        "Plain Widget",
        "Shampoo 16 fl oz",
        "",
        None,
    ])
    def test_no_signal(self, title):
        assert has_pack_signal(title) is False


class TestFilterMultipacksFix:
    """El fix del bug 'N count' no descarta unidades sueltas legítimas."""

    def test_keeps_n_count_singles(self):
        # ANTES (bug): "Vitamin C, 100 Count" se descartaba como multipack.
        # AHORA: se conserva (es una unidad simple legítima).
        products = [
            {"asin": "B1", "title": "Vitamin C, 100 Count"},
            {"asin": "B2", "title": "Vitamin C, 250 Count"},
        ]
        result = _filter_multipacks(products)
        assert len(result) == 2

    def test_filters_real_packs_keeps_single(self):
        products = [
            {"asin": "B1", "title": "Vitamin C (Pack of 3)"},  # pack inequívoco
            {"asin": "B2", "title": "Vitamin C, 100 Count"},   # unidad simple
        ]
        result = _filter_multipacks(products)
        assert len(result) == 1
        assert result[0]["asin"] == "B2"

    def test_no_filter_if_all_packs(self):
        # Si todos son multipack, no filtra (datos incompletos > sin datos).
        products = [
            {"asin": "B1", "title": "Soap (Pack of 6)"},
            {"asin": "B2", "title": "Soap (Pack of 12)"},
        ]
        result = _filter_multipacks(products)
        assert len(result) == 2

    def test_handles_missing_title(self):
        products = [{"asin": "B1"}, {"asin": "B2", "title": "Soap (Pack of 6)"}]
        result = _filter_multipacks(products)
        # B1 sin título no es multipack; B2 sí → queda B1.
        assert len(result) == 1
        assert result[0]["asin"] == "B1"
