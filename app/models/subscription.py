from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)

    # Provider: "stripe" | "apple"
    provider: Mapped[str] = mapped_column(String(20), default="stripe")

    # Stripe fields (nullable for Apple subscriptions)
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    stripe_price_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Apple fields (nullable for Stripe subscriptions)
    apple_original_transaction_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    apple_product_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Common fields
    status: Mapped[str] = mapped_column(String(50))  # active|past_due|canceled|expired|refunded
    plan: Mapped[str] = mapped_column(String(20))  # starter|pro
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="subscription")
