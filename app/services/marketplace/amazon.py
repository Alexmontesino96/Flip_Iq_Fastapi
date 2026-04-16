"""Cliente Amazon via Keepa API.

Keepa provee datos reales de Amazon: precios Buy Box, historial,
sales rank, y ofertas de sellers. Se mapean al formato CompsResult
para integrarse con el pipeline de análisis existente.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.services.marketplace.base import CompsResult, MarketplaceClient, MarketplaceListing

logger = logging.getLogger(__name__)

KEEPA_BASE = "https://api.keepa.com"
KEEPA_EPOCH = datetime(2011, 1, 1, tzinfo=timezone.utc)
KEEPA_TIMEOUT = 20  # segundos

# Indices del array csv de Keepa
CSV_AMAZON = 0
CSV_NEW = 1
CSV_USED = 2
CSV_SALES_RANK = 3
CSV_BUY_BOX = 18


def keepa_time_to_datetime(keepa_minutes: int) -> datetime:
    """Convierte Keepa time (minutos desde 2011-01-01 UTC) a datetime."""
    return KEEPA_EPOCH + timedelta(minutes=keepa_minutes)


def estimate_sales_per_day(sales_rank: int | None) -> float:
    """Estima ventas diarias a partir del Best Sellers Rank (BSR).

    Heurística simplificada basada en rangos típicos de Amazon US.
    """
    if sales_rank is None or sales_rank <= 0:
        return 0.0
    if sales_rank < 5_000:
        return 10.0
    if sales_rank < 50_000:
        return 3.5
    if sales_rank < 200_000:
        return 0.75
    return 0.15


MAX_REASONABLE_PRICE = 5000.0  # Cap de sanidad — filtrar precios > $5,000


def _map_keepa_offers(product: dict) -> list[MarketplaceListing]:
    """Convierte ofertas de sellers de Keepa a MarketplaceListing.

    offerCSV usa TRIPLES: [keepa_time, price_cents, shipping_cents, ...]
    """
    listings: list[MarketplaceListing] = []
    offers = product.get("offers") or []
    title = product.get("title", "")
    asin = product.get("asin", "")

    for offer in offers:
        csv = offer.get("offerCSV", [])
        if not csv or len(csv) < 3:
            continue

        # offerCSV es TRIPLES: [keepa_time, price, shipping, keepa_time, price, shipping, ...]
        # Tomamos el último triple con precio válido (iteramos hacia atrás)
        price_val = None
        ship_val = 0.0
        for i in range(len(csv) - 3, -1, -3):
            p = csv[i + 1]  # precio en centavos
            if p is not None and p > 0:
                price_val = p / 100.0
                s = csv[i + 2]  # shipping en centavos
                ship_val = s / 100.0 if s and s > 0 else 0.0
                break

        if price_val is None or price_val <= 0 or price_val > MAX_REASONABLE_PRICE:
            continue

        condition_raw = offer.get("condition", 1)
        # Keepa: 1=New, 2=Used-Like New, 3=Used-Very Good, 4=Used-Good, 5=Used-Acceptable
        condition = "new" if condition_raw == 1 else "used"

        seller = offer.get("sellerName") or offer.get("sellerId", "")
        is_fba = offer.get("isFBA", False)

        # Para FBA el shipping ya está incluido
        shipping = 0.0 if is_fba else ship_val

        listings.append(MarketplaceListing(
            title=title,
            price=price_val,
            condition=condition,
            url=f"https://www.amazon.com/dp/{asin}",
            sold=True,
            marketplace="amazon",
            item_id=asin,
            shipping_price=shipping,
            total_price=round(price_val + shipping, 2),
            ended_at=datetime.now(timezone.utc),
            seller_username=seller,
        ))

    return listings


def _map_buybox_history(product: dict, days: int = 30) -> list[MarketplaceListing]:
    """Convierte historial Buy Box de Keepa a MarketplaceListing.

    csv[18] (BUY_BOX_SHIPPING) usa TRIPLES: [keepa_time, price_cents, shipping_cents, ...]
    """
    listings: list[MarketplaceListing] = []
    csv_data = product.get("csv") or []
    title = product.get("title", "")
    asin = product.get("asin", "")

    if len(csv_data) <= CSV_BUY_BOX:
        return listings

    buybox_history = csv_data[CSV_BUY_BOX]
    if not buybox_history:
        return listings

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # buybox_history (csv[18]) es TRIPLES: [keepa_time, price, shipping, ...]
    for i in range(0, len(buybox_history) - 2, 3):
        keepa_time = buybox_history[i]
        price_cents = buybox_history[i + 1]
        shipping_cents = buybox_history[i + 2]

        if keepa_time is None or price_cents is None or price_cents <= 0:
            continue

        dt = keepa_time_to_datetime(keepa_time)
        if dt < cutoff:
            continue

        price = price_cents / 100.0
        if price > MAX_REASONABLE_PRICE:
            continue

        shipping = shipping_cents / 100.0 if shipping_cents and shipping_cents > 0 else 0.0

        listings.append(MarketplaceListing(
            title=title,
            price=price,
            condition="new",
            url=f"https://www.amazon.com/dp/{asin}",
            sold=True,
            marketplace="amazon",
            item_id=asin,
            shipping_price=shipping,
            total_price=round(price + shipping, 2),
            ended_at=dt,
            seller_username="Amazon Buy Box",
        ))

    return listings


class AmazonClient(MarketplaceClient):
    """Cliente Amazon usando Keepa REST API."""

    def __init__(self) -> None:
        self._api_key = settings.keepa_api_key

    async def _keepa_get(self, endpoint: str, params: dict) -> dict | None:
        """GET genérico a Keepa API."""
        if not self._api_key:
            return None

        params["key"] = self._api_key
        try:
            async with httpx.AsyncClient(timeout=KEEPA_TIMEOUT) as client:
                resp = await client.get(
                    f"{KEEPA_BASE}/{endpoint}",
                    params=params,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning("Keepa API error %s: %s", e.response.status_code, e)
            return None
        except Exception as e:
            logger.warning("Keepa request failed: %s", e)
            return None

    async def _keepa_search(self, keyword: str, limit: int = 10) -> list[str]:
        """Busca ASINs por keyword.

        El endpoint /search devuelve products[] con datos parciales (sin offers).
        Extraemos los ASINs para luego pedir datos completos via /product.
        """
        data = await self._keepa_get("search", {
            "domain": 1,
            "type": "product",
            "term": keyword,
        })
        if not data:
            return []

        # La respuesta tiene products[] con asin, no asinList
        products = data.get("products", [])
        asins = [p["asin"] for p in products if p.get("asin")]
        return asins[:limit]

    async def _keepa_product(
        self,
        asins: list[str],
        stats: int = 30,
        offers: int = 20,
    ) -> list[dict]:
        """Obtiene datos de productos por ASIN(s)."""
        if not asins:
            return []

        data = await self._keepa_get("product", {
            "domain": 1,
            "asin": ",".join(asins),
            "stats": stats,
            "buybox": 1,
            "history": 1,
            "days": 90,
            "offers": offers,
        })
        if not data:
            return []

        return data.get("products", [])

    async def _keepa_product_by_code(self, code: str) -> list[dict]:
        """Obtiene datos de producto por UPC/EAN."""
        data = await self._keepa_get("product", {
            "domain": 1,
            "code": code,
            "stats": 30,
            "buybox": 1,
            "history": 1,
            "days": 90,
            "offers": 20,
        })
        if not data:
            return []

        return data.get("products", [])

    async def search_by_barcode(self, barcode: str) -> list[MarketplaceListing]:
        products = await self._keepa_product_by_code(barcode)
        listings: list[MarketplaceListing] = []
        for product in products:
            listings.extend(_map_keepa_offers(product))
        return listings

    async def search_by_keyword(self, keyword: str, limit: int = 20) -> list[MarketplaceListing]:
        asins = await self._keepa_search(keyword, limit=limit)
        if not asins:
            return []

        products = await self._keepa_product(asins)
        listings: list[MarketplaceListing] = []
        for product in products:
            listings.extend(_map_keepa_offers(product))
        return listings[:limit]

    async def get_sold_comps(
        self,
        barcode: str | None = None,
        keyword: str | None = None,
        days: int = 30,
        limit: int = 50,
        product_type: str | None = None,
    ) -> CompsResult:
        """Obtiene comps de Amazon via Keepa.

        Combina ofertas actuales de sellers + historial Buy Box
        para crear un dataset comparable al de eBay sold.
        """
        if not self._api_key:
            logger.info("No KEEPA_API_KEY, retornando CompsResult vacío")
            return CompsResult(marketplace="amazon")

        products: list[dict] = []

        # Intentar por barcode primero
        if barcode:
            products = await self._keepa_product_by_code(barcode)

        # Si no hay resultados por barcode, buscar por keyword
        if not products and keyword:
            asins = await self._keepa_search(keyword, limit=10)
            if asins:
                products = await self._keepa_product(asins)

        if not products:
            logger.info("Keepa: sin resultados para barcode=%s keyword=%s", barcode, keyword)
            return CompsResult(marketplace="amazon")

        # Filtro por product_type: excluir productos cuyo título no contiene el tipo
        if product_type:
            pt_lower = product_type.lower().strip()
            # Generar variantes singular/plural
            pt_variants = {pt_lower}
            if pt_lower.endswith("s"):
                pt_variants.add(pt_lower[:-1])
            else:
                pt_variants.add(pt_lower + "s")
            if pt_lower.endswith("ies"):
                pt_variants.add(pt_lower[:-3] + "y")
            elif pt_lower.endswith("y") and not pt_lower.endswith("ey"):
                pt_variants.add(pt_lower[:-1] + "ies")

            filtered_products = [
                p for p in products
                if any(
                    re.search(r"\b" + re.escape(v) + r"\b", (p.get("title") or "").lower())
                    for v in pt_variants
                )
            ]
            if filtered_products:
                removed = len(products) - len(filtered_products)
                if removed > 0:
                    logger.info(
                        "Keepa: filtrado %d/%d productos por product_type='%s'",
                        removed, len(products), product_type,
                    )
                products = filtered_products
            # Si filtrar deja 0 productos, no filtrar (datos incompletos > sin datos)

        # Mapear productos a listings
        all_listings: list[MarketplaceListing] = []
        best_rank: int | None = None

        for product in products:
            # Ofertas actuales de sellers
            all_listings.extend(_map_keepa_offers(product))
            # Historial Buy Box
            all_listings.extend(_map_buybox_history(product, days=days))

            # Mejor sales rank para estimar velocidad
            rank = None
            stats = product.get("stats")
            if stats:
                rank = stats.get("salesRankReference") or stats.get("current", [None] * 4)[CSV_SALES_RANK]
            if rank and (best_rank is None or rank < best_rank):
                best_rank = rank

        if not all_listings:
            logger.info("Keepa: productos encontrados pero sin listings mapeables")
            return CompsResult(marketplace="amazon")

        # Limitar a 'limit' listings
        all_listings = all_listings[:limit]

        result = CompsResult.from_listings(all_listings, marketplace="amazon", days=days)

        # Sobreescribir sales_per_day con estimación de BSR (más precisa que contar listings)
        if best_rank:
            result.sales_per_day = estimate_sales_per_day(best_rank)

        logger.info(
            "Keepa: %d listings para '%s' (rank=%s, spd=%.1f)",
            len(all_listings),
            keyword or barcode,
            best_rank,
            result.sales_per_day,
        )

        return result
