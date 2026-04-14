"""Tests de lógica de análisis (migrados a nuevos motores).

Equivalentes a los tests originales pero usando los motores nuevos.
"""

from app.services.engines.comp_cleaner import clean_comps
from app.services.engines.profit_engine import compute_profit
from app.services.engines.risk_engine import compute_risk
from app.services.engines.velocity_engine import compute_velocity
from app.services.analysis_service import _compute_opportunity_score, _decide, _validate_buy, _detect_distribution_shape
from app.services.engines.competition_engine import CompetitionResult
from app.services.engines.confidence_engine import ConfidenceResult
from app.services.engines.title_risk import TitleRiskResult
from app.services.engines.trend_engine import TrendResult
from app.services.marketplace.base import CleanedComps, CompsResult


def _make_comps(total_sold: int = 20, median: float = 100, min_p: float = 80, max_p: float = 120, days: int = 30) -> CompsResult:
    return CompsResult(
        avg_price=median,
        median_price=median,
        min_price=min_p,
        max_price=max_p,
        total_sold=total_sold,
        days_of_data=days,
        marketplace="ebay",
    )


# --- Opportunity score (reemplaza flip_score) ---

def test_opportunity_high_profit():
    """Alto ROI produce opportunity score alto."""
    profit = compute_profit(150.0, 50.0, "ebay")  # ROI ~64%
    from app.services.engines.velocity_engine import VelocityResult
    from app.services.engines.risk_engine import RiskResult

    velocity = VelocityResult(score=70, sales_per_day=1.0, category="rapido", market_sale_interval_days=1.0, estimated_days_to_sell=None)
    risk = RiskResult(score=75, category="bajo", factors={})
    confidence = ConfidenceResult(score=60, category="media", factors={})
    competition = CompetitionResult(hhi=0.1, dominant_seller_share=0.1, unique_sellers=10, category="sano")
    trend = TrendResult(demand_trend=5.0, price_trend=2.0, coverage_ratio=0.5, burstiness=0.1, confidence="media", category="estable")

    score = _compute_opportunity_score(profit, velocity, risk, confidence, competition, trend)
    assert score >= 60


def test_opportunity_low_profit():
    """ROI negativo produce opportunity score bajo."""
    profit = compute_profit(50.0, 60.0, "ebay")  # pérdida
    from app.services.engines.velocity_engine import VelocityResult
    from app.services.engines.risk_engine import RiskResult

    velocity = VelocityResult(score=20, sales_per_day=0.1, category="lento", market_sale_interval_days=10.0, estimated_days_to_sell=None)
    risk = RiskResult(score=30, category="alto", factors={})
    confidence = ConfidenceResult(score=30, category="baja", factors={})
    competition = CompetitionResult(hhi=0.3, dominant_seller_share=0.5, unique_sellers=3, category="concentrado")
    trend = TrendResult(demand_trend=-20.0, price_trend=-10.0, coverage_ratio=0.2, burstiness=0.5, confidence="baja", category="bajando")

    score = _compute_opportunity_score(profit, velocity, risk, confidence, competition, trend)
    assert score < 40


# --- Risk (Motor F) ---

def test_risk_no_data():
    cleaned = CleanedComps()
    raw = CompsResult()
    risk = compute_risk(cleaned, raw)
    assert risk.score == 0
    assert risk.category == "alto"


def test_risk_stable_market():
    cleaned = CleanedComps(
        clean_total=20, raw_total=22, outliers_removed=2,
        median_price=100, iqr=10, cv=0.10, days_of_data=30,
    )
    raw = CompsResult(total_sold=22)
    risk = compute_risk(cleaned, raw)
    assert risk.score >= 60  # Bajo riesgo


def test_risk_volatile_market():
    cleaned = CleanedComps(
        clean_total=5, raw_total=15, outliers_removed=10,
        median_price=100, iqr=60, cv=0.55, days_of_data=30,
    )
    raw = CompsResult(total_sold=15)
    risk = compute_risk(cleaned, raw)
    assert risk.score < 50  # Alto riesgo


# --- Velocity (Motor E) ---

def test_velocity_no_data():
    cleaned = CleanedComps(sales_per_day=0)
    result = compute_velocity(cleaned)
    assert result.score == 0


def test_velocity_high():
    cleaned = CleanedComps(sales_per_day=2.0, clean_total=60, days_of_data=30)
    result = compute_velocity(cleaned)
    assert result.score >= 80


def test_velocity_medium():
    cleaned = CleanedComps(sales_per_day=0.5, clean_total=15, days_of_data=30)
    result = compute_velocity(cleaned)
    assert 40 <= result.score <= 80


# --- Decision ---

def test_decide_buy():
    profit = compute_profit(150.0, 50.0, "ebay")
    from app.services.engines.risk_engine import RiskResult
    risk = RiskResult(score=70, category="bajo", factors={})
    confidence = ConfidenceResult(score=60, category="media", factors={})
    result = _decide(70, profit, risk, confidence)
    assert result == "buy"


