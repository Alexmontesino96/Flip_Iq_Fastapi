"""Motor J — Trend Engine.

Compara últimos 7 días vs 7 días previos para detectar tendencias
en demanda y precio. Incluye nivel de confianza del trend.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.marketplace.base import CleanedComps


@dataclass
class TrendResult:
    demand_trend: float      # % cambio en volumen (positivo=subiendo)
    price_trend: float       # % cambio en precio medio
    coverage_ratio: float    # días con ventas / lookback_days
    burstiness: float        # max_daily / total (0=uniforme, 1=todo en 1 día)
    confidence: str          # high|medium|medium_low|low
    category: str            # rising|stable|declining|no_data


def _trend_confidence(coverage_ratio: float, recent_count: int, prev_count: int, config=None) -> str:
    """Evalúa qué tan confiable es la tendencia detectada."""
    hi_cov = config.trend_high_coverage if config else 0.5
    hi_cnt = config.trend_high_min_count if config else 5
    med_cov = config.trend_medium_coverage if config else 0.35
    med_cnt = config.trend_medium_min_count if config else 3
    lo_cov = config.trend_low_coverage if config else 0.2
    lo_cnt = config.trend_low_min_count if config else 2

    if coverage_ratio >= hi_cov and recent_count >= hi_cnt and prev_count >= hi_cnt:
        return "high"
    elif coverage_ratio >= med_cov and recent_count >= med_cnt and prev_count >= med_cnt:
        return "medium"
    elif coverage_ratio >= lo_cov and (recent_count >= lo_cnt or prev_count >= lo_cnt):
        return "medium_low"
    else:
        return "low"


def compute_trend(cleaned: CleanedComps, config=None) -> TrendResult:
    """Compara ventas recientes vs previas para detectar tendencias."""
    if cleaned.clean_total == 0 or not cleaned.listings:
        return TrendResult(
            demand_trend=0.0,
            price_trend=0.0,
            coverage_ratio=0.0,
            burstiness=0.0,
            confidence="low",
            category="no_data",
        )

    # Agrupar ventas por día
    daily: dict[str, list[float]] = defaultdict(list)
    latest_date: datetime | None = None

    for l in cleaned.listings:
        if not l.ended_at:
            continue
        date_str = l.ended_at.strftime("%Y-%m-%d")
        price = l.total_price or l.price
        daily[date_str].append(price)
        if latest_date is None or l.ended_at > latest_date:
            latest_date = l.ended_at

    if not daily or latest_date is None:
        return TrendResult(
            demand_trend=0.0,
            price_trend=0.0,
            coverage_ratio=0.0,
            burstiness=0.0,
            confidence="low",
            category="stable",
        )

    # Dividir en últimos 7 días vs 7 días previos (comparar como strings YYYY-MM-DD)
    cutoff_str = (latest_date - timedelta(days=7)).strftime("%Y-%m-%d")
    cutoff_prev_str = (latest_date - timedelta(days=14)).strftime("%Y-%m-%d")

    recent_count = 0
    recent_prices: list[float] = []
    prev_count = 0
    prev_prices: list[float] = []

    for date_str, prices in daily.items():
        if date_str >= cutoff_str:
            recent_count += len(prices)
            recent_prices.extend(prices)
        elif date_str >= cutoff_prev_str:
            prev_count += len(prices)
            prev_prices.extend(prices)

    # Demand trend: % cambio en volumen
    if prev_count > 0:
        demand_trend = ((recent_count - prev_count) / prev_count) * 100
    elif recent_count > 0:
        demand_trend = 50.0  # Datos solo recientes, no comparable. Valor moderado.
    else:
        demand_trend = 0.0

    # Price trend: % cambio en precio medio
    recent_avg = sum(recent_prices) / len(recent_prices) if recent_prices else 0
    prev_avg = sum(prev_prices) / len(prev_prices) if prev_prices else 0
    if prev_avg > 0:
        price_trend = ((recent_avg - prev_avg) / prev_avg) * 100
    else:
        price_trend = 0.0

    # Coverage: días con ventas / lookback_days
    coverage_ratio = len(daily) / max(cleaned.days_of_data, 1)

    # Burstiness: max_daily / total
    daily_counts = [len(prices) for prices in daily.values()]
    total = sum(daily_counts)
    burstiness = max(daily_counts) / total if total > 0 else 0.0

    # Confianza del trend
    confidence = _trend_confidence(coverage_ratio, recent_count, prev_count, config)

    # Categoría
    demand_delta = config.trend_demand_delta if config else 15
    if demand_trend > demand_delta:
        category = "rising"
    elif demand_trend < -demand_delta:
        category = "declining"
    else:
        category = "stable"

    return TrendResult(
        demand_trend=round(demand_trend, 2),
        price_trend=round(price_trend, 2),
        coverage_ratio=round(coverage_ratio, 4),
        burstiness=round(burstiness, 4),
        confidence=confidence,
        category=category,
    )
