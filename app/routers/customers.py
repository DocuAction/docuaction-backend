"""Customers — full intake, CRUD, search."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import Customer
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/customers", tags=["Customers"], dependencies=[Depends(require_role("contributor"))])
FIELDS = ['name','customer_type','division','department','agency_code','website',
    'contact_name','contact_title','contact_email','contact_phone',
    'contact2_name','contact2_title','contact2_email','contact2_phone',
    'billing_address','billing_city','billing_state','billing_zip',
    'shipping_address','shipping_city','shipping_state','shipping_zip',
    'mailing_address','mailing_city','mailing_state','mailing_zip',
    'cage_code','uei_number','duns_number','tax_exempt_id','credit_limit',
    'payment_terms','contract_vehicle','notes','status']


def _serialize(c):
    return {
        "id": str(c.id), "name": c.name, "customer_type": str(c.customer_type),
        "division": getattr(c, 'division', None), "department": getattr(c, 'department', None),
        "agency_code": getattr(c, 'agency_code', None), "website": getattr(c, 'website', None),
        "contact_name": getattr(c, 'contact_name', None), "contact_title": getattr(c, 'contact_title', None),
        "contact_email": getattr(c, 'contact_email', None), "contact_phone": getattr(c, 'contact_phone', None),
        "contact2_name": getattr(c, 'contact2_name', None), "contact2_title": getattr(c, 'contact2_title', None),
        "contact2_email": getattr(c, 'contact2_email', None), "contact2_phone": getattr(c, 'contact2_phone', None),
        "billing_address": c.billing_address, "billing_city": getattr(c, 'billing_city', None),
        "billing_state": getattr(c, 'billing_state', None), "billing_zip": getattr(c, 'billing_zip', None),
        "shipping_address": getattr(c, 'shipping_address', None), "shipping_city": getattr(c, 'shipping_city', None),
        "shipping_state": getattr(c, 'shipping_state', None), "shipping_zip": getattr(c, 'shipping_zip', None),
        "mailing_address": getattr(c, 'mailing_address', None), "mailing_city": getattr(c, 'mailing_city', None),
        "mailing_state": getattr(c, 'mailing_state', None), "mailing_zip": getattr(c, 'mailing_zip', None),
        "cage_code": getattr(c, 'cage_code', None), "uei_number": getattr(c, 'uei_number', None),
        "duns_number": getattr(c, 'duns_number', None), "tax_exempt_id": c.tax_exempt_id,
        "credit_limit": float(c.credit_limit) if c.credit_limit else None,
        "payment_terms": c.payment_terms, "contract_vehicle": getattr(c, 'contract_vehicle', None),
        "notes": getattr(c, 'notes', None), "status": getattr(c, 'status', 'Active'),
    }


@router.post("", status_code=201)
async def create_customer(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    c = Customer(name=payload.get('name', 'Unnamed'))
    for f in FIELDS:
        if f in payload and hasattr(c, f):
            setattr(c, f, payload[f])
    db.add(c)
    await db.flush()
    return _serialize(c)


@router.get("")
async def list_customers(search: str | None = None, customer_type: str | None = None,
                         limit: int = 100, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(Customer).order_by(Customer.name).limit(limit)
    if search:
        q = q.where(or_(
            Customer.name.ilike(f"%{search}%"),
            Customer.contact_name.ilike(f"%{search}%"),
            Customer.contact_email.ilike(f"%{search}%"),
            Customer.division.ilike(f"%{search}%"),
            Customer.department.ilike(f"%{search}%"),
        ))
    if customer_type:
        q = q.where(Customer.customer_type == customer_type)
    result = await db.execute(q)
    return [_serialize(c) for c in result.scalars().all()]


@router.get("/{cid}")
async def get_customer(cid: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == cid))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    return _serialize(c)


@router.patch("/{cid}")
async def update_customer(cid: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == cid))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    for f in FIELDS:
        if f in payload and hasattr(c, f):
            setattr(c, f, payload[f])
    await db.flush()
    return _serialize(c)


@router.delete("/{cid}")
async def delete_customer(cid: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.id == cid))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404)
    await db.delete(c)
    await db.flush()
    return {"status": "deleted"}
