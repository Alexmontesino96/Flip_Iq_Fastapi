# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is FlipIQ

SaaS para resellers: evalúa productos antes de comprarlos para revender. Calcula margen neto, riesgo, velocidad de venta y canales recomendados. Backend API-first con FastAPI.

## Commands

### Run the app
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker (app + PostgreSQL + Redis)
```bash
docker-compose up
# Postgres en 5433, Redis en 6379, App en 8000
```

### Tests
```bash
pytest tests/ -k "not integration"          # All unit tests (142+)
pytest tests/test_comp_cleaner.py -v         # Single file
pytest tests/test_fees.py::test_name -v      # Single test
pytest tests/test_ebay_client.py -v -m integration  # Integration (needs APIFY_TOKEN)
```

### Database migrations
```bash
alembic upgrade head                              # Apply all
alembic revision --autogenerate -m "description"  # Create new
alembic downgrade -1                              # Rollback one
```

### Profiling
```bash
python -m scripts.profile_analysis
```

## Architecture

### Analysis Pipeline (core of the app)

`app/services/analysis_service.py::run_analysis()` orchestrates 13 specialized engines in sequence:

```
Apify (eBay sold comps, 50 items)
  → LLM Title Enricher (Gemini 2.5 Flash / GPT-4o-mini fallback / regex fallback)
    → Motor A: Comp Cleaner (temporal filter, outliers, relevance, bundle normalization)
      → Motors B-K: Pricing → Profit → Max Buy → Velocity → Risk → Confidence
                     → Seller Premium → Competition → Trend → Listing Strategy
        → Motor L: AI Explanation (parallel)
        → Motor M: Market Intelligence (premium only, parallel with L)
          → Decision: _decide() + _validate_buy() → buy|buy_small|watch|pass
```

### Key architectural patterns

- **Fully async**: httpx, asyncpg, SQLAlchemy async, Alembic async. No threads.
- **LLM fallback chain**: Gemini → OpenAI → regex. Controlled in `app/core/llm.py` with per-request disable flag.
- **CleanedComps dataclass** (`marketplace/base.py`): immutable output from comp_cleaner, consumed by all downstream engines.
- **Validation gate**: `_validate_buy()` degrades recommendations (buy→buy_small→watch→pass) based on confidence, title risk, condition mismatch, headroom.
- **engines_data JSON blob**: every analysis persists all engine outputs for audit/ML.
- **Marketplace calculators** in `app/core/fees.py` are pluggable (eBay 13.25%, Amazon FBA 15%+$3.50, MercadoLibre 16%, Facebook 5%).

### Data flow

- **Data source**: Apify actor `caffein.dev~ebay-sold-listings` (POST run-sync-get-dataset-items). Returns soldPrice, shippingPrice, totalPrice, endedAt, sellerUsername, condition, bids as strings/ISO 8601.
- **UPC lookup**: upcitemdb.com free API for barcode→product info.
- **DB**: PostgreSQL with models User, Product, Analysis, Watchlist/WatchlistItem. DB failures are tolerated (analysis returns result without persistence).

### Module layout

- `app/api/v1/` — FastAPI routes (auth, analysis, products, watchlists)
- `app/core/` — fees.py (marketplace calculators), security.py (JWT), llm.py (Gemini/OpenAI client)
- `app/models/` — SQLAlchemy models
- `app/schemas/` — Pydantic request/response schemas
- `app/services/engines/` — 13 specialized analysis engines (each is a pure function or async function)
- `app/services/marketplace/` — base.py (MarketplaceListing, CompsResult, CleanedComps), ebay.py (Apify client), amazon.py (placeholder)

## Environment Variables

Required in `.env` (see `.env.example`):
- `DATABASE_URL` — PostgreSQL async connection string
- `SECRET_KEY` — JWT signing key
- `APIFY_TOKEN` — eBay sold data via Apify
- `GEMINI_API_KEY` or `OPENAI_API_KEY` — at least one for LLM features (both optional, regex fallback exists)

Optional:
- `REDIS_URL`, `BRAVE_SEARCH_API_KEY`, `EBAY_APP_ID`, `AMAZON_*` credentials

## Decision Logic

```
buy:       opportunity >= 60, profit > 0, risk >= 40, confidence >= 30
buy_small: opportunity >= 45, profit > 0, ROI > 20%, risk >= 30
watch:     opportunity >= 35 or ROI > 10%
pass:      everything else
```

Validator degrades buy→buy_small (never watch) for: low confidence, high title risk, few comps.
