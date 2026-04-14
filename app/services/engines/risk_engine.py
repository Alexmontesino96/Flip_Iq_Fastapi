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
    score: int           # 0-100 (100=bajo riesgo, 0=alto riesgo)
    category: str        # bajo|medio|alto
    factors: dict[str, float]


def compute_risk(cleaned: CleanedComps, raw: CompsResult) -> RiskResult:
    """Calcula score de riesgo/estabilidad basado en CV y dispersión."""
    if cleaned.clean_total == 0:
        return RiskResult(
            score=0,
            category="alto",
            factors={"no_data": 1.0},
        )

    cv = cleaned.cv
    raw_total = max(cleaned.raw_total, 1)
    n_clean = cleaned.clean_total

    # Dispersión: IQR relativa a la mediana
    dispersion_ratio = (
        cleaned.iqr / cleaned.median_price if cleaned.median_price > 0 else 0.0
    )

    outlier_share = cleaned.outliers_removed / raw_total
    sample_penalty = max(0, (15 - n_clean) / 15)

    score = (
        100
        - 35 * min(1, cv / 0.60)
        - 30 * min(1, dispersion_ratio / 0.60)
        - 20 * outlier_share
        - 15 * sample_penalty
    )

    score = min(100, max(0, round(score)))

    if score >= 70:
        category = "bajo"
    elif score >= 40:
        category = "medio"
    else:
        category = "alto"

    return RiskResult(
        score=score,
        category=category,
        factors={
            "cv_penalty": round(35 * min(1, cv / 0.60), 2),
            "dispersion_penalty": round(30 * min(1, dispersion_ratio / 0.60), 2),
            "outlier_penalty": round(20 * outlier_share, 2),
            "sample_penalty": round(15 * sample_penalty, 2),
        },
    )
