"""Title Risk Detector.

Detecta keywords peligrosas en títulos de comps que pueden contaminar
el análisis: "box only", "empty box", "case only", "replacement", etc.

Retorna flags semánticas y un score de riesgo de título.
"""

import re
from dataclasses import dataclass, field

from app.services.marketplace.base import CleanedComps

# Patrones peligrosos con su peso de riesgo (0-1)
DANGER_PATTERNS: list[tuple[str, float, str]] = [
    # (regex, weight, flag_name)
    (r"\bbox\s*only\b", 1.0, "box_only"),
    (r"\bempty\s*box\b", 1.0, "empty_box"),
    (r"\bcase\s*only\b", 1.0, "case_only"),
    (r"\bno\s*(console|device|unit|phone|tablet|laptop)\b", 1.0, "no_device"),
    (r"\breplacement\b", 0.7, "replacement"),
    (r"\bfor\s*parts\b", 0.9, "for_parts"),
    (r"\bas[\s-]*is\b", 0.6, "as_is"),
    (r"\bnot\s*working\b", 0.9, "not_working"),
    (r"\bbroken\b", 0.8, "broken"),
    (r"\bdamaged\b", 0.6, "damaged"),
    (r"\bdefective\b", 0.8, "defective"),
    (r"\bread\b.*\bdescription\b", 0.5, "read_description"),
    (r"\block(ed)?\b", 0.7, "locked"),
    (r"\bicloud\s*lock\b", 0.9, "icloud_locked"),
    (r"\bcracked\b", 0.7, "cracked"),
    (r"\blot\s*of\b", 0.4, "bulk_lot"),
    (r"\bbundle\b", 0.3, "bundle"),
    (r"\baccessor(y|ies)\s*only\b", 0.8, "accessories_only"),
    (r"\bmanual\s*only\b", 0.9, "manual_only"),
    (r"\bcharger\s*only\b", 0.8, "charger_only"),
    (r"\bcable\s*only\b", 0.8, "cable_only"),
    (r"(?<!\bin\s)(?<!\bwith\s)(?<!\bopen\s)(?<!\bsealed\s)(?<!\bnew\sin\s)(?<!\bnew\swith\s)\bbox\b", 0.4, "box_standalone"),
    (r"\bvariant\b", 0.2, "variant"),
    (r"\bcustom\b", 0.3, "custom"),
    (r"\bprototype\b", 0.5, "prototype"),
    (r"\bsealed\b", 0.15, "sealed"),
    (r"\blimited\s*edition\b", 0.3, "limited_edition"),
    (r"\bcollector'?s?\b", 0.3, "collector"),
    (r"\bspecial\s*edition\b", 0.3, "special_edition"),
    (r"\bdisplay\s*only\b", 0.9, "display_only"),
    (r"\bshell\s*only\b", 0.9, "shell_only"),
    (r"\bmotherboard\s*only\b", 0.8, "motherboard_only"),
    (r"\bscreen\s*only\b", 0.8, "screen_only"),
    (r"\bjunk\b", 0.8, "junk"),
    (r"\bsalvage\b", 0.7, "salvage"),
]

# Compilar una vez
_COMPILED = [(re.compile(p, re.IGNORECASE), w, f) for p, w, f in DANGER_PATTERNS]


@dataclass
class TitleRiskResult:
    risk_score: float                    # 0-1 (1 = muy peligroso)
    flagged_listings: int                # cuántos listings tienen flags
    flagged_pct: float                   # % de listings con flags
    semantic_flags: dict[str, int]       # flag → count de listings
    manual_review_required: bool         # True si risk_score > 0.3
    top_flags: list[str] = field(default_factory=list)  # las 3 flags más frecuentes


# Flags que describen variantes legítimas del producto y no riesgos reales.
# Se suprimen cuando el keyword de búsqueda ya contiene esas palabras.
_SUPPRESSIBLE_FLAGS = {
    "special_edition", "limited_edition", "collector",
    "bundle", "box_standalone",
}

# Mapeo de flag → keywords que, si aparecen en el query, suprimen el flag
_SUPPRESS_KEYWORDS: dict[str, list[str]] = {
    "special_edition": ["special edition"],
    "limited_edition": ["limited edition"],
    "collector": ["collector"],
    "bundle": ["bundle"],
    "box_standalone": ["box", "in box", "with box", "sealed", "nib", "bnib"],
}


def _build_suppressed_flags(keyword: str | None) -> set[str]:
    """Determina qué flags suprimir basándose en el keyword de búsqueda."""
    if not keyword:
        return set()
    kw_lower = keyword.lower()
    suppressed = set()
    for flag, triggers in _SUPPRESS_KEYWORDS.items():
        if any(t in kw_lower for t in triggers):
            suppressed.add(flag)
    return suppressed


def scan_title(title: str, suppressed: set[str] | None = None) -> list[tuple[str, float]]:
    """Escanea un título y retorna lista de (flag, weight) encontrados."""
    hits = []
    suppressed = suppressed or set()
    for pattern, weight, flag in _COMPILED:
        if flag in suppressed:
            continue
        if pattern.search(title):
            hits.append((flag, weight))
    return hits


def compute_title_risk(
    cleaned: CleanedComps,
    keyword: str | None = None,
) -> TitleRiskResult:
    """Analiza títulos de comps limpios para detectar riesgos semánticos.

    Args:
        cleaned: Comps limpios.
        keyword: Keyword de búsqueda original. Se usa para suprimir flags
                 que describen el producto legítimo (ej: "edition" en un
                 producto que ES una edición especial).
    """
    if cleaned.clean_total == 0 or not cleaned.listings:
        return TitleRiskResult(
            risk_score=0.0,
            flagged_listings=0,
            flagged_pct=0.0,
            semantic_flags={},
            manual_review_required=False,
        )

    suppressed = _build_suppressed_flags(keyword)

    flag_counts: dict[str, int] = {}
    flagged_listings = 0
    max_weight_per_listing: list[float] = []

    for listing in cleaned.listings:
        title = listing.title or ""
        hits = scan_title(title, suppressed)
        if hits:
            flagged_listings += 1
            max_w = 0.0
            for flag, weight in hits:
                flag_counts[flag] = flag_counts.get(flag, 0) + 1
                max_w = max(max_w, weight)
            max_weight_per_listing.append(max_w)

    n = cleaned.clean_total
    flagged_pct = flagged_listings / n if n > 0 else 0.0

    # Risk score: combinación de % flagged y severidad promedio
    if max_weight_per_listing:
        avg_severity = sum(max_weight_per_listing) / len(max_weight_per_listing)
        risk_score = flagged_pct * 0.6 + avg_severity * flagged_pct * 0.4
    else:
        risk_score = 0.0

    risk_score = min(1.0, risk_score)

    # Top flags ordenadas por frecuencia
    sorted_flags = sorted(flag_counts.items(), key=lambda x: x[1], reverse=True)
    top_flags = [f for f, _ in sorted_flags[:3]]

    return TitleRiskResult(
        risk_score=round(risk_score, 4),
        flagged_listings=flagged_listings,
        flagged_pct=round(flagged_pct, 4),
        semantic_flags=flag_counts,
        manual_review_required=risk_score > 0.3 or flagged_pct > 0.2,
        top_flags=top_flags,
    )
