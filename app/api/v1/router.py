from fastapi import APIRouter

from app.api.v1 import auth, products, analysis, watchlists

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(products.router, prefix="/products", tags=["products"])
api_router.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(watchlists.router, prefix="/watchlists", tags=["watchlists"])
