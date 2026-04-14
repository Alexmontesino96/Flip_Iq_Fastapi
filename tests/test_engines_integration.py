"""Tests de integración para el pipeline completo de motores.

Verifica que todos los motores trabajan juntos correctamente
usando datos sintéticos (sin llamadas a Apify).
"""

import math
from datetime import datetime, timedelta

from app.services.engines.comp_cleaner import clean_comps
from app.services.engines.competition_engine import compute_competition
from app.services.engines.confidence_engine import compute_confidence
from app.services.engines.listing_strategy import compute_listing_strategy
from app.services.engines.max_buy_price import compute_max_buy
from app.services.engines.pricing_engine import compute_pricing
from app.services.engines.profit_engine import compute_profit
from app.services.engines.risk_engine import compute_risk
from app.services.engines.seller_premium import compute_seller_premium
from app.services.engines.trend_engine import compute_trend
from app.services.engines.velocity_engine import compute_velocity
from app.services.marketplace.base import CleanedComps, CompsResult, MarketplaceListing


def _make_realistic_comps(
    base_price: float = 100.0,
    count: int = 20,
    days: int = 30,
    spread: float = 0.15,
) -> CompsResult:
    """Crea comps realistas con precios distribuidos alrededor de base_price."""
    now = datetime.now()
    listings = []
    for i in range(count):
        # Distribuir precios con variación
        offset = (i - count / 2) / count * 2 * spread
        price = round(base_price * (1 + offset), 2)
        shipping = round(5 + (i % 3) * 2, 2)

        listings.append(MarketplaceListing(
            title=f"Test Product {i}",
            price=price,
            shipping_price=shipping,
            total_price=round(price + shipping, 2),
            sold=True,
            marketplace="ebay",
            item_id=f"item_{i}",
            seller_username=f"seller_{i % 7}",  # 7 sellers diferentes
            seller_feedback_pct=95.0 + (i % 6),  # 95-100%
            ended_at=now - timedelta(days=i % days),
            condition="Used",
        ))

    return CompsResult.from_listings(listings, marketplace="ebay", days=days)


class TestFullPipeline:
    """Ejecuta el pipeline completo con datos sintéticos."""

    def test_pipeline_produces_all_results(self):
        raw = _make_realistic_comps(base_price=100.0, count=25)
        keyword = "iPhone 15 Pro Max 256GB"
        cost_price = 50.0

        # Motor A
        cleaned = clean_comps(raw, keyword=keyword)
        assert cleaned.clean_total > 0

        # Motor B
        pricing = compute_pricing(cleaned)
        assert pricing.market_list > 0
        assert pricing.quick_list <= pricing.market_list

        # Motor C
        profit_market = compute_profit(pricing.market_list, cost_price, "ebay")
        assert profit_market.profit > 0  # Con costo 50 y precio ~100, debe haber profit

        # Motor D
        max_buy = compute_max_buy(profit_market)
        assert max_buy.recommended_max > 0

        # Motor E
        velocity = compute_velocity(cleaned)
        assert 0 <= velocity.score <= 100
        assert velocity.category in ("muy_rapido", "rapido", "saludable", "lento", "muy_lento")

        # Motor F
        risk = compute_risk(cleaned, raw)
        assert 0 <= risk.score <= 100
        assert risk.category in ("bajo", "medio", "alto")

        # Motor G
        confidence = compute_confidence(cleaned, raw)
        assert 0 <= confidence.score <= 100

        # Motor H
        seller = compute_seller_premium(cleaned)
        assert seller.overall_median > 0

        # Motor I
        competition = compute_competition(cleaned)
        assert competition.unique_sellers > 0
        assert 0 <= competition.hhi <= 1

        # Motor J
        trend = compute_trend(cleaned)
        assert trend.category in ("subiendo", "estable", "bajando")

        # Motor K
        listing = compute_listing_strategy(cleaned, velocity, risk)
        assert listing.recommended_format in ("fixed_price", "auction", "best_offer")

    def test_pipeline_with_empty_comps(self):
        """Verifica que el pipeline no crashea con comps vacíos."""
        raw = CompsResult()

        cleaned = clean_comps(raw)
        assert cleaned.clean_total == 0

        pricing = compute_pricing(cleaned)
        assert pricing.market_list == 0.0

        profit = compute_profit(0.0, 50.0, "ebay")
        assert profit.profit < 0

        max_buy = compute_max_buy(profit)
        assert max_buy.recommended_max >= 0

        velocity = compute_velocity(cleaned)
        assert velocity.score == 0

        risk = compute_risk(cleaned, raw)
        assert risk.score == 0

        confidence = compute_confidence(cleaned, raw)
        assert confidence.score == 0

        competition = compute_competition(cleaned)
        assert competition.unique_sellers == 0
        assert competition.category == "sin_datos"

        trend = compute_trend(cleaned)
        assert trend.category == "sin_datos"

    def test_pipeline_with_few_comps(self):
        """Verifica comportamiento con pocos comps (3)."""
        raw = _make_realistic_comps(base_price=50.0, count=3, days=30)

        cleaned = clean_comps(raw)
        assert cleaned.clean_total > 0

        pricing = compute_pricing(cleaned)
        assert pricing.market_list > 0

        velocity = compute_velocity(cleaned)
        assert velocity.score >= 0

        risk = compute_risk(cleaned, raw)
        # Pocos comps = sample penalty alto
        assert risk.factors.get("sample_penalty", 0) > 0


