"""Motor A — Comp Cleaner.

Limpia comps brutos antes de cualquier cálculo:
1. Normaliza precios (price + shipping)
2. Filtra outliers con IQR Tukey
3. Filtra por condición (new/used)
4. Filtra por relevancia (si detailedSearch=true)
5. Recalcula estadísticas sobre comps limpios
"""

import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from app.services.engines.title_risk import scan_title
from app.services.marketplace.base import (
    CleanedComps,
    CompsResult,
    MarketplaceListing,
)

# Mapeo de condiciones eBay a categorías normalizadas
_NEW_KEYWORDS = ("brand new", "new with", "new without", "new other")
_USED_KEYWORDS = ("pre-owned", "used", "very good", "good", "acceptable", "like new")
_REFURBISHED_KEYWORDS = ("refurbished", "certified -")
_OPEN_BOX_KEYWORDS = ("open box",)
_PARTS_KEYWORDS = ("for parts", "not working")


def normalize_condition(raw: str | None) -> str:
    """Normaliza condición eBay a: new, used, refurbished, open_box, for_parts, unknown."""
    if not raw:
        return "unknown"
    lower = raw.lower().strip()

    if lower == "new":
        return "new"
    if any(kw in lower for kw in _NEW_KEYWORDS):
        return "new"
    if any(kw in lower for kw in _PARTS_KEYWORDS):
        return "for_parts"
    if any(kw in lower for kw in _REFURBISHED_KEYWORDS):
        return "refurbished"
    if any(kw in lower for kw in _OPEN_BOX_KEYWORDS):
        return "open_box"
    if any(kw in lower for kw in _USED_KEYWORDS):
        return "used"
    return "unknown"


def _matches_condition(normalized: str, requested: str) -> bool:
    """Determina si una condición normalizada coincide con la solicitada."""
    if requested == "any":
        return True
    if requested == "new":
        return normalized == "new"
    if requested == "used":
        return normalized in ("used", "for_parts")
    if requested == "open_box":
        return normalized == "open_box"
    if requested == "refurbished":
        return normalized == "refurbished"
    return True


def _normalize_price(listing: MarketplaceListing) -> float:
    """Precio total = price + shipping, normalizado por lot_size si es bundle."""
    if listing.total_price is not None and listing.total_price > 0:
        base = listing.total_price
    else:
        shipping = listing.shipping_price or 0.0
        base = listing.price + shipping

    # Normalizar bundles: dividir precio entre lot_size
    if listing.is_bundle and listing.lot_size > 1:
        return round(base / listing.lot_size, 2)

    return base


# Words that indicate a listing is an accessory, not the main product.
# Used by _matches_product_type to reject titles like "Screen Protector for Switch OLED"
# even when they contain all keyword words.
_ACCESSORY_WORDS = frozenset({
    "case", "cover", "skin", "sleeve", "pouch", "bag",
    "protector", "film", "guard", "tempered", "shield",
    "shell", "bumper", "faceplate",
    "charger", "cable", "cord", "adapter", "wire",
    "stand", "mount", "holder", "cradle",
    "strap", "decal", "sticker", "wrap",
    "stylus", "pen", "cloth", "cleaning",
    "carrying", "travel",
    "replacement", "repair", "spare",
})

_MODEL_CONTEXT_PREFIXES = frozenset({
    "size", "sz", "us", "uk", "eu", "pack", "lot", "set", "x", "qty", "quantity", "count",
    "mens", "womens", "men", "women", "gs", "youth", "grade", "jr", "junior",
    "kids", "toddler", "boys", "girls", "infant",
})

_MODEL_NUMBER_RE = re.compile(r"(?<!\$)\b([a-z][a-z0-9]*[-]?[a-z]*)\s*(\d+(?:\.\d+)?)\b")


def _extract_model_numbers(text: str) -> dict[str, str]:
    """Extrae pares {base_word: number} de un texto.

    Ej: "Nike Vomero 6 Size 10" → {"vomero": "6"}
    Ignora prefijos de contexto (size, pack, lot, etc.) y precios ($).
    """
    result: dict[str, str] = {}
    for match in _MODEL_NUMBER_RE.finditer(text.lower()):
        base_word = match.group(1)
        number = match.group(2)
        if base_word not in _MODEL_CONTEXT_PREFIXES:
            result[base_word] = number
    return result


