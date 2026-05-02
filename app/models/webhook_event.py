from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))  # "apple" | "stripe"
    event_type: Mapped[str] = mapped_column(String(100))  # "SUBSCRIBED", "checkout.session.completed"
    event_subtype: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # "success" | "error" | "ignored"
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
