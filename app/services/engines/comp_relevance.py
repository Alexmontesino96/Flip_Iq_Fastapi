"""LLM Comp Relevance Filter — Filtra comps que no son el mismo producto.

Usa una sola llamada LLM ultra-ligera (~750 tokens) para clasificar cada comp
como match (1) o no-match (0) respecto al keyword buscado.

Resuelve el problema de variantes semánticas que regex no puede distinguir:
- GS vs Mens (Nike Vomero 5 GS vs Nike Vomero 5 Mens)
- Accesorios vs producto completo
- Colorways premium vs estándar

Costo: ~$0.00005/análisis con Gemini 2.5 Flash.
"""

import json
import logging

from app.services.marketplace.base import CompsResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a product matching expert for resellers.
Given a search keyword and numbered listing titles, classify each as 1 (same product) or 0 (different product).

Rules:
- Model numbers must match (Vomero 5 ≠ Vomero 6, iPhone 14 ≠ iPhone 15)
- Size category must match (GS/Grade School ≠ Mens ≠ Womens ≠ Toddler)
- Accessories, parts, cases, boxes-only = 0
- Different colors of same model+variant = 1
- Bundles/lots of the correct product = 1

Return ONLY a JSON array of 1s and 0s. Example: [1,0,1,0]"""

# Preferred sample size after filtering. Smaller 2-4 samples are still better
# than keeping obvious mismatches, but they should lower confidence downstream.
_MIN_COMPS_AFTER_FILTER = 5
_MIN_LOW_SAMPLE_AFTER_FILTER = 2


async def filter_comps_by_relevance(
    comps: CompsResult,
    keyword: str,
) -> CompsResult:
    """Filtra comps irrelevantes usando una llamada LLM.

    Safety nets:
    - Si no hay LLM disponible, retorna sin filtrar.
    - Si el LLM falla, retorna sin filtrar.
    - Si quedan 2-4 comps, filtra pero marca baja muestra.
    - Si quedan < 2 comps después del filtro, no filtra.
    - Si el array de respuesta no tiene el tamaño correcto, no filtra.
    """
    if not comps.listings or not keyword:
        return comps

    from app.core.llm import disable_gemini, get_llm_client, has_llm, is_gemini_error

    if not has_llm():
        return comps

    titles = [l.title or "" for l in comps.listings]

    try:
        verdicts = await _call_llm(titles, keyword)
    except Exception as e:
        logger.warning("Comp relevance filter failed, skipping: %s", e)
        return comps

    if verdicts is None:
        return comps

    # Validate array length
    if len(verdicts) != len(comps.listings):
        logger.warning(
            "Relevance filter: LLM returned %d verdicts for %d titles, skipping",
            len(verdicts), len(comps.listings),
        )
        return comps

    # Filter
    filtered = [
        listing for listing, verdict in zip(comps.listings, verdicts)
        if verdict == 1
    ]
    removed = len(comps.listings) - len(filtered)
    original_count = len(comps.listings)

    comps.diagnostics["relevance_filter"] = {
        "original_count": original_count,
        "matched_count": len(filtered),
        "removed_count": removed,
        "min_preferred": _MIN_COMPS_AFTER_FILTER,
    }

    # Safety net: one match is too thin to price from, so keep all with metadata.
    if len(filtered) < _MIN_LOW_SAMPLE_AFTER_FILTER:
        logger.info(
            "Relevance filter: only %d/%d would survive, keeping all",
            len(filtered), original_count,
        )
        comps.diagnostics["relevance_filter"]["applied"] = False
        comps.diagnostics["relevance_filter"]["reason"] = "too_few_matches"
        return comps

    if removed > 0:
        logger.info(
            "Relevance filter: removed %d/%d comps for '%s'",
            removed, original_count, keyword,
        )
        # Rebuild CompsResult with filtered listings
        comps.listings = filtered
        comps.total_sold = len(filtered)
        comps.diagnostics["relevance_filter"]["applied"] = True
        if len(filtered) < _MIN_COMPS_AFTER_FILTER:
            warning = (
                f"Only {len(filtered)} highly relevant comps remained after filtering; "
                "pricing confidence is limited."
            )
            comps.warnings.append(warning)
            comps.diagnostics["relevance_filter"]["low_sample"] = True
    else:
        comps.diagnostics["relevance_filter"]["applied"] = False
        comps.diagnostics["relevance_filter"]["reason"] = "all_matched"

    return comps


async def _call_llm(
    titles: list[str],
    keyword: str,
) -> list[int] | None:
    """Hace la llamada LLM y parsea la respuesta.

    Retorna lista de 1/0 o None si falla.
    """
    from app.core.llm import disable_gemini, get_llm_client, is_gemini_error

    client, model = get_llm_client(fast=True)
    if client is None:
        return None

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    user_content = f"Keyword: {keyword}\n\nTitles:\n{numbered}"

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1024,
            temperature=0.0,
            timeout=20,
        )
    except Exception as e:
        if is_gemini_error(e):
            disable_gemini(str(e)[:100])
            # Retry with fallback
            client, model = get_llm_client(fast=True)
            if client is None:
                return None
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=1024,
                    temperature=0.0,
                    timeout=20,
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
