from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user_optional
from app.database import get_db
from app.models.product import Product
from app.models.user import User
from app.schemas.product import ProductOut, ProductSearch

router = APIRouter()


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return product


@router.post("/search", response_model=list[ProductOut])
async def search_products(
    payload: ProductSearch,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    query = select(Product)
    if payload.barcode:
        query = query.where(Product.barcode == payload.barcode)
    elif payload.keyword:
        query = query.where(Product.title.ilike(f"%{payload.keyword}%"))
    else:
        raise HTTPException(status_code=400, detail="Proporciona barcode o keyword")

    result = await db.execute(query.limit(20))
    return result.scalars().all()
