"""Tests for GET /admin/analyses."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1.admin import list_analyses


def _admin():
    u = MagicMock()
    u.email = "alexmontesinocastro9@gmail.com"
    return u


def _db_returning(rows):
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_rejects_non_admin():
    user = MagicMock()
    user.email = "someone@example.com"
    with pytest.raises(HTTPException) as exc:
        await list_analyses(db=AsyncMock(), user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_returns_serialized_rows():
    a = MagicMock()
    a.id = 7
    a.marketplace = "ebay"
    a.cost_price = 12.5
    a.recommendation = "buy"
    a.flip_score = 81
    a.opportunity_score = 66
    a.no_comps_found = False
    a.created_at = datetime(2026, 11, 9, tzinfo=timezone.utc)

    db = _db_returning([(a, "Nintendo Switch OLED", "u@example.com", "pro")])
    resp = await list_analyses(
        tier="pro", recommendation="buy", limit=50, offset=0, db=db, user=_admin(),
    )

    assert resp["analyses"] == [{
        "id": 7,
        "user_email": "u@example.com",
        "user_tier": "pro",
        "product_title": "Nintendo Switch OLED",
        "marketplace": "ebay",
        "cost_price": 12.5,
        "recommendation": "buy",
        "flip_score": 81,
        "opportunity_score": 66,
        "no_comps_found": False,
        "created_at": "2026-11-09T00:00:00+00:00",
    }]
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_anonymous_analysis_has_null_user():
    a = MagicMock()
    a.cost_price = None
    a.created_at = None
    db = _db_returning([(a, "Some product", None, None)])
    resp = await list_analyses(limit=50, offset=0, db=db, user=_admin())
    row = resp["analyses"][0]
    assert row["user_email"] is None
    assert row["cost_price"] is None
    assert row["created_at"] is None
