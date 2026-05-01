from decimal import Decimal

from app.core.fees import calculate_margin, ebay_fees, amazon_fba_fees


def test_ebay_fees_basic():
    result = ebay_fees(Decimal("100"))
    assert result["marketplace"] == "ebay"
    assert result["final_value_fee"] == 13.55  # 13.25% + $0.30 per-order
    assert result["net_proceeds"] == 86.45


def test_amazon_fba_fees_basic():
    result = amazon_fba_fees(Decimal("100"))
    assert result["marketplace"] == "amazon_fba"
    assert result["referral_fee"] == 15.0
    assert result["fba_fee"] == 3.5
    assert result["net_proceeds"] == 81.5


def test_calculate_margin_positive():
    result = calculate_margin(Decimal("30"), Decimal("100"), "ebay")
    assert result["profit"] > 0
    assert result["margin_pct"] > 0
    assert result["roi_pct"] > 0


def test_calculate_margin_negative():
    result = calculate_margin(Decimal("95"), Decimal("100"), "amazon_fba")
    assert result["profit"] < 0
