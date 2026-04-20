"""Test helpers — factory functions for engine test fixtures."""

from app.services.marketplace.base import CleanedComps, CompsResult, MarketplaceListing


def make_cleaned_comps(
    clean_total: int = 20,
    raw_total: int | None = None,
    median_price: float = 100.0,
    avg_price: float | None = None,
    p25: float = 80.0,
    p75: float = 120.0,
    iqr: float = 40.0,
    cv: float = 0.20,
    min_price: float = 70.0,
    max_price: float = 130.0,
    sales_per_day: float = 0.5,
    days_of_data: float = 30,
    outliers_removed: int = 0,
    listings: list | None = None,
    **kwargs,
) -> CleanedComps:
    """Create a CleanedComps with sensible defaults for unit tests."""
    if raw_total is None:
        raw_total = clean_total + outliers_removed
    if avg_price is None:
        avg_price = median_price

    return CleanedComps(
        clean_total=clean_total,
        raw_total=raw_total,
        median_price=median_price,
        avg_price=avg_price,
        p25=p25,
        p75=p75,
        iqr=iqr,
        cv=cv,
        std_dev=median_price * cv,
        min_price=min_price,
        max_price=max_price,
        sales_per_day=sales_per_day,
        days_of_data=days_of_data,
        outliers_removed=outliers_removed,
        listings=listings or [],
        **kwargs,
    )


def make_comps_result(
    listings: list | None = None,
    total_sold: int = 20,
    **kwargs,
) -> CompsResult:
    """Create a CompsResult with sensible defaults for unit tests."""
    return CompsResult(
        listings=listings or [],
        total_sold=total_sold,
        **kwargs,
    )
