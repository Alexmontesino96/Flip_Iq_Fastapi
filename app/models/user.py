from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supabase_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tier: Mapped[str] = mapped_column(String(20), default="free")  # free|starter|pro
    credits_remaining: Mapped[int] = mapped_column(Integer, default=150)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    onesignal_subscription_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    customerio_synced: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    analyses = relationship("Analysis", back_populates="user")
    watchlists = relationship("Watchlist", back_populates="user")
    subscription = relationship("Subscription", back_populates="user", uselist=False)
