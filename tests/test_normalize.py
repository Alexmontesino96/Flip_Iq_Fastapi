"""Tests for title normalization."""

from app.core.normalize import normalize_title


def test_lowercase():
    assert normalize_title("NIKE AIR MAX") == "nike air max"


def test_accent_removal():
    assert normalize_title("café résumé") == "cafe resume"


def test_size_variants_collapsed():
    assert "sz" in normalize_title("Size 10")
    assert "sz" in normalize_title("Talla 10")
    assert "sz" in normalize_title("Sz 10")
    assert "sz" in normalize_title("T10")


def test_number_letter_separation():
    assert normalize_title("airmax90") == "airmax 90"
    assert normalize_title("90white") == "90 white"


def test_special_chars_removed():
    assert normalize_title("Nike - Air Max (2024)") == "nike air max 2024"


def test_whitespace_collapsed():
    assert normalize_title("  Nike   Air   Max  ") == "nike air max"


def test_complex_title():
    result = normalize_title("NIKE Air Max 90 White Sz 10 - NEW!")
    assert result == "nike air max 90 white sz 10 new"


def test_empty_and_minimal():
    assert normalize_title("") == ""
    assert normalize_title("a") == "a"
    assert normalize_title("42") == "42"
