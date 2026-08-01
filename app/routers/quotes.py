from uuid import UUID
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.security import require_role
from app.database import get_db
from app.models import (
    Quote, QuoteLineItem, RFQ, SupplierPriceSnapshot, QuoteStatus
)
from app.schemas import QuoteCreateRequest, QuoteResponse, PricingRequest
from app.services.pricing import price_request
from app.services.audit import log_action

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/quotes", tags=["Quotes"], dependencies=[Depends(require_role("contributor"))])
@router.post("", response_model=QuoteResponse, status_code=201)
async def create_quote(req: QuoteCreateRequest, db: AsyncSession = Depends(get_db)):
    # Verify RFQ
    result = await db.execute(select(RFQ).where(RFQ.id == req.rfq_id))
    rfq = result.scalar_one_or_none()
    if not rfq:
        raise HTTPException(404, "RFQ not found")

    # Determine next version
    ver_result = await db.execute(
        select(func.coalesce(func.max(Quote.version), 0)).where(Quote.rfq_id == req.rfq_id)
    )
    next_version = ver_result.scalar() + 1

    # Supersede previous drafts
    prev = await db.execute(
        select(Quote).where(Quote.rfq_id == req.rfq_id, Quote.status == QuoteStatus.DRAFT)
    )
    for old_q in prev.scalars():
        old_q.status = QuoteStatus.SUPERSEDED

    # Run pricing engine
    pricing_req = PricingRequest(
        rfq_id=req.rfq_id,
        is_taxable=rfq.is_taxable,
        lines=req.lines,
        min_margin_pct=req.min_margin_pct,
    )
    pricing = price_request(pricing_req)

    # Generate auto quote number
    from datetime import datetime
    year = datetime.now().year
    count_result_all = await db.execute(select(func.count(Quote.id)))
    seq = count_result_all.scalar() + 1
    quote_number = f"AGT-Q-{year}-{str(seq).zfill(4)}"

    # Create quote
    quote = Quote(
        rfq_id=req.rfq_id,
        quote_number=quote_number,
        version=next_version,
        total_sell_price=pricing.total_sell,
        total_cost=pricing.total_cost,
        overall_margin_pct=pricing.blended_margin_pct,
        total_tax=pricing.total_tax,
    )
    db.add(quote)
    await db.flush()

    # Create line items + supplier price snapshots
    for i, (line_input, line_result) in enumerate(zip(req.lines, pricing.lines)):
        # Create supplier price snapshot
        snapshot_id = None
        if line_input.supplier_id:
            snap = SupplierPriceSnapshot(
                supplier_id=line_input.supplier_id,
                part_number=line_input.part_number or f"line-{i+1}",
                unit_price=line_input.unit_cost,
                source="manual",
            )
            db.add(snap)
            await db.flush()
            snapshot_id = snap.id

        qli = QuoteLineItem(
            quote_id=quote.id,
            bom_item_id=line_input.bom_item_id,
            supplier_id=line_input.supplier_id,
            part_number=getattr(line_input, 'part_number', None) or f"ITEM-{i+1}",
            description=getattr(line_input, 'description', None) or getattr(line_input, 'part_number', None) or f"Line Item {i+1}",
            quantity=line_input.quantity,
            unit_cost=line_input.unit_cost,
            inbound_freight=line_input.inbound_freight,
            duty_rate=line_input.duty_rate,
            handling_fee=line_input.handling_fee,
            forex_buffer_pct=line_input.forex_buffer_pct,
            landed_cost=line_result.landed_cost_per_unit,
            sell_price=line_result.sell_price_per_unit,
            margin_pct=line_result.margin_pct,
            tax_amount=line_result.tax_per_unit,
            is_override=line_result.is_override,
            override_justification=line_input.override_justification,
            deal_registration_id=line_input.deal_registration_id,
            snapshot_id=snapshot_id,
        )
        db.add(qli)

    await db.flush()
    await log_action(db, "quotes", quote.id, "INSERT", new_value=f"v{next_version}")

    # Update RFQ status
    rfq.status = "Quoted"
    await db.flush()

    # Auto-record price history for intelligence layer
    try:
        from app.models import PriceHistory, ProductCatalog
        for i, (line_input, line_result) in enumerate(zip(req.lines, pricing.lines)):
            pn = getattr(line_input, 'part_number', None) or f"ITEM-{i+1}"
            ph = PriceHistory(
                product_part_number=pn,
                unit_cost=line_input.unit_cost,
                sell_price=line_result.sell_price_per_unit,
                margin_pct=line_result.margin_pct,
                rfq_id=req.rfq_id,
                quote_id=quote.id,
                agency=rfq.agency,
            )
            db.add(ph)
            # Update product catalog last_known_cost if product exists
            prod = await db.execute(select(ProductCatalog).where(ProductCatalog.part_number == pn))
            p = prod.scalar_one_or_none()
            if p:
                p.last_known_cost = line_input.unit_cost
            else:
                # Auto-add to product catalog
                db.add(ProductCatalog(
                    part_number=pn,
                    manufacturer=getattr(line_input, 'manufacturer', '') or 'Unknown',
                    description=getattr(line_input, 'description', None),
                    last_known_cost=line_input.unit_cost,
                ))
        await db.flush()
    except Exception:
        pass  # Don't block quote creation if intel recording fails

    # Reload with line items
    result = await db.execute(
        select(Quote).where(Quote.id == quote.id).options(selectinload(Quote.line_items))
    )
    return result.scalar_one()


