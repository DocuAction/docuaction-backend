from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import DealRegistration
from app.schemas import DealRegCreate, DealRegResponse

router = APIRouter(prefix="/deal-registrations", tags=["Deal Registrations"])


@router.post("", response_model=DealRegResponse, status_code=201)
async def create_deal_reg(payload: DealRegCreate, db: AsyncSession = Depends(get_db)):
    dr = DealRegistration(**payload.model_dump())
    db.add(dr)
    await db.flush()
    await db.refresh(dr)
    return dr


@router.get("", response_model=list[DealRegResponse])
async def list_deal_regs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DealRegistration).order_by(DealRegistration.created_at.desc()))
    return result.scalars().all()


@router.get("/{dr_id}", response_model=DealRegResponse)
async def get_deal_reg(dr_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DealRegistration).where(DealRegistration.id == dr_id))
    dr = result.scalar_one_or_none()
    if not dr:
        raise HTTPException(404, "Deal registration not found")
    return dr
