"""Motor G — Confidence Engine.

Evalúa qué tan confiable es el análisis.

Formula:
  confidence = 100 * (0.30*sample + 0.25*consistency + 0.20*attribute
                      + 0.15*timeline + 0.10*detailed_flag)

Thresholds: baja (0-49), media (50-69), media_alta (70-84), alta (85+)
"""

from dataclasses import dataclass

from app.services.marketplace.base import CleanedComps, CompsResult


@dataclass
class ConfidenceResult:
    score: int           # 0-100
    category: str        # high|medium_high|medium|low
    factors: dict[str, float]


def compute_confidence(
    cleaned: CleanedComps,
    raw: CompsResult,
    enriched: bool = False,
    title_risk_score: float = 0.0,
    burstiness: float = 0.0,
) -> ConfidenceResult:
    """Calcula score de confianza del análisis.

    Args:
        enriched: True si los listings fueron enriquecidos (LLM o detailedSearch).
        title_risk_score: 0-1 del title risk detector. Penaliza confianza.
    """
    if cleaned.clean_total == 0:
        return ConfidenceResult(
            score=0,
            category="low",
            factors={"no_data": 1.0},
        )

    n_clean = cleaned.clean_total
    raw_total = max(cleaned.raw_total, 1)

    # sample_score: min(1, n_clean / 20)
    sample_score = min(1.0, n_clean / 20)

    # consistency_score: 1 - outlier_share
    outlier_share = cleaned.outliers_removed / raw_total
    consistency_score = 1 - outlier_share

    # attribute_score: comps con brand/model (del LLM enrichment o detailedSearch)
    attribute_score = 0.0
    if cleaned.listings:
        with_attrs = sum(
            1 for l in cleaned.listings if l.brand or l.model
        )
        attribute_score = with_attrs / len(cleaned.listings)

    # timeline_score: días con ventas / lookback_days
    days_with_sales = len(set(
        l.ended_at.strftime("%Y-%m-%d")
        for l in cleaned.listings
        if l.ended_at
    ))
    timeline_score = days_with_sales / max(cleaned.days_of_data, 1)

    # enrichment_quality: reemplaza detailed_flag
    # 1.0 si los datos fueron enriquecidos (LLM o detailed), 0.0 si raw
    enrichment_quality = 1.0 if enriched else 0.0

    score = 100 * (
        0.30 * sample_score
        + 0.25 * consistency_score
        + 0.20 * attribute_score
        + 0.15 * timeline_score
        + 0.10 * enrichment_quality
    )

    # Penalizar por title risk (títulos ambiguos contaminan la confianza)
    if title_risk_score > 0:
        penalty = title_risk_score * 20  # hasta -20 puntos
        score -= penalty

    # Penalizar por burstiness alta (ventas concentradas en pocos días)
    burst_penalty = 0.0
    if burstiness > 0.3:
        burst_penalty = min(15, (burstiness - 0.3) * 40)
        score -= burst_penalty

    score = min(100, max(0, round(score)))

    # 4 niveles de confianza
    if score >= 85:
        category = "high"
    elif score >= 70:
        category = "medium_high"
    elif score >= 50:
        category = "medium"
    else:
        category = "low"

    return ConfidenceResult(
        score=score,
        category=category,
        factors={
            "sample": round(sample_score, 3),
            "consistency": round(consistency_score, 3),
            "attribute": round(attribute_score, 3),
            "timeline": round(timeline_score, 3),
            "enrichment": enrichment_quality,
            "title_risk_penalty": round(title_risk_score * 20, 2),
            "burstiness_penalty": round(burst_penalty, 2),
        },
    )
