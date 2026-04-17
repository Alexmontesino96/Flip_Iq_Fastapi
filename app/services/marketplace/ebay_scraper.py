"""Scraper directo de eBay sold listings con curl_cffi + BeautifulSoup.

Reemplaza Apify para eliminar costos por resultado.
Usa curl_cffi con impersonate="chrome" para replicar el TLS fingerprint
de Chrome y evitar detección por Cloudflare/eBay.
Retorna dicts con el mismo formato que el actor de Apify.
"""

import logging
import random
import re
from datetime import datetime, timezone

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException  # noqa: F401
from curl_cffi.requests.exceptions import HTTPError  # noqa: F401
from curl_cffi.requests.exceptions import Timeout  # noqa: F401

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EBAY_SEARCH_URL = "https://www.ebay.com/sch/i.html"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

SCRAPER_TIMEOUT = 20  # segundos

_DEFAULT_EXCLUSIONS = [
    "lot", "bundle", "wholesale", "bulk",
    "broken", "defective", "junk", "salvage",
    # Accessory exclusions — prevents eBay from returning accessories
    # that match the keyword (e.g. "case for Nintendo Switch OLED").
    # Terms present in the user's keyword are automatically kept by _build_search_query.
    "case", "cover", "protector", "skin",
    "charger", "cable", "adapter",
    "stand", "mount", "holder",
    "strap", "decal", "replacement",
]

_EBAY_CONDITION_IDS = {
    "new": "1000|1500|1750",
    "used": "3000|4000|5000|6000",
    "refurbished": "2000|2010|2020|2030|2500",
    "open_box": "1500",
    "for_parts": "7000",
}


def _build_search_query(keyword: str, exclude_terms: list[str] | None = None) -> str:
    """Agrega exclusiones a la query para filtrar basura desde eBay.

    Solo excluye términos que NO aparecen en el keyword del usuario.
    Si el keyword es numérico (barcode/UPC), NO excluir nada — la búsqueda
    por UPC ya es específica y las exclusiones pueden filtrar resultados
    legítimos (ej. "-case" elimina AirPods Pro "MagSafe Case").
    """
    # Barcode/UPC: no aplicar exclusiones
    if keyword.strip().isdigit():
        return keyword

    kw_lower = keyword.lower()
    exclusions = _DEFAULT_EXCLUSIONS if exclude_terms is None else exclude_terms
    safe = [t for t in exclusions if t.lower() not in kw_lower]
    if safe:
        return keyword + " " + " ".join(f"-{t}" for t in safe)
    return keyword


def _get_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _parse_price(text: str | None) -> float:
    """Extrae precio numérico de texto como '$208.48' o '$1,299.99'."""
    if not text:
        return 0.0
    match = re.search(r"\$?([\d,]+\.?\d*)", text.strip())
    if match:
        return float(match.group(1).replace(",", ""))
    return 0.0


def _parse_shipping(text: str | None) -> float:
    """Parsea costo de envío. 'Free shipping' → 0, '+$X.XX shipping' → X.XX."""
    if not text:
        return 0.0
    lower = text.strip().lower()
    if "free" in lower:
        return 0.0
    return _parse_price(text)


