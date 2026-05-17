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

    # Supabase Auth
    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    supabase_service_role_key: str = ""

    # Rate limiting
    rate_limit_per_minute: int = 60

    # eBay scraper
    ebay_data_source: str = "scraper"  # "scraper" (default) | "rpi" (proxy residencial)

    # RPi Scraper Proxy (pool de proxies residenciales)
    # Comma-separated URLs: "https://rpi1.tunnel.com,https://rpi2.tunnel.com"
    rpi_scraper_urls: str = ""
    rpi_scraper_api_key: str = ""

    # Proxy residencial para scraper directo (BrightData, IPRoyal, Smartproxy, etc.)
    # Formato: http://user:pass@host:port
    residential_proxy_url: str = ""

    # eBay Browse API
    ebay_app_id: str = ""
    ebay_cert_id: str = ""
    ebay_sandbox: bool = False
    ebay_oauth_token: str = ""  # OAuth User Token (preferred for Browse API)
    # eBay Webhook (marketplace account deletion)
    ebay_verification_token: str = ""
    ebay_webhook_endpoint: str = ""

    # Amazon SP-API
    amazon_refresh_token: str = ""
    amazon_lwa_client_id: str = ""
    amazon_lwa_client_secret: str = ""
    amazon_marketplace_id: str = "ATVPDKIKX0DER"

    # Keepa (https://keepa.com) — datos de Amazon
    keepa_api_key: str = ""

    # Apple App Store
    apple_bundle_id: str = ""              # e.g. "com.getflipiq.app"
    apple_shared_secret: str = ""          # App-specific shared secret
    apple_environment: str = "Production"  # "Production" | "Sandbox"

    # OneSignal (push notifications / Journeys)
    onesignal_app_id: str = ""
    onesignal_rest_api_key: str = ""

    # Customer.io (email / messaging)
    customerio_site_id: str = ""
    customerio_api_key: str = ""

    # Cron jobs
    cron_secret: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_starter: str = ""
    stripe_price_pro: str = ""

    # AI — Gemini preferido, OpenAI fallback
    gemini_api_key: str = ""
    openai_api_key: str = ""
    brave_search_api_key: str = ""

    # ML Models (local, reemplazan LLM para comp_relevance y title_enricher)
    ml_models_dir: str = "models"
    ml_comp_relevance_enabled: bool = False
    ml_condition_enabled: bool = False
    ml_shadow_mode: bool = True  # True = ejecuta ML + LLM y compara

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
            if not self.supabase_jwt_secret:
                raise ValueError(
                    "SUPABASE_JWT_SECRET is required in production. "
                    "Find it in Supabase Dashboard → Settings → API → JWT Secret."
                )
            if self.debug:
                warnings.warn("DEBUG=True en produccion. Considera desactivarlo.", stacklevel=2)


settings = Settings()
settings.validate_production()
