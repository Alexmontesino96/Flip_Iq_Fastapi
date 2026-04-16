"""Rate limiting shared instance — avoids circular imports."""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings


def _analysis_key(request: Request) -> str:
    """Key by user token (auth) or IP (anon). Each pool has separate counters."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 20:
        return f"auth:{hash(auth)}"
    return get_remote_address(request)


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)