class TestVelocityEngine:
    def test_zero_sales(self):
        cleaned = CleanedComps(sales_per_day=0)
        result = compute_velocity(cleaned)
        assert result.score == 0
        assert result.category == "muy_lento"

    def test_high_velocity(self):
        cleaned = CleanedComps(sales_per_day=2.0, clean_total=60, days_of_data=30)
        result = compute_velocity(cleaned)
        assert result.score >= 80
        assert result.category == "muy_rapido"

    def test_logarithmic_formula(self):
        cleaned = CleanedComps(sales_per_day=1.0, clean_total=30, days_of_data=30)
        result = compute_velocity(cleaned)
        expected = min(100, round(25 * math.log(1 + 30 * 1.0)))
        assert result.score == expected

    def test_market_sale_interval_days(self):
        cleaned = CleanedComps(sales_per_day=0.5, clean_total=15, days_of_data=30)
        result = compute_velocity(cleaned)
        assert result.market_sale_interval_days == 2.0
        assert result.estimated_days_to_sell == 2.0

    def test_estimated_days_to_sell_calculated(self):
        """estimated_days_to_sell = 1/spd, clamped 1-90."""
        cleaned = CleanedComps(sales_per_day=0.2, clean_total=6, days_of_data=30)
        result = compute_velocity(cleaned)
        assert result.estimated_days_to_sell == 5.0

    def test_estimated_days_to_sell_clamped_min(self):
        """spd muy alto → estimated_days_to_sell = 1.0 (mínimo)."""
        cleaned = CleanedComps(sales_per_day=10.0, clean_total=300, days_of_data=30)
        result = compute_velocity(cleaned)
        assert result.estimated_days_to_sell == 1.0

    def test_estimated_days_to_sell_clamped_max(self):
        """spd muy bajo → estimated_days_to_sell = 90.0 (máximo)."""
        cleaned = CleanedComps(sales_per_day=0.005, clean_total=1, days_of_data=30)
        result = compute_velocity(cleaned)
        assert result.estimated_days_to_sell == 90.0

    def test_estimated_days_to_sell_zero_spd(self):
        """spd = 0 → estimated_days_to_sell = None."""
        cleaned = CleanedComps(sales_per_day=0)
        result = compute_velocity(cleaned)
        assert result.estimated_days_to_sell is None


class TestRiskEngine:
    def test_stable_market_low_risk(self):
        """Mercado estable con muchos comps = bajo riesgo."""
        from app.services.marketplace.base import CleanedComps
        cleaned = CleanedComps(
            clean_total=20, raw_total=22, outliers_removed=2,
            median_price=100, iqr=10, cv=0.10, days_of_data=30,
        )
        raw = CompsResult(total_sold=22)
        result = compute_risk(cleaned, raw)
        assert result.score >= 60  # Bajo riesgo
        assert result.category in ("bajo", "medio")

    def test_volatile_market_high_risk(self):
        """Mercado volátil = alto riesgo."""
        from app.services.marketplace.base import CleanedComps
        cleaned = CleanedComps(
            clean_total=5, raw_total=15, outliers_removed=10,
            median_price=100, iqr=60, cv=0.55, days_of_data=30,
        )
        raw = CompsResult(total_sold=15)
        result = compute_risk(cleaned, raw)
        assert result.score < 50
        assert result.category in ("medio", "alto")


