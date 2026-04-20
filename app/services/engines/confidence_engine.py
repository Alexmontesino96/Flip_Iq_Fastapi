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
    config=None,
) -> ConfidenceResult:
    """Calcula score de confianza del análisis.

    Args:
        enriched: True si los listings fueron enriquecidos (LLM o detailedSearch).
        title_risk_score: 0-1 del title risk detector. Penaliza confianza.
        config: ResolvedConfig with category-specific thresholds (optional).
    """
    if cleaned.clean_total == 0:
        return ConfidenceResult(
            score=0,
            category="low",
            factors={"no_data": 1.0},
        )

    # Category-tunable thresholds
    sample_size = config.confidence_sample_size if config else 20
    weights = config.confidence_weights if config else [0.30, 0.25, 0.20, 0.15, 0.10]
    burst_thresh = config.confidence_burstiness_threshold if config else 0.3
    burst_mult = config.confidence_burstiness_multiplier if config else 40
    burst_cap = config.confidence_burstiness_cap if config else 15
    tr_mult = config.confidence_title_risk_multiplier if config else 20
    we_penalty_val = config.confidence_window_expansion_penalty if config else 10.0

    n_clean = cleaned.clean_total
    raw_total = max(cleaned.raw_total, 1)

    # sample_score
    sample_score = min(1.0, n_clean / sample_size)

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
    # Usar initial_days_requested como base (no la ventana expandida)
    # para evitar penalizar artificialmente cuando se expande 30→90 días.
    days_with_sales = len(set(
        l.ended_at.strftime("%Y-%m-%d")
        for l in cleaned.listings
        if l.ended_at
    ))
    timeline_base_days = cleaned.initial_days_requested if cleaned.temporal_window_expanded else cleaned.days_of_data
    timeline_score = days_with_sales / max(timeline_base_days, 1)
    timeline_score = min(1.0, timeline_score)  # Cap a 1.0

    # enrichment_quality: reemplaza detailed_flag
    # 1.0 si los datos fueron enriquecidos (LLM o detailed), 0.0 si raw
    enrichment_quality = 1.0 if enriched else 0.0

    score = 100 * (
        weights[0] * sample_score
        + weights[1] * consistency_score
        + weights[2] * attribute_score
        + weights[3] * timeline_score
        + weights[4] * enrichment_quality
    )

    # Penalización explícita por expansión temporal:
    window_expansion_penalty = 0.0
    if cleaned.temporal_window_expanded:
        window_expansion_penalty = we_penalty_val
        score -= window_expansion_penalty

    # Penalizar por title risk (títulos ambiguos contaminan la confianza)
    if title_risk_score > 0:
        penalty = title_risk_score * tr_mult
        score -= penalty

    # Penalizar por burstiness alta (ventas concentradas en pocos días)
    burst_penalty = 0.0
    if burstiness > burst_thresh:
        burst_penalty = min(burst_cap, (burstiness - burst_thresh) * burst_mult)
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
            "window_expansion_penalty": round(window_expansion_penalty, 2),
        },
    )
