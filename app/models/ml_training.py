from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MLTrainingSample(Base):
    """Samples de entrenamiento para modelos ML locales.

    Cada vez que el LLM clasifica un titulo, se guarda el par
    (input, output) para entrenar modelos offline.
    """

    __tablename__ = "ml_training_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task: Mapped[str] = mapped_column(String(30), index=True)  # comp_relevance | title_enrichment
    input_keyword: Mapped[str | None] = mapped_column(String(500), nullable=True)
    input_title: Mapped[str] = mapped_column(Text)
    llm_output: Mapped[dict] = mapped_column(JSON)
    llm_provider: Mapped[str] = mapped_column(String(20))  # gemini | openai
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class MLShadowComparison(Base):
    """Comparaciones shadow-mode entre ML y LLM."""

    __tablename__ = "ml_shadow_comparisons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task: Mapped[str] = mapped_column(String(30), index=True)
    input_keyword: Mapped[str | None] = mapped_column(String(500), nullable=True)
    input_title: Mapped[str] = mapped_column(Text)
    ml_prediction: Mapped[dict] = mapped_column(JSON)
    llm_prediction: Mapped[dict] = mapped_column(JSON)
    agreed: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
