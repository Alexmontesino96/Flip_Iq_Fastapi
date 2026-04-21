from fastapi import APIRouter

from app.api.v1 import auth, products, analysis, watchlists, waitlist_route, search, ebay_webhook, billing

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(products.router, prefix="/products", tags=["products"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(watchlists.router, prefix="/watchlists", tags=["watchlists"])
api_router.include_router(waitlist_route.router, prefix="/waitlist", tags=["waitlist"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(ebay_webhook.router, prefix="/ebay/webhook", tags=["ebay-webhook"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
