"""Interfaz base para integraciones con marketplaces."""

import math
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MarketplaceListing:
    title: str
    price: float
    condition: str | None = None
    url: str | None = None
    sold: bool = False
    marketplace: str = ""
    item_id: str | None = None
    shipping_price: float | None = None
    total_price: float | None = None
    ended_at: datetime | None = None
    seller_username: str | None = None
    seller_feedback_pct: float | None = None
    # Campos de detailedSearch
    brand: str | None = None
    model: str | None = None
    category_path: str | None = None
    item_specifics: dict | None = None
    quantity_sold: int | None = None
    bids: int | None = None
    # Bundle detection (del LLM enrichment)
    is_bundle: bool = False
    lot_size: int = 1


@dataclass
class PriceBucket:
    """Un rango de precio con la cantidad de unidades vendidas en ese rango."""
    range_min: float
    range_max: float
    count: int
    pct_of_total: float  # porcentaje del total


@dataclass
class SalesByDate:
    """Ventas agrupadas por fecha."""
    date: str  # YYYY-MM-DD
    count: int
    avg_price: float
    min_price: float
    max_price: float


@dataclass
class CompsResult:
    """Resultado agregado de comparables de ventas."""
    listings: list[MarketplaceListing] = field(default_factory=list)
    # Stats de precio
    avg_price: float = 0.0
    median_price: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    std_dev: float = 0.0
    p25: float = 0.0  # percentil 25
    p75: float = 0.0  # percentil 75
    # Volumen
    total_sold: int = 0
    days_of_data: int = 30
    sales_per_day: float = 0.0
    marketplace: str = ""
    # Distribucion
    price_buckets: list[PriceBucket] = field(default_factory=list)
    sales_by_date: list[SalesByDate] = field(default_factory=list)
    # Enrichment
    image_url: str | None = None
    # Flag: True si ya pasó por reranker semántico (saltar filtros heurísticos en comp_cleaner)
    reranked: bool = False

    @classmethod
    def from_listings(cls, listings: list[MarketplaceListing], marketplace: str = "", days: int = 30) -> "CompsResult":
        if not listings:
            return cls(marketplace=marketplace, days_of_data=days)

        prices = sorted(l.total_price or l.price for l in listings)
        n = len(prices)

        avg = sum(prices) / n
        median = prices[n // 2] if n % 2 == 1 else (prices[n // 2 - 1] + prices[n // 2]) / 2
        variance = sum((p - avg) ** 2 for p in prices) / n
        std_dev = math.sqrt(variance)
        p25 = prices[max(0, n // 4 - 1)] if n >= 4 else prices[0]
        p75 = prices[min(n - 1, 3 * n // 4)] if n >= 4 else prices[-1]

        result = cls(
            listings=listings,
            avg_price=round(avg, 2),
            median_price=round(median, 2),
            min_price=round(prices[0], 2),
            max_price=round(prices[-1], 2),
            std_dev=round(std_dev, 2),
            p25=round(p25, 2),
            p75=round(p75, 2),
            total_sold=n,
            days_of_data=days,
            sales_per_day=round(n / max(days, 1), 2),
            marketplace=marketplace,
        )

        result.price_buckets = build_price_buckets(prices)
        result.sales_by_date = build_sales_by_date(listings)
        return result


def build_price_buckets(prices: list[float], num_buckets: int = 5) -> list[PriceBucket]:
    """Agrupa precios en buckets para ver distribucion."""
    if not prices:
        return []

    min_p, max_p = prices[0], prices[-1]
    if min_p == max_p:
        return [PriceBucket(range_min=min_p, range_max=max_p, count=len(prices), pct_of_total=100.0)]

    step = (max_p - min_p) / num_buckets
    buckets = []
    n = len(prices)

    for i in range(num_buckets):
        lo = round(min_p + step * i, 2)
        hi = round(min_p + step * (i + 1), 2)
        count = sum(1 for p in prices if lo <= p < hi) if i < num_buckets - 1 else sum(1 for p in prices if lo <= p <= hi)
        buckets.append(PriceBucket(
            range_min=lo,
            range_max=hi,
            count=count,
            pct_of_total=round(count / n * 100, 1) if n else 0,
        ))

    return [b for b in buckets if b.count > 0]


def build_sales_by_date(listings: list[MarketplaceListing]) -> list[SalesByDate]:
    """Agrupa ventas por fecha."""
    by_date: dict[str, list[float]] = {}
    for l in listings:
        if not l.ended_at:
            continue
        date_str = l.ended_at.strftime("%Y-%m-%d")
        price = l.total_price or l.price
        by_date.setdefault(date_str, []).append(price)

    result = []
    for date_str in sorted(by_date.keys()):
        prices = by_date[date_str]
        result.append(SalesByDate(
            date=date_str,
            count=len(prices),
            avg_price=round(sum(prices) / len(prices), 2),
            min_price=round(min(prices), 2),
            max_price=round(max(prices), 2),
        ))
    return result


@dataclass
class CleanedComps:
    """Resultado de comps después de limpieza (Motor A)."""
    listings: list[MarketplaceListing] = field(default_factory=list)
    raw_total: int = 0
    clean_total: int = 0
    outliers_removed: int = 0
    relevance_filtered: int = 0
    condition_filtered: int = 0
    median_price: float = 0.0
    avg_price: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    iqr: float = 0.0
    std_dev: float = 0.0
    cv: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    sales_per_day: float = 0.0
    days_of_data: int = 30
    # Condition analysis
    requested_condition: str = "any"
    condition_counts: dict[str, int] = field(default_factory=dict)
    condition_match_rate: float = 1.0
    # Product type / danger filtering
    danger_filtered: int = 0
    product_type_filtered: int = 0
    # Condition subset stats (cuando safety net impide filtrar)
    condition_subset_count: int = 0
    condition_subset_median: float | None = None


class MarketplaceClient(ABC):
    """Interfaz que cada marketplace debe implementar."""

    @abstractmethod
    async def search_by_barcode(self, barcode: str) -> list[MarketplaceListing]:
        ...

    @abstractmethod
    async def search_by_keyword(self, keyword: str, limit: int = 20) -> list[MarketplaceListing]:
        ...

    @abstractmethod
    async def get_sold_comps(self, barcode: str | None = None, keyword: str | None = None, days: int = 30, limit: int = 50) -> CompsResult:
        """Obtiene ventas completadas (comps) para estimar precio de venta."""
        ...
