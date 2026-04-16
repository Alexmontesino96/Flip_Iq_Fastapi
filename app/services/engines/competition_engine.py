"""Motor I — Competition Engine.

Calcula HHI (Herfindahl-Hirschman Index) de concentración de sellers.
HHI > 0.25 = mercado concentrado (pocos sellers dominan).
"""

from collections import Counter
from dataclasses import dataclass

from app.services.marketplace.base import CleanedComps


@dataclass
class CompetitionResult:
    hhi: float                     # 0-1 (>0.25 = concentrado)
    dominant_seller_share: float   # % del seller más grande
    unique_sellers: int
    category: str                  # healthy|moderate|concentrated


def compute_competition(cleaned: CleanedComps) -> CompetitionResult:
    """Calcula concentración de mercado usando HHI."""
    if cleaned.clean_total == 0:
        return CompetitionResult(
            hhi=0.0,
            dominant_seller_share=0.0,
            unique_sellers=0,
            category="no_data",
        )

    # Contar ventas por seller
    seller_counts: Counter[str] = Counter()
    for l in cleaned.listings:
        seller = l.seller_username or "unknown"
        seller_counts[seller] += 1

    total = sum(seller_counts.values())
    unique_sellers = len(seller_counts)

    if total == 0 or unique_sellers == 0:
        return CompetitionResult(
            hhi=0.0,
            dominant_seller_share=0.0,
            unique_sellers=0,
            category="no_data",
        )

    # Si todos los sellers son "unknown", no tenemos datos reales
    if unique_sellers == 1 and "unknown" in seller_counts:
        return CompetitionResult(
            hhi=0.0,
            dominant_seller_share=0.0,
            unique_sellers=0,
            category="no_data",
        )

    # HHI = sum(share_j^2)
    shares = [count / total for count in seller_counts.values()]
    hhi = sum(s ** 2 for s in shares)

    dominant_share = max(shares)

    if hhi > 0.25:
        category = "concentrated"
    elif hhi > 0.15:
        category = "moderate"
    else:
        category = "healthy"

    return CompetitionResult(
        hhi=round(hhi, 4),
        dominant_seller_share=round(dominant_share, 4),
        unique_sellers=unique_sellers,
        category=category,
    )