class TestCompetitionEngine:
    def test_monopoly_high_hhi(self):
        """Un solo seller = HHI = 1.0."""
        from app.services.marketplace.base import CleanedComps
        listings = [
            MarketplaceListing(title="P", price=100, seller_username="monopolist")
            for _ in range(10)
        ]
        cleaned = CleanedComps(listings=listings, clean_total=10)
        result = compute_competition(cleaned)
        assert result.hhi == 1.0
        assert result.category == "concentrado"

    def test_many_sellers_low_hhi(self):
        """Muchos sellers diferentes = HHI bajo."""
        from app.services.marketplace.base import CleanedComps
        listings = [
            MarketplaceListing(title="P", price=100, seller_username=f"seller_{i}")
            for i in range(20)
        ]
        cleaned = CleanedComps(listings=listings, clean_total=20)
        result = compute_competition(cleaned)
        assert result.hhi <= 0.15
        assert result.category == "sano"


class TestTrendEngine:
    def test_upward_trend(self):
        """Más ventas recientes que previas = subiendo."""
        from app.services.marketplace.base import CleanedComps
        now = datetime.now()
        listings = []
        # 3 ventas en periodo previo (7-14 días atrás)
        for i in range(3):
            listings.append(MarketplaceListing(
                title="P", price=100,
                total_price=100,
                ended_at=now - timedelta(days=10 + i),
            ))
        # 8 ventas recientes (últimos 7 días)
        for i in range(8):
            listings.append(MarketplaceListing(
                title="P", price=100,
                total_price=100,
                ended_at=now - timedelta(days=i),
            ))
        cleaned = CleanedComps(listings=listings, clean_total=11, days_of_data=30)
        result = compute_trend(cleaned)
        assert result.demand_trend > 0
        assert result.category == "subiendo"


class TestSellerPremium:
    def test_premium_detection(self):
        """Sellers top venden más caro."""
        from app.services.marketplace.base import CleanedComps
        listings = [
            # Sellers normales a $100
            MarketplaceListing(title="P", price=100, total_price=100,
                              seller_feedback_pct=95.0),
            MarketplaceListing(title="P", price=100, total_price=100,
                              seller_feedback_pct=96.0),
            MarketplaceListing(title="P", price=100, total_price=100,
                              seller_feedback_pct=97.0),
            # Sellers top a $120
            MarketplaceListing(title="P", price=120, total_price=120,
                              seller_feedback_pct=99.5),
            MarketplaceListing(title="P", price=125, total_price=125,
                              seller_feedback_pct=100.0),
        ]
        cleaned = CleanedComps(
            listings=listings, clean_total=5,
            median_price=100.0,
        )
        result = compute_seller_premium(cleaned)
        assert result.premium_median is not None
        assert result.premium_median > result.overall_median
        assert result.premium_pct > 0
        assert result.top_seller_count == 2


class TestConsistencyGate:
    """Verifica que cuando no hay comps válidos, todo el response es consistente."""

    def test_no_comps_condition_filter_match_rate_zero(self):
        """Si condition != any y no hay comps, match_rate debe ser 0."""
        raw = CompsResult()
        cleaned = clean_comps(raw, condition="used")
        assert cleaned.condition_match_rate == 0.0
        assert cleaned.requested_condition == "used"

    def test_no_comps_condition_any_match_rate_one(self):
        """Si condition == any y no hay comps, match_rate = 1 (no relevante)."""
        raw = CompsResult()
        cleaned = clean_comps(raw, condition="any")
        assert cleaned.condition_match_rate == 1.0

    def test_no_comps_pricing_is_zero(self):
        """Sin comps, pricing engine debe devolver 0."""
        cleaned = CleanedComps(clean_total=0)
        pricing = compute_pricing(cleaned)
        assert pricing.market_list == 0.0
        assert pricing.quick_list == 0.0
        assert pricing.stretch_list == 0.0

    def test_no_comps_all_engines_return_zero_safely(self):
        """Sin comps, ningún motor debe crashear."""
        raw = CompsResult()
        cleaned = clean_comps(raw, condition="used")

        pricing = compute_pricing(cleaned)
        assert pricing.market_list == 0.0

        profit = compute_profit(0.0, 120.0, "ebay")
        assert profit.profit < 0  # -120 cost

        velocity = compute_velocity(cleaned)
        assert velocity.score == 0

        risk = compute_risk(cleaned, raw)
        assert risk.score == 0

        confidence = compute_confidence(cleaned, raw)
        assert confidence.score == 0

        competition = compute_competition(cleaned)
        assert competition.unique_sellers == 0


