"""eBay Browse API client — keyword search for autocomplete suggestions.

Supports two auth modes:
1. OAuth User Token (EBAY_OAUTH_TOKEN) — preferred, used directly as Bearer token
2. Client Credentials (EBAY_APP_ID + EBAY_CERT_ID) — fallback, auto-refreshes Application token
"""

from __future__ import annotations

import base64
import logging
import re
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("flipiq.ebay_browse")

_SANDBOX_BASE = "https://api.sandbox.ebay.com"
_PRODUCTION_BASE = "https://api.ebay.com"

# Module-level token cache (only used for client_credentials flow)
_token_cache: dict[str, object] = {"token": None, "expires_at": 0.0}


def _base_url() -> str:
    return _SANDBOX_BASE if settings.ebay_sandbox else _PRODUCTION_BASE


async def _get_token(client: httpx.AsyncClient) -> str:
    """Get a valid Bearer token.

    Priority: OAuth User Token > client_credentials Application Token.
    """
    # 1. Direct OAuth User Token (no refresh needed, expires 2027)
    if settings.ebay_oauth_token:
        return settings.ebay_oauth_token

    # 2. Client credentials flow (Application Access Token)
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
    logger.info("eBay OAuth2 app token refreshed, expires in %ss", data.get("expires_in"))
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


