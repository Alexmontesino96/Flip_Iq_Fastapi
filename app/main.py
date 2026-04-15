from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.v1.router import api_router
from app.config import settings


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Demasiadas solicitudes. Intenta de nuevo en un momento."},
    )


limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.rate_limit_per_minute}/minute"])


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

    yield
    # Shutdown
    from app.database import engine

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
