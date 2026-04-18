"""Cliente eBay: scraper directo con pool de RPi proxies.

Usa scraper propio (curl_cffi + BeautifulSoup) para obtener ventas completadas de eBay.
Configurable via EBAY_DATA_SOURCE env var: "scraper" (default) | "rpi".
"""

import itertools
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.marketplace.base import CompsResult, MarketplaceClient, MarketplaceListing
from app.services.marketplace.ebay_scraper import scrape_sold_listings

logger = logging.getLogger(__name__)


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
    """Convierte un item del dataset del scraper a MarketplaceListing."""
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
        item_location=item.get("itemLocation"),
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
    """Cliente de eBay: scraper directo con pool de RPi proxies."""

    def __init__(self) -> None:
        self._data_source = settings.ebay_data_source
        self._rpi_api_key = settings.rpi_scraper_api_key
        self._proxy_url = settings.residential_proxy_url or None
        self._last_fetch_meta: dict[str, object] = {}

        # Pool de RPis: parsear URLs separadas por coma
        raw = settings.rpi_scraper_urls.strip()
        self._rpi_urls = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()] if raw else []
        # Round-robin infinito sobre el pool
        self._rpi_cycle = itertools.cycle(self._rpi_urls) if self._rpi_urls else None

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
        category_id: int | None = None,
    ) -> CompsResult:
        """Obtiene ventas completadas reales de eBay.

        Args:
            barcode: UPC/EAN del producto.
            keyword: Texto de búsqueda.
            days: Días de datos para cálculo de sales_per_day.
            limit: Máximo de resultados.
            min_price: Filtro de precio mínimo (se filtra post).
            max_price: Filtro de precio máximo (se filtra post).
            condition: Filtro de condición (new, used, refurbished, etc.). "any" = sin filtro.
            category_id: ID de categoría eBay (_sacat) para filtrar resultados.
        """
        query = barcode or keyword
        if not query:
            return CompsResult(
                marketplace="ebay",
                query_used=None,
                scrape_source=None,
                scrape_status="empty",
                error_reason="missing_query",
            )

        logger.info(
            "eBay get_sold_comps: query='%s' limit=%d data_source='%s' proxy=%s",
            query, limit, self._data_source,
            bool(self._proxy_url),
        )

        # Normalizar: "any" → None para el scraper (sin filtro de condición en URL)
        cond = condition if condition and condition != "any" else None

        data: list[dict] | None = None
        source_used: str | None = None
        status = "empty"
        fallback_used = False
        error_reason: str | None = None
        attempts: list[dict[str, object]] = []

        def _record_attempt(source: str, result: list[dict] | None) -> None:
            nonlocal source_used, status, error_reason
            meta = dict(getattr(self, "_last_fetch_meta", {}) or {})
            if meta.get("source") != source:
                meta = {"source": source}
            if "source" not in meta:
                meta["source"] = source
            if result is not None and "status" not in meta:
                meta["status"] = "ok" if result else "empty"
            attempts.append(meta)
            if result is not None:
                source_used = source
                status = str(meta.get("status") or ("ok" if result else "empty"))
                error_reason = meta.get("error_reason") if isinstance(meta.get("error_reason"), str) else None

        if self._data_source == "rpi":
            data = await self._fetch_via_rpi(query, limit, condition=cond, category_id=category_id)
            _record_attempt("rpi", data)
            if data is None:
                logger.info("RPi proxy falló, intentando scraper directo para '%s'", query)
                fallback_used = True
                data = await self._fetch_via_scraper(query, limit, condition=cond, category_id=category_id)
                _record_attempt("scraper", data)
        else:
            # data_source == "scraper" (default)
            data = await self._fetch_via_scraper(query, limit, condition=cond, category_id=category_id)
            _record_attempt("scraper", data)
            logger.info(
                "Scraper result: %d items for '%s'",
                len(data) if data else 0, query,
            )

        if not data:
            logger.warning("eBay: 0 items para '%s' después de todos los intentos", query)
            if attempts:
                last = attempts[-1]
                source = last.get("source")
                if source_used is None and isinstance(source, str):
                    source_used = source
                status = str(last.get("status") or status)
                reason = last.get("error_reason")
                error_reason = reason if isinstance(reason, str) else error_reason
            return CompsResult(
                marketplace="ebay",
                days_of_data=days,
                query_used=query,
                scrape_source=source_used,
                scrape_status=status,
                fallback_used=fallback_used,
                error_reason=error_reason,
                diagnostics={
                    "attempts": attempts,
                    "raw_count": 0,
                    "mapped_count": 0,
                    "requested_condition": condition,
                    "category_id": category_id,
                },
            )

        listings = self._map_and_filter(data, min_price, max_price)
        logger.info(
            "eBay FINAL: data_source='%s' %d raw → %d listings para '%s'",
            self._data_source, len(data), len(listings), query,
        )

        result = CompsResult.from_listings(
            listings,
            marketplace="ebay",
            days=days,
            query_used=query,
            scrape_source=source_used,
            scrape_status=status if listings else "empty",
            fallback_used=fallback_used,
            error_reason=error_reason,
            diagnostics={
                "attempts": attempts,
                "raw_count": len(data),
                "mapped_count": len(listings),
                "requested_condition": condition,
                "category_id": category_id,
            },
        )
        if fallback_used:
            result.warnings.append(
                "eBay scraper fallback was used; source filters may be less precise."
            )
        return result

    async def _fetch_via_rpi(
        self, query: str, limit: int, condition: str | None = None,
        category_id: int | None = None,
    ) -> list[dict] | None:
        """Obtiene datos via pool de RPi Scraper Proxies (IPs residenciales).

        Round-robin entre proxies disponibles. Si uno falla, prueba el siguiente.
        Retorna None solo si todos fallan.
        """
        if not self._rpi_urls or not self._rpi_cycle:
            logger.debug("RPI_SCRAPER_URLS no configuradas")
            self._last_fetch_meta = {
                "source": "rpi",
                "status": "empty",
                "error_reason": "rpi_not_configured",
            }
            return None

        headers = {}
        if self._rpi_api_key:
            headers["X-API-Key"] = self._rpi_api_key

        # Intentar cada RPi del pool una vez (round-robin con failover)
        for _ in range(len(self._rpi_urls)):
            rpi_url = next(self._rpi_cycle)
            try:
                body: dict = {"keyword": query, "limit": limit}
                if condition is not None:
                    body["condition"] = condition
                if category_id is not None:
                    body["category_id"] = category_id
                async with httpx.AsyncClient(timeout=25) as client:
                    resp = await client.post(
                        f"{rpi_url}/scrape",
                        json=body,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                if not isinstance(data, list):
                    logger.warning("RPi %s response no es lista: %s", rpi_url, type(data))
                    continue
                if not data:
                    logger.warning("RPi %s retornó 0 resultados para '%s'", rpi_url, query)
                    self._last_fetch_meta = {
                        "source": "rpi",
                        "status": "empty",
                        "error_reason": "empty_response",
                    }
                    return None  # 0 resultados es válido, no reintentar
                logger.info("RPi %s: %d resultados para '%s'", rpi_url, len(data), query)
                self._last_fetch_meta = {
                    "source": "rpi",
                    "status": "ok",
                    "raw_count": len(data),
                }
                return data

            except httpx.TimeoutException:
                logger.warning("RPi %s timeout para '%s', probando siguiente", rpi_url, query)
            except httpx.HTTPStatusError as e:
                logger.warning("RPi %s HTTP %s para '%s', probando siguiente", rpi_url, e.response.status_code, query)
            except Exception as e:
                logger.warning("RPi %s error para '%s': %s, probando siguiente", rpi_url, query, e)

        logger.warning("Todos los RPi proxies fallaron para '%s'", query)
        self._last_fetch_meta = {
            "source": "rpi",
            "status": "blocked",
            "error_reason": "all_rpi_proxies_failed",
        }
        return None

    async def _fetch_via_scraper(
        self, query: str, limit: int, condition: str | None = None,
        category_id: int | None = None,
    ) -> list[dict] | None:
        """Intenta obtener datos via scraper directo. Usa proxy residencial si está configurado.

        Reintenta UNA vez si obtiene resultados pero muy pocos (< 5),
        ya que eBay puede servir HTML parcial por sesión/IP.
        """
        for attempt in range(2):
            try:
                data = await scrape_sold_listings(
                    query, limit=limit, proxy_url=self._proxy_url,
                    condition=condition, category_id=category_id,
                )
                if data and (len(data) >= 5 or attempt == 1):
                    self._last_fetch_meta = {
                        "source": "scraper",
                        "status": "ok" if len(data) >= 5 else "partial",
                        "raw_count": len(data),
                    }
                    return data
                if not data:
                    logger.warning("Scraper retornó 0 resultados para '%s'", query)
                    self._last_fetch_meta = {
                        "source": "scraper",
                        "status": "empty",
                        "error_reason": "empty_response",
                    }
                    return None
                logger.info("Scraper retornó solo %d resultados para '%s', reintentando", len(data), query)
            except Exception as e:
                # curl_cffi exceptions: HTTPError, Timeout, RequestException
                exc_name = type(e).__name__
                status = "blocked"
                if exc_name == "Timeout":
                    logger.warning("Scraper timeout para '%s'", query)
                    if attempt == 0:
                        continue
                    self._last_fetch_meta = {
                        "source": "scraper",
                        "status": status,
                        "error_reason": "timeout",
                    }
                    return None
                logger.warning("Scraper error (%s) para '%s': %s", exc_name, query, e)
                self._last_fetch_meta = {
                    "source": "scraper",
                    "status": status,
                    "error_reason": exc_name,
                }
                return None
        self._last_fetch_meta = {
            "source": "scraper",
            "status": "empty",
            "error_reason": "unknown",
        }
        return None

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