@router.post("/{quote_id}/version", response_model=QuoteResponse, status_code=201)
async def create_new_version(quote_id: UUID, req: QuoteCreateRequest, db: AsyncSession = Depends(get_db)):
    """Create a new version of an existing quote (clones and re-prices)."""
    result = await db.execute(select(Quote).where(Quote.id == quote_id))
    existing = result.scalar_one_or_none()
    if not existing:
        raise HTTPException(404, "Quote not found")
    if existing.is_locked:
        raise HTTPException(400, "Cannot version a locked/submitted quote")

    # Override rfq_id from existing quote
    req.rfq_id = existing.rfq_id
    return await create_quote(req, db)


@router.get("/{quote_id}", response_model=QuoteResponse)
async def get_quote(quote_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.line_items))
    )
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(404, "Quote not found")
    return quote


@router.get("", response_model=list[QuoteResponse])
async def list_quotes(rfq_id: UUID | None = None, limit: int = 50, db: AsyncSession = Depends(get_db)):
    q = select(Quote).options(selectinload(Quote.line_items)).order_by(Quote.created_at.desc()).limit(limit)
    if rfq_id:
        q = q.where(Quote.rfq_id == rfq_id)
    result = await db.execute(q)
    return result.scalars().unique().all()


@router.post("/{quote_id}/submit", response_model=QuoteResponse)
async def submit_quote(quote_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.line_items))
    )
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Check for blocked line items
    for li in quote.line_items:
        if li.margin_pct < 12.0 and not li.is_override:
            raise HTTPException(400, f"Line item has margin {li.margin_pct}% below minimum. Override required.")

    quote.status = QuoteStatus.SUBMITTED
    quote.is_locked = True
    from datetime import datetime, timezone
    quote.submitted_at = datetime.now(timezone.utc)
    await db.flush()
    await log_action(db, "quotes", quote.id, "UPDATE", "status", "Draft", "Submitted")
    await db.refresh(quote)
    return quote


@router.get("/{quote_id}/pdf")
async def download_quote_pdf(quote_id: UUID, db: AsyncSession = Depends(get_db)):
    """Generate and download a PDF quote document."""
    from app.services.pdf_generator import generate_quote_pdf

    # Get quote with line items
    result = await db.execute(
        select(Quote).where(Quote.id == quote_id).options(selectinload(Quote.line_items))
    )
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(404, "Quote not found")

    # Get RFQ details
    rfq_result = await db.execute(select(RFQ).where(RFQ.id == quote.rfq_id))
    rfq = rfq_result.scalar_one_or_none()

    # Build data dicts
    quote_data = {
        "version": quote.version,
        "quote_number": quote.quote_number or f"AGT-Q-DRAFT-{str(quote_id)[:8]}",
        "status": quote.status,
        "total_cost": float(quote.total_cost or 0),
        "total_sell_price": float(quote.total_sell_price or 0),
        "total_tax": float(quote.total_tax or 0),
        "shipping_cost": float(quote.shipping_cost or 0) if hasattr(quote, 'shipping_cost') and quote.shipping_cost else 0,
        "overall_margin_pct": float(quote.overall_margin_pct or 0),
        "created_at": str(quote.created_at),
    }

    rfq_data = {
        "title": rfq.title if rfq else "N/A",
        "agency": rfq.agency if rfq else "N/A",
        "solicitation_number": rfq.solicitation_number if rfq else "N/A",
        "customer_type": rfq.customer_type if rfq else "N/A",
        "due_date": str(rfq.due_date) if rfq and rfq.due_date else "N/A",
        "naics_code": rfq.naics_code if rfq else "N/A",
        "contract_officer_name": getattr(rfq, 'contract_officer_name', None) or "N/A",
        "contract_officer_email": getattr(rfq, 'contract_officer_email', None) or "",
        "contract_officer_phone": getattr(rfq, 'contract_officer_phone', None) or "",
        "department": getattr(rfq, 'department', None) or rfq.agency if rfq else "N/A",
        "ship_to_address": getattr(rfq, 'ship_to_address', None) or "",
        "ship_to_city": getattr(rfq, 'ship_to_city', None) or "",
        "ship_to_state": getattr(rfq, 'ship_to_state', None) or "",
        "ship_to_zip": getattr(rfq, 'ship_to_zip', None) or "",
        "shipping_method": getattr(rfq, 'shipping_method', None) or "",
    }

    line_items = []
    for i, li in enumerate(quote.line_items):
        line_items.append({
            "part_number": getattr(li, 'part_number', None) or f"ITEM-{i+1}",
            "description": getattr(li, 'description', None) or getattr(li, 'part_number', None) or f"Line Item {i+1}",
            "manufacturer": getattr(li, 'manufacturer', '') or '',
            "taa_compliant": getattr(li, 'taa_compliant', 'Yes') or 'Yes',
            "product_type": getattr(li, 'product_type', 'Hardware') or 'Hardware',
            "clin": getattr(li, 'clin', '') or str(i+1).zfill(4),
            "quantity": li.quantity,
            "sell_price": float(li.sell_price or 0),
            "unit_cost": float(li.unit_cost or 0),
            "landed_cost": float(li.landed_cost or 0),
            "margin_pct": float(li.margin_pct or 0),
            "tax_amount": float(li.tax_amount or 0),
        })

    pdf_bytes = generate_quote_pdf(quote_data, rfq_data, line_items)
    filename = f"{quote.quote_number or 'AGT-Quote'}-v{quote.version}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
