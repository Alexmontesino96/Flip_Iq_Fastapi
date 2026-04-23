import json
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.limiter import check_analysis_gate, increment_analysis_counter
from app.core.redis_client import get_redis
from app.core.security import get_current_user, get_current_user_optional
from app.database import async_session, get_db
from app.models.analysis import Analysis, AnalysisFeedback
from app.models.product import Product
from app.models.user import User
from app.schemas.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    AnalysisHistory,
    FeedbackRequest,
    FeedbackResponse,
    FlaggedItem,
    NotFoundItem,
)
from app.services.analysis_service import run_analysis, run_analysis_progressive

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/", response_model=AnalysisResponse)
async def analyze_product(
    request: Request,
    response: Response,
    payload: AnalysisRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    user: User | None = Depends(get_current_user_optional),
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

    # Resolve user_id ONCE and bind to this request
    current_user_id = user.id if user else None

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
        user_id=current_user_id,
    )

    # Increment counter after successful analysis
    await increment_analysis_counter(request, redis, gate)

    # Rate-limit headers on the normal response
    remaining = max(gate.remaining - 1, 0)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Tier"] = gate.tier
    return result


@router.post("/stream")
async def analyze_product_stream(
    request: Request,
    payload: AnalysisRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    user: User | None = Depends(get_current_user_optional),
):
    """SSE endpoint: envía progreso + 2 chunks para respuesta progresiva.

    NOTA: La sesión DB del generador se crea dentro del stream (no usa la
    inyectada por Depends) porque FastAPI cierra las dependencias cuando el
    handler retorna, pero StreamingResponse sigue corriendo después.
    """
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

    await increment_analysis_counter(request, redis, gate)

    # Capturar user_id ANTES del stream (Depends se cierra cuando el handler retorna)
    user_id = user.id if user else None

    async def event_stream():
        async with async_session() as stream_db:
            try:
                async for chunk in run_analysis_progressive(
                    db=stream_db,
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
                    user_id=user_id,
                ):
                    event = chunk["event"]
                    data = chunk["data"]
                    if hasattr(data, "model_dump_json"):
                        json_str = data.model_dump_json()
                    else:
                        json_str = json.dumps(data)
                    yield f"event: {event}\ndata: {json_str}\n\n"
                yield "event: done\ndata: {}\n\n"
            except Exception as e:
                logger.exception("SSE stream error: %s", e)
                error_data = json.dumps({"error": str(e)})
                yield f"event: error\ndata: {error_data}\n\n"

    remaining = max(gate.remaining - 1, 0)
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Tier": gate.tier,
        },
    )