def test_decide_buy_small():
    """ROI alto pero confidence/opportunity no alcanza para buy completo → buy_small."""
    profit = compute_profit(120.0, 50.0, "ebay")  # ROI > 20%
    from app.services.engines.risk_engine import RiskResult
    risk = RiskResult(score=50, category="medio", factors={})
    confidence = ConfidenceResult(score=20, category="baja", factors={})
    # opportunity 50: entre 45 y 60, profit > 0, roi > 0.20, risk >= 30
    result = _decide(50, profit, risk, confidence)
    assert result == "buy_small"


def test_decide_watch():
    profit = compute_profit(80.0, 60.0, "ebay")
    from app.services.engines.risk_engine import RiskResult
    risk = RiskResult(score=50, category="medio", factors={})
    confidence = ConfidenceResult(score=40, category="media", factors={})
    result = _decide(40, profit, risk, confidence)
    assert result == "watch"


def test_decide_pass():
    profit = compute_profit(50.0, 60.0, "ebay")  # pérdida
    from app.services.engines.risk_engine import RiskResult
    risk = RiskResult(score=20, category="alto", factors={})
    confidence = ConfidenceResult(score=20, category="baja", factors={})
    result = _decide(20, profit, risk, confidence)
    assert result == "pass"


# --- Validate buy → buy_small degradation ---

