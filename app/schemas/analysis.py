from datetime import datetime

from pydantic import BaseModel


class AnalysisRequest(BaseModel):
    """Solicitud de análisis de un producto para reventa."""
    barcode: str | None = None
    keyword: str | None = None
    cost_price: float
    marketplace: str = "ebay"  # ebay|amazon_fba
    # Costos opcionales del revendedor
    shipping_cost: float = 0.0
    packaging_cost: float = 0.0
    prep_cost: float = 0.0
    promo_cost: float = 0.0
    return_reserve_pct: float = 0.05
    target_profit: float = 10.0
    target_roi: float = 0.35
    detailed: bool = False
    condition: str = "any"
    mode: str = "standard"  # "standard" | "premium"
    product_type: str | None = None  # Override manual del tipo de producto


class ChannelBreakdown(BaseModel):
    marketplace: str
    estimated_sale_price: float
    net_proceeds: float
    profit: float
    roi_pct: float       # profit / cost * 100
    margin_pct: float    # profit / sale_price * 100
    label: str | None = None        # "BEST PROFIT" | "BEST ROI" | None
    is_estimated: bool = False      # True si no tiene datos scrapeados propios


class PriceBucketOut(BaseModel):
    range_min: float
    range_max: float
    count: int
    pct_of_total: float


class SalesByDateOut(BaseModel):
    date: str
    count: int
    avg_price: float
    min_price: float
    max_price: float


class CompsInfo(BaseModel):
    """Datos de comparables de ventas reales usados para la estimacion."""
    total_sold: int
    avg_price: float
    median_price: float
    min_price: float
    max_price: float
    std_dev: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    sales_per_day: float = 0.0
    days_of_data: float
    source: str  # "ebay_sold_cleaned", "fallback"
    distribution_shape: str = "unknown"  # normal|bimodal|skewed|insufficient
    price_distribution: list[PriceBucketOut] = []
    sales_timeline: list[SalesByDateOut] = []


# --- Sub-schemas de los motores ---

class PricingOut(BaseModel):
    quick_list: float
    market_list: float
    stretch_list: float
    stretch_allowed: bool


class ProfitOut(BaseModel):
    sale_price: float
    fee_rate: float
    marketplace_fees: float
    shipping_cost: float
    packaging_cost: float
    prep_cost: float
    promo_cost: float
    return_reserve: float
    gross_proceeds: float        # sale - fees - shipping - packaging - promo
    risk_adjusted_net: float     # gross_proceeds - return_reserve
    profit: float
    roi: float
    margin: float


class MaxBuyOut(BaseModel):
    max_by_profit: float
    max_by_roi: float
    recommended_max: float


class VelocityOut(BaseModel):
    score: int
    sales_per_day: float
    category: str
    market_sale_interval_days: float | None
    estimated_days_to_sell: float | None


class RiskOut(BaseModel):
    score: int
    category: str
    factors: dict[str, float]


class ConfidenceOut(BaseModel):
    score: int
    category: str
    factors: dict[str, float]


class SellerPremiumOut(BaseModel):
    premium_median: float | None
    overall_median: float
    premium_delta: float
    premium_pct: float
    top_seller_count: int


class CompetitionOut(BaseModel):
    hhi: float
    dominant_seller_share: float
    unique_sellers: int
    category: str


class TrendOut(BaseModel):
    demand_trend: float
    price_trend: float
    coverage_ratio: float
    burstiness: float
    confidence: str
    category: str


class ListingStrategyOut(BaseModel):
    recommended_format: str
    reasoning: str
    auction_signal: float
    fixed_price_signal: float
    suggested_min_offer: float | None = None


class TitleRiskOut(BaseModel):
    risk_score: float
    flagged_listings: int
    flagged_pct: float
    semantic_flags: dict[str, int]
    manual_review_required: bool
    top_flags: list[str]


class ConditionAnalysisOut(BaseModel):
    requested_condition: str          # "any", "new", "used"
    filter_applied: bool              # True si se filtró por condición
    condition_counts: dict[str, int]  # condición normalizada → cantidad
    condition_match_rate: float       # % de comps finales que coinciden
    condition_filtered: int           # comps removidos por condición
    mixed_conditions: bool            # True si hay mezcla new+used
    raw_condition_total: int = 0      # Total de comps antes del filtro de condición
    condition_subset_count: int = 0         # Comps que matchean la condición pedida
    condition_subset_median: float | None = None  # Mediana del subset (cuando safety net activa)
    condition_subset_pricing: dict | None = None  # Mini-pipeline: {count, median, profit, roi_pct, margin_pct, max_buy}


