"""Tests for Stripe billing service and endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.stripe_service import (
    _set_credits,
    _ts,
    plan_for_price,
    register_price,
    handle_webhook_event,
    get_subscription_status,
)


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------

class TestSetCredits:
    def test_free_tier(self):
        user = MagicMock()
        _set_credits(user, "free")
        assert user.credits_remaining == 150

    def test_starter_tier(self):
        user = MagicMock()
        _set_credits(user, "starter")
        assert user.credits_remaining == 900

    def test_pro_tier(self):
        user = MagicMock()
        _set_credits(user, "pro")
        assert user.credits_remaining == 3000

    def test_unknown_tier_defaults_to_free(self):
        user = MagicMock()
        _set_credits(user, "unknown")
        assert user.credits_remaining == 150


class TestTimestamp:
    def test_none_returns_none(self):
        assert _ts(None) is None

    def test_valid_timestamp(self):
        ts = 1700000000
        result = _ts(ts)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_epoch_zero(self):
        result = _ts(0)
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)


class TestPlanForPrice:
    def test_registered_price(self):
        register_price("price_test_123", "pro")
        assert plan_for_price("price_test_123") == "pro"

    def test_unknown_price_defaults_to_starter(self):
        assert plan_for_price("price_unknown_xyz") == "starter"


# ---------------------------------------------------------------------------
# Subscription status
# ---------------------------------------------------------------------------

class TestGetSubscriptionStatus:
    @pytest.mark.asyncio
    async def test_no_subscription(self):
        user = MagicMock(id=1)
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        result = await get_subscription_status(user, db)
        assert result is None

    @pytest.mark.asyncio
    async def test_with_subscription(self):
        user = MagicMock(id=1)
        db = AsyncMock()

        sub_mock = MagicMock()
        sub_mock.stripe_subscription_id = "sub_123"
        sub_mock.status = "active"
        sub_mock.plan = "starter"
        sub_mock.current_period_end = datetime(2026, 5, 20, tzinfo=timezone.utc)
        sub_mock.cancel_at_period_end = False

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = sub_mock
        db.execute = AsyncMock(return_value=result_mock)

        result = await get_subscription_status(user, db)
        assert result is not None
        assert result["plan"] == "starter"
        assert result["status"] == "active"
        assert result["cancel_at_period_end"] is False


# ---------------------------------------------------------------------------
# Webhook event handling
# ---------------------------------------------------------------------------

class TestWebhookHandling:
    @pytest.mark.asyncio
    async def test_unhandled_event_type(self):
        """Unhandled event types should not raise."""
        event = MagicMock()
        event.type = "some.unknown.event"
        db = AsyncMock()
        await handle_webhook_event(event, db)

    @pytest.mark.asyncio
    async def test_checkout_completed_non_subscription(self):
        """checkout.session.completed with mode != subscription is ignored."""
        event = MagicMock()
        event.type = "checkout.session.completed"
        event.data.object.mode = "payment"

        db = AsyncMock()
        await handle_webhook_event(event, db)
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.stripe_service.stripe")
    async def test_subscription_deleted(self, mock_stripe):
        """subscription.deleted sets user tier to free."""
        event = MagicMock()
        event.type = "customer.subscription.deleted"
        event.data.object.id = "sub_del_123"

        sub_mock = MagicMock()
        sub_mock.user_id = 1
        sub_mock.status = "active"

        user_mock = MagicMock()
        user_mock.tier = "starter"

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = sub_mock
        db.execute = AsyncMock(return_value=result_mock)
        db.get = AsyncMock(return_value=user_mock)

        await handle_webhook_event(event, db)

        assert sub_mock.status == "canceled"
        assert user_mock.tier == "free"
        assert user_mock.credits_remaining == 150


# ---------------------------------------------------------------------------
# Endpoint tests (schema validation)
# ---------------------------------------------------------------------------

class TestBillingSchemas:
    def test_checkout_request(self):
        from app.schemas.billing import CheckoutRequest
        req = CheckoutRequest(
            price_id="price_123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert req.price_id == "price_123"

    def test_subscription_status_free(self):
        from app.schemas.billing import SubscriptionStatus
        status = SubscriptionStatus(has_subscription=False, plan="free")
        assert status.status is None
        assert status.cancel_at_period_end is False

    def test_subscription_status_active(self):
        from app.schemas.billing import SubscriptionStatus
        status = SubscriptionStatus(
            has_subscription=True,
            plan="starter",
            status="active",
            current_period_end="2026-05-20T00:00:00+00:00",
            cancel_at_period_end=False,
            stripe_customer_id="cus_123",
        )
        assert status.plan == "starter"
        assert status.stripe_customer_id == "cus_123"