def test_validate_buy_degrades_to_watch_on_low_confidence():
    """buy con confianza < 50 → watch (no buy_small)."""
    confidence = ConfidenceResult(score=20, category="baja", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=10, raw_total=15, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(150.0, 50.0, "ebay")
    rec, warnings = _validate_buy("buy", confidence, title_risk, cleaned, profit)
    assert rec == "watch"
    assert any("Confianza" in w for w in warnings)


def test_validate_buy_degrades_to_buy_small_on_few_comps():
    """buy con < 5 comps → buy_small."""
    confidence = ConfidenceResult(score=60, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=4, raw_total=10, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(150.0, 50.0, "ebay")
    rec, warnings = _validate_buy("buy", confidence, title_risk, cleaned, profit)
    assert rec == "buy_small"


def test_validate_buy_degrades_to_buy_small_on_title_risk():
    """buy con title_risk alto → buy_small."""
    confidence = ConfidenceResult(score=60, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.5, flagged_listings=5, flagged_pct=0.5,
        semantic_flags={"box_only": 5}, manual_review_required=True,
        top_flags=["box_only"],
    )
    cleaned = CleanedComps(clean_total=10, raw_total=15, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(150.0, 50.0, "ebay")
    rec, warnings = _validate_buy("buy", confidence, title_risk, cleaned, profit)
    assert rec == "buy_small"


def test_validate_buy_negative_profit_to_pass():
    """buy con profit negativo → pass."""
    confidence = ConfidenceResult(score=60, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=20, raw_total=25, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(50.0, 200.0, "ebay")  # pérdida
    rec, warnings = _validate_buy("buy", confidence, title_risk, cleaned, profit)
    assert rec == "pass"


# --- Headroom check ---

def test_validate_buy_headroom_negative_degrades_to_watch():
    """buy_small cuando cost > max_buy → watch con warning de negociación."""
    from app.services.engines.max_buy_price import MaxBuyResult
    confidence = ConfidenceResult(score=60, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=20, raw_total=25, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(100.0, 70.0, "ebay")
    max_buy = MaxBuyResult(max_by_profit=63.0, max_by_roi=60.0, recommended_max=63.0)
    rec, warnings = _validate_buy(
        "buy_small", confidence, title_risk, cleaned, profit,
        max_buy=max_buy, cost_price=70.0,
    )
    assert rec == "watch"
    assert any("excede el máximo" in w for w in warnings)
    assert any("$63.00" in w for w in warnings)


def test_validate_buy_headroom_positive_no_degrade():
    """cost < max_buy → no degrada por headroom."""
    from app.services.engines.max_buy_price import MaxBuyResult
    confidence = ConfidenceResult(score=60, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=20, raw_total=25, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(150.0, 50.0, "ebay")
    max_buy = MaxBuyResult(max_by_profit=80.0, max_by_roi=75.0, recommended_max=80.0)
    rec, warnings = _validate_buy(
        "buy", confidence, title_risk, cleaned, profit,
        max_buy=max_buy, cost_price=50.0,
    )
    assert rec == "buy"
    assert not any("excede" in w for w in warnings)


# --- Confidence < 50 threshold ---

def test_validate_buy_confidence_49_degrades_to_watch():
    """confidence 49 → watch (no buy_small)."""
    confidence = ConfidenceResult(score=49, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=20, raw_total=25, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(150.0, 50.0, "ebay")
    rec, warnings = _validate_buy("buy", confidence, title_risk, cleaned, profit)
    assert rec == "watch"


def test_validate_buy_confidence_50_no_degrade():
    """confidence 50 → no degrada por confianza."""
    confidence = ConfidenceResult(score=50, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=20, raw_total=25, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(150.0, 50.0, "ebay")
    rec, warnings = _validate_buy("buy", confidence, title_risk, cleaned, profit)
    assert rec == "buy"
    assert not any("Confianza" in w for w in warnings)


# --- Distribution bimodal warning ---

def test_validate_buy_bimodal_adds_warning():
    """Distribución bimodal agrega warning."""
    confidence = ConfidenceResult(score=60, category="media", factors={})
    title_risk = TitleRiskResult(
        risk_score=0.0, flagged_listings=0, flagged_pct=0.0,
        semantic_flags={}, manual_review_required=False,
    )
    cleaned = CleanedComps(clean_total=20, raw_total=25, requested_condition="any",
                           condition_match_rate=1.0, condition_filtered=0)
    profit = compute_profit(150.0, 50.0, "ebay")
    rec, warnings = _validate_buy(
        "buy", confidence, title_risk, cleaned, profit,
        distribution_shape="bimodal",
    )
    assert any("bimodal" in w for w in warnings)


# --- Distribution shape detection ---

def test_detect_distribution_insufficient():
    """< 5 precios → insufficient."""
    assert _detect_distribution_shape([10, 20, 30]) == "insufficient"


def test_detect_distribution_normal():
    """Precios uniformes sin gap grande → normal."""
    prices = [90, 95, 100, 105, 110]
    assert _detect_distribution_shape(prices) == "normal"


def test_detect_distribution_bimodal_clear_gap():
    """Dos clusters con gap claro → bimodal."""
    prices = [80, 85, 90, 120, 125]  # gap de 30 entre 90 y 120
    assert _detect_distribution_shape(prices) == "bimodal"


def test_detect_distribution_bimodal_hoka_case():
    """Caso real HOKA Bondi: 2 ventas bajas + 4 ventas altas con gap $97-$120."""
    prices = [74.0, 85.0, 120.0, 130.0, 145.0, 188.0]
    assert _detect_distribution_shape(prices) == "bimodal"


def test_detect_distribution_normal_even_spread():
    """Precios con spread uniforme → normal, no bimodal."""
    prices = [80, 90, 100, 110, 120]
    assert _detect_distribution_shape(prices) == "normal"


def test_detect_distribution_identical_prices():
    """Todos los precios iguales → normal."""
    prices = [100, 100, 100, 100, 100]
    assert _detect_distribution_shape(prices) == "normal"


# --- Confidence burstiness penalty ---

def test_confidence_burstiness_penalty():
    """burstiness > 0.3 reduce el score de confidence."""
    from app.services.engines.confidence_engine import compute_confidence
    from app.services.marketplace.base import MarketplaceListing
    from datetime import datetime, timezone, timedelta

    listings = [
        MarketplaceListing(
            title="Test item", price=100, total_price=100, sold=True,
            marketplace="ebay",
            ended_at=datetime.now(timezone.utc) - timedelta(days=d),
        )
        for d in range(1, 21)
    ]
    cleaned = CleanedComps(
        clean_total=20, raw_total=25, outliers_removed=5,
        median_price=100, iqr=10, cv=0.10, days_of_data=30,
        listings=listings,
    )
    raw = CompsResult(total_sold=25)

    score_no_burst = compute_confidence(cleaned, raw, burstiness=0.0).score
    score_high_burst = compute_confidence(cleaned, raw, burstiness=0.5).score

    assert score_high_burst < score_no_burst
    # burstiness 0.5 → penalty = (0.5 - 0.3) * 40 = 8
    assert score_no_burst - score_high_burst >= 7


def test_confidence_burstiness_below_threshold_no_penalty():
    """burstiness <= 0.3 no penaliza."""
    from app.services.engines.confidence_engine import compute_confidence

    cleaned = CleanedComps(
        clean_total=20, raw_total=25, outliers_removed=5,
        median_price=100, iqr=10, cv=0.10, days_of_data=30,
    )
    raw = CompsResult(total_sold=25)

    score_zero = compute_confidence(cleaned, raw, burstiness=0.0).score
    score_low = compute_confidence(cleaned, raw, burstiness=0.3).score

    assert score_zero == score_low


# --- Dominant seller warning ---

def test_dominant_seller_warning_in_pipeline():
    """Pipeline agrega warning cuando dominant_seller_share > 40%."""
    from app.services.analysis_service import _run_pipeline
    from app.services.marketplace.base import MarketplaceListing
    from datetime import datetime, timezone, timedelta

    # Crear listings donde un seller domina
    listings = []
    for i in range(10):
        listings.append(MarketplaceListing(
            title="Test item", price=50, total_price=50, sold=True,
            marketplace="ebay",
            seller_username="dominant_seller" if i < 7 else f"seller_{i}",
            ended_at=datetime.now(timezone.utc) - timedelta(days=i + 1),
        ))
    comps = CompsResult.from_listings(listings, marketplace="ebay", days=30)

    result = _run_pipeline(
        comps, keyword="test", condition="any",
        cost_price=30.0, marketplace_name="ebay",
    )

    # Verificar que hay warning de dominant seller
    assert any("Buy Box" in w for w in result.warnings)
