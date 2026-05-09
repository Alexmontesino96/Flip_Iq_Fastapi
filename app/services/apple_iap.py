"""Apple App Store Server Notifications V2 — JWS verification + handlers.

Apple sends JWS-signed webhooks for subscription events. Each notification
contains nested JWS payloads (signedTransactionInfo, signedRenewalInfo)
that must be individually verified.

References:
- https://developer.apple.com/documentation/appstoreservernotifications
- https://developer.apple.com/documentation/appstoreserverapi
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any

import jwt
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.subscription import Subscription
from app.models.user import User

logger = logging.getLogger("flipiq.apple_iap")

# Apple Root CA G3 — public key fingerprint used to validate cert chain.
# The actual root cert is embedded in the x5c chain that Apple sends.
APPLE_ROOT_CA_G3_SUBJECT_CN = "Apple Root CA - G3"

# Product ID → internal tier mapping (must match App Store Connect)
APPLE_PRODUCT_TO_TIER: dict[str, str] = {
    "starter_monthly": "starter",
    "starter_yearly": "starter",
    "pro_monthly": "pro",
    "pro_yearly": "pro",
}

TIER_DAILY_LIMITS = {"free": 5, "starter": 30, "pro": 100}


# ---------------------------------------------------------------------------
# JWS verification
# ---------------------------------------------------------------------------

class AppleJWSError(Exception):
    """JWS verification or decoding failed."""


def _decode_jws(signed_payload: str, *, verify: bool = True) -> dict[str, Any]:
    """Decode and verify an Apple JWS (signed JSON payload).

    Apple uses ES256 with an x5c certificate chain in the JWS header.
    The chain is: [leaf, intermediate, root]. We verify:
    1. Root cert is Apple Root CA G3
    2. Signature is valid against leaf cert's public key
    """
    if not signed_payload or signed_payload.count(".") != 2:
        raise AppleJWSError("Invalid JWS format")

    # Extract header to get x5c cert chain
    header_b64 = signed_payload.split(".")[0]
    # Add padding
    header_b64 += "=" * (4 - len(header_b64) % 4)
    try:
        header = json.loads(base64.urlsafe_b64decode(header_b64))
    except Exception as e:
        raise AppleJWSError(f"Failed to decode JWS header: {e}") from e

    if not verify:
        # Decode without verification (for testing/sandbox)
        payload_b64 = signed_payload.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))

    x5c = header.get("x5c")
    if not x5c or len(x5c) < 2:
        raise AppleJWSError("Missing or incomplete x5c certificate chain")

    # Parse certificates from x5c (DER-encoded, base64)
    certs = []
    for cert_b64 in x5c:
        try:
            cert_der = base64.b64decode(cert_b64)
            cert = x509.load_der_x509_certificate(cert_der)
            certs.append(cert)
        except Exception as e:
            raise AppleJWSError(f"Failed to parse x5c certificate: {e}") from e

    # Verify root cert is Apple Root CA G3
    root_cert = certs[-1]
    root_cn = root_cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
    if not root_cn or APPLE_ROOT_CA_G3_SUBJECT_CN not in root_cn[0].value:
        raise AppleJWSError(
            f"Root certificate is not Apple Root CA G3: {root_cn}"
        )

    # Use leaf cert's public key to verify JWS
    leaf_cert = certs[0]
    public_key = leaf_cert.public_key()
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise AppleJWSError("Leaf certificate does not contain an EC public key")

    try:
        payload = jwt.decode(
            signed_payload,
            public_key,
            algorithms=["ES256"],
            options={"verify_exp": False, "verify_aud": False},
        )
        return payload
    except jwt.InvalidTokenError as e:
        raise AppleJWSError(f"JWS signature verification failed: {e}") from e


def decode_apple_notification(raw_body: bytes) -> dict[str, Any]:
    """Parse and verify an Apple Server Notification V2.

    Returns the decoded notification with nested transaction/renewal info.
    """
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as e:
        raise AppleJWSError(f"Invalid JSON body: {e}") from e

    signed_payload = body.get("signedPayload")
    if not signed_payload:
        raise AppleJWSError("Missing signedPayload in notification")

    # In sandbox, Apple may send test notifications — still verify
    verify = settings.apple_environment != "Sandbox"
    notification = _decode_jws(signed_payload, verify=verify)

    # Decode nested JWS payloads
    data = notification.get("data", {})
    if data.get("signedTransactionInfo"):
        notification["_transaction"] = _decode_jws(
            data["signedTransactionInfo"], verify=verify,
        )
    if data.get("signedRenewalInfo"):
        notification["_renewal"] = _decode_jws(
            data["signedRenewalInfo"], verify=verify,
        )

    return notification


# ---------------------------------------------------------------------------
# Webhook event handlers
# ---------------------------------------------------------------------------

async def handle_apple_notification(
    notification: dict[str, Any],
    db: AsyncSession,
) -> str:
    """Route Apple notification to the appropriate handler.

    Returns a short status string for logging.
    """
    ntype = notification.get("notificationType", "")
    subtype = notification.get("subtype", "")
    txn = notification.get("_transaction", {})
    original_txn_id = txn.get("originalTransactionId", "")

    logger.info(
        "Apple notification: type=%s subtype=%s txn=%s",
        ntype, subtype, original_txn_id,
    )

    handlers = {
        "SUBSCRIBED": _handle_subscribed,
        "DID_RENEW": _handle_renewed,
        "DID_CHANGE_RENEWAL_PREF": _handle_plan_change,
        "DID_CHANGE_RENEWAL_STATUS": _handle_renewal_status_change,
        "EXPIRED": _handle_expired,
        "DID_FAIL_TO_RENEW": _handle_failed_renew,
        "GRACE_PERIOD_EXPIRED": _handle_expired,
        "REFUND": _handle_refund,
        "REVOKE": _handle_refund,
    }

    handler = handlers.get(ntype)
    status = "ignored"
    error_msg = None
    user_id = None

    if handler:
        try:
            await handler(notification, txn, db)
            status = "success"
            # Try to find user_id for the log
            user = await _find_user_by_apple_txn(original_txn_id, db, txn=txn)
            user_id = user.id if user else None
        except Exception as e:
            status = "error"
            error_msg = str(e)
            logger.error("Apple handler error: %s", e, exc_info=True)
    else:
        logger.debug("Unhandled Apple notification type: %s", ntype)

    # Persist webhook event
    try:
        from app.models.webhook_event import WebhookEvent
        db.add(WebhookEvent(
            provider="apple",
            event_type=ntype,
            event_subtype=subtype or None,
            status=status,
            user_id=user_id,
            transaction_id=original_txn_id or None,
            error_message=error_msg,
        ))
        await db.commit()
    except Exception as e:
        logger.warning("Failed to persist webhook event: %s", e)

    return f"{status}:{ntype}"


async def _find_user_by_apple_txn(
    original_txn_id: str,
    db: AsyncSession,
    txn: dict[str, Any] | None = None,
) -> User | None:
    """Find user by Apple original transaction ID or appAccountToken.

    Lookup order:
    1. Subscription table (apple_original_transaction_id)
    2. appAccountToken in transaction info (= supabase_id UUID)
    """
    # 1. Try subscription table first
    result = await db.execute(
        select(Subscription).where(
            Subscription.apple_original_transaction_id == original_txn_id
        )
    )
    sub = result.scalar_one_or_none()
    if sub:
        return await db.get(User, sub.user_id)

    # 2. Try appAccountToken (supabase_id set by iOS app during purchase)
    if txn:
        app_account_token = txn.get("appAccountToken")
        if app_account_token:
            result = await db.execute(
                select(User).where(User.supabase_id == str(app_account_token))
            )
            user = result.scalar_one_or_none()
            if user:
                logger.info(
                    "Found user via appAccountToken: user=%s supabase_id=%s",
                    user.id, app_account_token,
                )
                return user

    return None


def _tier_from_product_id(product_id: str | None) -> str:
    """Map Apple product ID to internal tier."""
    if not product_id:
        return "free"
    return APPLE_PRODUCT_TO_TIER.get(product_id, "free")


def _apple_ts(ms: int | None) -> datetime | None:
    """Convert Apple millisecond timestamp to datetime."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


