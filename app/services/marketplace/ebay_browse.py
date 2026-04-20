"""eBay Browse API client — keyword search for autocomplete suggestions.

Uses OAuth2 Application Access Token (client_credentials grant).
Token is cached in-memory and refreshed automatically when expired.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("flipiq.ebay_browse")

_SANDBOX_BASE = "https://api.sandbox.ebay.com"
_PRODUCTION_BASE = "https://api.ebay.com"

# Module-level token cache
_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


def _base_url() -> str:
    return _SANDBOX_BASE if settings.ebay_sandbox else _PRODUCTION_BASE


async def _get_app_token(client: httpx.AsyncClient) -> str:
    """Get or refresh OAuth2 Application Access Token."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    credentials = f"{settings.ebay_app_id}:{settings.ebay_cert_id}"
    b64 = base64.b64encode(credentials.encode()).decode()

    resp = await client.post(
        f"{_base_url()}/identity/v1/oauth2/token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {b64}",
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 7200)
    logger.info("eBay OAuth2 token refreshed, expires in %ss", data.get("expires_in"))
    return data["access_token"]


@dataclass
class SearchSuggestion:
    """Lightweight item suggestion for autocomplete."""

    title: str
    price: float | None = None
    currency: str = "USD"
    image_url: str | None = None
    condition: str | None = None
    item_id: str | None = None
    item_url: str | None = None
    category: str | None = None
    brand: str | None = None
    epid: str | None = None


async def _do_search(
    client: httpx.AsyncClient, token: str, params: dict[str, str],
) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    return await client.get(
        f"{_base_url()}/buy/browse/v1/item_summary/search",
        params=params,
        headers=headers,
    )


async def search_keywords(
    query: str,
    *,
    limit: int = 10,
    category_ids: str | None = None,
) -> list[SearchSuggestion]:
    """Search eBay Browse API for active listings matching a keyword."""
    if not settings.ebay_app_id or not settings.ebay_cert_id:
        logger.warning("eBay Browse API credentials not configured")
        return []

    params: dict[str, str] = {
        "q": query,
        "limit": str(min(limit, 50)),
        "auto_correct": "KEYWORD",
        "fieldgroups": "MATCHING_ITEMS",
    }
    if category_ids:
        params["category_ids"] = category_ids

    async with httpx.AsyncClient(timeout=5.0) as client:
        token = await _get_app_token(client)
        resp = await _do_search(client, token, params)

        if resp.status_code == 401:
            _token_cache["token"] = None
            token = await _get_app_token(client)
            resp = await _do_search(client, token, params)

        resp.raise_for_status()

    data = resp.json()
    items = data.get("itemSummaries", [])

    suggestions: list[SearchSuggestion] = []
    for item in items:
        price_info = item.get("price", {})
        image = item.get("image", {})
        categories = item.get("categories", [])

        brand = None
        for aspect in item.get("itemAspects", []):
            if aspect.get("name") == "Brand":
                brand = aspect.get("value")
                break

        suggestions.append(SearchSuggestion(
            title=item.get("title", ""),
            price=float(price_info["value"]) if price_info.get("value") else None,
            currency=price_info.get("currency", "USD"),
            image_url=image.get("imageUrl"),
            condition=item.get("condition"),
            item_id=item.get("itemId"),
            item_url=item.get("itemWebUrl"),
            category=categories[0].get("categoryName") if categories else None,
            brand=brand,
            epid=item.get("epid"),
        ))

    return suggestions
