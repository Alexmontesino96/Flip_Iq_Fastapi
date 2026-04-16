"""Motor M — Market Intelligence (Premium).

Usa Brave Search + GPT-4o-mini para generar contexto de mercado:
ciclo de vida, riesgo de depreciación, estacionalidad, eventos y timing.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

VALID_LIFECYCLES = {"new_release", "mature", "end_of_life", "discontinued"}
VALID_TIMINGS = {"buy_now", "wait", "sell_fast", "hold"}
VALID_IMPACTS = {"positive", "negative", "neutral"}
VALID_RELEVANCES = {"high", "medium", "low"}
VALID_CONFIDENCES = {"high", "medium", "low"}


@dataclass
class MarketEvent:
    event: str
    impact: str       # positive|negative|neutral
    relevance: str    # high|medium|low


@dataclass
class MarketIntelligenceResult:
    product_lifecycle: str     # new_release|mature|end_of_life|discontinued
    depreciation_risk: int     # 0-100
    seasonal_factor: float     # -1.0 a 1.0
    market_events: list[MarketEvent] = field(default_factory=list)
    timing_recommendation: str = "hold"  # buy_now|wait|sell_fast|hold
    intelligence_summary: str = ""
    confidence: str = "medium"  # high|medium|low
    search_source: str = "llm_knowledge"  # brave_search|llm_knowledge


SYSTEM_PROMPT = """You are a market analyst expert in consumer products and reselling.
Analyze the product and provided web context. Respond ONLY with valid JSON (no markdown, no ```).

Exact JSON structure:
{
  "product_lifecycle": "new_release|mature|end_of_life|discontinued",
  "depreciation_risk": 0-100,
  "seasonal_factor": -1.0 to 1.0,
  "market_events": [{"event": "short description", "impact": "positive|negative|neutral", "relevance": "high|medium|low"}],
  "timing_recommendation": "buy_now|wait|sell_fast|hold",
  "intelligence_summary": "2-3 sentences in English about the current market for this product",
  "confidence": "high|medium|low"
}

Rules:
- product_lifecycle: "new_release" if < 6 months old, "mature" if stable, "end_of_life" if being replaced, "discontinued" if no longer manufactured
- depreciation_risk: 0=no depreciation (collectible), 100=loses value quickly (obsolete tech)
- seasonal_factor: -1.0=worst time to sell, 0=no seasonal effect, 1.0=best time (e.g. Christmas for toys)
- market_events: max 3 current relevant events affecting price/demand
- timing_recommendation: "buy_now" if good time, "wait" if price will drop, "sell_fast" if rapid depreciation, "hold" if stable
- intelligence_summary: in English, practical for a reseller
- confidence: "high" if clear data, "medium" if reasonable, "low" if speculative"""

USER_TEMPLATE = """Producto: {keyword}
Marketplace: {marketplace}
Fecha actual: {today}

Datos de ventas completadas (últimos 30 días):
- Ventas limpias: {cleaned_total}
- Precio mediano: ${median_price:.2f}
- Rango: ${min_price:.2f} - ${max_price:.2f}
- Ventas/día: {sales_per_day:.2f}
- Tendencia demanda: {demand_trend:+.1f}%
- Tendencia precio: {price_trend:+.1f}%

{web_context}

Analiza el mercado actual de este producto y responde con el JSON estructurado."""


async def _brave_search(query: str, count: int = 5) -> list[dict] | None:
    """Busca en Brave Search API. Retorna None si no hay key o falla."""
    if not settings.brave_search_api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": settings.brave_search_api_key},
                params={"q": query, "count": count, "freshness": "pm"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("web", {}).get("results", [])
            return [
                {"title": r.get("title", ""), "description": r.get("description", "")}
                for r in results[:count]
            ]
    except Exception as e:
        logger.warning("Brave Search failed: %s", e)
        return None


def _parse_intelligence_result(
    data: dict, search_source: str
) -> MarketIntelligenceResult:
    """Parsea y valida el JSON del LLM con defaults seguros."""
    # Lifecycle
    lifecycle = data.get("product_lifecycle", "mature")
    if lifecycle not in VALID_LIFECYCLES:
        lifecycle = "mature"

    # Depreciation risk (clamp 0-100)
    try:
        dep_risk = int(data.get("depreciation_risk", 50))
    except (TypeError, ValueError):
        dep_risk = 50
    dep_risk = max(0, min(100, dep_risk))

    # Seasonal factor (clamp -1.0 a 1.0)
    try:
        seasonal = float(data.get("seasonal_factor", 0.0))
    except (TypeError, ValueError):
        seasonal = 0.0
    seasonal = max(-1.0, min(1.0, seasonal))

    # Market events (max 3)
    raw_events = data.get("market_events", [])
    if not isinstance(raw_events, list):
        raw_events = []
    events = []
    for ev in raw_events[:3]:
        if not isinstance(ev, dict) or not ev.get("event"):
            continue
        impact = ev.get("impact", "neutral")
        if impact not in VALID_IMPACTS:
            impact = "neutral"
        relevance = ev.get("relevance", "medium")
        if relevance not in VALID_RELEVANCES:
            relevance = "medium"
        events.append(MarketEvent(
            event=str(ev["event"])[:200],
            impact=impact,
            relevance=relevance,
        ))

    # Timing
    timing = data.get("timing_recommendation", "hold")
    if timing not in VALID_TIMINGS:
        timing = "hold"

    # Summary
    summary = str(data.get("intelligence_summary", ""))[:500]

    # Confidence
    confidence = data.get("confidence", "medium")
    if confidence not in VALID_CONFIDENCES:
        confidence = "medium"

    return MarketIntelligenceResult(
        product_lifecycle=lifecycle,
        depreciation_risk=dep_risk,
        seasonal_factor=seasonal,
        market_events=events,
        timing_recommendation=timing,
        intelligence_summary=summary,
        confidence=confidence,
        search_source=search_source,
    )


async def compute_market_intelligence(
    keyword: str,
    marketplace: str,
    cleaned_total: int,
    median_price: float,
    min_price: float,
    max_price: float,
    sales_per_day: float,
    demand_trend: float,
    price_trend: float,
) -> MarketIntelligenceResult | None:
    """Motor M: inteligencia de mercado premium.

    Retorna None si no hay openai_api_key o si falla cualquier paso.
    """
    from app.core.llm import has_llm, get_llm_client

    if not has_llm():
        return None

    try:
        import openai

        # Paso 1: Brave Search (opcional)
        search_results = await _brave_search(
            f"{keyword} market price trend 2025 2026"
        )

        if search_results:
            search_source = "brave_search"
            web_lines = ["Recent web context:"]
            for r in search_results:
                web_lines.append(f"- {r['title']}: {r['description']}")
            web_context = "\n".join(web_lines)
        else:
            search_source = "llm_knowledge"
            web_context = "No hay contexto web disponible. Usa tu conocimiento general."

        # Paso 2: GPT-4o-mini
        user_msg = USER_TEMPLATE.format(
            keyword=keyword,
            marketplace=marketplace,
            today=date.today().isoformat(),
            cleaned_total=cleaned_total,
            median_price=median_price,
            min_price=min_price,
            max_price=max_price,
            sales_per_day=sales_per_day,
            demand_trend=demand_trend,
            price_trend=price_trend,
            web_context=web_context,
        )

        client, model = get_llm_client()

        async def _call_llm(c, m):
            resp = await c.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=600,
                temperature=0.3,
                timeout=15,
            )
            raw = resp.choices[0].message.content or ""
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()
            return json.loads(raw)

        try:
            data = await _call_llm(client, model)
        except Exception as e:
            # Auto-fallback a OpenAI si Gemini falla
            from app.core.llm import disable_gemini, is_gemini_error
            if is_gemini_error(e):
                disable_gemini(str(e)[:100])
                fb_client, fb_model = get_llm_client()
                if fb_client is not None:
                    data = await _call_llm(fb_client, fb_model)
                else:
                    raise
            else:
                raise

        # Paso 3: Parsear y validar
        return _parse_intelligence_result(data, search_source)

    except Exception as e:
        logger.warning("Market intelligence failed: %s", e)
        return None
