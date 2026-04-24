from uuid import UUID
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import RFQ
from app.schemas import RFQCreate, RFQResponse
from app.services.audit import log_action

router = APIRouter(prefix="/rfq", tags=["RFQ"])


@router.post("", response_model=RFQResponse, status_code=201)
async def create_rfq(payload: RFQCreate, db: AsyncSession = Depends(get_db)):
    rfq = RFQ(**payload.model_dump())
    db.add(rfq)
    await db.flush()
    await log_action(db, "rfqs", rfq.id, "INSERT")

    # Auto-generate tasks for new RFQ
    try:
        from datetime import timedelta
        from app.models import Task
        due = rfq.due_date or (date.today() + timedelta(days=7))
        for t in [
            {"title": "Request supplier quotes", "type": "supplier_quote", "offset": 5},
            {"title": "Enter BOM line items", "type": "bom_entry", "offset": 4},
            {"title": "Complete pricing", "type": "pricing", "offset": 3},
            {"title": "Generate quote", "type": "quote_review", "offset": 2},
            {"title": "Submit to customer", "type": "submission", "offset": 1},
        ]:
            db.add(Task(rfq_id=rfq.id, title=t['title'], task_type=t['type'],
                        due_date=max(due - timedelta(days=t['offset']), date.today())))
        await db.flush()
    except Exception:
        pass  # Don't block RFQ creation

    await db.refresh(rfq)
    return rfq


@router.get("", response_model=list[RFQResponse])
async def list_rfqs(
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import or_
    q = select(RFQ).order_by(RFQ.created_at.desc()).limit(limit).offset(offset)
    if status:
        q = q.where(RFQ.status == status)
    if search:
        try:
            q = q.where(or_(
                RFQ.title.ilike(f"%{search}%"),
                RFQ.solicitation_number.ilike(f"%{search}%"),
                RFQ.agency.ilike(f"%{search}%"),
                RFQ.contract_officer_name.ilike(f"%{search}%"),
                RFQ.contract_officer_email.ilike(f"%{search}%"),
                RFQ.contract_officer_phone.ilike(f"%{search}%"),
            ))
        except Exception:
            # Fallback if officer columns don't exist yet
            q = q.where(or_(
                RFQ.title.ilike(f"%{search}%"),
                RFQ.solicitation_number.ilike(f"%{search}%"),
                RFQ.agency.ilike(f"%{search}%"),
            ))
    try:
        result = await db.execute(q)
        return result.scalars().all()
    except Exception:
        # If search with officer fields fails, retry without them
        await db.rollback()
        q2 = select(RFQ).order_by(RFQ.created_at.desc()).limit(limit)
        if status:
            q2 = q2.where(RFQ.status == status)
        if search:
            q2 = q2.where(or_(
                RFQ.title.ilike(f"%{search}%"),
                RFQ.solicitation_number.ilike(f"%{search}%"),
                RFQ.agency.ilike(f"%{search}%"),
            ))
        result = await db.execute(q2)
        return result.scalars().all()


@router.get("/{rfq_id}", response_model=RFQResponse)
async def get_rfq(rfq_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RFQ).where(RFQ.id == rfq_id))
    rfq = result.scalar_one_or_none()
    if not rfq:
        raise HTTPException(404, "RFQ not found")
    return rfq


@router.patch("/{rfq_id}", response_model=RFQResponse)
async def update_rfq(rfq_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RFQ).where(RFQ.id == rfq_id))
    rfq = result.scalar_one_or_none()
    if not rfq:
        raise HTTPException(404, "RFQ not found")
    for k, v in payload.items():
        if hasattr(rfq, k):
            old = str(getattr(rfq, k))
            setattr(rfq, k, v)
            await log_action(db, "rfqs", rfq.id, "UPDATE", k, old, str(v))
    await db.flush()
    await db.refresh(rfq)
    return rfq