def _clean_listing_title(title: str) -> str:
    """Strip seller noise from an eBay listing title to get a clean product name.

    "Apple AirPods Pro 2nd Generation with MagSafe (USB‑C) - White MTJV3AM/A"
    → "Apple AirPods Pro 2nd Generation with MagSafe"

    "*NEW* MEN NIKE DUNK LOW RETRO DARK PONY / PEARL WHITE (IM6670 202) 👍"
    → "Nike Dunk Low Retro Dark Pony / Pearl White"
    """
    s = title.strip()

    # 1. Remove emoji and non-ASCII decorations (keep basic punctuation)
    s = re.sub(r"[^\x20-\x7E]", " ", s)

    # 2. Remove wrapping asterisks: *NEW*, **Apple**
    s = re.sub(r"\*+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # 3. Remove leading noise: "NEW", "USED", "GENUINE", "OEM", gender (repeat to catch post-asterisk)
    for _ in range(2):
        s = re.sub(
            r"^(?:NEW|USED|OPEN\s*BOX|REFURBISHED|SEALED|GENUINE|AUTHENTIC|OEM|MEN'?S?|WOMEN'?S?)\b[\s,]*",
            "", s, flags=re.I,
        )

    # 4. Remove gender words anywhere
    s = re.sub(r"\b(?:MEN'?S?|WOMEN'?S?|MENS|WOMENS|BOYS?|GIRLS?|KIDS?|ADULT|UNISEX)\b", "", s, flags=re.I)

    # 5. Remove part/model numbers: MTJV3AM/A, CHE00076.201, CW1590-100, HEG-001, IB2267-001
    s = re.sub(r"\s+[A-Z]{2,}\d[\w./-]{3,}\s*$", "", s)
    s = re.sub(r"\s+[A-Z]{1,5}\d*-\d{2,}\w*", "", s)  # HEG-001, CW1590-100
    s = re.sub(r"\s+[A-Z]{1,4}\d{3,}[-/]\w+", "", s)
    s = re.sub(r"\s+\d{4,}[-/]\d+", "", s)

    # 6. Remove parenthesized model/part numbers: (IM6670 202), (USB-C)
    s = re.sub(r"\s*\([A-Z]{1,4}\d{3,}[\w\s/-]*\)", "", s)
    s = re.sub(r"\s*\(USB[- ]?C\)", "", s, flags=re.I)

    # 7. Remove trailing condition/seller descriptors after dash
    s = re.sub(
        r"\s*[-–—]\s*(?:New|Used|Open Box|Sealed|Excellent|Good|Fair|Great|Like New|"
        r"Brand New|NWT|NWOT|NIB|NWB|BNIB|Free Shipping|Fast Ship|"
        r"Japan Import|US plug|READ|See Desc|See Photos|Fully Functional|"
        r"Works Great|Tested|w/?o? Box).*$",
        "", s, flags=re.I,
    )

    # 8. Remove trailing size: "Size Medium", "Sz 10", standalone S/M/L/XL
    s = re.sub(r"\s+(?:Size\s+)?(?:XXS|XS|Small|Medium|Large|XXL|XXXL)\s*$", "", s, flags=re.I)
    # Only strip standalone S/M/L/XL if preceded by space (avoid stripping from "AirPods Pro S")
    s = re.sub(r"\s+(?:S|M|L|XL)\s*$", "", s)

    # 9. Remove trailing color after last meaningful word
    s = re.sub(
        r"\s+(?:Black|White|Blue|Red|Grey|Gray|Pink|Green|Purple|Orange|Yellow|Neon\s+\w+)"
        r"(?:\s+(?:Matt|Matte))?\s*$",
        "", s, flags=re.I,
    )

    # 10. Remove trailing noise phrases
    s = re.sub(r"\s*[-–]\s*(?:Fully Functional|Works Great|Tested).*$", "", s, flags=re.I)

    # 11. Remove quantity/capacity noise at end: "64GB", "+ Controllers, Case"
    s = re.sub(r"\s*\+\s*\w[\w\s,]*$", "", s)

    # 12. Remove trailing standalone condition words
    s = re.sub(r"\s+(?:New|Used|Sealed|Open Box)\s*$", "", s, flags=re.I)

    # 13. Cleanup
    s = re.sub(r"\(\s*\)", "", s)      # empty parens
    s = re.sub(r"\[\s*\]", "", s)      # empty brackets
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip("- –—.,;:+/'\"")

    # 14. Title-case if ALL CAPS
    if s == s.upper() and len(s) > 5:
        s = s.title()

    return s or title.strip()


def _dedupe_by_epid(suggestions: list[SearchSuggestion]) -> list[SearchSuggestion]:
    """Deduplicate suggestions by ePID, keeping the one with the shortest title.

    Items without ePID are kept as-is.
    """
    seen_epids: dict[str, int] = {}  # epid → index in result
    result: list[SearchSuggestion] = []

    for s in suggestions:
        if not s.epid:
            result.append(s)
            continue

        if s.epid in seen_epids:
            idx = seen_epids[s.epid]
            # Keep the shorter (cleaner) title
            if len(s.title) < len(result[idx].title):
                result[idx] = s
        else:
            seen_epids[s.epid] = len(result)
            result.append(s)

    return result


async def search_keywords(
    query: str,
    *,
    limit: int = 10,
    category_ids: str | None = None,
) -> list[SearchSuggestion]:
    """Search eBay Browse API for active listings matching a keyword.

    Returns product-level suggestions (deduplicated by ePID, clean titles).
    """
    has_oauth = bool(settings.ebay_oauth_token)
    has_client_creds = bool(settings.ebay_app_id and settings.ebay_cert_id)

    if not has_oauth and not has_client_creds:
        logger.warning("eBay Browse API: no OAuth token or client credentials configured")
        return []

    # Fetch more than needed so deduplication still yields enough results
    fetch_limit = min(limit * 3, 50)

    params: dict[str, str] = {
        "q": query,
        "limit": str(fetch_limit),
        "auto_correct": "KEYWORD",
        "fieldgroups": "MATCHING_ITEMS",
    }
    if category_ids:
        params["category_ids"] = category_ids

    async with httpx.AsyncClient(timeout=5.0) as client:
        token = await _get_token(client)
        resp = await _do_search(client, token, params)

        if resp.status_code == 401 and not has_oauth:
            _token_cache["token"] = None
            token = await _get_token(client)
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

        raw_title = item.get("title", "")
        clean_title = _clean_listing_title(raw_title)

        suggestions.append(SearchSuggestion(
            title=clean_title,
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

    # Deduplicate by ePID (same product, different listings)
    suggestions = _dedupe_by_epid(suggestions)

    return suggestions[:limit]
