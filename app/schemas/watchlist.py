from datetime import date, datetime

from pydantic import BaseModel


class WatchlistCreate(BaseModel):
    name: str = "Mi Watchlist"


class WatchlistItemAdd(BaseModel):
    product_id: int
    target_buy_price: float | None = None
    notes: str | None = None


class PriceSnapshot(BaseModel):
    ebay_median_price: float | None = None
    amazon_price: float | None = None
    price_updated_at: datetime | None = None
    price_change_7d_pct: float | None = None
    below_target: bool = False


class WatchlistItemOut(BaseModel):
    id: int
    product_id: int
    product_title: str | None = None
    product_image_url: str | None = None
    target_buy_price: float | None
    notes: str | None
    added_at: datetime
    pricing: PriceSnapshot | None = None


class WatchlistOut(BaseModel):
    id: int
    name: str
    items: list[WatchlistItemOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class PriceHistoryPoint(BaseModel):
    date: date
    ebay_price: float | None = None
    amazon_price: float | None = None
