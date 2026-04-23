from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ManualReviewRequest(Base):
    """Producto no encontrado que requiere revisión manual."""

    __tablename__ = "manual_review_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("analyses.id"), nullable=True, index=True
    )

    # Qué buscó el usuario
    query: Mapped[str] = mapped_column(String(500))  # barcode o keyword usado
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_price: Mapped[float | None] = mapped_column(nullable=True)
    marketplace: Mapped[str] = mapped_column(String(50), default="ebay")

    # Estado del review
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )  # pending | in_progress | resolved | dismissed

    # Resultado: análisis manual vinculado
    resolved_analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("analyses.id"), nullable=True
    )
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", foreign_keys=[user_id])
    original_analysis = relationship("Analysis", foreign_keys=[analysis_id])
    resolved_analysis = relationship("Analysis", foreign_keys=[resolved_analysis_id])
