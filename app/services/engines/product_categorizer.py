"""Product Categorizer — extrae product_type y ebay_category_id del keyword.

Strategy: eBay Taxonomy API (free, ~100ms) → LLM fallback → None.

Permite filtrar datos contaminantes (e.g. viseras cuando buscas cascos).
También mapea a una categoría de eBay (_sacat) para mejorar la precisión del scraper.
"""

import json
import logging
from dataclasses import dataclass

from app.core.llm import get_llm_client, disable_gemini, is_gemini_error
from app.services.marketplace.ebay_taxonomy import get_category_suggestions

logger = logging.getLogger(__name__)

# Categorías curadas de eBay para resellers — usadas como _sacat en búsquedas
EBAY_CATEGORIES: dict[int, str] = {
    9355: "Cell Phones & Smartphones",
    15032: "Video Games",
    139971: "Video Game Consoles",
    175673: "Laptops & Netbooks",
    171485: "Tablets & eReaders",
    112529: "Headphones",
    178893: "Smartwatches",
    11450: "Clothing, Shoes & Accessories",
    15709: "Athletic Shoes",
    95672: "Action Cameras",
    31388: "Digital Cameras",
    3676: "Desktop & All-In-One PCs",
    183454: "Trading Cards",
    261068: "Action Figures",
    11116: "Building Toys",
    20710: "Drones & Quadcopters",
    73839: "TV, Video & Home Audio",
    11700: "Home Appliances",
    169291: "GPU / Video Cards",
    164: "Monitors",
    40054: "Handbags",
    3034: "Power Tools",
    11071: "Golf Clubs",
    15724: "Bicycles",
}

_CATEGORY_LIST = "\n".join(f"  {cid}: {name}" for cid, name in EBAY_CATEGORIES.items())

CATEGORIZE_PROMPT = """Given a product search keyword, extract:
- product_type: the core product noun (e.g. "helmet", "sneakers", "phone")
- category: human-readable category (e.g. "Cycling Helmet", "Running Shoes")
- confidence: 0.0-1.0
- ebay_category_id: the best matching eBay category ID from the list below, or null if none fits

eBay categories:
{categories}

Return JSON only: {{"product_type": "...", "category": "...", "confidence": 0.9, "ebay_category_id": 9355}}

Keyword: "{keyword}"
"""


@dataclass
class CategoryResult:
    product_type: str              # "helmet"
    category: str                  # "Cycling Helmet"
    confidence: float              # 0.0-1.0
    ebay_category_id: int | None   # 139971 (eBay _sacat)


async def categorize_product(keyword: str) -> CategoryResult | None:
    """Extrae product_type y ebay_category_id del keyword.

    Strategy:
    1. eBay Taxonomy API getCategorySuggestions (free, fast, accurate)
    2. LLM fallback (Gemini Flash → OpenAI)
    3. None if both fail
    """
    # --- Strategy 1: eBay Taxonomy API ---
    result = await _categorize_via_taxonomy(keyword)
    if result:
        return result

    # --- Strategy 2: LLM fallback ---
    return await _categorize_via_llm(keyword)


async def _categorize_via_taxonomy(keyword: str) -> CategoryResult | None:
    """Use eBay's Taxonomy API getCategorySuggestions."""
    try:
        suggestions = await get_category_suggestions(keyword)
    except Exception as e:
        logger.debug("Taxonomy API unavailable: %s", e)
        return None

    if not suggestions:
        return None

    best = suggestions[0]

    # Extract product_type from keyword (last noun-like word)
    product_type = _extract_product_type(keyword)

    # Build category path string
    category = best.category_name
    if best.parent_path:
        category = f"{best.parent_path[-1]} > {best.category_name}" if len(best.parent_path) > 0 else best.category_name

    return CategoryResult(
        product_type=product_type,
        category=category,
        confidence=0.9 if len(suggestions) == 1 else 0.8,
        ebay_category_id=best.category_id,
    )


def _extract_product_type(keyword: str) -> str:
    """Extract the core product noun from a keyword string.

    Simple heuristic: last meaningful word (skip brands/sizes/colors).
    """
    # Common words to skip
    skip = {
        "new", "used", "sealed", "brand", "lot", "bundle", "set",
        "black", "white", "blue", "red", "green", "gold", "silver", "pink",
        "small", "medium", "large", "xl", "xxl", "xs",
        "pro", "max", "plus", "mini", "ultra", "lite",
    }
    words = keyword.lower().split()
    # Try from the end, find a noun-like word
    for word in reversed(words):
        if word not in skip and len(word) > 2 and not word.isdigit():
            return word
    return words[-1] if words else keyword.lower()


async def _categorize_via_llm(keyword: str) -> CategoryResult | None:
    """Fallback: use LLM to categorize when Taxonomy API fails."""
    client, model = get_llm_client(fast=True)
    if client is None:
        return None

    prompt = CATEGORIZE_PROMPT.format(keyword=keyword, categories=_CATEGORY_LIST)

    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                temperature=0.0,
                timeout=15,
            )
            text = (resp.choices[0].message.content or "").strip()
            # Limpiar markdown fences si las hay
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            data = json.loads(text)
            product_type = data.get("product_type", "").strip().lower()
            if not product_type:
                return None

            # Validar ebay_category_id contra lista curada
            raw_cat_id = data.get("ebay_category_id")
            ebay_category_id = None
            if raw_cat_id is not None:
                try:
                    cat_id = int(raw_cat_id)
                    if cat_id in EBAY_CATEGORIES:
                        ebay_category_id = cat_id
                except (ValueError, TypeError):
                    pass

            return CategoryResult(
                product_type=product_type,
                category=data.get("category", ""),
                confidence=float(data.get("confidence", 0.5)),
                ebay_category_id=ebay_category_id,
            )
        except json.JSONDecodeError:
            logger.warning("Categorizer: respuesta no es JSON válido: %s", text[:200])
            return None
        except Exception as e:
            if is_gemini_error(e) and attempt == 0:
                disable_gemini(f"categorizer: {e}")
                client, model = get_llm_client(fast=True)
                if client is None:
                    return None
                continue
            logger.warning("Categorizer LLM failed: %s", e)
            return None

    return None
