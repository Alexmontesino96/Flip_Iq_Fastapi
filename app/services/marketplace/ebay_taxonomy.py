"""eBay Taxonomy API client — category suggestions and tree lookup.

Uses client_credentials auth (same as Browse API).
Endpoint: /commerce/taxonomy/v1/category_tree/0 (US marketplace)

Primary use: getCategorySuggestions — replaces LLM-based categorization
with eBay's own category classifier. Faster, free, and always accurate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from app.services.marketplace.ebay_browse import _base_url, _get_token, _token_cache

logger = logging.getLogger("flipiq.ebay_taxonomy")

# eBay US category tree ID
_TREE_ID = "0"


@dataclass
class CategorySuggestion:
    """A category suggestion from eBay's Taxonomy API."""

    category_id: int
    category_name: str
    parent_path: list[str] = field(default_factory=list)  # ancestor names top→bottom


@dataclass
class CategoryNode:
    """A node in the category tree."""

    category_id: int
    category_name: str
    level: int
    is_leaf: bool
    children: list[CategoryNode] = field(default_factory=list)


async def get_category_suggestions(query: str) -> list[CategorySuggestion]:
    """Get eBay's suggested categories for a keyword.

    Uses getCategorySuggestions endpoint — eBay's own ML classifier.
    Returns up to 3-5 suggestions sorted by relevance.

    This is preferred over LLM-based categorization because:
    - It's eBay's own model (always matches their taxonomy)
    - It's free (no LLM tokens)
    - It's fast (~100ms)
    """
    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            token = await _get_token(client)
        except Exception:
            logger.warning("Taxonomy API: could not obtain token")
            return []

        try:
            resp = await client.get(
                f"{_base_url()}/commerce/taxonomy/v1/category_tree/{_TREE_ID}/get_category_suggestions",
                params={"q": query},
                headers={"Authorization": f"Bearer {token}"},
            )

            if resp.status_code == 401:
                # Token expired, force refresh
                _token_cache["token"] = None
                token = await _get_token(client)
                resp = await client.get(
                    f"{_base_url()}/commerce/taxonomy/v1/category_tree/{_TREE_ID}/get_category_suggestions",
                    params={"q": query},
                    headers={"Authorization": f"Bearer {token}"},
                )

            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning("Taxonomy API suggestions failed: %s", e.response.status_code)
            return []
        except httpx.RequestError as e:
            logger.warning("Taxonomy API request error: %s", e)
            return []

    data = resp.json()
    suggestions: list[CategorySuggestion] = []

    for item in data.get("categorySuggestions", []):
        cat = item.get("category", {})
        cat_id = cat.get("categoryId")
        cat_name = cat.get("categoryName", "")

        if not cat_id:
            continue

        # Build parent path from ancestors
        ancestors = item.get("categoryTreeNodeAncestors", [])
        # Ancestors come child→root, reverse to get root→child
        parent_path = [a.get("categoryName", "") for a in reversed(ancestors)]

        suggestions.append(CategorySuggestion(
            category_id=int(cat_id),
            category_name=cat_name,
            parent_path=parent_path,
        ))

    return suggestions


async def get_category_subtree(category_id: int) -> CategoryNode | None:
    """Get the subtree rooted at a specific category.

    Useful for exploring subcategories of a known L1/L2 category.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            token = await _get_token(client)
        except Exception:
            logger.warning("Taxonomy API: could not obtain token for subtree")
            return None

        try:
            resp = await client.get(
                f"{_base_url()}/commerce/taxonomy/v1/category_tree/{_TREE_ID}/get_category_subtree",
                params={"category_id": str(category_id)},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning("Taxonomy API subtree failed for %d: %s", category_id, e)
            return None

    data = resp.json()
    root_node = data.get("categorySubtreeNode")
    if not root_node:
        return None

    return _parse_node(root_node)


def _parse_node(node: dict) -> CategoryNode:
    """Recursively parse a category tree node."""
    cat = node.get("category", {})
    children_data = node.get("childCategoryTreeNodes", [])

    children = [_parse_node(child) for child in children_data]

    return CategoryNode(
        category_id=int(cat.get("categoryId", 0)),
        category_name=cat.get("categoryName", ""),
        level=node.get("categoryTreeNodeLevel", 0),
        is_leaf=node.get("leafCategoryTreeNode", False),
        children=children,
    )
