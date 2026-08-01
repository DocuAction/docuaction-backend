from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import Invoice, InvoiceLineItem, InvoiceStatus
from app.services.auth import require_role
import io

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/invoices", tags=["Invoices"], dependencies=[Depends(require_role("contributor"))])
class InvoiceLineCreate(BaseModel):
    description: str
    quantity: Decimal = Decimal("1")
    unit: str = "Hours"
    rate: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")


class InvoiceCreate(BaseModel):
    invoice_date: date
    due_date: date | None = None
    client_name: str
    client_address: str | None = None
    client_email: str | None = None
    client_phone: str | None = None
    rfq_id: UUID | None = None
    project_id: UUID | None = None
    contract_reference: str | None = None
    consultant_name: str | None = None
    tax_amount: Decimal = Decimal("0")
    other_charges: Decimal = Decimal("0")
    payment_terms: str | None = "Net 30"
    notes: str | None = None
    line_items: list[InvoiceLineCreate] = []


class InvoiceLineResponse(BaseModel):
    id: UUID
    description: str
    quantity: Decimal
    unit: str
    rate: Decimal
    amount: Decimal

    class Config:
        from_attributes = True


class InvoiceResponse(BaseModel):
    id: UUID
    invoice_number: str
    invoice_date: date
    due_date: date | None
    status: InvoiceStatus
    client_name: str
    client_address: str | None
    client_email: str | None
    contract_reference: str | None
    consultant_name: str | None
    subtotal: Decimal
    tax_amount: Decimal
    other_charges: Decimal
    total: Decimal
    payment_terms: str | None
    notes: str | None
    created_at: datetime | None = None
    line_items: list[InvoiceLineResponse] = []

    class Config:
        from_attributes = True


@router.post("", response_model=InvoiceResponse, status_code=201)
async def create_invoice(
    payload: InvoiceCreate,
    admin=Depends(require_role("Admin")),
    db: AsyncSession = Depends(get_db),
):
    # Auto-generate invoice number
    year = datetime.now().year
    count = await db.execute(select(func.count(Invoice.id)))
    seq = count.scalar() + 1
    inv_number = f"AGT-INV-{year}-{str(seq).zfill(4)}"

    # Calculate totals
    subtotal = sum(li.amount for li in payload.line_items)
    total = subtotal + payload.tax_amount + payload.other_charges

    invoice = Invoice(
        invoice_number=inv_number,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        client_name=payload.client_name,
        client_address=payload.client_address,
        client_email=payload.client_email,
        client_phone=payload.client_phone,
        rfq_id=payload.rfq_id,
        project_id=payload.project_id,
        contract_reference=payload.contract_reference,
        consultant_name=payload.consultant_name,
        subtotal=subtotal,
        tax_amount=payload.tax_amount,
        other_charges=payload.other_charges,
        total=total,
        payment_terms=payload.payment_terms,
        notes=payload.notes,
    )
    db.add(invoice)
    await db.flush()

    for li in payload.line_items:
        item = InvoiceLineItem(
            invoice_id=invoice.id,
            description=li.description,
            quantity=li.quantity,
            unit=li.unit,
            rate=li.rate,
            amount=li.amount,
        )
        db.add(item)

    await db.flush()
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice.id).options(selectinload(Invoice.line_items))
    )
    return result.scalar_one()


@router.get("", response_model=list[InvoiceResponse])
async def list_invoices(
    admin=Depends(require_role("Admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Invoice).options(selectinload(Invoice.line_items)).order_by(Invoice.created_at.desc())
    )
    return result.scalars().unique().all()


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: UUID,
    admin=Depends(require_role("Admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.line_items))
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return inv


@router.patch("/{invoice_id}/status")
async def update_invoice_status(
    invoice_id: UUID,
    status: str,
    admin=Depends(require_role("Admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")
    inv.status = status
    await db.flush()
    await db.refresh(inv)
    return {"status": inv.status, "invoice_number": inv.invoice_number}


@router.get("/{invoice_id}/pdf")
async def download_invoice_pdf(
    invoice_id: UUID,
    admin=Depends(require_role("Admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.line_items))
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(404, "Invoice not found")

    from app.services.pdf_generator import generate_invoice_pdf

    invoice_data = {
        "invoice_number": inv.invoice_number,
        "client_name": inv.client_name,
        "client_address": inv.client_address,
        "client_email": inv.client_email,
        "client_phone": inv.client_phone,
        "invoice_date": str(inv.invoice_date) if inv.invoice_date else None,
        "due_date": str(inv.due_date) if inv.due_date else None,
        "payment_terms": inv.payment_terms,
        "po_number": getattr(inv, 'contract_reference', None),
        "subtotal": float(inv.subtotal or 0),
        "tax_amount": float(inv.tax_amount or 0),
        "total": float(inv.total or 0),
    }

    items = [{
        "description": str(li.description)[:50],
        "quantity": float(li.quantity),
        "unit": li.unit,
        "rate": float(li.rate or 0),
        "amount": float(li.amount or 0),
    } for li in inv.line_items]

    pdf_bytes = generate_invoice_pdf(invoice_data, items)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{inv.invoice_number}.pdf"'}
    )