class TestListingStrategy:
    def test_high_demand_suggests_auction(self):
        from app.services.marketplace.base import CleanedComps
        from app.services.engines.velocity_engine import VelocityResult
        from app.services.engines.risk_engine import RiskResult

        cleaned = CleanedComps(clean_total=20, cv=0.40)
        velocity = VelocityResult(score=85, sales_per_day=2.0,
                                  category="muy_rapido", market_sale_interval_days=0.5,
                                  estimated_days_to_sell=None)
        risk = RiskResult(score=80, category="bajo", factors={})

        result = compute_listing_strategy(cleaned, velocity, risk)
        assert result.auction_signal > result.fixed_price_signal

    def test_stable_market_suggests_fixed_price(self):
        from app.services.marketplace.base import CleanedComps
        from app.services.engines.velocity_engine import VelocityResult
        from app.services.engines.risk_engine import RiskResult

        cleaned = CleanedComps(clean_total=25, cv=0.15)
        velocity = VelocityResult(score=50, sales_per_day=0.5,
                                  category="saludable", market_sale_interval_days=2.0,
                                  estimated_days_to_sell=None)
        risk = RiskResult(score=75, category="bajo", factors={})

        result = compute_listing_strategy(cleaned, velocity, risk)
        assert result.recommended_format == "fixed_price"

    def test_few_comps_adds_qualifier(self):
        """Con < 10 comps, el reasoning incluye nota de muestra limitada."""
        from app.services.marketplace.base import CleanedComps
        from app.services.engines.velocity_engine import VelocityResult
        from app.services.engines.risk_engine import RiskResult

        cleaned = CleanedComps(clean_total=5, cv=0.15)
        velocity = VelocityResult(score=50, sales_per_day=0.5,
                                  category="saludable", market_sale_interval_days=2.0,
                                  estimated_days_to_sell=None)
        risk = RiskResult(score=75, category="bajo", factors={})

        result = compute_listing_strategy(cleaned, velocity, risk)
        assert "muestra limitada" in result.reasoning
        assert "5 comps" in result.reasoning

    def test_many_comps_no_qualifier(self):
        """Con >= 10 comps, el reasoning NO incluye nota de muestra limitada."""
        from app.services.marketplace.base import CleanedComps
        from app.services.engines.velocity_engine import VelocityResult
        from app.services.engines.risk_engine import RiskResult

        cleaned = CleanedComps(clean_total=15, cv=0.15)
        velocity = VelocityResult(score=50, sales_per_day=0.5,
                                  category="saludable", market_sale_interval_days=2.0,
                                  estimated_days_to_sell=2.0)
        risk = RiskResult(score=75, category="bajo", factors={})

        result = compute_listing_strategy(cleaned, velocity, risk)
        assert "muestra limitada" not in result.reasoning

    def test_best_offer_has_suggested_min_offer(self):
        """best_offer con quick_price → suggested_min_offer = quick_price."""
        from app.services.marketplace.base import CleanedComps
        from app.services.engines.velocity_engine import VelocityResult
        from app.services.engines.risk_engine import RiskResult

        cleaned = CleanedComps(clean_total=5, cv=0.30)
        velocity = VelocityResult(score=15, sales_per_day=0.05,
                                  category="muy_lento", market_sale_interval_days=20.0,
                                  estimated_days_to_sell=20.0)
        risk = RiskResult(score=30, category="alto", factors={})

        result = compute_listing_strategy(cleaned, velocity, risk, quick_price=85.50)
        assert result.recommended_format == "best_offer"
        assert result.suggested_min_offer == 85.50

    def test_non_best_offer_no_suggested_min_offer(self):
        """fixed_price → suggested_min_offer = None."""
        from app.services.marketplace.base import CleanedComps
        from app.services.engines.velocity_engine import VelocityResult
        from app.services.engines.risk_engine import RiskResult

        cleaned = CleanedComps(clean_total=25, cv=0.15)
        velocity = VelocityResult(score=50, sales_per_day=0.5,
                                  category="saludable", market_sale_interval_days=2.0,
                                  estimated_days_to_sell=2.0)
        risk = RiskResult(score=75, category="bajo", factors={})

        result = compute_listing_strategy(cleaned, velocity, risk, quick_price=85.50)
        assert result.recommended_format == "fixed_price"
        assert result.suggested_min_offer is None
