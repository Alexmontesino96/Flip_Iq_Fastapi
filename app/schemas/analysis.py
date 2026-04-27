from datetime import datetime

from pydantic import BaseModel, field_validator


class AnalysisRequest(BaseModel):
    """Solicitud de análisis de un producto para reventa."""
    barcode: str | None = None
    keyword: str | None = None
    cost_price: float

    @field_validator("cost_price")
    @classmethod
    def cost_price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(
                "cost_price must be greater than 0. "
                "Enter the actual product cost to get accurate ROI and profit calculations."
            )
        return v
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
    execution_score: int | None = None
    win_probability: float | None = None
    expected_profit: float | None = None
    channel_role: str | None = None  # recommended|best_profit|test_only|candidate
    execution_note: str | None = None


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
    temporal_window_expanded: bool = False  # True si ventana se expandió (ej. 30→90 días)
    initial_days_requested: float | None = None  # Días originales pedidos antes de expansión


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
    estimated_days_to_sell: str | None


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


class ExecutionPenaltyOut(BaseModel):
    code: str
    severity: str
    points: int
    message: str


class ExecutionAnalysisOut(BaseModel):
    score: int
    category: str
    win_probability: float
    expected_profit: float
    max_recommendation: str
    quantity_guidance: str
    channel_role: str = "candidate"
    penalties: list[ExecutionPenaltyOut] = []
    warnings: list[str] = []


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
    execution_analysis: ExecutionAnalysisOut | None = None
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


class ScoreBreakdown(BaseModel):
    """Scores organizados por categoría para jerarquía visual en el frontend.

    Nivel 1 (hero): flip_score — oportunidad general (0-100)
    Nivel 2 (market): velocity, risk — salud del mercado
    Nivel 3 (data_quality): confidence — fiabilidad del análisis
    Nivel 4 (execution): execution_score, win_probability — viabilidad de venta
    """
    # Hero score
    flip_score: int                    # Opportunity score (0-100)
    # Market health
    velocity: int                      # Velocidad de venta (0-100)
    risk: int                          # Estabilidad del mercado (0-100, alto = estable)
    risk_label: str = "low"            # low|medium|high
    # Data quality
    confidence: int                    # Fiabilidad del análisis (0-100)
    confidence_label: str = "low"      # low|medium|medium_high|high
    temporal_window_expanded: bool = False  # True si se expandió la ventana temporal
    # Execution
    execution_score: int | None = None       # Viabilidad de ejecución (0-100)
    win_probability: float | None = None     # Probabilidad de venta (0-1)
    # Composite
    final_score: int | None = None           # market × execution (0-100)


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
    scores: ScoreBreakdown | None = None  # Scores organizados por categoría


class AICompleteEvent(BaseModel):
    """Chunk 2 de SSE: AI explanation + campos que pueden cambiar post-intelligence."""
    ai_explanation: str | None = None
    market_intelligence: MarketIntelligenceOut | None = None
    # Campos que market_intelligence puede modificar
    risk_score: int | None = None
    flip_score: int | None = None
    recommendation: str | None = None
    summary: AnalysisSummary | None = None
    # DB persistence
    id: int | None = None


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
    ai_locked: bool = False  # True when user tier doesn't include AI explanation
    market_intelligence: MarketIntelligenceOut | None = None

    # Categorización de producto
    detected_category: str | None = None
    category_confidence: float | None = None
    category_slug: str | None = None
    observation_mode: bool = False

    # No comps found — frontend should show "Product Not Found" UI
    no_comps_found: bool = False

    # Análisis dual: cada marketplace con su pipeline completo
    ebay_analysis: MarketplaceAnalysis | None = None
    amazon_analysis: MarketplaceAnalysis | None = None
    best_marketplace: str | None = None  # marketplace con mejor oportunidad
    best_marketplace_reason: str | None = None  # "best_profit" | "best_opportunity"
    best_profit_marketplace: str | None = None
    recommended_marketplace: str | None = None
    execution_analysis: ExecutionAnalysisOut | None = None
    market_score: int | None = None
    final_score: int | None = None

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
    product_id: int | None = None
    product_title: str
    cost_price: float
    net_profit: float | None
    flip_score: int | None
    recommendation: str | None
    marketplace: str
    created_at: datetime


# --- Feedback & Not Found ---

_VALID_FEEDBACK_TYPES = {"incorrect_price", "incorrect_recommendation", "outdated", "missing_data", "other"}


class FeedbackRequest(BaseModel):
    """Request para reportar un análisis como incorrecto."""
    feedback_type: str  # incorrect_price|incorrect_recommendation|outdated|missing_data|other
    comment: str | None = None
    actual_sale_price: float | None = None  # precio real si el user vendió

    @field_validator("feedback_type")
    @classmethod
    def validate_feedback_type(cls, v: str) -> str:
        if v not in _VALID_FEEDBACK_TYPES:
            raise ValueError(
                f"feedback_type must be one of: {', '.join(sorted(_VALID_FEEDBACK_TYPES))}"
            )
        return v


class FeedbackResponse(BaseModel):
    id: int
    analysis_id: int
    feedback_type: str
    comment: str | None
    actual_sale_price: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotFoundItem(BaseModel):
    """Análisis donde no se encontraron comps."""
    id: int
    product_title: str
    barcode: str | None
    keyword: str | None
    marketplace: str
    cost_price: float
    created_at: datetime


class FlaggedItem(BaseModel):
    """Análisis marcado como incorrecto por el usuario."""
    analysis_id: int
    product_title: str
    marketplace: str
    recommendation: str | None
    flip_score: int | None
    net_profit: float | None
    feedback_type: str
    comment: str | None
    actual_sale_price: float | None
    flagged_at: datetime
