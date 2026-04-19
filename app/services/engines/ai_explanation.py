"""Motor L — AI Explanation.

Genera explicación en lenguaje natural usando GPT-4o-mini via OpenAI API.
Fallback: texto genérico basado en los scores si la API falla.
"""

import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert assistant in product reselling/flipping.
Your job is to give a short decision brief for a reseller standing in a store.
Be direct, practical and use simple language. Respond in English.

FORMATTING: Write plain text only. Do NOT use markdown, asterisks, headers, or bullets.

You MUST write exactly 4 short lines, each on its own line:

Decision: YES, YES (LIMITED), NOT YET, or NO, followed by the shortest reason.
Why: one sentence explaining the main upside.
Risk: one sentence naming the main execution risk.
Action: one sentence telling the user what to do next, including quantity if relevant.

Keep the whole response under 110 words.

Important rules:
- The Decision line MUST match the provided DECISION value exactly:
  buy = YES, buy_small = YES (LIMITED), watch = NOT YET, pass = NO.
  Do not upgrade or downgrade that label in the AI text.
- Do not repeat every metric. Pick the few numbers that change the decision.
- If confidence is below 40 or comps are fewer than 5, the decision should be NOT YET or NO unless the action clearly says verify manually.
- If recommendation is buy_small, use YES (LIMITED), not YES.
- If execution analysis is provided, lead with execution confidence and quantity guidance, not raw ROI.
- If marketplace comparison is provided, name the recommended selling channel in the Action line."""

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


_DECISION_LABELS = {
    "buy": "YES",
    "buy_small": "YES (LIMITED)",
    "watch": "NOT YET",
    "pass": "NO",
}


def _align_decision_line(text: str, recommendation: str) -> str:
    """Keep the AI brief's first line aligned with the deterministic engine."""
    label = _DECISION_LABELS.get(recommendation)
    if not text or not label:
        return text

    lines = text.splitlines()
    if not lines:
        return f"Decision: {label}"

    first = lines[0].strip()
    if first.lower().startswith("decision:"):
        reason = first.split(":", 1)[1].strip()
        reason = re.sub(
            r"^(YES\s*\(LIMITED\)|YES|NOT\s+YET|NO)(?:\s*[,;:.-]\s*|\s+|$)",
            "",
            reason,
            flags=re.IGNORECASE,
        ).strip()
        lines[0] = f"Decision: {label}" + (f", {reason}" if reason else "")
    else:
        lines.insert(0, f"Decision: {label}")

    return "\n".join(lines)


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
            max_tokens=700,
            temperature=0.3,
            timeout=25,
        )

        text = response.choices[0].message.content
        if response.choices[0].finish_reason == "length":
            logger.warning("AI explanation truncated (hit max_tokens)")
        return _align_decision_line(_strip_markdown(text), recommendation)

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
                        max_tokens=700,
                        temperature=0.3,
                        timeout=25,
                    )
                    text = response.choices[0].message.content
                    if response.choices[0].finish_reason == "length":
                        logger.warning("AI explanation truncated on fallback (hit max_tokens)")
                    return _align_decision_line(_strip_markdown(text), recommendation)
                except Exception as e2:
                    logger.warning("OpenAI fallback also failed: %s", e2)
        logger.warning("AI explanation failed: %s", e)
        return None
