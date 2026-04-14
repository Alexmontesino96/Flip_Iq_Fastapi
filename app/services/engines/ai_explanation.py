"""Motor L — AI Explanation.

Genera explicación en lenguaje natural usando GPT-4o-mini via OpenAI API.
Fallback: texto genérico basado en los scores si la API falla.
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Eres un asistente experto en reselling/flipping de productos.
Tu trabajo es explicar resultados de análisis de mercado a revendedores.
Sé directo, práctico y usa lenguaje simple. Máximo 4 párrafos cortos.
Responde en español.

Reglas importantes:
- Los scores son 0-100 donde mayor = mejor. Un risk score de 74 = mercado estable (bajo riesgo).
- Si el riesgo es medio (40-65), matiza la conclusión: recomendar cantidades moderadas, no sobrecomprometerse.
- Si la recomendación es "buy" pero el riesgo es el score más bajo, menciona que el riesgo es el factor más débil y sugiere prudencia en volumen.
- Nunca digas "la decisión de comprar es acertada" sin cualificar si hay factores de riesgo. Usa "acertada para cantidades moderadas" o similar.
- Siempre identifica cuál es el factor más débil del análisis y menciónalo.
- Si hay datos de comparación entre marketplaces (eBay vs Amazon), analiza las diferencias de precio, velocidad y tendencias. Recomienda en cuál marketplace conviene vender y por qué."""

USER_TEMPLATE = """Analiza estos resultados para un revendedor:

Producto: {keyword}
Costo de compra: ${cost_price:.2f}
Marketplace: {marketplace}

PRECIOS RECOMENDADOS:
- Venta rápida: ${quick_list:.2f}
- Precio de mercado: ${market_list:.2f}
- Precio premium: ${stretch_list:.2f}

PROFIT (al precio de mercado):
- Ganancia neta: ${profit:.2f}
- ROI: {roi:.1%}
- Margen: {margin:.1%}

PRECIO MÁXIMO DE COMPRA: ${max_buy:.2f}

SCORES:
- Velocidad de venta: {velocity_score}/100 ({velocity_cat})
- Estabilidad del mercado: {risk_score}/100 (riesgo {risk_cat} — score alto = más estable, menos riesgo)
- Confianza del análisis: {confidence_score}/100 ({confidence_cat})
- Opportunity Score: {opportunity_score}/100

MERCADO:
- Comps analizados: {clean_total} de {raw_total} (después de limpieza)
- Nota sobre muestra: {sample_note}
- Días estimados para vender: {days_to_sell}
- Tendencia de demanda: {demand_trend}
- Competencia: {competition_cat} (HHI: {hhi:.3f})
- Formato recomendado: {listing_format}

DECISIÓN: {recommendation}

Da tu análisis en español. Sé específico con los números."""


async def generate_explanation(
    keyword: str | None,
    cost_price: float,
    marketplace: str,
    pricing: "PricingResult",
    profit_market: "ProfitResult",
    max_buy: "MaxBuyResult",
    velocity: "VelocityResult",
    risk: "RiskResult",
    confidence: "ConfidenceResult",
    competition: "CompetitionResult",
    trend: "TrendResult",
    listing: "ListingStrategyResult",
    opportunity_score: int,
    recommendation: str,
    cleaned_total: int,
    raw_total: int,
    comparison_text: str | None = None,
) -> str | None:
    """Genera explicación con IA. Retorna None si no hay API key o falla."""
    from app.core.llm import get_llm_client

    client, model = get_llm_client()
    if client is None:
        return None

    try:

        sample_note = (
            "Muestra pequeña — ser prudente con conclusiones firmes"
            if cleaned_total < 10
            else "Muestra razonable"
        )

        user_msg = USER_TEMPLATE.format(
            keyword=keyword or "Desconocido",
            cost_price=cost_price,
            marketplace=marketplace,
            quick_list=pricing.quick_list,
            market_list=pricing.market_list,
            stretch_list=pricing.stretch_list,
            profit=profit_market.profit,
            roi=profit_market.roi,
            margin=profit_market.margin,
            max_buy=max_buy.recommended_max,
            velocity_score=velocity.score,
            velocity_cat=velocity.category,
            risk_score=risk.score,
            risk_cat=risk.category,
            confidence_score=confidence.score,
            confidence_cat=confidence.category,
            opportunity_score=opportunity_score,
            clean_total=cleaned_total,
            raw_total=raw_total,
            sample_note=sample_note,
            days_to_sell=velocity.estimated_days_to_sell or "N/A",
            demand_trend=f"{trend.demand_trend:+.1f}%",
            competition_cat=competition.category,
            hhi=competition.hhi,
            listing_format=listing.recommended_format,
            recommendation=recommendation,
        )

        if comparison_text:
            user_msg += comparison_text

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
            temperature=0.7,
            timeout=15,
        )

        return response.choices[0].message.content

    except Exception as e:
        # Auto-fallback a OpenAI si Gemini falla
        from app.core.llm import disable_gemini, is_gemini_error
        if is_gemini_error(e):
            disable_gemini(str(e)[:100])
            fallback_client, fallback_model = get_llm_client()
            if fallback_client is not None:
                try:
                    response = await fallback_client.chat.completions.create(
                        model=fallback_model,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user_msg},
                        ],
                        max_tokens=500,
                        temperature=0.7,
                        timeout=15,
                    )
                    return response.choices[0].message.content
                except Exception as e2:
                    logger.warning("OpenAI fallback also failed: %s", e2)
        logger.warning("AI explanation failed: %s", e)
        return None
