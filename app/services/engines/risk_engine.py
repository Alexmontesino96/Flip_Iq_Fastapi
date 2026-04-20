"""Motor F — Risk Engine.

Calcula estabilidad del mercado (100=bajo riesgo, 0=alto riesgo).

Formula:
  risk_score = 100 - 35*min(1, CV/0.60)
                   - 30*min(1, dispersion_ratio/0.60)
                   - 20*outlier_share
                   - 15*sample_penalty
"""

from dataclasses import dataclass

from app.services.marketplace.base import CleanedComps, CompsResult


@dataclass
class RiskResult:
    score: int           # 0-100 (100=low risk, 0=high risk)
    category: str        # low|medium|high
    factors: dict[str, float]


def compute_risk(cleaned: CleanedComps, raw: CompsResult, config=None) -> RiskResult:
    """Calcula score de riesgo/estabilidad basado en CV y dispersión."""
    if cleaned.clean_total == 0:
        return RiskResult(
            score=0,
            category="high",
            factors={"no_data": 1.0},
        )

    # Category-tunable thresholds
    cv_thresh = config.risk_cv_threshold if config else 0.60
    disp_thresh = config.risk_dispersion_threshold if config else 0.60
    w_cv = config.risk_cv_weight if config else 35
    w_disp = config.risk_dispersion_weight if config else 30
    w_outlier = config.risk_outlier_weight if config else 20
    w_sample = config.risk_sample_weight if config else 15
    sample_cap = config.risk_sample_cap if config else 15

    cv = cleaned.cv
    raw_total = max(cleaned.raw_total, 1)
    n_clean = cleaned.clean_total

    # Dispersión: IQR relativa a la mediana
    dispersion_ratio = (
        cleaned.iqr / cleaned.median_price if cleaned.median_price > 0 else 0.0
    )

    outlier_share = cleaned.outliers_removed / raw_total
    sample_penalty = max(0, (sample_cap - n_clean) / sample_cap)

    score = (
        100
        - w_cv * min(1, cv / cv_thresh)
        - w_disp * min(1, dispersion_ratio / disp_thresh)
        - w_outlier * outlier_share
        - w_sample * sample_penalty
    )

    score = min(100, max(0, round(score)))

    if score >= 70:
        category = "low"
    elif score >= 40:
        category = "medium"
    else:
        category = "high"

    return RiskResult(
        score=score,
        category=category,
        factors={
            "cv_penalty": round(w_cv * min(1, cv / cv_thresh), 2),
            "dispersion_penalty": round(w_disp * min(1, dispersion_ratio / disp_thresh), 2),
            "outlier_penalty": round(w_outlier * outlier_share, 2),
            "sample_penalty": round(w_sample * sample_penalty, 2),
        },
    )
