from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.limiter import check_analysis_gate, increment_analysis_counter
from app.core.redis_client import get_redis
from app.database import get_db
from app.models.analysis import Analysis
from app.models.product import Product
from app.schemas.analysis import AnalysisRequest, AnalysisResponse, AnalysisHistory
from app.services.analysis_service import run_analysis

router = APIRouter()


@router.post("/", response_model=AnalysisResponse)
async def analyze_product(
    request: Request,
    response: Response,
    payload: AnalysisRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    # Soft gate check
    gate = await check_analysis_gate(request, redis, db)
    if not gate.allowed:
        return JSONResponse(
            status_code=403,
            content={
                "reason": "free_limit_reached",
                "tier": gate.tier,
                "remaining": gate.remaining,
                "reset_in_seconds": gate.reset_in,
            },
        )

    result = await run_analysis(
        db=db,
        barcode=payload.barcode,
        keyword=payload.keyword,
        cost_price=payload.cost_price,
        marketplace=payload.marketplace,
        shipping_cost=payload.shipping_cost,
        packaging_cost=payload.packaging_cost,
        prep_cost=payload.prep_cost,
        promo_cost=payload.promo_cost,
        return_reserve_pct=payload.return_reserve_pct,
        target_profit=payload.target_profit,
        target_roi=payload.target_roi,
        detailed=payload.detailed,
        condition=payload.condition,
        mode=payload.mode,
        product_type=payload.product_type,
    )

    # Increment counter after successful analysis
    await increment_analysis_counter(request, redis, gate)

    # Rate-limit headers on the normal response
    remaining = max(gate.remaining - 1, 0)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Tier"] = gate.tier
    return result


@router.get("/history", response_model=list[AnalysisHistory])
async def get_analysis_history(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Analysis, Product.title)
        .join(Product)
        .order_by(Analysis.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()
    return [
        AnalysisHistory(
            id=a.id,
            product_title=title,
            cost_price=float(a.cost_price),
            net_profit=float(a.net_profit) if a.net_profit else None,
            flip_score=a.flip_score,
            recommendation=a.recommendation,
            marketplace=a.marketplace,
            created_at=a.created_at,
        )
        for a, title in rows
    ]
