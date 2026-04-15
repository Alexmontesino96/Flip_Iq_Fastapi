import warnings

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "FlipIQ"
    environment: str = "development"  # development | staging | production
    debug: bool = True

    # CORS
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://flip-iq-front.vercel.app",
        "https://www.getflipiq.com",
    ]

    # Database
    database_url: str = "postgresql+asyncpg://flipiq:flipiq@localhost:5432/flipiq"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    secret_key: str = "change-me-to-a-random-secret"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Rate limiting
    rate_limit_per_minute: int = 60

    # Apify
    apify_token: str = ""

    # eBay scraper
    ebay_data_source: str = "apify"  # "apify" (default) | "scraper" (solo local) | "rpi" (proxy residencial)

    # RPi Scraper Proxy (pool de proxies residenciales)
    # Comma-separated URLs: "https://rpi1.tunnel.com,https://rpi2.tunnel.com"
    rpi_scraper_urls: str = ""
    rpi_scraper_api_key: str = ""

    # Proxy residencial para scraper directo (BrightData, IPRoyal, Smartproxy, etc.)
    # Formato: http://user:pass@host:port
    residential_proxy_url: str = ""

    # eBay (legacy — no se usan)
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    ebay_sandbox: bool = True

    # Amazon SP-API
    amazon_refresh_token: str = ""
    amazon_lwa_client_id: str = ""
    amazon_lwa_client_secret: str = ""
    amazon_marketplace_id: str = "ATVPDKIKX0DER"

    # Keepa (https://keepa.com) — datos de Amazon
    keepa_api_key: str = ""

    # AI — Gemini preferido, OpenAI fallback
    gemini_api_key: str = ""
    openai_api_key: str = ""
    brave_search_api_key: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _fix_database_url(self) -> "Settings":
        url = self.database_url
        if url.startswith("postgres://"):
            self.database_url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            self.database_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self

    def validate_production(self) -> None:
        if self.environment == "production":
            if self.secret_key == "change-me-to-a-random-secret":
                raise ValueError(
                    "SECRET_KEY no puede ser el valor por defecto en produccion. "
                    "Genera uno con: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
                )
            if self.debug:
                warnings.warn("DEBUG=True en produccion. Considera desactivarlo.", stacklevel=2)


settings = Settings()
settings.validate_production()
