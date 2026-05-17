from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.database import get_db
from app.models.price_history import ProductPriceHistory
from app.models.product import Product
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.watchlist import (
    PriceHistoryPoint,
    PriceSnapshot,
    WatchlistCreate,
    WatchlistItemAdd,
    WatchlistItemOut,
    WatchlistOut,
)

router = APIRouter()


@router.get("/", response_model=list[WatchlistOut])
async def list_watchlists(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Watchlist)
        .where(Watchlist.user_id == user.id)
        .options(selectinload(Watchlist.items).selectinload(WatchlistItem.product))
    )
    watchlists = result.scalars().all()

    # Batch fetch latest prices for all products in watchlists
    product_ids = {
        item.product_id for wl in watchlists for item in wl.items
    }
    price_map = await _get_latest_prices(db, product_ids) if product_ids else {}

    return [_serialize_watchlist(wl, price_map) for wl in watchlists]


@router.post("/", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_watchlist(
    payload: WatchlistCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    wl = Watchlist(name=payload.name, user_id=user.id)
    db.add(wl)
    await db.commit()
    await db.refresh(wl)
    return WatchlistOut(id=wl.id, name=wl.name, items=[], created_at=wl.created_at)


@router.post("/{watchlist_id}/items", response_model=WatchlistItemOut)
async def add_item(
    watchlist_id: int,
    payload: WatchlistItemAdd,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    wl = await _get_watchlist(db, watchlist_id, user.id)
    product = await db.get(Product, payload.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    item = WatchlistItem(
        watchlist_id=wl.id,
        product_id=payload.product_id,
        target_buy_price=payload.target_buy_price,
        notes=payload.notes,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return WatchlistItemOut(
        id=item.id,
        product_id=item.product_id,
        product_title=product.title,
        product_image_url=product.image_url,
        target_buy_price=float(item.target_buy_price) if item.target_buy_price else None,
        notes=item.notes,
        added_at=item.added_at,
    )


@router.delete("/{watchlist_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_item(
    watchlist_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get_watchlist(db, watchlist_id, user.id)
    item = await db.get(WatchlistItem, item_id)
    if not item or item.watchlist_id != watchlist_id:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    await db.delete(item)
    await db.commit()


@router.get("/{watchlist_id}/items/{item_id}/history", response_model=list[PriceHistoryPoint])
async def get_price_history(
    watchlist_id: int,
    item_id: int,
    days: int = Query(30, ge=7, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get price history for a watchlist item (for charts)."""
    await _get_watchlist(db, watchlist_id, user.id)
    item = await db.get(WatchlistItem, item_id)
    if not item or item.watchlist_id != watchlist_id:
        raise HTTPException(status_code=404, detail="Item no encontrado")

    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(ProductPriceHistory)
        .where(
            ProductPriceHistory.product_id == item.product_id,
            ProductPriceHistory.recorded_date >= since,
        )
        .order_by(ProductPriceHistory.recorded_date)
    )
    rows = result.scalars().all()
    return [
        PriceHistoryPoint(
            date=row.recorded_date,
            ebay_price=float(row.ebay_median_price) if row.ebay_median_price else None,
            amazon_price=float(row.amazon_price) if row.amazon_price else None,
        )
        for row in rows
    ]


# ─── Helpers ────────────────────────────────────────────────────────────────

async def _get_watchlist(db: AsyncSession, watchlist_id: int, user_id: int) -> Watchlist:
    result = await db.execute(
        select(Watchlist).where(
            Watchlist.id == watchlist_id,
            Watchlist.user_id == user_id,
        )
    )
    wl = result.scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist no encontrada")
    return wl


async def _get_latest_prices(
    db: AsyncSession, product_ids: set[int]
) -> dict[int, PriceSnapshot]:
    """Get latest price snapshot + 7d change for a set of products."""
    today = date.today()
    week_ago = today - timedelta(days=7)

    # Latest price per product
    result = await db.execute(
        select(ProductPriceHistory)
        .where(ProductPriceHistory.product_id.in_(product_ids))
        .order_by(ProductPriceHistory.product_id, desc(ProductPriceHistory.recorded_date))
    )
    rows = result.scalars().all()

    # Group by product_id: latest row + 7d-ago row
    latest_map: dict[int, ProductPriceHistory] = {}
    week_ago_map: dict[int, ProductPriceHistory] = {}

    for row in rows:
        pid = row.product_id
        if pid not in latest_map:
            latest_map[pid] = row
        if row.recorded_date <= week_ago and pid not in week_ago_map:
            week_ago_map[pid] = row

    price_map: dict[int, PriceSnapshot] = {}
    for pid in product_ids:
        latest = latest_map.get(pid)
        if not latest:
            continue

        # Compute 7d change
        change_pct = None
        old = week_ago_map.get(pid)
        if old:
            old_price = float(old.ebay_median_price or old.amazon_price or 0)
            new_price = float(latest.ebay_median_price or latest.amazon_price or 0)
            if old_price > 0 and new_price > 0:
                change_pct = round((new_price - old_price) / old_price * 100, 1)

        price_map[pid] = PriceSnapshot(
            ebay_median_price=float(latest.ebay_median_price) if latest.ebay_median_price else None,
            amazon_price=float(latest.amazon_price) if latest.amazon_price else None,
            price_updated_at=latest.created_at,
            price_change_7d_pct=change_pct,
        )

    return price_map


def _serialize_watchlist(wl: Watchlist, price_map: dict[int, PriceSnapshot]) -> WatchlistOut:
    items = []
    for item in wl.items:
        pricing = price_map.get(item.product_id)
        target = float(item.target_buy_price) if item.target_buy_price else None

        # Check if below target
        if pricing and target:
            current = pricing.ebay_median_price or pricing.amazon_price
            if current and current <= target:
                pricing.below_target = True

        items.append(
            WatchlistItemOut(
                id=item.id,
                product_id=item.product_id,
                product_title=item.product.title if item.product else None,
                product_image_url=item.product.image_url if item.product else None,
                target_buy_price=target,
                notes=item.notes,
                added_at=item.added_at,
                pricing=pricing,
            )
        )

    return WatchlistOut(id=wl.id, name=wl.name, created_at=wl.created_at, items=items)
