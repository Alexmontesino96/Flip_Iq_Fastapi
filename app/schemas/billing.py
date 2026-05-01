from datetime import datetime

from pydantic import BaseModel, HttpUrl


class CheckoutRequest(BaseModel):
    price_id: str
    success_url: str
    cancel_url: str


class PortalRequest(BaseModel):
    return_url: str


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class AppleIAPSyncRequest(BaseModel):
    product_id: str  # e.g. "starter_monthly", "pro_monthly"
    original_transaction_id: str
    action: str = "purchase"  # "purchase" or "cancel"


class SubscriptionStatus(BaseModel):
    has_subscription: bool
    plan: str  # free|starter|pro
    status: str | None = None  # active|past_due|canceled|trialing|unpaid
    current_period_end: str | None = None
    cancel_at_period_end: bool = False
    stripe_customer_id: str | None = None
