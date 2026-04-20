from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    barcode: Mapped[str | None] = mapped_column(String(50), index=True)
    ebay_epid: Mapped[str | None] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(500))
    normalized_title: Mapped[str | None] = mapped_column(Text, index=True)
    brand: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(Text)
    avg_sell_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    # Popularity signals
    search_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scan_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Cached eBay pricing
    ebay_avg_sold_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    ebay_listing_count: Mapped[int | None] = mapped_column(Integer)
    price_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    analyses = relationship("Analysis", back_populates="product")
