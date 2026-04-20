"""eBay Marketplace Account Deletion/Closure notification endpoint.

Required by eBay Developer Program. Handles:
1. GET  — challenge verification (eBay sends challenge_code, we respond with hash)
2. POST — actual deletion/closure notification
"""

from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.config import settings

logger = logging.getLogger("flipiq.ebay_webhook")

router = APIRouter()


def _compute_challenge_response(challenge_code: str) -> str:
    """Hash = SHA-256(challenge_code + verification_token + endpoint_url)."""
    endpoint = settings.ebay_webhook_endpoint
    token = settings.ebay_verification_token
    raw = f"{challenge_code}{token}{endpoint}"
    return hashlib.sha256(raw.encode()).hexdigest()


@router.get("/account-deletion")
async def verify_challenge(
    challenge_code: str = Query(..., alias="challenge_code"),
):
    """eBay sends a GET to verify we own this endpoint."""
    response_hash = _compute_challenge_response(challenge_code)
    return JSONResponse(
        content={"challengeResponse": response_hash},
        headers={"Content-Type": "application/json"},
    )


@router.post("/account-deletion")
async def account_deletion_notification(request: Request):
    """eBay notifies us when a user requests account deletion.

    Per eBay policy we must delete/anonymize any stored user data.
    Currently we don't store eBay user data, so we just acknowledge.
    """
    body = await request.json()
    logger.info(
        "eBay account deletion notification received: topic=%s",
        body.get("metadata", {}).get("topic", "unknown"),
    )
    # TODO: si en el futuro guardamos datos de usuarios de eBay,
    # aquí se procesan las eliminaciones.
    return JSONResponse(status_code=200, content={"status": "ok"})
