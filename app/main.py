import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.core.limiter import limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_req_logger = logging.getLogger("flipiq.requests")


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        _req_logger.info(
            "%s %s %d %.0fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Try again later."},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — run migrations
    import asyncio
    import logging

    logger = logging.getLogger("flipiq.startup")
    try:
        proc = await asyncio.create_subprocess_exec(
            "alembic", "upgrade", "head",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            logger.info("Migraciones aplicadas correctamente")
        else:
            logger.error("Error en migraciones: %s", stderr.decode())
    except Exception as e:
        logger.error("No se pudieron ejecutar migraciones: %s", e)

    # Warm up Redis connection for soft gate
    from app.core.redis_client import get_redis, close_redis

    await get_redis()

    # Cargar modelos ML locales (si existen)
    from app.core.ml_models import load_models

    load_models()

    # Start internal scheduler for daily price updates
    from app.services.scheduler import start_scheduler

    scheduler_task = start_scheduler()

    yield
    # Shutdown
    if scheduler_task:
        scheduler_task.cancel()
    from app.database import engine

    await close_redis()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description="API para evaluar productos antes de comprarlos para revender. "
    "Calcula margen neto, riesgo, velocidad de venta y canales recomendados.",
    version="0.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

app.add_middleware(TimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    from sqlalchemy import text

    from app.database import async_session

    status = {"service": settings.app_name, "environment": settings.environment}
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        status["database"] = "ok"
    except Exception as e:
        status["database"] = f"error: {e}"

    status["status"] = "ok" if status["database"] == "ok" else "degraded"
    return status
