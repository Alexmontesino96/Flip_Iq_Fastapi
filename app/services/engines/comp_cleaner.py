"""Motor A — Comp Cleaner.

Limpia comps brutos antes de cualquier cálculo:
1. Normaliza precios (price + shipping)
2. Filtra outliers con IQR Tukey
3. Filtra por condición (new/used)
4. Filtra por relevancia (si detailedSearch=true)
5. Recalcula estadísticas sobre comps limpios
"""

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

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


def _compute_relevance(listing: MarketplaceListing, keyword: str) -> float:
    """Calcula relevancia de un listing respecto al keyword buscado.

    relevance = 0.40*model_match + 0.25*brand_match + 0.20*condition_match + 0.15*specifics_match
    """
    keyword_lower = keyword.lower()
    title_lower = (listing.title or "").lower()

    # Model match: qué tan bien coincide el título con el keyword
    model_match = SequenceMatcher(None, keyword_lower, title_lower).ratio()

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


def clean_comps(
    raw: CompsResult,
    keyword: str | None = None,
    detailed: bool = False,
    condition: str = "any",
) -> CleanedComps:
    """Limpia comps brutos y retorna CleanedComps con estadísticas recalculadas."""
    no_match_rate = 0.0 if condition != "any" else 1.0

    if not raw.listings:
        return CleanedComps(
            raw_total=0,
            clean_total=0,
            days_of_data=raw.days_of_data,
            requested_condition=condition,
            condition_match_rate=no_match_rate,
        )

    raw_total = len(raw.listings)

    # 0. Filtrar por ventana temporal
    now_utc = datetime.now(timezone.utc)
    cutoff_aware = now_utc - timedelta(days=raw.days_of_data)
    cutoff_naive = cutoff_aware.replace(tzinfo=None)
    temporal_filtered = []
    for listing in raw.listings:
        if listing.ended_at is not None:
            # Comparar respetando si el datetime es aware o naive
            if listing.ended_at.tzinfo is not None:
                if listing.ended_at < cutoff_aware:
                    continue
            else:
                if listing.ended_at < cutoff_naive:
                    continue
        temporal_filtered.append(listing)

    if not temporal_filtered:
        return CleanedComps(
            raw_total=raw_total,
            days_of_data=raw.days_of_data,
            requested_condition=condition,
            condition_match_rate=no_match_rate,
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
            days_of_data=raw.days_of_data,
            requested_condition=condition,
            condition_match_rate=no_match_rate,
        )

    # 2. Filtrar outliers con IQR Tukey
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

    # 3. Filtrar por condición
    condition_filtered = 0
    # Contar condiciones normalizadas en todos los comps (pre-filtro)
    all_conditions = Counter(
        normalize_condition(l.condition) for l in after_outliers
    )

    if condition != "any":
        matched = [
            l for l in after_outliers
            if _matches_condition(normalize_condition(l.condition), condition)
        ]
        condition_filtered = len(after_outliers) - len(matched)
        # Solo aplicar filtro si quedan suficientes comps
        if len(matched) >= 3:
            after_outliers = matched
        else:
            # No hay suficientes comps con esa condición,
            # mantener todos pero registrar el filtro fallido
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
        # Solo aplicar filtro si quedan suficientes comps
        if len(relevant) >= 3:
            after_outliers = relevant
        else:
            relevance_filtered = 0  # No filtrar si quedarían muy pocos

    clean = after_outliers

    if not clean:
        return CleanedComps(
            raw_total=raw_total,
            outliers_removed=outliers_removed,
            relevance_filtered=relevance_filtered,
            condition_filtered=condition_filtered,
            days_of_data=raw.days_of_data,
            requested_condition=condition,
            condition_counts=dict(all_conditions),
            condition_match_rate=no_match_rate,
        )

    # 5. Recalcular estadísticas
    clean_prices = [l.total_price for l in clean]
    stats = _compute_stats(clean_prices)

    sales_per_day = len(clean) / max(raw.days_of_data, 1)

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
        days_of_data=raw.days_of_data,
        requested_condition=condition,
        condition_counts=dict(final_conditions),
        condition_match_rate=round(match_rate, 4),
    )
