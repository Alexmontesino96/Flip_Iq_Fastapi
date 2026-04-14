"""Cliente eBay: scraper directo (default) con fallback a Apify.

Usa scraper propio (httpx + BeautifulSoup) para obtener ventas completadas de eBay.
Si el scraper falla (429, CAPTCHA, etc.), cae a Apify como fallback si hay token.
Configurable via EBAY_DATA_SOURCE env var: "scraper" (default) | "apify".
"""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.marketplace.base import CompsResult, MarketplaceClient, MarketplaceListing
from app.services.marketplace.ebay_scraper import scrape_sold_listings

logger = logging.getLogger(__name__)

APIFY_ACTOR = "caffein.dev~ebay-sold-listings"
APIFY_RUN_URL = (
    f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
)
APIFY_TIMEOUT = 25  # segundos (Apify típicamente tarda 5-10s)


def _parse_float(value) -> float:
    """Convierte string o número a float de forma segura."""
    if value is None:
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _parse_datetime(value: str | None) -> datetime | None:
    """Parsea ISO 8601 datetime string."""
    if not value:
        return None
    try:
        # "2026-04-12T00:00:00.000Z"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt
    except (ValueError, TypeError):
        return None


def _map_listing(item: dict) -> MarketplaceListing | None:
    """Convierte un item del dataset de Apify a MarketplaceListing."""
    title = item.get("title", "")
    if not title:
        return None

    sold_price = _parse_float(item.get("soldPrice"))
    if sold_price <= 0:
        return None

    shipping = _parse_float(item.get("shippingPrice"))
    total = _parse_float(item.get("totalPrice"))
    if total <= 0:
        total = sold_price + shipping

    return MarketplaceListing(
        title=title,
        price=sold_price,
        url=item.get("url"),
        sold=True,
        marketplace="ebay",
        item_id=item.get("itemId"),
        shipping_price=shipping,
        total_price=total,
        ended_at=_parse_datetime(item.get("endedAt")),
        seller_username=item.get("sellerUsername"),
        seller_feedback_pct=item.get("sellerFeedbackPercent"),
        condition=item.get("condition"),
        bids=item.get("bids"),
        quantity_sold=item.get("quantitySold"),
        brand=item.get("brand"),
        model=item.get("model"),
        category_path=item.get("category"),
        item_specifics=item.get("itemSpecifics"),
    )


UPC_LOOKUP_URL = "https://api.upcitemdb.com/prod/trial/lookup"


async def lookup_upc(barcode: str) -> dict | None:
    """Busca info del producto por UPC/EAN en upcitemdb.com (API gratuita).

    Returns dict con {title, brand, model, image_url} o None si falla.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(UPC_LOOKUP_URL, params={"upc": barcode})
            resp.raise_for_status()
            data = resp.json()

        items = data.get("items", [])
        if not items:
            return None

        item = items[0]
        images = item.get("images", [])
        return {
            "title": item.get("title", ""),
            "brand": item.get("brand", ""),
            "model": item.get("model", ""),
            "image_url": images[0] if images else None,
        }
    except Exception as e:
        logger.debug("UPC lookup failed for %s: %s", barcode, e)
        return None


class EbayClient(MarketplaceClient):
    """Cliente de eBay: scraper directo (default) con fallback a Apify."""

    def __init__(self) -> None:
        self._token = settings.apify_token
        self._data_source = settings.ebay_data_source

    async def search_by_barcode(self, barcode: str) -> list[MarketplaceListing]:
        comps = await self.get_sold_comps(barcode=barcode, days=30, limit=20)
        return comps.listings

    async def search_by_keyword(self, keyword: str, limit: int = 20) -> list[MarketplaceListing]:
        comps = await self.get_sold_comps(keyword=keyword, days=30, limit=limit)
        return comps.listings

    async def get_sold_comps(
        self,
        barcode: str | None = None,
        keyword: str | None = None,
        days: int = 30,
        limit: int = 50,
        min_price: float | None = None,
        max_price: float | None = None,
        condition: str = "any",
    ) -> CompsResult:
        """Obtiene ventas completadas reales de eBay.

        Usa scraper directo como default, Apify como fallback.

        Args:
            barcode: UPC/EAN del producto.
            keyword: Texto de búsqueda.
            days: Días de datos para cálculo de sales_per_day.
            limit: Máximo de resultados.
            min_price: Filtro de precio mínimo (se filtra post).
            max_price: Filtro de precio máximo (se filtra post).
            condition: Ignorado (se filtra en comp_cleaner).
        """
        query = barcode or keyword
        if not query:
            return CompsResult(marketplace="ebay")

        data: list[dict] | None = None

        if self._data_source == "scraper":
            data = await self._fetch_via_scraper(query, limit)
            if data is None and self._token:
                logger.info("Scraper falló, intentando fallback a Apify para '%s'", query)
                data = await self._fetch_via_apify(query, limit)
        else:
            # data_source == "apify"
            data = await self._fetch_via_apify(query, limit)

        if not data:
            return CompsResult(marketplace="ebay", days_of_data=days)

        listings = self._map_and_filter(data, min_price, max_price)
        source = "Scraper" if self._data_source == "scraper" else "Apify"
        logger.info("%s: %d items → %d listings para '%s'", source, len(data), len(listings), query)

        return CompsResult.from_listings(listings, marketplace="ebay", days=days)

    async def _fetch_via_scraper(self, query: str, limit: int) -> list[dict] | None:
        """Intenta obtener datos via scraper directo. Retorna None si falla."""
        try:
            data = await scrape_sold_listings(query, limit=limit)
            if not data:
                logger.warning("Scraper retornó 0 resultados para '%s'", query)
                return None
            return data
        except httpx.HTTPStatusError as e:
            logger.warning("Scraper HTTP error %s para '%s'", e.response.status_code, query)
            return None
        except httpx.TimeoutException:
            logger.warning("Scraper timeout para '%s'", query)
            return None
        except Exception as e:
            logger.warning("Scraper error para '%s': %s", query, e)
            return None

    async def _fetch_via_apify(self, query: str, limit: int) -> list[dict] | None:
        """Obtiene datos via Apify. Retorna None si falla."""
        if not self._token:
            logger.error("APIFY_TOKEN no configurado")
            return None

        actor_input = {
            "keyword": query,
            "maxItems": limit,
            "detailedSearch": False,
        }

        try:
            async with httpx.AsyncClient(timeout=APIFY_TIMEOUT) as client:
                resp = await client.post(
                    APIFY_RUN_URL,
                    params={"token": self._token},
                    json=actor_input,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            logger.error("Apify timeout después de %ds para query='%s'", APIFY_TIMEOUT, query)
            return None
        except httpx.HTTPStatusError as e:
            logger.error("Apify HTTP error %s: %s", e.response.status_code, e.response.text[:200])
            return None
        except Exception as e:
            logger.error("Apify error: %s", e)
            return None

        if not isinstance(data, list):
            logger.warning("Apify response no es una lista: %s", type(data))
            return None

        return data

    @staticmethod
    def _map_and_filter(
        data: list[dict],
        min_price: float | None = None,
        max_price: float | None = None,
    ) -> list[MarketplaceListing]:
        """Mapea dicts a MarketplaceListing y aplica filtros de precio."""
        listings: list[MarketplaceListing] = []
        for item in data:
            listing = _map_listing(item)
            if listing is None:
                continue
            price = listing.total_price or listing.price
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue
            listings.append(listing)
        return listings
