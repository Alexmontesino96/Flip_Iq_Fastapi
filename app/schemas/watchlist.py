from datetime import datetime

from pydantic import BaseModel


class WatchlistCreate(BaseModel):
    name: str = "Mi Watchlist"


class WatchlistItemAdd(BaseModel):
    product_id: int
    target_buy_price: float | None = None
    notes: str | None = None


class WatchlistItemOut(BaseModel):
    id: int
    product_id: int
    product_title: str | None = None
    target_buy_price: float | None
    notes: str | None
    added_at: datetime


class WatchlistOut(BaseModel):
    id: int
    name: str
    items: list[WatchlistItemOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}
