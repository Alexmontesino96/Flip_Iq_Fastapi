"""Tests for eBay Taxonomy API client."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.marketplace.ebay_taxonomy import (
    CategorySuggestion,
    get_category_suggestions,
    get_category_subtree,
)


def _make_response(status_code: int, json_data: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.ebay.com/test"),
    )


FAKE_SUGGESTIONS_RESPONSE = {
    "categorySuggestions": [
        {
            "category": {
                "categoryId": "9355",
                "categoryName": "Cell Phones & Smartphones",
            },
            "categoryTreeNodeAncestors": [
                {
                    "categoryId": "15032",
                    "categoryName": "Cell Phones & Accessories",
                },
                {
                    "categoryId": "0",
                    "categoryName": "Root",
                },
            ],
            "categoryTreeNodeLevel": 2,
        },
        {
            "category": {
                "categoryId": "42428",
                "categoryName": "Cases, Covers & Skins",
            },
            "categoryTreeNodeAncestors": [
                {
                    "categoryId": "9394",
                    "categoryName": "Cell Phone Accessories",
                },
                {
                    "categoryId": "15032",
                    "categoryName": "Cell Phones & Accessories",
                },
            ],
            "categoryTreeNodeLevel": 3,
        },
    ]
}

FAKE_SUBTREE_RESPONSE = {
    "categorySubtreeNode": {
        "category": {
            "categoryId": "15032",
            "categoryName": "Cell Phones & Accessories",
        },
        "categoryTreeNodeLevel": 1,
        "leafCategoryTreeNode": False,
        "childCategoryTreeNodes": [
            {
                "category": {
                    "categoryId": "9355",
                    "categoryName": "Cell Phones & Smartphones",
                },
                "categoryTreeNodeLevel": 2,
                "leafCategoryTreeNode": True,
                "childCategoryTreeNodes": [],
            },
            {
                "category": {
                    "categoryId": "9394",
                    "categoryName": "Cell Phone Accessories",
                },
                "categoryTreeNodeLevel": 2,
                "leafCategoryTreeNode": False,
                "childCategoryTreeNodes": [
                    {
                        "category": {
                            "categoryId": "42428",
                            "categoryName": "Cases, Covers & Skins",
                        },
                        "categoryTreeNodeLevel": 3,
                        "leafCategoryTreeNode": True,
                        "childCategoryTreeNodes": [],
                    }
                ],
            },
        ],
    }
}


@pytest.fixture(autouse=True)
def _reset_token_cache():
    from app.services.marketplace.ebay_browse import _token_cache
    _token_cache["token"] = "fake-token"
    _token_cache["expires_at"] = 9999999999.0
    yield
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0.0


@pytest.mark.asyncio
async def test_get_category_suggestions():
    """Should parse suggestions with ancestors."""
    resp = _make_response(200, FAKE_SUGGESTIONS_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await get_category_suggestions("iphone 14")

    assert len(results) == 2
    assert results[0].category_id == 9355
    assert results[0].category_name == "Cell Phones & Smartphones"
    # Ancestors are reversed: root→child order
    assert results[0].parent_path == ["Root", "Cell Phones & Accessories"]
    assert results[1].category_id == 42428


@pytest.mark.asyncio
async def test_get_category_suggestions_empty():
    """Empty response returns empty list."""
    resp = _make_response(200, {"categorySuggestions": []})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await get_category_suggestions("xyznonexistent")

    assert results == []


@pytest.mark.asyncio
async def test_get_category_suggestions_error():
    """HTTP errors return empty list gracefully."""
    resp = _make_response(500, {"error": "internal"})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        results = await get_category_suggestions("test")

    assert results == []


@pytest.mark.asyncio
async def test_get_category_subtree():
    """Should parse subtree with children recursively."""
    resp = _make_response(200, FAKE_SUBTREE_RESPONSE)

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        tree = await get_category_subtree(15032)

    assert tree is not None
    assert tree.category_id == 15032
    assert tree.category_name == "Cell Phones & Accessories"
    assert len(tree.children) == 2
    assert tree.children[0].category_id == 9355
    assert tree.children[0].is_leaf is True
    assert tree.children[1].category_id == 9394
    assert len(tree.children[1].children) == 1
    assert tree.children[1].children[0].category_id == 42428


@pytest.mark.asyncio
async def test_get_category_subtree_not_found():
    """Missing category returns None."""
    resp = _make_response(404, {"errors": [{"message": "Not found"}]})

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        client_instance.get = AsyncMock(return_value=resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        tree = await get_category_subtree(99999999)

    assert tree is None