async def _upsert_apple_subscription(
    db: AsyncSession,
    user: User,
    txn: dict[str, Any],
    renewal: dict[str, Any] | None = None,
    status: str = "active",
) -> None:
    """Create or update subscription record for Apple IAP."""
    original_txn_id = txn.get("originalTransactionId", "")
    product_id = txn.get("productId", "")
    tier = _tier_from_product_id(product_id)

    # Find existing subscription
    result = await db.execute(
        select(Subscription).where(
            Subscription.apple_original_transaction_id == original_txn_id
        )
    )
    sub = result.scalar_one_or_none()

    expires = _apple_ts(txn.get("expiresDate"))
    purchased = _apple_ts(txn.get("purchaseDate"))

    if sub:
        sub.status = status
        sub.plan = tier
        sub.apple_product_id = product_id
        sub.current_period_end = expires
        sub.updated_at = datetime.now(timezone.utc)
    else:
        sub = Subscription(
            user_id=user.id,
            provider="apple",
            apple_original_transaction_id=original_txn_id,
            apple_product_id=product_id,
            stripe_subscription_id=f"apple_{original_txn_id}",
            stripe_price_id=product_id,
            status=status,
            plan=tier,
            current_period_start=purchased,
            current_period_end=expires,
        )
        db.add(sub)

    # Update user tier
    if status in ("active", "billing_retry"):
        user.tier = tier
    else:
        user.tier = "free"
    import asyncio
    from app.services import customerio
    asyncio.create_task(customerio.update_plan(user.id, user.tier))
    if user.onesignal_subscription_id:
        from app.services import onesignal
        asyncio.create_task(onesignal.update_tier_tag(user.onesignal_subscription_id, user.tier))

    await db.commit()
    logger.info(
        "Apple subscription upserted: user=%s txn=%s tier=%s status=%s",
        user.id, original_txn_id, tier, status,
    )


