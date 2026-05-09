import logging

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# JWKS client para tokens ES256 de Supabase (cachea las claves automáticamente)
_jwks_client: jwt.PyJWKClient | None = None


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def _decode_token(token: str) -> dict:
    """Decodifica un JWT de Supabase (soporta HS256 y ES256)."""
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")

    if alg == "HS256":
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )

    # ES256 — obtener clave pública del JWKS de Supabase
    signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
    )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = _decode_token(token)
    except jwt.PyJWTError as e:
        logger.warning("JWT decode failed: %s (token: %s...)", e, token[:30] if token else "")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supabase_id = payload.get("sub")
    if not supabase_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await db.execute(select(User).where(User.supabase_id == supabase_id))
    user = result.scalar_one_or_none()

    if user is None:
        email = payload.get("email", "")
        try:
            user = User(supabase_id=supabase_id, email=email)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            logger.info("Auto-created user: id=%s email=%s", user.id, email)
            import asyncio
            from app.services import customerio
            asyncio.create_task(customerio.track_signup(user))
            if user.onesignal_subscription_id:
                from app.services import onesignal
                asyncio.create_task(onesignal.tag_new_user(user.onesignal_subscription_id, user))
        except Exception as e:
            await db.rollback()
            logger.warning("User creation failed, retrying lookup: %s", e)
            # Race condition: another request created the user first
            result = await db.execute(
                select(User).where(User.supabase_id == supabase_id)
            )
            user = result.scalar_one_or_none()
            if user is None:
                logger.error(
                    "User creation failed and no user found for supabase_id=%s email=%s: %s",
                    supabase_id, email, e,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create user account",
                )

    # Sync to Customer.io on first authenticated request
    if user and not user.customerio_synced:
        import asyncio
        from app.services import customerio as _cio

        async def _sync_cio(u, db_session):
            await _cio.track_signup(u)
            u.customerio_synced = True
            try:
                await db_session.commit()
            except Exception:
                pass

        asyncio.create_task(_sync_cio(user, db))

    return user


async def get_current_user_optional(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Resolve user from JWT. Returns None ONLY if no token is present.

    If a token IS present but user resolution fails, this still raises
    to prevent analyses from being saved without a user_id.
    """
    if not token:
        return None
    try:
        return await get_current_user(token=token, db=db)
    except HTTPException as e:
        if e.status_code == 401:
            logger.warning("Auth optional: invalid token (%s)", e.detail)
            return None
        # 500 errors (user creation failed) should propagate
        raise
