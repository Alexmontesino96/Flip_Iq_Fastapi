"""Extracción del bundle factor del título de un listing de Amazon.

¿Cuántas unidades base agrupa el listing? Un "Pack of 12" agrupa 12. Sin esta
señal, el coste de UNA unidad (el cost_price que ingresa el usuario) se compara
contra el precio del paquete completo → ROI fantasma (caso Trojan: coste $1.30
vs pack de 12 a $28.80 → 700% irreal).

Diseño híbrido (portado de batchflip_core, ver docs/MULTIPACK_IMPLEMENTATION.md):
  1. Pre-filtro barato (has_pack_signal): sin ninguna señal de pack → unidad
     simple, sin trabajo extra. Útil para gatear el LLM en una fase posterior.
  2. Regex determinista (regex_bundle_factor): patrones INEQUÍVOCOS de bundle
     ("Pack of N", "N-Pack", "Case of N", twin/triple). Ignora "N Count"/"N ct"
     porque es AMBIGUO: puede describir la unidad base ("Condoms, 3 Count" = 3
     por caja → factor 1) o el bundle ("Paper Towels, 12 Count" = 12 rollos).
     Devuelve None ante el caso ambiguo (lo decide el LLM en una fase posterior).

Fuente ÚNICA de verdad del criterio multipack: amazon.py la usa para filtrar
comps y el guard de coste para detectar el mismatch. Nunca lanza.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash-lite"

# Caché en memoria título→factor (el bundle de un ASIN no cambia). Cap simple.
_LLM_CACHE: dict[str, int] = {}
_CACHE_MAX = 2048

_SYSTEM_PROMPT = (
    "You are a packaging-quantity auditor for an Amazon arbitrage tool. Given an "
    "Amazon product TITLE, determine the BUNDLE FACTOR: how many base "
    "selling-units the listing bundles together.\n"
    "\n"
    "Rules:\n"
    "1. The base selling-unit is the item as normally sold individually. Tokens "
    "like '3 Count', '3ct', '2 oz', '500mg' usually DESCRIBE the base unit — they "
    "are NOT a bundle multiplier.\n"
    "2. A bundle multiplier is an explicit grouping of several base units: 'Pack "
    "of N', 'N Pack', 'N-Pack', 'Case of N', 'Box of N', 'Set of N', 'Lot of N', "
    "'Bundle of N'. 'N Count'/'N ct' is a multiplier ONLY when N clearly counts "
    "whole units (e.g. 'Paper Towels, 12 Count' = 12 rolls), NOT the contents of "
    "one unit (e.g. 'Condoms, 3 Count' = 3 per box → base unit, factor 1).\n"
    "3. If the title has BOTH a unit descriptor and a bundle multiplier (e.g. "
    "'3 Count (Pack of 12)'), the bundle factor is the multiplier (12), never the "
    "descriptor (3).\n"
    "4. If there is no bundle multiplier, the bundle factor is 1.\n"
    "\n"
    'Respond ONLY as JSON: {"bundle_factor": <integer >= 1>, '
    '"base_unit": "<short>", "reasoning": "<1 sentence>"}.'
)

# Cota de cordura del factor. Un bundle inequívoco real rara vez supera ~144
# (una "gross", 12 docenas). Por encima es casi seguro un número espurio del
# título (año, gramaje, nº de modelo). El gate fee-ratio / package_quantity del
# guard recupera cualquier pack mayor que se escape por aquí.
_MAX_REASONABLE_FACTOR = 144

# Pre-filtro: ¿el título tiene ALGUNA señal de pack/cantidad?
# Conservador: ante la duda, marcar señal (un falso positivo solo gasta una rama
# extra; un falso negativo perdería un multipack). Incluye el conteo PEGADO
# ("12ct", "36CT", "12pk") que un \b entre dígito y letra no capturaría.
_PACK_SIGNAL_RE = re.compile(
    r"(?i)"
    r"\b(pack|packs|pk|count|ct|cnt|case|box|set|lot|bundle|"
    r"twin|triple|dozen|multipack|qty)\b"
    r"|\d+\s*(?:ct|cnt|pk|pack|count)s?\b"
    r"|\b\d+\s*[-x]\s*\d+\b"
    r"|\(\s*\d+\s*\)"
)

# Patrones INEQUÍVOCOS de bundle (resueltos sin LLM). Llevan una palabra
# explícita de agrupación (pack/case/box/set/lot/bundle) o la forma "N-Pack".
# NO incluyen "N Count"/"N ct" (ambiguos → los decide el LLM en otra fase).
_BUNDLE_RE = re.compile(
    r"(?i)(?:"
    r"pack\s+of\s+(\d+)"
    r"|case\s+of\s+(\d+)"
    r"|box\s+of\s+(\d+)"
    r"|set\s+of\s+(\d+)"
    r"|lot\s+of\s+(\d+)"
    r"|bundle\s+of\s+(\d+)"
    r"|(\d+)\s*[-\s]?pack\b"
    r"|(\d+)\s*[-\s]?pk\b"
    r")"
)
_TWIN_RE = re.compile(r"(?i)\btwin[\s-]+pack\b")
_TRIPLE_RE = re.compile(r"(?i)\btriple[\s-]+pack\b")


def has_pack_signal(title: str) -> bool:
    """True si el título contiene alguna señal de pack/cantidad."""
    if not title or not isinstance(title, str):
        return False
    return bool(_PACK_SIGNAL_RE.search(title))


def regex_bundle_factor(title: str) -> int | None:
    """Factor de bundle por patrones INEQUÍVOCOS del título, o None si ambiguo.

    Determinista, sin red. Toma el MAYOR N de los patrones explícitos de
    agrupación ("Pack of N", "N-Pack", "Case of N"…) + twin(2)/triple(3). Ignora
    "N Count"/"N ct" (ambiguo → None). Cap defensivo a _MAX_REASONABLE_FACTOR.

    Ej.: "Trojan ... 3 Count (Pack of 12)" → 12 (ignora el "3 Count").
         "Paper Towels, 12 Count"          → None (ambiguo → lo decide el LLM).
         "Vitamin C, 100 Count"            → None (unidad base, NO se filtra).
    """
    if not title or not isinstance(title, str):
        return None
    factors: list[int] = []
    for m in _BUNDLE_RE.finditer(title):
        for g in m.groups():
            if g:
                try:
                    n = int(g)
                except (TypeError, ValueError):
                    continue
                if 1 <= n <= _MAX_REASONABLE_FACTOR:
                    factors.append(n)
    if _TWIN_RE.search(title):
        factors.append(2)
    if _TRIPLE_RE.search(title):
        factors.append(3)
    return max(factors) if factors else None


def is_multipack_title(title: str) -> bool:
    """True si el título es un multipack INEQUÍVOCO (factor > 1).

    "N Count"/"N ct" devuelve False (ambiguo → no se trata como multipack):
    evita descartar de los comps unidades sueltas válidas de categorías muy
    comunes con "N Count" (vitaminas, baterías, K-cups, cosméticos). Reemplaza
    al antiguo _PACK_RE que sí trataba "N count" como multipack (falso positivo).
    """
    factor = regex_bundle_factor(title)
    return factor is not None and factor > 1


def _parse_factor(raw: str | None) -> int | None:
    """Parsea la respuesta JSON del LLM → bundle_factor (int en [1, MAX]) o None."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        factor = int(data.get("bundle_factor"))
    except (TypeError, ValueError):
        return None
    if factor < 1 or factor > _MAX_REASONABLE_FACTOR:
        return None
    return factor


