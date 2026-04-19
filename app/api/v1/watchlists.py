from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_user
from app.database import get_db
from app.models.product import Product
from app.models.user import User
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.watchlist import WatchlistCreate, WatchlistItemAdd, WatchlistItemOut, WatchlistOut

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
    return [_serialize_watchlist(wl) for wl in watchlists]


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


def _serialize_watchlist(wl: Watchlist) -> WatchlistOut:
    return WatchlistOut(
        id=wl.id,
        name=wl.name,
        created_at=wl.created_at,
        items=[
            WatchlistItemOut(
                id=item.id,
                product_id=item.product_id,
                product_title=item.product.title if item.product else None,
                target_buy_price=float(item.target_buy_price) if item.target_buy_price else None,
                notes=item.notes,
                added_at=item.added_at,
            )
            for item in wl.items
        ],
    )
