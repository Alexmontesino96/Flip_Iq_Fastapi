"""Motor L — AI Explanation.

Genera explicación en lenguaje natural usando GPT-4o-mini via OpenAI API.
Fallback: texto genérico basado en los scores si la API falla.
"""

import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert assistant in product reselling/flipping.
Your job is to explain market analysis results to resellers.
Be direct, practical and use simple language. Respond in English.

FORMATTING: Write plain text only. Do NOT use markdown, asterisks, bold, headers, bullet points, or any special formatting. Just write natural paragraphs separated by blank lines.

You MUST write exactly 4 paragraphs with this structure:

1. Market Overview — Median sale price, number of comps analyzed, sell-through rate/velocity, and what they tell us about demand.
2. Profit Analysis — Purchase cost, expected sale price, fees, net profit, ROI. State whether the margin is healthy, thin, or negative.
3. Risk Factors — Identify the weakest score in the analysis and explain why it matters. Mention any warnings about competition, price volatility, or low confidence.
4. Recommendation — Clear action: buy (how many units), watch (what trigger to wait for), or pass (why). Include timing if relevant.

Important rules:
- Scores are 0-100 where higher = better. A risk score of 74 = stable market (low risk).
- If risk is medium (40-65), qualify the conclusion: recommend moderate quantities, don't overcommit.
- If the recommendation is "buy" but risk is the lowest score, mention that risk is the weakest factor and suggest caution on volume.
- Never say "the buy decision is correct" without qualifying if there are risk factors. Use "correct for moderate quantities" or similar.
- Always identify which is the weakest factor in the analysis and mention it.
- If there is comparison data between marketplaces (eBay vs Amazon), analyze price, velocity and trend differences. Recommend which marketplace is best to sell on and why."""

USER_TEMPLATE = """Analyze these results for a reseller:

Product: {keyword}
Purchase cost: ${cost_price:.2f}
Marketplace: {marketplace}

RECOMMENDED PRICES:
- Quick sale: ${quick_list:.2f}
- Market price: ${market_list:.2f}
- Stretch price: ${stretch_list:.2f}

PROFIT (at market price):
- Net profit: ${profit:.2f}
- ROI: {roi:.1%}
- Margin: {margin:.1%}

MAX BUY PRICE: ${max_buy:.2f}

SCORES:
- Sell-through rate: {velocity_score}/100 ({velocity_cat})
- Market stability: {risk_score}/100 (risk {risk_cat} — high score = more stable, less risk)
- Analysis confidence: {confidence_score}/100 ({confidence_cat})
- Opportunity Score: {opportunity_score}/100

MARKET:
- Comps analyzed: {clean_total} of {raw_total} (after cleanup)
- Sample note: {sample_note}
- Estimated days to sell: {days_to_sell}
- Demand trend: {demand_trend}
- Competition: {competition_cat} (HHI: {hhi:.3f})
- Recommended format: {listing_format}

DECISION: {recommendation}

Give your analysis in English. Be specific with numbers."""


def _strip_markdown(text: str) -> str:
    """Elimina markdown residual que el LLM pueda incluir a pesar de la instrucción."""
    if not text:
        return text
    # Remove bold/italic: **text**, *text*, __text__, _text_
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    # Remove headers: ## Header → Header
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bullet points: - item → item, * item → item
    text = re.sub(r"^[\-\*]\s+", "", text, flags=re.MULTILINE)
    # Remove numbered markdown lists with dots: 1. item → item (but keep "1." mid-sentence)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    return text.strip()


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
            "Small sample — be cautious with firm conclusions"
            if cleaned_total < 10
            else "Reasonable sample"
        )

        user_msg = USER_TEMPLATE.format(
            keyword=keyword or "Unknown",
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
            max_tokens=4000,
            temperature=0.7,
            timeout=25,
        )

        text = response.choices[0].message.content
        if response.choices[0].finish_reason == "length":
            logger.warning("AI explanation truncated (hit max_tokens)")
        return _strip_markdown(text)

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
                        max_tokens=4000,
                        temperature=0.7,
                        timeout=25,
                    )
                    text = response.choices[0].message.content
                    if response.choices[0].finish_reason == "length":
                        logger.warning("AI explanation truncated on fallback (hit max_tokens)")
                    return _strip_markdown(text)
                except Exception as e2:
                    logger.warning("OpenAI fallback also failed: %s", e2)
        logger.warning("AI explanation failed: %s", e)
        return None
