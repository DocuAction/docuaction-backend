from uuid import UUID
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import Product, LifecycleStatus

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/products", tags=["Products"], dependencies=[Depends(require_role("contributor"))])
class ProductCreate(BaseModel):
    manufacturer: str
    part_number: str
    description: str | None = None
    category: str | None = None
    msrp: Decimal | None = None
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE


class ProductResponse(ProductCreate):
    id: UUID
    created_at: str | None = None

    class Config:
        from_attributes = True


@router.post("", response_model=ProductResponse, status_code=201)
async def create_product(payload: ProductCreate, db: AsyncSession = Depends(get_db)):
    product = Product(**payload.model_dump())
    db.add(product)
    await db.flush()
    await db.refresh(product)
    return product


@router.get("", response_model=list[ProductResponse])
async def list_products(
    manufacturer: str | None = None,
    search: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    q = select(Product).order_by(Product.manufacturer, Product.part_number).limit(limit)
    if manufacturer:
        q = q.where(Product.manufacturer.ilike(f"%{manufacturer}%"))
    if search:
        q = q.where(
            Product.part_number.ilike(f"%{search}%") | Product.description.ilike(f"%{search}%")
        )
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(product_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Product not found")
    return product