def _compute_relevance(listing: MarketplaceListing, keyword: str) -> float:
    """Calcula relevancia de un listing respecto al keyword buscado.

    relevance = 0.40*model_match + 0.25*brand_match + 0.20*condition_match + 0.15*specifics_match
    """
    keyword_lower = keyword.lower()
    title_lower = (listing.title or "").lower()

    # Model match: fracción de palabras del keyword presentes en el título.
    # Más robusto que SequenceMatcher que penaliza títulos largos vs keywords cortos.
    kw_words = set(keyword_lower.split())
    title_words = set(title_lower.split())
    model_match = len(kw_words & title_words) / len(kw_words) if kw_words else 0.0

    # Penalizar si el modelo numérico es diferente (e.g. Vomero 6 vs Vomero 5)
    kw_models = _extract_model_numbers(keyword_lower)
    title_models = _extract_model_numbers(title_lower)
    if kw_models:
        for base_word, kw_number in kw_models.items():
            if base_word in title_models and title_models[base_word] != kw_number:
                model_match *= 0.3  # Penalización 70% por modelo incorrecto
                break

    # Brand match: si el listing tiene brand y está en el keyword
    brand_match = 0.0
    if listing.brand:
        brand_lower = listing.brand.lower()
        if brand_lower in keyword_lower or brand_lower in title_lower:
            brand_match = 1.0
        else:
            brand_match = SequenceMatcher(None, brand_lower, keyword_lower).ratio()

    # Condition match: si tiene condición definida
    condition_match = 1.0 if listing.condition else 0.5

    # Specifics match: si tiene item_specifics con datos
    specifics_match = 0.0
    if listing.item_specifics:
        specifics_match = min(1.0, len(listing.item_specifics) / 5.0)

    return (
        0.40 * model_match
        + 0.25 * brand_match
        + 0.20 * condition_match
        + 0.15 * specifics_match
    )


