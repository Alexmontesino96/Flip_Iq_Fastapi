"""Comp Relevance Filter — Filtra comps que no son el mismo producto.

Usa Cohere Rerank 4 Pro vía OpenRouter para scoring semántico de relevancia.
Cada título se compara contra el keyword y recibe un score 0.0–1.0.

Fallback: si OpenRouter no está disponible o falla, usa LLM (Gemini/OpenAI).

Costo: ~$0.0025/búsqueda con Cohere Rerank 4 Pro.
"""

import json
import logging

import httpx

from app.config import settings
from app.services.marketplace.base import CompsResult

logger = logging.getLogger(__name__)

_RERANK_URL = "https://openrouter.ai/api/v1/rerank"
_RERANK_MODEL = "cohere/rerank-4-pro"
_RERANK_THRESHOLD = 0.5  # score >= 0.5 → match (1)

# Minimum comps to keep after filtering; below this we skip the filter
_MIN_COMPS_AFTER_FILTER = 5

_LLM_SYSTEM_PROMPT = """You are a product matching expert for resellers.
Given a search keyword and numbered listing titles, classify each as 1 (same product) or 0 (different product).

Rules:
- Model numbers must match (Vomero 5 ≠ Vomero 6, iPhone 14 ≠ iPhone 15)
- Size category must match (GS/Grade School ≠ Mens ≠ Womens ≠ Toddler)
- Accessories, parts, cases, boxes-only = 0
- Different colors of same model+variant = 1
- Bundles/lots of the correct product = 1

Return ONLY a JSON array of 1s and 0s. Example: [1,0,1,0]"""


async def filter_comps_by_relevance(
    comps: CompsResult,
    keyword: str,
) -> CompsResult:
    """Filtra comps irrelevantes usando Cohere Rerank (o LLM fallback).

    Safety nets:
    - Si no hay API disponible, retorna sin filtrar.
    - Si la API falla, intenta fallback LLM, sino retorna sin filtrar.
    - Si quedan < _MIN_COMPS_AFTER_FILTER después del filtro, no filtra.
    """
    if not comps.listings or not keyword:
        return comps

    titles = [l.title or "" for l in comps.listings]

    # Intentar rerank primero, luego fallback a LLM
    verdicts = await _call_rerank(titles, keyword)
    used_rerank = verdicts is not None

    if verdicts is None:
        try:
            verdicts = await _call_llm(titles, keyword)
        except Exception as e:
            logger.warning("Comp relevance filter failed (both rerank and LLM), skipping: %s", e)
            return comps

    if verdicts is None:
        return comps

    # Validate array length
    if len(verdicts) != len(comps.listings):
        logger.warning(
            "Relevance filter: got %d verdicts for %d titles, skipping",
            len(verdicts), len(comps.listings),
        )
        return comps

    # Filter
    filtered = [
        listing for listing, verdict in zip(comps.listings, verdicts)
        if verdict == 1
    ]
    removed = len(comps.listings) - len(filtered)

    # Safety net: don't filter if too few remain
    if len(filtered) < _MIN_COMPS_AFTER_FILTER:
        logger.info(
            "Relevance filter: only %d/%d would survive, keeping all",
            len(filtered), len(comps.listings),
        )
        return comps

    if removed > 0:
        logger.info(
            "Relevance filter (%s): removed %d/%d comps for '%s'",
            "rerank" if used_rerank else "llm",
            removed, len(comps.listings), keyword,
        )
        comps.listings = filtered
        comps.total_sold = len(filtered)

    # Mark as reranked if Cohere did the filtering
    if used_rerank:
        comps.reranked = True

    return comps


async def _call_rerank(
    titles: list[str],
    keyword: str,
) -> list[int] | None:
    """Llama a Cohere Rerank 4 Pro vía OpenRouter.

    Retorna lista de 1/0 basada en threshold, o None si falla.
    """
    api_key = settings.openrouter_api_key
    if not api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _RERANK_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": _RERANK_MODEL,
                    "query": keyword,
                    "documents": titles,
                    "top_n": len(titles),
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning("Rerank API failed, falling back to LLM: %s", e)
        return None

    results = data.get("results")
    if not results or not isinstance(results, list):
        logger.warning("Rerank API returned unexpected format: %s", str(data)[:200])
        return None

    # Build verdicts array indexed by original position
    verdicts = [0] * len(titles)
    for item in results:
        idx = item.get("index")
        score = item.get("relevance_score", 0.0)
        if idx is not None and 0 <= idx < len(titles):
            verdicts[idx] = 1 if score >= _RERANK_THRESHOLD else 0

    return verdicts


async def _call_llm(
    titles: list[str],
    keyword: str,
) -> list[int] | None:
    """Fallback: hace la llamada LLM y parsea la respuesta.

    Retorna lista de 1/0 o None si falla.
    """
    from app.core.llm import disable_gemini, get_llm_client, has_llm, is_gemini_error

    if not has_llm():
        return None

    client, model = get_llm_client()
    if client is None:
        return None

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    user_content = f"Keyword: {keyword}\n\nTitles:\n{numbered}"

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=150,
            temperature=0.0,
            timeout=10,
        )
    except Exception as e:
        if is_gemini_error(e):
            disable_gemini(str(e)[:100])
            # Retry with fallback
            client, model = get_llm_client()
            if client is None:
                return None
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=150,
                    temperature=0.0,
                    timeout=10,
                )
            except Exception:
                return None
        else:
            return None

    raw_text = response.choices[0].message.content.strip()
    return _parse_response(raw_text, len(titles))


def _parse_response(raw_text: str, expected_len: int) -> list[int] | None:
    """Parsea la respuesta del LLM a una lista de 1/0.

    Retorna None si no puede parsear o el tamaño no coincide.
    """
    # Strip markdown code fences
    if raw_text.startswith("```"):
        import re
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning("Relevance filter: could not parse LLM response: %s", raw_text[:200])
        return None

    if not isinstance(parsed, list):
        logger.warning("Relevance filter: expected list, got %s", type(parsed).__name__)
        return None

    if len(parsed) != expected_len:
        return None

    # Normalize to strict 0/1
    result = []
    for v in parsed:
        if v in (1, True, "1"):
            result.append(1)
        elif v in (0, False, "0"):
            result.append(0)
        else:
            # Unexpected value — abort
            logger.warning("Relevance filter: unexpected value %r in response", v)
            return None

    return result