def _cache_set(key: str, factor: int) -> None:
    if len(_LLM_CACHE) >= _CACHE_MAX:
        _LLM_CACHE.clear()  # política simple: vaciar al llegar al cap
    _LLM_CACHE[key] = factor


async def extract_bundle_factor(title: str, *, client=None, model: str | None = None) -> int | None:
    """Cuántas unidades base agrupa el ASIN según su título (regex → LLM).

    Contrato:
      - >= 2 : multipack (el coste por-unidad debe escalarse).
      - == 1 : unidad simple (sin señal de pack, o el LLM lo confirma).
      - None : ambiguo y LLM no disponible/falló (el caller usa los otros gates).

    Best-effort: NUNCA lanza. El LLM (Gemini Flash-Lite) SOLO se consulta para el
    caso ambiguo "N Count"/"N ct" sin patrón inequívoco; los demás los resuelve el
    regex sin red. `client` permite inyectar un AsyncOpenAI (tests).
    """
    if not title or not isinstance(title, str):
        return None
    # (1) Sin señal de pack → unidad simple, sin LLM.
    if not has_pack_signal(title):
        return 1
    # (2) Patrón inequívoco → resuelto por regex, sin LLM.
    rx = regex_bundle_factor(title)
    if rx is not None:
        return rx
    # (3) Ambiguo ("N Count"/"N ct"): desambiguar con el LLM.
    key = title.strip().lower()
    cached = _LLM_CACHE.get(key)
    if cached is not None:
        return cached

    owns_client = False
    if client is None:
        from app.core.llm import get_llm_client

        client, model = get_llm_client(fast=True)
        if client is None:
            return None
        owns_client = True
    elif model is None:
        model = _DEFAULT_MODEL

    factor: int | None = None
    try:
        response = await client.chat.completions.create(
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"title": title.strip()}, ensure_ascii=False)},
            ],
        )
        factor = _parse_factor(response.choices[0].message.content)
    except Exception as e:
        logger.warning("multipack LLM failed for %.50s: %s", title, e)
        factor = None
    finally:
        if owns_client:
            try:
                await client.close()
            except Exception:
                pass

    if factor is not None:
        _cache_set(key, factor)
        logger.info("multipack LLM: '%.50s' → bundle_factor=%d", title, factor)
    return factor
