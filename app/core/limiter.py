"""Rate limiting shared instance — avoids circular imports."""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


def _analysis_key(request: Request) -> str:
    """Auth users get keyed by token hash (higher limit pool), others by IP."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 20:
        return f"auth:{hash(auth)}"
    return get_remote_address(request)


def _analysis_limit(request: Request) -> str:
    """100/hour for authenticated, 20/hour for anonymous."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 20:
        return "100/hour"
    return "20/hour"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)
