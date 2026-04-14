from datetime import datetime

from pydantic import BaseModel


class ProductOut(BaseModel):
    id: int
    barcode: str | None
    title: str
    brand: str | None
    category: str | None
    image_url: str | None
    avg_sell_price: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ProductSearch(BaseModel):
    """Buscar producto por barcode o keyword."""
    barcode: str | None = None
    keyword: str | None = None
