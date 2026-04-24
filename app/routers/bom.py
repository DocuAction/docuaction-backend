from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import BOMItem, RFQ
from app.schemas import BOMItemCreate, BOMItemResponse, BOMUpload
from app.services.audit import log_action

router = APIRouter(prefix="/rfq/{rfq_id}/bom", tags=["BOM"])


@router.post("", response_model=list[BOMItemResponse], status_code=201)
async def upload_bom(rfq_id: UUID, payload: BOMUpload, db: AsyncSession = Depends(get_db)):
    # Verify RFQ exists
    result = await db.execute(select(RFQ).where(RFQ.id == rfq_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "RFQ not found")

    items = []
    for item_data in payload.items:
        item = BOMItem(rfq_id=rfq_id, **item_data.model_dump())
        db.add(item)
        items.append(item)

    await db.flush()
    for item in items:
        await db.refresh(item)
        await log_action(db, "bom_items", item.id, "INSERT")

    return items


@router.get("", response_model=list[BOMItemResponse])
async def get_bom(rfq_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BOMItem).where(BOMItem.rfq_id == rfq_id).order_by(BOMItem.line_number)
    )
    return result.scalars().all()


@router.patch("/{item_id}", response_model=BOMItemResponse)
async def update_bom_item(
    rfq_id: UUID, item_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(BOMItem).where(BOMItem.id == item_id, BOMItem.rfq_id == rfq_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(404, "BOM item not found")

    # Optimistic lock check
    if "version" in payload:
        if payload.pop("version") != item.version:
            raise HTTPException(409, "Conflict: item was modified by another user. Refresh and retry.")

    for k, v in payload.items():
        if hasattr(item, k) and k not in ("id", "rfq_id"):
            setattr(item, k, v)
    item.version += 1
    await db.flush()
    await db.refresh(item)
    return item