def _parse_bids(text: str | None) -> int | None:
    """Extrae número de bids de texto como '15 bids'."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*bid", text.strip())
    if match:
        return int(match.group(1))
    return None


def _parse_sold_date(text: str | None) -> str | None:
    """Parsea fecha de venta de texto como 'Sold  Apr 10, 2026' a ISO 8601."""
    if not text:
        return None
    match = re.search(r"Sold\s+(\w+\s+\d{1,2},?\s*\d{4})", text.strip())
    if match:
        date_str = match.group(1).replace(",", "")
        try:
            dt = datetime.strptime(date_str, "%b %d %Y")
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    # Fallback: buscar patrón "Mon DD, YYYY" sin "Sold"
    match = re.search(r"(\w{3})\s+(\d{1,2}),?\s*(\d{4})", text.strip())
    if match:
        try:
            dt = datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%b %d %Y")
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return None


def _extract_item_id(url: str | None) -> str | None:
    """Extrae itemId del URL de eBay como '/itm/123456789'."""
    if not url:
        return None
    match = re.search(r"/itm/(\d+)", url)
    return match.group(1) if match else None


def _extract_seller(text: str | None) -> str | None:
    """Extrae seller username de texto de info del vendedor."""
    if not text:
        return None
    # Formatos: "seller_name (1234) 99.5%", "seller_name"
    match = re.match(r"([\w\.\-]+)", text.strip())
    return match.group(1) if match else None


def _find_text_by_classes(card, required_classes: set[str], text_filter=None) -> str | None:
    """Busca un span.su-styled-text que tenga las clases requeridas y pase el filtro."""
    for el in card.select("span.su-styled-text"):
        el_classes = set(el.get("class", []))
        if required_classes.issubset(el_classes):
            text = el.get_text(strip=True)
            if text and (text_filter is None or text_filter(text)):
                return text
    return None


def parse_sold_listings(html: str) -> list[dict]:
    """Parsea HTML de búsqueda de eBay sold listings y retorna lista de dicts.

    Soporta el layout actual de eBay (li.s-card, 2025-2026) y el legacy (li.s-item).

    Retorna dicts con los mismos campos que el actor de Apify:
    title, soldPrice, shippingPrice, totalPrice, endedAt, condition,
    bids, sellerUsername, url, itemId
    """
    soup = BeautifulSoup(html, "html.parser")

    # Intentar layout actual (s-card) primero, fallback a legacy (s-item)
    cards = soup.select("li.s-card")
    if cards:
        return _parse_s_card_layout(cards)

    items = soup.select("li.s-item")
    if items:
        return _parse_s_item_layout(items)

    return []


def _parse_s_card_layout(cards) -> list[dict]:
    """Parsea el layout actual de eBay (li.s-card, 2025-2026)."""
    results = []

    for card in cards:
        # Título: span.su-styled-text.primary.default dentro del header
        title_el = _find_text_by_classes(card, {"su-styled-text", "primary", "default"})
        if not title_el:
            continue
        title = title_el.strip()
        if title.lower().startswith("shop on ebay"):
            continue

        # Precio: primer span con clase s-card__price (excluir "to")
        price_els = card.select(".s-card__price")
        sold_price = 0.0
        for pel in price_els:
            text = pel.get_text(strip=True)
            if text.lower() == "to":
                continue
            sold_price = _parse_price(text)
            break  # tomar el primer precio real
        if sold_price <= 0:
            continue

        # Envío: buscar span.su-styled-text.secondary.large con "delivery" o "shipping"
        shipping_price = 0.0
        for el in card.select("span.su-styled-text"):
            el_classes = set(el.get("class", []))
            if {"secondary", "large"}.issubset(el_classes):
                text = el.get_text(strip=True).lower()
                if "delivery" in text or "shipping" in text:
                    shipping_price = _parse_shipping(el.get_text(strip=True))
                    break

        total_price = sold_price + shipping_price

        # URL y itemId: segundo a.s-card__link (primero es imagen)
        links = card.select("a.s-card__link")
        url = None
        item_id = card.get("data-listingid")
        if len(links) > 1:
            url = links[1].get("href")
        elif links:
            url = links[0].get("href")
        if not item_id:
            item_id = _extract_item_id(url)

        # Condición: span.su-styled-text.secondary.default (no "Sell one like this")
        condition = None
        for el in card.select("span.su-styled-text"):
            el_classes = set(el.get("class", []))
            if {"secondary", "default"}.issubset(el_classes) and "large" not in el_classes:
                text = el.get_text(strip=True)
                if text.lower() not in ("sell one like this",) and text:
                    condition = text
                    break

        # Fecha de venta: span.su-styled-text.positive.default con "Sold"
        ended_at = None
        sold_text = _find_text_by_classes(
            card, {"su-styled-text", "positive", "default"},
            text_filter=lambda t: "Sold" in t,
        )
        if sold_text:
            ended_at = _parse_sold_date(sold_text)

        # Bids: buscar texto con "bid" en spans secondary
        bids = None
        for el in card.select("span.su-styled-text"):
            text = el.get_text(strip=True)
            if "bid" in text.lower():
                bids = _parse_bids(text)
                if bids is not None:
                    break

        # Seller: span.su-styled-text.primary.large (primero es username, segundo feedback)
        # Excluir frases comunes que no son sellers: "with coupon", "X% off", etc.
        _NOT_SELLER = {"with coupon", "or best offer", "buy it now", "best offer accepted"}
        seller_username = None
        for el in card.select("span.su-styled-text"):
            el_classes = set(el.get("class", []))
            if {"primary", "large"}.issubset(el_classes):
                text = el.get_text(strip=True)
                if (text and "%" not in text and "positive" not in text.lower()
                        and text.lower() not in _NOT_SELLER):
                    seller_username = _extract_seller(text)
                    break
        # Fallback: seller en formato "username  99.6% positive (10.2K)" en .default
        if not seller_username:
            for el in card.select("span.su-styled-text.default"):
                text = el.get_text(strip=True)
                if "positive" in text.lower() and "%" in text:
                    seller_username = _extract_seller(text)
                    break

        results.append({
            "title": title,
            "soldPrice": str(sold_price),
            "shippingPrice": str(shipping_price),
            "totalPrice": str(total_price),
            "endedAt": ended_at,
            "condition": condition,
            "bids": bids,
            "sellerUsername": seller_username,
            "url": url,
            "itemId": item_id,
        })

    return results


def _parse_s_item_layout(items) -> list[dict]:
    """Parsea el layout legacy de eBay (li.s-item)."""
    results = []

    for item in items:
        title_el = item.select_one(".s-item__title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if title.lower().startswith("shop on ebay"):
            continue

        price_el = item.select_one("span.s-item__price")
        sold_price = _parse_price(price_el.get_text() if price_el else None)
        if sold_price <= 0:
            continue

        shipping_el = item.select_one("span.s-item__logisticsCost")
        shipping_price = _parse_shipping(shipping_el.get_text() if shipping_el else None)
        total_price = sold_price + shipping_price

        link_el = item.select_one("a.s-item__link")
        url = link_el["href"] if link_el and link_el.has_attr("href") else None
        item_id = _extract_item_id(url)

        condition_el = item.select_one(".SECONDARY_INFO")
        condition = condition_el.get_text(strip=True) if condition_el else None

        ended_at = None
        date_el = item.select_one(".s-item__ended-date")
        if date_el:
            ended_at = _parse_sold_date(date_el.get_text())
        if not ended_at:
            for span in item.select("span.POSITIVE"):
                text = span.get_text()
                if "Sold" in text:
                    ended_at = _parse_sold_date(text)
                    if ended_at:
                        break
        if not ended_at:
            for span in item.select("span"):
                text = span.get_text()
                if "Sold" in text:
                    ended_at = _parse_sold_date(text)
                    if ended_at:
                        break

        bids_el = item.select_one(".s-item__bidCount")
        bids = _parse_bids(bids_el.get_text() if bids_el else None)

        seller_el = item.select_one("span.s-item__seller-info-text")
        seller_username = _extract_seller(seller_el.get_text() if seller_el else None)

        results.append({
            "title": title,
            "soldPrice": str(sold_price),
            "shippingPrice": str(shipping_price),
            "totalPrice": str(total_price),
            "endedAt": ended_at,
            "condition": condition,
            "bids": bids,
            "sellerUsername": seller_username,
            "url": url,
            "itemId": item_id,
        })

    return results


async def scrape_sold_listings(
    keyword: str,
    limit: int = 50,
    proxy_url: str | None = None,
    condition: str | None = None,
    exclude_terms: list[str] | None = None,
    category_id: int | None = None,
) -> list[dict]:
    """Scraper directo a eBay sold listings.

    Args:
        keyword: Término de búsqueda.
        limit: Máximo de resultados a retornar.
        proxy_url: URL de proxy residencial (http://user:pass@host:port).
                   Si se pasa, cada request sale por IP residencial rotativa.
        condition: Filtro de condición para eBay (new, used, refurbished, etc.).
        exclude_terms: Términos a excluir de la búsqueda. Si None, usa defaults.
        category_id: ID de categoría eBay (_sacat) para filtrar resultados.

    Returns:
        Lista de dicts con formato compatible con Apify.

    Raises:
        HTTPError: Si eBay responde con error (429, 503, etc).
        Timeout: Si la conexión se agota.
    """
    results: list[dict] = []
    items_per_page = 240
    page = 1
    max_pages = max(1, (limit + items_per_page - 1) // items_per_page)

    client_kwargs: dict = {
        "timeout": SCRAPER_TIMEOUT,
        "allow_redirects": True,
        "impersonate": "chrome120",
    }
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    async with AsyncSession(**client_kwargs) as client:
        # Warmup: visitar eBay homepage para establecer cookies de sesión
        # y evitar challenge/CAPTCHA en la búsqueda de sold listings
        try:
            await client.get("https://www.ebay.com/", headers=_get_headers())
        except Exception:
            pass  # best-effort warmup

        while len(results) < limit and page <= max_pages:
            params = {
                "_nkw": _build_search_query(keyword, exclude_terms),
                "LH_Sold": "1",
                "LH_Complete": "1",
                "LH_PrefLoc": "1",   # US-only sellers → prices always in USD
                "_ipg": str(items_per_page),
                "_sop": "13",        # ended recently first
                "rt": "nc",          # no cache
            }
            if category_id is not None:
                params["_sacat"] = str(category_id)
            if condition and condition in _EBAY_CONDITION_IDS:
                params["LH_ItemCondition"] = _EBAY_CONDITION_IDS[condition]
            if page > 1:
                params["_pgn"] = str(page)

            resp = await client.get(
                EBAY_SEARCH_URL,
                params=params,
                headers=_get_headers(),
            )
            resp.raise_for_status()

            # Detectar CAPTCHA/challenge redirect
            if "challenge" in str(resp.url):
                logger.warning("eBay CAPTCHA detectado para '%s'", keyword)
                break

            page_results = parse_sold_listings(resp.text)
            if not page_results:
                break

            results.extend(page_results)
            page += 1

    return results[:limit]
