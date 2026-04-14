"""Title Enricher — Enriquece listings con datos inferidos de títulos.

Usa GPT-4o-mini para extraer condition, brand, model, bundle info de títulos eBay.
Fallback: regex-based extraction cuando LLM no está disponible.

Costo estimado: ~$0.001 por análisis (50 títulos).
"""

import asyncio
import json
import logging
import re

from dataclasses import dataclass

from app.config import settings
from app.services.marketplace.base import CompsResult

logger = logging.getLogger(__name__)

# Batches de 25 títulos: Flash-Lite es rápido, batches grandes reducen roundtrips.
_LLM_BATCH_SIZE = 25
# Concurrencia: 3 batches simultáneos (Flash-Lite tiene límites más altos)
_LLM_CONCURRENCY = 3

# Condiciones detectables por regex en títulos eBay
_NEW_PATTERNS = re.compile(
    r"\b(NEW|SEALED|NIB|BNIB|BNWT|NWT|NWB|BRAND\s*NEW|FACTORY\s*SEALED|UNOPENED)\b",
    re.IGNORECASE,
)
_USED_PATTERNS = re.compile(
    r"\b(USED|PRE[\s-]?OWNED|PREOWNED|SECOND\s*HAND)\b",
    re.IGNORECASE,
)
_REFURBISHED_PATTERNS = re.compile(
    r"\b(REFURB|REFURBISHED|RENEWED|RECONDITIONED)\b",
    re.IGNORECASE,
)
_OPEN_BOX_PATTERNS = re.compile(
    r"\b(OPEN\s*BOX|OPENED|UNBOXED)\b",
    re.IGNORECASE,
)
_PARTS_PATTERNS = re.compile(
    r"\b(FOR\s*PARTS|NOT\s*WORKING|BROKEN|AS[\s-]?IS|DEFECTIVE|FAULTY)\b",
    re.IGNORECASE,
)
_BUNDLE_PATTERNS = re.compile(
    r"\b(LOT\s+OF\s+(\d+)|BUNDLE\s+OF\s+(\d+)|SET\s+OF\s+(\d+)|(\d+)\s*(?:X|x)\s+|(\d+)\s*(?:PACK|PCS?|PIECES?|COUNT|CT|UNITS?))\b",
    re.IGNORECASE,
)
_BUNDLE_KEYWORDS = re.compile(
    r"\b(LOT|BUNDLE|BULK|WHOLESALE|COLLECTION|SET)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """Extract product attributes from eBay sold listing titles. Return a JSON array with one object per title.

Each object must have:
- "condition": one of "new", "used", "refurbished", "open_box", "for_parts", or null if unclear
- "brand": brand name or null
- "model": model name/number or null
- "is_bundle": true if the listing is a lot/bundle/multi-pack, false otherwise
- "lot_size": number of items in the lot (1 if not a bundle)

Rules:
- "SEALED", "NIB", "BNIB", "NWT", "FACTORY SEALED" → "new"
- "PRE-OWNED", "PREOWNED" → "used"
- "OPEN BOX" → "open_box"
- "FOR PARTS", "AS-IS", "NOT WORKING" → "for_parts"
- "REFURBISHED", "RENEWED" → "refurbished"
- If title says "LOT OF 3" → is_bundle=true, lot_size=3
- Extract brand from title context (e.g. "Apple MacBook" → brand="Apple")
- Extract model from title (e.g. "iPhone 15 Pro Max" → model="iPhone 15 Pro Max")

Return ONLY the JSON array, no other text."""


@dataclass
class TitleEnrichment:
    condition: str | None  # new|used|refurbished|open_box|for_parts
    brand: str | None
    model: str | None
    is_bundle: bool
    lot_size: int  # 1 = item individual


def _regex_condition(title: str) -> str | None:
    """Extrae condition de un título usando regex."""
    if _PARTS_PATTERNS.search(title):
        return "for_parts"
    if _REFURBISHED_PATTERNS.search(title):
        return "refurbished"
    if _OPEN_BOX_PATTERNS.search(title):
        return "open_box"
    if _NEW_PATTERNS.search(title):
        return "new"
    if _USED_PATTERNS.search(title):
        return "used"
    return None


def _regex_bundle(title: str) -> tuple[bool, int]:
    """Detecta si es bundle y extrae lot_size."""
    match = _BUNDLE_PATTERNS.search(title)
    if match:
        # Extraer el número del grupo que matcheó
        for group in match.groups()[1:]:  # Skip el grupo completo
            if group and group.isdigit():
                size = int(group)
                if size > 1:
                    return True, size
        # Si matcheó pero no extrajo número, es bundle de tamaño desconocido
        return True, 1

    if _BUNDLE_KEYWORDS.search(title):
        return True, 1

    return False, 1


def _regex_fallback(titles: list[str]) -> list[TitleEnrichment]:
    """Extrae metadata de títulos usando regex (fallback sin LLM)."""
    results = []
    for title in titles:
        condition = _regex_condition(title)
        is_bundle, lot_size = _regex_bundle(title)
        results.append(TitleEnrichment(
            condition=condition,
            brand=None,
            model=None,
            is_bundle=is_bundle,
            lot_size=lot_size,
        ))
    return results


def _parse_llm_response(
    raw_text: str,
    titles: list[str],
) -> list[TitleEnrichment]:
    """Parsea la respuesta JSON del LLM.

    Tolerante: si el LLM devuelve menos items que títulos,
    acepta los primeros N y completa el resto con regex.
    """
    # Limpiar markdown code fences si las hay
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

    parsed = json.loads(raw_text)

    # json_object mode puede devolver {"results": [...]} en vez de [...]
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                parsed = v
                break
        else:
            raise ValueError(f"JSON object has no list value: {list(parsed.keys())}")

    if not isinstance(parsed, list):
        raise ValueError(f"LLM returned {type(parsed)}, expected list")

    results = []
    for item in parsed:
        cond = item.get("condition")
        if cond not in ("new", "used", "refurbished", "open_box", "for_parts", None):
            cond = None

        lot_size = item.get("lot_size", 1)
        if not isinstance(lot_size, int) or lot_size < 1:
            lot_size = 1

        results.append(TitleEnrichment(
            condition=cond,
            brand=item.get("brand") or None,
            model=item.get("model") or None,
            is_bundle=bool(item.get("is_bundle", False)),
            lot_size=lot_size,
        ))

    # Si devolvió menos, completar con regex para los títulos restantes
    if len(results) < len(titles):
        missing_titles = titles[len(results):]
        results.extend(_regex_fallback(missing_titles))
        logger.info(
            "LLM returned %d/%d items, padded %d with regex",
            len(parsed), len(titles), len(missing_titles),
        )

    # Si devolvió más, truncar
    return results[:len(titles)]


async def _llm_extract_batch(
    client,
    model: str,
    titles: list[str],
    keyword: str | None = None,
) -> list[TitleEnrichment]:
    """Extrae metadata de UN batch de títulos."""
    user_content = json.dumps({
        "search_keyword": keyword,
        "titles": titles,
    })

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=4096,
        temperature=0.1,
        timeout=15,
    )

    raw_text = response.choices[0].message.content.strip()
    return _parse_llm_response(raw_text, titles)


async def _llm_extract(
    titles: list[str],
    keyword: str | None = None,
) -> list[TitleEnrichment]:
    """Extrae metadata de títulos en batches paralelos.

    - Usa Gemini (preferido) con auto-fallback a OpenAI
    - Divide en batches de 15 para evitar timeouts y truncamiento
    - Si Gemini falla (429/500), desactiva Gemini y reintenta con OpenAI
    """
    from app.core.llm import disable_gemini, get_llm_client, is_gemini_error

    client, model = get_llm_client()
    if client is None:
        return _regex_fallback(titles)

    # Dividir en batches
    batches = [
        titles[i:i + _LLM_BATCH_SIZE]
        for i in range(0, len(titles), _LLM_BATCH_SIZE)
    ]

    semaphore = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def _process_batch(batch_titles: list[str]) -> list[TitleEnrichment]:
        async with semaphore:
            try:
                return await _llm_extract_batch(client, model, batch_titles, keyword)
            except Exception as e:
                # Si es error de Gemini, desactivar y reintentar con OpenAI
                if is_gemini_error(e):
                    disable_gemini(str(e)[:100])
                    fallback_client, fallback_model = get_llm_client()
                    if fallback_client is not None:
                        try:
                            return await _llm_extract_batch(
                                fallback_client, fallback_model, batch_titles, keyword,
                            )
                        except Exception as e2:
                            logger.warning(
                                "OpenAI fallback also failed (%d titles): %s",
                                len(batch_titles), e2,
                            )
                logger.warning(
                    "LLM batch failed (%d titles), regex fallback: %s",
                    len(batch_titles), e,
                )
                return _regex_fallback(batch_titles)

    batch_results = await asyncio.gather(*[_process_batch(b) for b in batches])

    # Aplanar resultados manteniendo orden
    results: list[TitleEnrichment] = []
    for br in batch_results:
        results.extend(br)

    return results


async def enrich_listings(
    comps: CompsResult,
    keyword: str | None = None,
) -> CompsResult:
    """Enriquece listings con datos inferidos por LLM.

    Envía títulos en batches paralelos a GPT-4o-mini.
    Fallback: regex-based extraction si LLM falla o no hay API key.
    """
    if not comps.listings:
        return comps

    titles = [l.title for l in comps.listings]

    from app.core.llm import has_llm

    if has_llm():
        try:
            enrichments = await _llm_extract(titles, keyword)
        except Exception as e:
            logger.warning("LLM title enrichment failed, using regex fallback: %s", e)
            enrichments = _regex_fallback(titles)
    else:
        enrichments = _regex_fallback(titles)

    for listing, enrichment in zip(comps.listings, enrichments):
        # Solo sobreescribir si el listing no tiene datos de Apify
        if not listing.condition and enrichment.condition:
            listing.condition = enrichment.condition
        if not listing.brand and enrichment.brand:
            listing.brand = enrichment.brand
        if not listing.model and enrichment.model:
            listing.model = enrichment.model
        # Bundle metadata siempre del enrichment
        listing.is_bundle = enrichment.is_bundle
        listing.lot_size = enrichment.lot_size

    return comps
