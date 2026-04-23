from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, String, DateTime, Integer, Numeric, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Analysis(Base):
    """Resultado de un análisis de rentabilidad para un producto."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)

    # Inputs
    cost_price: Mapped[float] = mapped_column(Numeric(10, 2))
    marketplace: Mapped[str] = mapped_column(String(50))  # ebay|amazon_fba

    # Results
    estimated_sale_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    net_profit: Mapped[float | None] = mapped_column(Numeric(10, 2))
    margin_pct: Mapped[float | None] = mapped_column(Numeric(10, 2))
    roi_pct: Mapped[float | None] = mapped_column(Numeric(10, 2))

    # Scores (0-100)
    flip_score: Mapped[int | None] = mapped_column(Integer)  # Score general de rentabilidad
    risk_score: Mapped[int | None] = mapped_column(Integer)  # 100=bajo riesgo, 0=alto
    velocity_score: Mapped[int | None] = mapped_column(Integer)  # Velocidad de venta estimada

    # Nuevos scores
    confidence_score: Mapped[int | None] = mapped_column(Integer)
    opportunity_score: Mapped[int | None] = mapped_column(Integer)

    recommendation: Mapped[str | None] = mapped_column(String(20))  # buy|pass|watch
    channels: Mapped[dict | None] = mapped_column(JSON)  # Desglose por marketplace

    # Data completa de motores (JSON blob)
    engines_data: Mapped[dict | None] = mapped_column(JSON)

    # AI
    ai_explanation: Mapped[str | None] = mapped_column(Text)

    # Inputs del usuario
    shipping_cost: Mapped[float | None] = mapped_column(Numeric(10, 2))
    prep_cost: Mapped[float | None] = mapped_column(Numeric(10, 2))

    # Data quality flags
    no_comps_found: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Share
    share_token: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="analyses")
    product = relationship("Product", back_populates="analyses")
    feedbacks = relationship("AnalysisFeedback", back_populates="analysis", cascade="all, delete-orphan")


class AnalysisFeedback(Base):
    """Feedback del usuario sobre un análisis (incorrecto, outdated, etc.)."""

    __tablename__ = "analysis_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    feedback_type: Mapped[str] = mapped_column(String(30))  # incorrect_price|incorrect_recommendation|outdated|other
    comment: Mapped[str | None] = mapped_column(Text)
    actual_sale_price: Mapped[float | None] = mapped_column(Numeric(10, 2))  # precio real si el user vendió

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    analysis = relationship("Analysis", back_populates="feedbacks")
    user = relationship("User")