@router.get("/history", response_model=list[AnalysisHistory])
async def get_analysis_history(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        select(Analysis, Product.title)
        .join(Product)
        .where(Analysis.user_id == user.id)
        .order_by(Analysis.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()
    return [
        AnalysisHistory(
            id=a.id,
            product_id=a.product_id,
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


@router.get("/not-found", response_model=list[NotFoundItem])
async def list_not_found(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Lista análisis donde no se encontraron comps de mercado."""
    query = (
        select(Analysis, Product)
        .join(Product)
        .where(Analysis.user_id == user.id, Analysis.no_comps_found.is_(True))
        .order_by(Analysis.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()
    engines = None
    return [
        NotFoundItem(
            id=a.id,
            product_title=p.title,
            barcode=p.barcode,
            keyword=(a.engines_data or {}).get("data_quality", {}).get("scraper", {}).get("query_used"),
            marketplace=a.marketplace,
            cost_price=float(a.cost_price),
            created_at=a.created_at,
        )
        for a, p in rows
    ]


@router.get("/flagged", response_model=list[FlaggedItem])
async def list_flagged(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Lista análisis que el usuario marcó como incorrectos."""
    query = (
        select(AnalysisFeedback, Analysis, Product)
        .join(Analysis, AnalysisFeedback.analysis_id == Analysis.id)
        .join(Product, Analysis.product_id == Product.id)
        .where(AnalysisFeedback.user_id == user.id)
        .order_by(AnalysisFeedback.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()
    return [
        FlaggedItem(
            analysis_id=f.analysis_id,
            product_title=p.title,
            marketplace=a.marketplace,
            recommendation=a.recommendation,
            flip_score=a.flip_score,
            net_profit=float(a.net_profit) if a.net_profit else None,
            feedback_type=f.feedback_type,
            comment=f.comment,
            actual_sale_price=float(f.actual_sale_price) if f.actual_sale_price else None,
            flagged_at=f.created_at,
        )
        for f, a, p in rows
    ]


@router.get("/share/{share_token}")
async def get_shared_analysis(
    share_token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — view a shared analysis without authentication."""
    query = select(Analysis).where(Analysis.share_token == share_token)
    result = await db.execute(query)
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Shared analysis not found")

    product = await db.get(Product, a.product_id)

    engines = a.engines_data or {}
    pricing = engines.get("pricing", {})
    max_buy = engines.get("max_buy", {})
    velocity = engines.get("velocity", {})
    risk = engines.get("risk", {})
    confidence = engines.get("confidence", {})

    summary = None
    if pricing:
        cost = float(a.cost_price)
        net = float(a.net_profit) if a.net_profit else 0
        sale = float(a.estimated_sale_price) if a.estimated_sale_price else 0
        max_buy_price = max_buy.get("recommended_max", 0)
        stretch = pricing.get("stretch_list") if pricing.get("stretch_allowed", False) else None
        summary = {
            "recommendation": a.recommendation or "pass",
            "signal": "positive" if a.recommendation in ("buy", "buy_small") else "neutral",
            "buy_box": {
                "recommended_max_buy": max_buy_price,
                "your_cost": cost,
                "headroom": max_buy_price - cost,
            },
            "sale_plan": {
                "recommended_list_price": pricing.get("market_list", sale),
                "quick_sale_price": pricing.get("quick_list", 0),
                "stretch_price": stretch,
            },
            "returns": {
                "profit": net,
                "roi_pct": float(a.roi_pct) if a.roi_pct else 0,
                "margin_pct": float(a.margin_pct) if a.margin_pct else 0,
            },
            "risk": risk.get("category", "medium"),
            "confidence": confidence.get("category", "medium"),
        }

    cleaned = engines.get("cleaned_comps", {})
    ebay_analysis = None
    if cleaned:
        ebay_analysis = {
            "marketplace": "ebay",
            "comps": {
                "total_sold": cleaned.get("clean_total", 0),
                "median_price": cleaned.get("median_price", 0),
                "p25": cleaned.get("p25", 0),
                "p75": cleaned.get("p75", 0),
                "sales_per_day": velocity.get("sales_per_day", 0),
                "days_of_data": cleaned.get("days_of_data", 0),
            },
            "velocity": {"score": velocity.get("score"), "category": velocity.get("category")},
            "confidence": {"score": confidence.get("score"), "category": confidence.get("category")},
        }

    return {
        "id": a.id,
        "product": {
            "id": product.id,
            "title": product.title,
            "brand": product.brand,
            "image_url": product.image_url,
        } if product else None,
        "cost_price": float(a.cost_price),
        "marketplace": a.marketplace,
        "estimated_sale_price": float(a.estimated_sale_price) if a.estimated_sale_price else None,
        "net_profit": float(a.net_profit) if a.net_profit else None,
        "roi_pct": float(a.roi_pct) if a.roi_pct else None,
        "flip_score": a.flip_score,
        "risk_score": a.risk_score,
        "velocity_score": a.velocity_score,
        "recommendation": a.recommendation,
        "channels": a.channels,
        "summary": summary,
        "ai_explanation": a.ai_explanation,
        "ebay_analysis": ebay_analysis,
        "created_at": a.created_at.isoformat(),
    }


@router.post("/{analysis_id}/share")
async def share_analysis(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a shareable link for an analysis."""
    result = await db.execute(
        select(Analysis).where(
            Analysis.id == analysis_id,
            Analysis.user_id == user.id,
        )
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if not a.share_token:
        a.share_token = secrets.token_urlsafe(16)
        await db.commit()

    frontend_url = settings.cors_origins[0] if settings.cors_origins else "https://www.getflipiq.com"
    # Prefer the production frontend URL
    for origin in settings.cors_origins:
        if "getflipiq" in origin or "vercel" in origin:
            frontend_url = origin
            break

    return {
        "share_token": a.share_token,
        "share_url": f"{frontend_url}/shared/{a.share_token}",
    }


@router.get("/shared/{share_token}")
async def get_shared_analysis(
    share_token: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint to view a shared analysis (no auth required)."""
    result = await db.execute(
        select(Analysis).where(Analysis.share_token == share_token)
    )
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Shared analysis not found")

    product = await db.get(Product, a.product_id) if a.product_id else None

    return {
        "id": a.id,
        "product": {
            "title": product.title,
            "brand": product.brand,
            "image_url": product.image_url,
        } if product else None,
        "cost_price": float(a.cost_price),
        "marketplace": a.marketplace,
        "estimated_sale_price": float(a.estimated_sale_price) if a.estimated_sale_price else None,
        "net_profit": float(a.net_profit) if a.net_profit else None,
        "roi_pct": float(a.roi_pct) if a.roi_pct else None,
        "flip_score": a.flip_score,
        "recommendation": a.recommendation,
        "ai_explanation": a.ai_explanation,
        "created_at": a.created_at.isoformat(),
    }


@router.post("/{analysis_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    analysis_id: int,
    payload: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reporta un análisis como incorrecto o impreciso."""
    # Verificar que el análisis existe y pertenece al usuario
    query = select(Analysis).where(
        Analysis.id == analysis_id,
        Analysis.user_id == user.id,
    )
    result = await db.execute(query)
    analysis = result.scalar_one_or_none()
    if analysis is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # Verificar que no haya feedback duplicado del mismo tipo
    existing = await db.execute(
        select(AnalysisFeedback).where(
            AnalysisFeedback.analysis_id == analysis_id,
            AnalysisFeedback.user_id == user.id,
            AnalysisFeedback.feedback_type == payload.feedback_type,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Feedback of type '{payload.feedback_type}' already submitted for this analysis",
        )

    feedback = AnalysisFeedback(
        analysis_id=analysis_id,
        user_id=user.id,
        feedback_type=payload.feedback_type,
        comment=payload.comment,
        actual_sale_price=payload.actual_sale_price,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)

    return FeedbackResponse(
        id=feedback.id,
        analysis_id=feedback.analysis_id,
        feedback_type=feedback.feedback_type,
        comment=feedback.comment,
        actual_sale_price=float(feedback.actual_sale_price) if feedback.actual_sale_price else None,
        created_at=feedback.created_at,
    )


@router.get("/{analysis_id}")
async def get_analysis_detail(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = (
        select(Analysis)
        .where(Analysis.id == analysis_id, Analysis.user_id == user.id)
    )
    result = await db.execute(query)
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    product = await db.get(Product, a.product_id)

    # Reconstruct summary from engines_data
    engines = a.engines_data or {}
    pricing = engines.get("pricing", {})
    profit = engines.get("profit_market", {})
    max_buy = engines.get("max_buy", {})
    velocity = engines.get("velocity", {})
    risk = engines.get("risk", {})
    confidence = engines.get("confidence", {})

    summary = None
    if pricing:
        cost = float(a.cost_price)
        net = float(a.net_profit) if a.net_profit else 0
        sale = float(a.estimated_sale_price) if a.estimated_sale_price else 0
        max_buy_price = max_buy.get("recommended_max", 0)
        stretch = pricing.get("stretch_list") if pricing.get("stretch_allowed", False) else None
        summary = {
            "recommendation": a.recommendation or "pass",
            "signal": "positive" if a.recommendation in ("buy", "buy_small") else "neutral",
            "buy_box": {
                "recommended_max_buy": max_buy_price,
                "your_cost": cost,
                "headroom": max_buy_price - cost,
            },
            "sale_plan": {
                "recommended_list_price": pricing.get("market_list", sale),
                "quick_sale_price": pricing.get("quick_list", 0),
                "stretch_price": stretch,
            },
            "returns": {
                "profit": net,
                "roi_pct": float(a.roi_pct) if a.roi_pct else 0,
                "margin_pct": float(a.margin_pct) if a.margin_pct else 0,
            },
            "risk": risk.get("category", "medium"),
            "confidence": confidence.get("category", "medium"),
            "warnings": [],
        }

    # Reconstruct ebay comps from engines_data
    ebay_analysis = None
    cleaned = engines.get("cleaned_comps", {})
    if cleaned:
        ebay_analysis = {
            "marketplace": "ebay",
            "comps": {
                "total_sold": cleaned.get("clean_total", 0),
                "median_price": cleaned.get("median_price", 0),
                "p25": cleaned.get("p25", 0),
                "p75": cleaned.get("p75", 0),
                "sales_per_day": velocity.get("sales_per_day", 0),
                "days_of_data": cleaned.get("days_of_data", 0),
            },
            "velocity": {
                "score": velocity.get("score"),
                "category": velocity.get("category"),
            },
            "confidence": {
                "score": confidence.get("score"),
                "category": confidence.get("category"),
            },
        }

    return {
        "id": a.id,
        "product": {
            "id": product.id,
            "barcode": product.barcode,
            "title": product.title,
            "brand": product.brand,
            "image_url": product.image_url,
        } if product else None,
        "cost_price": float(a.cost_price),
        "marketplace": a.marketplace,
        "estimated_sale_price": float(a.estimated_sale_price) if a.estimated_sale_price else None,
        "net_profit": float(a.net_profit) if a.net_profit else None,
        "margin_pct": float(a.margin_pct) if a.margin_pct else None,
        "roi_pct": float(a.roi_pct) if a.roi_pct else None,
        "flip_score": a.flip_score,
        "risk_score": a.risk_score,
        "velocity_score": a.velocity_score,
        "recommendation": a.recommendation,
        "channels": a.channels,
        "summary": summary,
        "ai_explanation": a.ai_explanation,
        "ebay_analysis": ebay_analysis,
        "amazon_analysis": None,
        "created_at": a.created_at.isoformat(),
    }


# ─── Share endpoints ───────────────────────────────────────────

@router.post("/{analysis_id}/share")
async def create_share_link(
    analysis_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate a public share token for an analysis (owner only)."""
    query = select(Analysis).where(
        Analysis.id == analysis_id,
        Analysis.user_id == user.id,
    )
    result = await db.execute(query)
    a = result.scalar_one_or_none()
    if a is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    # Reuse existing token if already shared
    if not a.share_token:
        a.share_token = secrets.token_urlsafe(16)
        await db.commit()
        await db.refresh(a)

    base_url = str(request.base_url).rstrip("/")
    share_url = f"{base_url}/api/v1/analysis/share/{a.share_token}"

    return {"share_token": a.share_token, "share_url": share_url}
