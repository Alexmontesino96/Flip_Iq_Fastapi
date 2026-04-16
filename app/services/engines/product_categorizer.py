"""Product Categorizer — extrae product_type del keyword usando LLM.

Permite filtrar datos contaminantes (e.g. viseras cuando buscas cascos).
Usa Gemini Flash (~100ms) con fallback a OpenAI, y None si no hay LLM.
"""

import json
import logging
from dataclasses import dataclass

from app.core.llm import get_llm_client, disable_gemini, is_gemini_error

logger = logging.getLogger(__name__)

CATEGORIZE_PROMPT = """Given a product search keyword, extract:
- product_type: the core product noun (e.g. "helmet", "sneakers", "phone")
- category: human-readable category (e.g. "Cycling Helmet", "Running Shoes")
- confidence: 0.0-1.0

Return JSON only: {{"product_type": "...", "category": "...", "confidence": 0.9}}

Keyword: "{keyword}"
"""


@dataclass
class CategoryResult:
    product_type: str       # "helmet"
    category: str           # "Cycling Helmet"
    confidence: float       # 0.0-1.0


async def categorize_product(keyword: str) -> CategoryResult | None:
    """Extrae product_type del keyword usando LLM.

    Returns None si no hay LLM configurado o si falla.
    """
    client, model = get_llm_client()
    if client is None:
        return None

    prompt = CATEGORIZE_PROMPT.format(keyword=keyword)

    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.0,
            )
            text = (resp.choices[0].message.content or "").strip()
            # Limpiar markdown fences si las hay
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            data = json.loads(text)
            product_type = data.get("product_type", "").strip().lower()
            if not product_type:
                return None

            return CategoryResult(
                product_type=product_type,
                category=data.get("category", ""),
                confidence=float(data.get("confidence", 0.5)),
            )
        except json.JSONDecodeError:
            logger.warning("Categorizer: respuesta no es JSON válido: %s", text[:200])
            return None
        except Exception as e:
            if is_gemini_error(e) and attempt == 0:
                disable_gemini(f"categorizer: {e}")
                client, model = get_llm_client()
                if client is None:
                    return None
                continue
            logger.warning("Categorizer failed: %s", e)
            return None

    return None
