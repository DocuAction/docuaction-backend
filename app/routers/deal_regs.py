from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import DealRegistration
from app.schemas import DealRegCreate, DealRegResponse

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/deal-registrations", tags=["Deal Registrations"], dependencies=[Depends(require_role("contributor"))])
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
