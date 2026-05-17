"""Price history for watchlist product tracking."""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProductPriceHistory(Base):
    __tablename__ = "product_price_history"
    __table_args__ = (
        UniqueConstraint("product_id", "recorded_date", name="uq_product_price_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    recorded_date: Mapped[date] = mapped_column(Date, index=True)
    ebay_median_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    amazon_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    ebay_sold_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="cron")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    product = relationship("Product")
