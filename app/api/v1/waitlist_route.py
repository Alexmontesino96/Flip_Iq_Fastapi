import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.limiter import TTL_30D, VERIFIED_LIMIT
from app.core.redis_client import get_redis
from app.database import get_db
from app.models.waitlist import WaitlistEntry

router = APIRouter()


class WaitlistRequest(BaseModel):
    email: EmailStr
    source: str | None = None


class WaitlistResponse(BaseModel):
    message: str
    email: str


class WaitlistStatus(BaseModel):
    verified: bool
    email: str | None = None
    remaining: int = 0


async def _set_verification_cookie(
    response: JSONResponse, email: str, redis
) -> str:
    """Generate token, store in Redis, set cookie. Returns token."""
    token = uuid.uuid4().hex
    if redis:
        await redis.set(f"email_token:{token}", email, ex=TTL_30D)
    is_prod = settings.environment == "production"
    response.set_cookie(
        key="flipiq_verified",
        value=token,
        max_age=TTL_30D,
        httponly=True,
        samesite="none" if is_prod else "lax",
        secure=is_prod,
    )
    return token


@router.post("/", status_code=status.HTTP_201_CREATED)
async def join_waitlist(
    payload: WaitlistRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    result = await db.execute(
        select(WaitlistEntry).where(WaitlistEntry.email == payload.email)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Re-verify: generate new token + cookie for returning user
        response = JSONResponse(
            status_code=200,
            content={"message": "Welcome back! You're verified.", "email": payload.email},
        )
        await _set_verification_cookie(response, payload.email, redis)
        return response

    entry = WaitlistEntry(email=payload.email, source=payload.source)
    db.add(entry)
    await db.commit()

    response = JSONResponse(
        status_code=201,
        content={"message": "You're on the list!", "email": payload.email},
    )
    await _set_verification_cookie(response, payload.email, redis)
    return response


@router.get("/status", response_model=WaitlistStatus)
async def waitlist_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    email: str | None = None

    # 1. Cookie (same-domain)
    if redis:
        token = request.cookies.get("flipiq_verified")
        if token:
            email = await redis.get(f"email_token:{token}")

    # 2. Fallback: X-Verified-Email header (cross-domain)
    if not email:
        header_email = request.headers.get("x-verified-email", "").strip().lower()
        if header_email:
            result = await db.execute(
                select(WaitlistEntry.id).where(WaitlistEntry.email == header_email).limit(1)
            )
            if result.scalar_one_or_none() is not None:
                email = header_email

    if not email:
        return WaitlistStatus(verified=False)

    count = 0
    if redis:
        count = int(await redis.get(f"verified:{email}") or 0)
    return WaitlistStatus(
        verified=True,
        email=email,
        remaining=max(VERIFIED_LIMIT - count, 0),
    )
