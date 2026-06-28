"""Desambiguación de identidad en la resolución code→ASIN (anti-contaminación).

Portado de batchflip_core/services/identity.py (único cambio: el import del
detector de multipack apunta a app.services.marketplace.multipack).

Problema (medido sobre catálogo real): un UPC/EAN puede estar reclamado por varios
ASINs de marcas DISTINTAS porque el catálogo de Amazon "contamina" la ficha de un
producto con el código de barras de otro. Caso real: el UPC de "Summer's Eve"
aparece también en el upcList del ASIN de "Arrid". Elegir "el primer candidato
con packageQuantity==1" podía dar el ASIN equivocado → un falso rentable con ROI
absurdo.

Señal discriminante (validada midiendo 97 ASINs reales): el CONSENSO DE MARCA
entre los candidatos del code. El dueño legítimo DOMINA (Summer's Eve 10/11) y el
contaminante es un OUTLIER minoritario (Arrid 1/11).

Dos capas, aplicadas por `choose_candidate`:
  · CAPA 1 (CORRIGE): si el candidato por defecto es un OUTLIER de marca y otra
    marca es MAYORÍA clara, se elige el mejor candidato de la marca mayoritaria.
  · CAPA 2 (DEGRADA): si hay conflicto de marca SIN mayoría clara (ambiguo), se
    señala `needs_review` para que el caller lo mande a revisión humana.

Conservador: sin señal de marca (1 candidato, o sin brand) o sin conflicto →
resultado IDÉNTICO al legacy (cero regresión).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# Umbrales anclados a la medición real (Arrid outlier 1/11≈0.09; mayoría
# Summer's Eve 10/11≈0.91). Conservadores: solo se CORRIGE ante una mayoría clara
# y solo se DEGRADA ante un outlier sin mayoría; la zona intermedia (una marca con
# 30-60%) se deja como el legacy.
OUTLIER_MAX_SHARE = 0.30
MAJORITY_MIN_SHARE = 0.60


@dataclass
class ChoiceResult:
    """Resultado de elegir un ASIN entre los candidatos de un code."""
    asin: str | None
    needs_review: bool          # CAPA 2: identidad ambigua → revisión humana
    chosen_brand: str | None
    dominant_brand: str | None
    n_candidates: int
    reason: str                 # no_candidates|no_brand_signal|default_ok|
    #                             corrected_to_majority_brand|ambiguous_brand_conflict
    dominant_share: float = 0.0  # fracción de candidatos de la marca dominante


def _norm_brand(b) -> str:
    return str(b).strip().lower() if b else ""


def _title_is_multipack(c: dict) -> bool:
    """¿El candidato es un multipack según su TÍTULO (regex inequívoco)?

    Usa regex_bundle_factor (distingue el multiplicador real del descriptor de
    unidad: 'Condoms, 3 Count' NO es multipack; 'Soap (Pack of 12)' sí). Sin
    título o sin patrón inequívoco → False (conservador).
    """
    title = c.get("title")
    if not title:
        return False
    try:
        from app.services.marketplace.multipack import regex_bundle_factor

        factor = regex_bundle_factor(title)
    except Exception:
        return False
    return factor is not None and factor > 1


def _best_pick(cands: list[dict]) -> dict | None:
    """Mejor candidato de un conjunto, criterio legacy + anti-multipack por título.

    Orden de preferencia:
      1. packageQuantity==1 Y no-multipack-por-título (la unidad suelta REAL).
      2. packageQuantity==1 (criterio legacy: pkg conocido).
      3. primer no-multipack (por título o por el flag is_multipack del provider).
      4. el primero.
    `package_quantity is None` se trata como aceptable (no excluye), igual que el
    código original.
    """
    for c in cands:
        if c.get("package_quantity") == 1 and not _title_is_multipack(c):
            return c
    for c in cands:
        if c.get("package_quantity") == 1:
            return c
    for c in cands:
        if not _title_is_multipack(c) and not c.get("is_multipack"):
            return c
    return cands[0] if cands else None


def choose_candidate(
    code: str, candidates: list[dict], *, id_type: str | None = None,
) -> ChoiceResult:
    """Elige el ASIN correcto entre los candidatos de un code por consenso de marca.

    Args:
        code: el UPC/EAN/identificador buscado (solo para trazabilidad).
        candidates: lista de dicts {asin, package_quantity, brand, is_multipack?,
            ...} en el ORDEN que devolvió el provider. `asin` es obligatorio.

    Returns:
        ChoiceResult con el `asin` elegido (CAPA 1) y `needs_review` (CAPA 2).
    """
    cands = [c for c in candidates if c.get("asin")]
    if not cands:
        return ChoiceResult(None, False, None, None, 0, "no_candidates")

    default = _best_pick(cands)
    default_asin = default["asin"]
    default_brand = _norm_brand(default.get("brand"))

    # Para hablar de consenso necesitamos ≥2 candidatos CON marca. Y el default
    # ELEGIDO debe tener marca: si no la tiene, no hay señal para reemplazarlo →
    # default legacy (cero regresión). Esto corrige el bug del original de
    # BatchFlip, donde un default sin marca se "corregía" espuriamente a la marca
    # mayoritaria, descartando el ASIN legítimo (Keepa a veces no pobla 'brand').
    branded = [c for c in cands if _norm_brand(c.get("brand"))]
    if len(branded) < 2 or not default_brand:
        return ChoiceResult(
            default_asin, False, default_brand or None, None, len(cands), "no_brand_signal",
        )

    counts = Counter(_norm_brand(c.get("brand")) for c in branded)
    total = sum(counts.values())
    dom_brand, dom_n = counts.most_common(1)[0]
    dom_share = dom_n / total
    default_share = counts.get(default_brand, 0) / total

    # El default ya es de la marca dominante, o su marca no es minoritaria → sin cambio.
    if default_brand == dom_brand or default_share > OUTLIER_MAX_SHARE:
        return ChoiceResult(
            default_asin, False, default_brand or None, dom_brand, len(cands),
            "default_ok", dom_share,
        )

    # El default es un OUTLIER de marca (≤30%). ¿Hay una mayoría clara que lo corrija?
    if dom_brand and dom_brand != default_brand and dom_share >= MAJORITY_MIN_SHARE:
        dom_cands = [c for c in cands if _norm_brand(c.get("brand")) == dom_brand]
        pick = _best_pick(dom_cands)
        return ChoiceResult(
            pick["asin"], False, dom_brand, dom_brand, len(cands),
            "corrected_to_majority_brand", dom_share,
        )

    # Outlier de marca SIN mayoría clara → identidad ambigua → degradar (CAPA 2).
    return ChoiceResult(
        default_asin, True, default_brand or None, dom_brand, len(cands),
        "ambiguous_brand_conflict", dom_share,
    )
