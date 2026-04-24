from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import AgencyContact
from app.services.auth import get_current_user

router = APIRouter(prefix="/agency-contacts", tags=["Agency Contacts"])


class ContactCreate(BaseModel):
    agency_name: str
    contact_name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    department: str | None = None
    notes: str | None = None
    rfq_id: UUID | None = None


class ContactResponse(ContactCreate):
    id: UUID
    created_at: str | None = None
    class Config:
        from_attributes = True


@router.post("", response_model=ContactResponse, status_code=201)
async def create_contact(payload: ContactCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    contact = AgencyContact(**payload.model_dump())
    db.add(contact)
    await db.flush()
    await db.refresh(contact)
    return contact


@router.get("", response_model=list[ContactResponse])
async def list_contacts(
    agency: str | None = None,
    search: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(AgencyContact).order_by(AgencyContact.agency_name)
    if agency:
        q = q.where(AgencyContact.agency_name.ilike(f"%{agency}%"))
    if search:
        q = q.where(
            AgencyContact.contact_name.ilike(f"%{search}%") |
            AgencyContact.agency_name.ilike(f"%{search}%") |
            AgencyContact.email.ilike(f"%{search}%")
        )
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(contact_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgencyContact).where(AgencyContact.id == contact_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    return c


@router.patch("/{contact_id}", response_model=ContactResponse)
async def update_contact(contact_id: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgencyContact).where(AgencyContact.id == contact_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    for k, v in payload.items():
        if hasattr(c, k) and k != 'id':
            setattr(c, k, v)
    await db.flush()
    await db.refresh(c)
    return c