class MarketplaceAnalysis(BaseModel):
    """Análisis completo de un marketplace individual."""
    marketplace: str          # "ebay", "amazon"
    estimated_sale_price: float | None = None
    net_profit: float | None = None
    roi_pct: float | None = None
    margin_pct: float | None = None
    flip_score: int | None = None
    recommendation: str = "pass"
    comps: CompsInfo | None = None
    pricing: PricingOut | None = None
    profit_detail: ProfitOut | None = None
    max_buy_price: MaxBuyOut | None = None
    velocity: VelocityOut | None = None
    risk: RiskOut | None = None
    confidence: ConfidenceOut | None = None
    seller_premium: SellerPremiumOut | None = None
    competition: CompetitionOut | None = None
    trend: TrendOut | None = None
    listing_strategy: ListingStrategyOut | None = None
    title_risk: TitleRiskOut | None = None
    condition_analysis: ConditionAnalysisOut | None = None
    warnings: list[str] = []


class MarketEventOut(BaseModel):
    event: str
    impact: str       # positive|negative|neutral
    relevance: str    # high|medium|low


class MarketIntelligenceOut(BaseModel):
    product_lifecycle: str     # new_release|mature|end_of_life|discontinued
    depreciation_risk: int     # 0-100
    seasonal_factor: float     # -1.0 a 1.0
    market_events: list[MarketEventOut]
    timing_recommendation: str # buy_now|wait|sell_fast|hold
    intelligence_summary: str  # 2-3 sentences in English
    confidence: str            # high|medium|low
    search_source: str         # brave_search|llm_knowledge


# --- Summary block ---

class BuyBox(BaseModel):
    recommended_max_buy: float
    your_cost: float
    headroom: float             # max_buy - your_cost


class SalePlan(BaseModel):
    recommended_list_price: float
    quick_sale_price: float
    stretch_price: float | None  # None si stretch no permitido


class Returns(BaseModel):
    profit: float
    roi_pct: float
    margin_pct: float


class AnalysisSummary(BaseModel):
    """Bloque resumen para decisión rápida del revendedor."""
    recommendation: str          # buy|buy_small|watch|pass
    signal: str = "neutral"      # positive|caution|negative|neutral
    buy_box: BuyBox
    sale_plan: SalePlan
    returns: Returns
    risk: str                    # low|medium|high
    confidence: str              # high|medium_high|medium|low
    warnings: list[str] = []     # alertas del validador


class AnalysisResponse(BaseModel):
    id: int | None = None
    product: "ProductSummary"

    cost_price: float
    marketplace: str

    # Summary scores del primary pipeline
    estimated_sale_price: float | None
    net_profit: float | None
    margin_pct: float | None
    roi_pct: float | None
    flip_score: int | None
    risk_score: int | None
    velocity_score: int | None
    recommendation: str | None  # buy|buy_small|pass|watch

    channels: list[ChannelBreakdown] | None

    # Resumen ejecutivo
    summary: AnalysisSummary | None = None
    ai_explanation: str | None = None
    market_intelligence: MarketIntelligenceOut | None = None

    # Categorización de producto
    detected_category: str | None = None
    category_confidence: float | None = None

    # Análisis dual: cada marketplace con su pipeline completo
    ebay_analysis: MarketplaceAnalysis | None = None
    amazon_analysis: MarketplaceAnalysis | None = None
    best_marketplace: str | None = None  # marketplace con mejor oportunidad
    best_marketplace_reason: str | None = None  # "best_profit" | "best_opportunity"

    created_at: datetime

    model_config = {"from_attributes": True}


class ProductSummary(BaseModel):
    id: int
    barcode: str | None
    title: str
    brand: str | None
    image_url: str | None

    model_config = {"from_attributes": True}


class AnalysisHistory(BaseModel):
    id: int
    product_title: str
    cost_price: float
    net_profit: float | None
    flip_score: int | None
    recommendation: str | None
    marketplace: str
    created_at: datetime