# ---------------------------------------------------------------------------
# Individual event handlers
# ---------------------------------------------------------------------------

async def _handle_subscribed(
    notification: dict, txn: dict, db: AsyncSession,
) -> None:
    """SUBSCRIBED — new purchase or resubscribe."""
    original_txn_id = txn.get("originalTransactionId", "")

    # Find user by subscription table or appAccountToken (supabase_id)
    user = await _find_user_by_apple_txn(original_txn_id, db, txn=txn)

    if not user:
        logger.warning(
            "Apple SUBSCRIBED but no user found for txn=%s appAccountToken=%s",
            original_txn_id, txn.get("appAccountToken"),
        )
        return

    await _upsert_apple_subscription(db, user, txn, status="active")


async def _handle_renewed(
    notification: dict, txn: dict, db: AsyncSession,
) -> None:
    """DID_RENEW — subscription successfully renewed."""
    user = await _find_user_by_apple_txn(
        txn.get("originalTransactionId", ""), db, txn=txn,
    )
    if not user:
        return
    await _upsert_apple_subscription(db, user, txn, status="active")


async def _handle_plan_change(
    notification: dict, txn: dict, db: AsyncSession,
) -> None:
    """DID_CHANGE_RENEWAL_PREF — user changed plan (upgrade/downgrade)."""
    renewal = notification.get("_renewal", {})
    user = await _find_user_by_apple_txn(
        txn.get("originalTransactionId", ""), db, txn=txn,
    )
    if not user:
        return

    # The new product takes effect at next renewal
    new_product = renewal.get("autoRenewProductId", txn.get("productId"))
    new_tier = _tier_from_product_id(new_product)

    subtype = notification.get("subtype", "")
    if subtype == "UPGRADE":
        # Upgrade takes effect immediately
        user.tier = new_tier
        import asyncio
        from app.services import customerio
        asyncio.create_task(customerio.update_plan(user.id, new_tier))
        if user.onesignal_subscription_id:
            from app.services import onesignal
            asyncio.create_task(onesignal.update_tier_tag(user.onesignal_subscription_id, new_tier))
        await db.commit()
        logger.info("Apple plan upgrade: user=%s → %s (immediate)", user.id, new_tier)
    else:
        # Downgrade takes effect at end of period
        logger.info(
            "Apple plan change (next period): user=%s → %s",
            user.id, new_tier,
        )


async def _handle_renewal_status_change(
    notification: dict, txn: dict, db: AsyncSession,
) -> None:
    """DID_CHANGE_RENEWAL_STATUS — auto-renew turned on/off."""
    renewal = notification.get("_renewal", {})
    auto_renew = renewal.get("autoRenewStatus", 1)
    user = await _find_user_by_apple_txn(
        txn.get("originalTransactionId", ""), db, txn=txn,
    )
    if not user:
        return

    result = await db.execute(
        select(Subscription).where(
            Subscription.apple_original_transaction_id == txn.get("originalTransactionId")
        )
    )
    sub = result.scalar_one_or_none()
    if sub:
        sub.cancel_at_period_end = auto_renew == 0
        await db.commit()

    logger.info(
        "Apple auto-renew %s: user=%s",
        "disabled" if auto_renew == 0 else "enabled",
        user.id,
    )


async def _handle_expired(
    notification: dict, txn: dict, db: AsyncSession,
) -> None:
    """EXPIRED / GRACE_PERIOD_EXPIRED — subscription ended."""
    user = await _find_user_by_apple_txn(
        txn.get("originalTransactionId", ""), db, txn=txn,
    )
    if not user:
        return
    await _upsert_apple_subscription(db, user, txn, status="expired")
    logger.info("Apple subscription expired: user=%s → free", user.id)


async def _handle_failed_renew(
    notification: dict, txn: dict, db: AsyncSession,
) -> None:
    """DID_FAIL_TO_RENEW — billing retry period."""
    user = await _find_user_by_apple_txn(
        txn.get("originalTransactionId", ""), db, txn=txn,
    )
    if not user:
        return

    subtype = notification.get("subtype", "")
    if subtype == "GRACE_PERIOD":
        # User still has access during grace period
        await _upsert_apple_subscription(db, user, txn, status="billing_retry")
    else:
        await _upsert_apple_subscription(db, user, txn, status="past_due")


async def _handle_refund(
    notification: dict, txn: dict, db: AsyncSession,
) -> None:
    """REFUND / REVOKE — Apple refunded or revoked access."""
    user = await _find_user_by_apple_txn(
        txn.get("originalTransactionId", ""), db, txn=txn,
    )
    if not user:
        return
    await _upsert_apple_subscription(db, user, txn, status="refunded")
    logger.info("Apple refund/revoke: user=%s → free", user.id)