def _compute_stats(prices: list[float]) -> dict:
    """Calcula estadísticas sobre una lista de precios."""
    if not prices:
        return {
            "median": 0.0, "avg": 0.0, "p25": 0.0, "p75": 0.0,
            "iqr": 0.0, "std_dev": 0.0, "cv": 0.0,
            "min": 0.0, "max": 0.0,
        }

    prices_sorted = sorted(prices)
    n = len(prices_sorted)

    avg = sum(prices_sorted) / n
    median = (
        prices_sorted[n // 2]
        if n % 2 == 1
        else (prices_sorted[n // 2 - 1] + prices_sorted[n // 2]) / 2
    )

    p25_idx = max(0, int(n * 0.25) - 1) if n >= 4 else 0
    p75_idx = min(n - 1, int(n * 0.75)) if n >= 4 else n - 1
    p25 = prices_sorted[p25_idx]
    p75 = prices_sorted[p75_idx]
    iqr = p75 - p25

    variance = sum((p - avg) ** 2 for p in prices_sorted) / n
    std_dev = math.sqrt(variance)
    cv = std_dev / avg if avg > 0 else 0.0

    return {
        "median": round(median, 2),
        "avg": round(avg, 2),
        "p25": round(p25, 2),
        "p75": round(p75, 2),
        "iqr": round(iqr, 2),
        "std_dev": round(std_dev, 2),
        "cv": round(cv, 4),
        "min": round(prices_sorted[0], 2),
        "max": round(prices_sorted[-1], 2),
    }


_DANGER_WEIGHT_THRESHOLD = 0.7  # Solo filtrar flags con peso >= 0.7


def _matches_product_type(title: str, product_type: str, keyword: str | None = None) -> bool:
    """Verifica si un título es relevante para el product_type.

    Acepta si:
    1. El título contiene el product_type (singular/plural), O
    2. El título contiene todas las palabras significativas del keyword (len >= 3).
       Esto resuelve el caso donde listings válidos no usan la palabra del product_type
       (ej: "Nintendo Switch OLED 64GB" no dice "console" pero es claramente el producto).
    """
    pt = product_type.lower().strip()
    title_lower = title.lower()
    # Chequear singular y plural simple
    variants = {pt}
    if pt.endswith("s"):
        variants.add(pt[:-1])  # "helmets" → "helmet"
    else:
        variants.add(pt + "s")  # "helmet" → "helmets"
    # También manejar "ies" / "y" (e.g. "battery"/"batteries")
    if pt.endswith("ies"):
        variants.add(pt[:-3] + "y")
    elif pt.endswith("y") and not pt.endswith("ey"):
        variants.add(pt[:-1] + "ies")
    if any(re.search(r"\b" + re.escape(v) + r"\b", title_lower) for v in variants):
        return True

    # Fallback: si el título contiene TODAS las palabras significativas del keyword
    # Y NO contiene palabras que indiquen que es un accesorio.
    # Ej: "Nintendo Switch OLED 64GB" → True (no dice "console" pero es el producto)
    # Ej: "Nintendo Switch OLED Screen Protector" → False ("protector" = accesorio)
    if keyword:
        kw_words = [w for w in keyword.lower().split() if len(w) >= 3]
        if kw_words and all(w in title_lower for w in kw_words):
            kw_word_set = set(keyword.lower().split())
            title_words = set(re.findall(r"\b\w+\b", title_lower))
            extra_words = title_words - kw_word_set
            if not (extra_words & _ACCESSORY_WORDS):
                return True

    return False


def _filter_by_danger(
    listings: list[MarketplaceListing],
    keyword: str | None = None,
) -> tuple[list[MarketplaceListing], int]:
    """Filtra listings con danger patterns de peso >= threshold.

    No filtra si el keyword contiene la misma palabra peligrosa.
    """
    keyword_lower = (keyword or "").lower()
    kept = []
    removed = 0
    for listing in listings:
        title = listing.title or ""
        hits = scan_title(title)
        # Solo considerar flags con weight >= threshold
        high_danger = [(flag, w) for flag, w in hits if w >= _DANGER_WEIGHT_THRESHOLD]
        if high_danger:
            # No filtrar si el keyword contiene la palabra base del flag
            # e.g. si buscamos "replacement visor", no filtrar "replacement"
            should_keep = False
            for flag, _ in high_danger:
                # Convertir flag_name a palabras: "box_only" → "box only"
                flag_words = flag.replace("_", " ")
                if flag_words in keyword_lower:
                    should_keep = True
                    break
            if should_keep:
                kept.append(listing)
            else:
                removed += 1
        else:
            kept.append(listing)
    return kept, removed


def clean_comps(
    raw: CompsResult,
    keyword: str | None = None,
    detailed: bool = False,
    condition: str = "any",
    product_type: str | None = None,
) -> CleanedComps:
    """Limpia comps brutos y retorna CleanedComps con estadísticas recalculadas."""
    no_match_rate = 0.0 if condition != "any" else 1.0
    data_quality_warnings = list(raw.warnings)

    def _filter_counts(
        raw_count: int,
        temporal_count: int = 0,
        priced_count: int = 0,
        product_type_removed: int = 0,
        outlier_removed: int = 0,
        danger_removed: int = 0,
        condition_removed: int = 0,
        relevance_removed: int = 0,
        clean_count: int = 0,
    ) -> dict[str, int]:
        return {
            "raw": raw_count,
            "temporal": temporal_count,
            "priced": priced_count,
            "product_type_filtered": product_type_removed,
            "outliers_removed": outlier_removed,
            "danger_filtered": danger_removed,
            "condition_filtered": condition_removed,
            "relevance_filtered": relevance_removed,
            "clean": clean_count,
        }

    if not raw.listings:
        return CleanedComps(
            raw_total=0,
            clean_total=0,
            days_of_data=raw.days_of_data,
            requested_condition=condition,
            condition_match_rate=no_match_rate,
            pricing_basis="no_data",
            data_quality_warnings=data_quality_warnings,
            filter_counts=_filter_counts(0),
        )

    raw_total = len(raw.listings)

    # 0. Filtrar por ventana temporal (adaptativa: 30 → 90 días si < 5 comps)
    _MIN_COMPS_ADAPTIVE = 5
    _EXPANDED_DAYS = 90

    now_utc = datetime.now(timezone.utc)
    initial_days = raw.days_of_data
    effective_days = initial_days

    def _temporal_filter(days: float) -> list:
        cutoff_aware = now_utc - timedelta(days=days)
        cutoff_naive = cutoff_aware.replace(tzinfo=None)
        filtered = []
        for listing in raw.listings:
            if listing.ended_at is not None:
                if listing.ended_at.tzinfo is not None:
                    if listing.ended_at < cutoff_aware:
                        continue
                else:
                    if listing.ended_at < cutoff_naive:
                        continue
            filtered.append(listing)
        return filtered

    temporal_filtered = _temporal_filter(initial_days)

    # Ventana adaptativa: si hay pocos comps, ampliar a 90 días
    if len(temporal_filtered) < _MIN_COMPS_ADAPTIVE and initial_days < _EXPANDED_DAYS:
        expanded = _temporal_filter(_EXPANDED_DAYS)
        if len(expanded) > len(temporal_filtered):
            temporal_filtered = expanded
            effective_days = _EXPANDED_DAYS
            data_quality_warnings.append(
                f"Temporal window expanded from {int(initial_days)} to {_EXPANDED_DAYS} days "
                f"due to low comp count ({len(_temporal_filter(initial_days))} < {_MIN_COMPS_ADAPTIVE})."
            )

    if not temporal_filtered:
        return CleanedComps(
            raw_total=raw_total,
            days_of_data=effective_days,
            requested_condition=condition,
            condition_match_rate=no_match_rate,
            pricing_basis="no_data",
            data_quality_warnings=data_quality_warnings,
            filter_counts=_filter_counts(raw_total, temporal_count=0),
        )

    # 1. Normalizar precios
    priced = []
    for listing in temporal_filtered:
        price = _normalize_price(listing)
        if price > 0:
            listing.total_price = price
            priced.append(listing)

    if not priced:
        return CleanedComps(
            raw_total=raw_total,
            days_of_data=effective_days,
            requested_condition=condition,
            condition_match_rate=no_match_rate,
            pricing_basis="no_data",
            data_quality_warnings=data_quality_warnings,
            filter_counts=_filter_counts(
                raw_total,
                temporal_count=len(temporal_filtered),
                priced_count=0,
            ),
        )

    # 2. Filtrar por product_type ANTES de IQR (si se proporcionó).
    # Esto evita que IQR calcule bounds sobre productos mixtos (ej: consolas + accesorios)
    # donde los productos reales serían removidos como "outliers".
    product_type_filtered = 0
    if product_type and priced:
        matched_pt = [
            l for l in priced
            if _matches_product_type(l.title or "", product_type, keyword)
        ]
        min_keep = max(3, int(len(priced) * 0.2))
        if len(matched_pt) >= min_keep:
            product_type_filtered = len(priced) - len(matched_pt)
            priced = matched_pt

    # 3. Filtrar outliers con IQR Tukey (ahora sobre items del mismo tipo)
    prices = sorted(l.total_price for l in priced)
    n = len(prices)
    p25_idx = max(0, int(n * 0.25) - 1) if n >= 4 else 0
    p75_idx = min(n - 1, int(n * 0.75)) if n >= 4 else n - 1
    p25 = prices[p25_idx]
    p75 = prices[p75_idx]
    iqr = p75 - p25

    lower_bound = max(0, p25 - 1.5 * iqr)
    upper_bound = p75 + 1.5 * iqr

    after_outliers = [
        l for l in priced if lower_bound <= l.total_price <= upper_bound
    ]
    outliers_removed = len(priced) - len(after_outliers)

    # 2.6. Safety net: filtrar listings con danger patterns de alto peso
    danger_filtered = 0
    if after_outliers:
        after_danger, danger_filtered = _filter_by_danger(after_outliers, keyword)
        # Solo aplicar si quedan >= 3 comps
        if len(after_danger) >= 3:
            after_outliers = after_danger
        else:
            danger_filtered = 0

    # 3. Filtrar por condición
    condition_filtered = 0
    # Contar condiciones normalizadas en todos los comps (pre-filtro)
    all_conditions = Counter(
        normalize_condition(l.condition) for l in after_outliers
    )

    condition_subset_count = 0
    condition_subset_median: float | None = None
    pricing_basis = "all_conditions"

    if condition != "any":
        matched = [
            l for l in after_outliers
            if _matches_condition(normalize_condition(l.condition), condition)
        ]
        condition_filtered = len(after_outliers) - len(matched)
        # Solo aplicar filtro si quedan suficientes comps
        if len(matched) >= 3:
            after_outliers = matched
            pricing_basis = "requested_condition"
        else:
            # No hay suficientes comps con esa condición,
            # mantener todos pero calcular stats del subset para informar al usuario
            condition_subset_count = len(matched)
            pricing_basis = "mixed_conditions"
            if matched:
                subset_prices = sorted(l.total_price for l in matched)
                n_sub = len(subset_prices)
                condition_subset_median = (
                    subset_prices[n_sub // 2]
                    if n_sub % 2 == 1
                    else (subset_prices[n_sub // 2 - 1] + subset_prices[n_sub // 2]) / 2
                )
                condition_subset_median = round(condition_subset_median, 2)
            data_quality_warnings.append(
                f"Only {len(matched)} comps matched requested condition '{condition}'; "
                "pricing is based on mixed-condition comps."
            )
            condition_filtered = 0
    else:
        # Auto-filtro: si la mayoría son "new", excluir "used"/"for_parts"
        # para evitar que listings usados contaminen el pricing de nuevos
        new_count = all_conditions.get("new", 0)
        used_count = all_conditions.get("used", 0) + all_conditions.get("for_parts", 0)
        total = len(after_outliers)
        if total >= 5 and new_count >= total * 0.5 and used_count > 0:
            filtered = [
                l for l in after_outliers
                if normalize_condition(l.condition) not in ("used", "for_parts")
            ]
            if len(filtered) >= 5:
                condition_filtered = len(after_outliers) - len(filtered)
                after_outliers = filtered
                pricing_basis = "auto_condition_filtered"

    # 4. Filtrar por relevancia si hay datos enriquecidos (LLM o detailedSearch)
    relevance_filtered = 0
    has_enriched = any(l.brand or l.model for l in after_outliers)
    if has_enriched and keyword:
        relevant = []
        for l in after_outliers:
            score = _compute_relevance(l, keyword)
            if score >= 0.75:
                relevant.append(l)
        relevance_filtered = len(after_outliers) - len(relevant)
        # Preferir pocos comps relevantes sobre muchos comps contaminados.
        if len(relevant) >= 2:
            after_outliers = relevant
            if len(relevant) < 5:
                data_quality_warnings.append(
                    f"Only {len(relevant)} enriched relevant comps remained; "
                    "pricing confidence is limited."
                )
        else:
            relevance_filtered = 0  # No filtrar si quedarían muy pocos

    clean = after_outliers

    if not clean:
        return CleanedComps(
            raw_total=raw_total,
            outliers_removed=outliers_removed,
            relevance_filtered=relevance_filtered,
            condition_filtered=condition_filtered,
            days_of_data=effective_days,
            requested_condition=condition,
            condition_counts=dict(all_conditions),
            condition_match_rate=no_match_rate,
            danger_filtered=danger_filtered,
            product_type_filtered=product_type_filtered,
            pricing_basis="no_data",
            data_quality_warnings=data_quality_warnings,
            filter_counts=_filter_counts(
                raw_total,
                temporal_count=len(temporal_filtered),
                priced_count=len(priced),
                product_type_removed=product_type_filtered,
                outlier_removed=outliers_removed,
                danger_removed=danger_filtered,
                condition_removed=condition_filtered,
                relevance_removed=relevance_filtered,
                clean_count=0,
            ),
        )

    # 5. Recalcular estadísticas
    clean_prices = [l.total_price for l in clean]
    stats = _compute_stats(clean_prices)

    sales_per_day = len(clean) / max(effective_days, 1)

    # Condition match rate: % de comps finales que coinciden con condition solicitada
    if condition != "any":
        matched_in_clean = sum(
            1 for l in clean
            if _matches_condition(normalize_condition(l.condition), condition)
        )
        match_rate = matched_in_clean / len(clean) if clean else 0.0
    else:
        match_rate = 1.0

    # Condiciones normalizadas en comps finales
    final_conditions = Counter(normalize_condition(l.condition) for l in clean)

    return CleanedComps(
        listings=clean,
        raw_total=raw_total,
        clean_total=len(clean),
        outliers_removed=outliers_removed,
        relevance_filtered=relevance_filtered,
        condition_filtered=condition_filtered,
        median_price=stats["median"],
        avg_price=stats["avg"],
        p25=stats["p25"],
        p75=stats["p75"],
        iqr=stats["iqr"],
        std_dev=stats["std_dev"],
        cv=stats["cv"],
        min_price=stats["min"],
        max_price=stats["max"],
        sales_per_day=round(sales_per_day, 4),
        days_of_data=effective_days,
        requested_condition=condition,
        condition_counts=dict(final_conditions),
        condition_match_rate=round(match_rate, 4),
        danger_filtered=danger_filtered,
        product_type_filtered=product_type_filtered,
        condition_subset_count=condition_subset_count,
        condition_subset_median=condition_subset_median,
        pricing_basis=pricing_basis,
        data_quality_warnings=data_quality_warnings,
        filter_counts=_filter_counts(
            raw_total,
            temporal_count=len(temporal_filtered),
            priced_count=len(priced),
            product_type_removed=product_type_filtered,
            outlier_removed=outliers_removed,
            danger_removed=danger_filtered,
            condition_removed=condition_filtered,
            relevance_removed=relevance_filtered,
            clean_count=len(clean),
        ),
    )
