"""
Intelligence Layer — The Brain
Product Catalog, Price History, Supplier Metrics, Deal Lifecycle, Purchase Orders
"""
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import (
    ProductCatalog, PriceHistory, SupplierMetric, TechnicalLibrary,
    PurchaseOrder, DealStatus, Supplier, RFQ, Quote
)
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/intel", tags=["Intelligence"], dependencies=[Depends(require_role("contributor"))])
# ══════════════════════════════════════════════════════════════
# PRODUCT CATALOG
# ══════════════════════════════════════════════════════════════

class ProductCreate(BaseModel):
    part_number: str
    manufacturer: str
    description: str | None = None
    category: str | None = None
    msrp: Decimal | None = None
    taa_compliant: bool = True


@router.post("/products", status_code=201)
async def create_product(p: ProductCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    prod = ProductCatalog(**p.model_dump())
    db.add(prod)
    await db.flush()
    return {"id": str(prod.id), "part_number": prod.part_number}


@router.get("/products")
async def list_products(search: str | None = None, manufacturer: str | None = None, limit: int = 50,
                        user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(ProductCatalog).order_by(ProductCatalog.manufacturer, ProductCatalog.part_number).limit(limit)
    if search:
        q = q.where(or_(
            ProductCatalog.part_number.ilike(f"%{search}%"),
            ProductCatalog.description.ilike(f"%{search}%"),
            ProductCatalog.manufacturer.ilike(f"%{search}%"),
        ))
    if manufacturer:
        q = q.where(ProductCatalog.manufacturer.ilike(f"%{manufacturer}%"))
    result = await db.execute(q)
    return [{
        "id": str(p.id), "part_number": p.part_number, "manufacturer": p.manufacturer,
        "description": p.description, "category": p.category,
        "msrp": float(p.msrp) if p.msrp else None,
        "last_known_cost": float(p.last_known_cost) if p.last_known_cost else None,
        "taa_compliant": p.taa_compliant, "lifecycle": p.lifecycle,
    } for p in result.scalars().all()]


@router.get("/products/suggest/{query}")
async def suggest_product(query: str, db: AsyncSession = Depends(get_db)):
    """Auto-suggest products for BOM entry. No auth required for speed."""
    q = select(ProductCatalog).where(or_(
        ProductCatalog.part_number.ilike(f"%{query}%"),
        ProductCatalog.description.ilike(f"%{query}%"),
    )).limit(8)
    result = await db.execute(q)
    return [{
        "part_number": p.part_number, "manufacturer": p.manufacturer,
        "description": p.description, "category": p.category,
        "last_known_cost": float(p.last_known_cost) if p.last_known_cost else None,
        "taa_compliant": p.taa_compliant,
    } for p in result.scalars().all()]


# ══════════════════════════════════════════════════════════════
# PRICE HISTORY
# ══════════════════════════════════════════════════════════════

@router.get("/price-history/{part_number}")
async def get_price_history(part_number: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get all historical prices for a part number."""
    q = select(PriceHistory).where(
        PriceHistory.product_part_number.ilike(f"%{part_number}%")
    ).order_by(desc(PriceHistory.date_quoted)).limit(20)
    result = await db.execute(q)
    entries = result.scalars().all()
    return {
        "part_number": part_number,
        "history": [{
            "unit_cost": float(e.unit_cost), "sell_price": float(e.sell_price) if e.sell_price else None,
            "margin_pct": e.margin_pct, "supplier_name": e.supplier_name,
            "date_quoted": str(e.date_quoted), "agency": e.agency, "won": e.won,
        } for e in entries],
        "last_cost": float(entries[0].unit_cost) if entries else None,
        "avg_cost": float(sum(float(e.unit_cost) for e in entries) / len(entries)) if entries else None,
        "lowest_cost": float(min(float(e.unit_cost) for e in entries)) if entries else None,
        "count": len(entries),
    }


@router.post("/price-history/record")
async def record_price(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Record a price point. Called automatically when quotes are created."""
    ph = PriceHistory(
        product_part_number=payload['part_number'],
        supplier_name=payload.get('supplier_name'),
        supplier_id=payload.get('supplier_id'),
        unit_cost=payload['unit_cost'],
        sell_price=payload.get('sell_price'),
        margin_pct=payload.get('margin_pct'),
        rfq_id=payload.get('rfq_id'),
        quote_id=payload.get('quote_id'),
        agency=payload.get('agency'),
        won=payload.get('won', False),
    )
    db.add(ph)
    # Update product catalog last_known_cost
    prod = await db.execute(
        select(ProductCatalog).where(ProductCatalog.part_number.ilike(f"%{payload['part_number']}%"))
    )
    p = prod.scalar_one_or_none()
    if p:
        p.last_known_cost = payload['unit_cost']
    await db.flush()
    return {"status": "recorded"}


# ══════════════════════════════════════════════════════════════
# SUPPLIER METRICS
# ══════════════════════════════════════════════════════════════

@router.get("/supplier-metrics")
async def list_supplier_metrics(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(SupplierMetric).order_by(desc(SupplierMetric.reliability_score))
    result = await db.execute(q)
    metrics = []
    for m in result.scalars().all():
        # Get supplier name
        s = await db.execute(select(Supplier).where(Supplier.id == m.supplier_id))
        supplier = s.scalar_one_or_none()
        metrics.append({
            "id": str(m.id), "supplier_id": str(m.supplier_id),
            "supplier_name": supplier.name if supplier else "Unknown",
            "total_quotes_requested": m.total_quotes_requested,
            "total_quotes_received": m.total_quotes_received,
            "avg_response_days": round(m.avg_response_days, 1),
            "total_deals_won": m.total_deals_won,
            "total_deals_lost": m.total_deals_lost,
            "win_rate_pct": round(m.win_rate_pct, 1),
            "avg_margin_pct": round(m.avg_margin_pct, 1),
            "authorized_brands": m.authorized_brands,
            "reliability_score": m.reliability_score,
        })
    return metrics


@router.get("/supplier-metrics/recommend/{manufacturer}")
async def recommend_supplier(manufacturer: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Recommend best supplier for a manufacturer based on metrics."""
    # Find suppliers that carry this manufacturer
    suppliers = await db.execute(
        select(Supplier).where(or_(
            Supplier.manufacturer_focus.ilike(f"%{manufacturer}%"),
            Supplier.name.ilike(f"%{manufacturer}%"),
        ))
    )
    recs = []
    for sup in suppliers.scalars().all():
        # Get metrics
        met = await db.execute(select(SupplierMetric).where(SupplierMetric.supplier_id == sup.id))
        m = met.scalar_one_or_none()
        # Get last price for this manufacturer
        ph = await db.execute(
            select(PriceHistory).where(
                PriceHistory.supplier_name == sup.name
            ).order_by(desc(PriceHistory.date_quoted)).limit(1)
        )
        last_price = ph.scalar_one_or_none()

        score = 50  # base
        if m:
            score = m.reliability_score
        recs.append({
            "supplier_name": sup.name, "supplier_id": str(sup.id),
            "type": getattr(sup, 'supplier_type', None),
            "phone": sup.contact_phone,
            "score": score,
            "win_rate": round(m.win_rate_pct, 1) if m else None,
            "avg_response_days": round(m.avg_response_days, 1) if m else None,
            "last_price": float(last_price.unit_cost) if last_price else None,
            "last_price_date": str(last_price.date_quoted) if last_price else None,
        })
    recs.sort(key=lambda x: x['score'], reverse=True)
    return {"manufacturer": manufacturer, "recommendations": recs[:5]}


# ══════════════════════════════════════════════════════════════
# TECHNICAL LIBRARY
# ══════════════════════════════════════════════════════════════

@router.post("/tech-library", status_code=201)
async def add_tech_content(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    entry = TechnicalLibrary(
        brand=payload['brand'], category=payload.get('category'),
        title=payload['title'], content=payload['content'],
        content_type=payload.get('content_type', 'technical_description'),
        warranty_terms=payload.get('warranty_terms'),
    )
    db.add(entry)
    await db.flush()
    return {"id": str(entry.id)}


@router.get("/tech-library")
async def list_tech_library(brand: str | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(TechnicalLibrary).order_by(TechnicalLibrary.brand)
    if brand:
        q = q.where(TechnicalLibrary.brand.ilike(f"%{brand}%"))
    result = await db.execute(q)
    return [{
        "id": str(t.id), "brand": t.brand, "category": t.category,
        "title": t.title, "content": t.content[:200],
        "content_type": t.content_type, "warranty_terms": t.warranty_terms,
    } for t in result.scalars().all()]


@router.get("/tech-library/for-bom")
async def get_tech_for_bom(manufacturers: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get technical content for all manufacturers in a BOM. Pass comma-separated list."""
    mfrs = [m.strip() for m in manufacturers.split(",")]
    content = {}
    for mfr in mfrs:
        q = select(TechnicalLibrary).where(TechnicalLibrary.brand.ilike(f"%{mfr}%"))
        result = await db.execute(q)
        entries = result.scalars().all()
        if entries:
            content[mfr] = [{"title": e.title, "content": e.content, "warranty": e.warranty_terms} for e in entries]
    return content


# ══════════════════════════════════════════════════════════════
# PURCHASE ORDERS (Post-Award)
# ══════════════════════════════════════════════════════════════

@router.post("/purchase-orders", status_code=201)
async def create_po(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Create PO from a won quote. Auto-generates PO number."""
    from datetime import datetime
    year = datetime.now().year
    count = await db.execute(select(func.count(PurchaseOrder.id)))
    seq = (count.scalar() or 0) + 1
    po_number = f"AGT-PO-{year}-{str(seq).zfill(4)}"

    po = PurchaseOrder(
        po_number=po_number,
        rfq_id=payload.get('rfq_id'),
        quote_id=payload.get('quote_id'),
        supplier_id=payload.get('supplier_id'),
        supplier_name=payload.get('supplier_name'),
        total_cost=payload.get('total_cost'),
        total_sell=payload.get('total_sell'),
        status=DealStatus.ORDERED,
        ordered_date=date.today(),
        notes=payload.get('notes'),
    )
    db.add(po)
    await db.flush()
    return {"id": str(po.id), "po_number": po_number, "status": "Ordered"}


@router.get("/purchase-orders")
async def list_pos(status: str | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(PurchaseOrder).order_by(desc(PurchaseOrder.created_at))
    if status:
        q = q.where(PurchaseOrder.status == status)
    result = await db.execute(q)
    return [{
        "id": str(po.id), "po_number": po.po_number,
        "supplier_name": po.supplier_name,
        "total_cost": float(po.total_cost) if po.total_cost else None,
        "total_sell": float(po.total_sell) if po.total_sell else None,
        "margin": round((float(po.total_sell or 0) - float(po.total_cost or 0)) / max(float(po.total_sell or 1), 1) * 100, 1) if po.total_sell else None,
        "status": po.status,
        "ordered_date": str(po.ordered_date) if po.ordered_date else None,
        "shipped_date": str(po.shipped_date) if po.shipped_date else None,
        "delivered_date": str(po.delivered_date) if po.delivered_date else None,
        "tracking_number": po.tracking_number,
        "notes": po.notes,
    } for po in result.scalars().all()]


@router.patch("/purchase-orders/{po_id}")
async def update_po(po_id: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == po_id))
    po = result.scalar_one_or_none()
    if not po:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(po, k) and k != 'id':
            setattr(po, k, v)
    # Auto-set dates
    if payload.get('status') == 'Shipped' and not po.shipped_date:
        po.shipped_date = date.today()
    if payload.get('status') == 'Delivered' and not po.delivered_date:
        po.delivered_date = date.today()
    await db.flush()
    return {"status": "updated"}


# ══════════════════════════════════════════════════════════════
# DEAL DASHBOARD — Unified Financial View
# ══════════════════════════════════════════════════════════════

@router.get("/deal-dashboard")
async def deal_dashboard(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Unified procurement intelligence dashboard."""
    # RFQ counts by status
    rfq_counts = {}
    for s in ['New', 'In Progress', 'Quoted', 'Submitted', 'Won', 'Lost']:
        c = await db.execute(select(func.count(RFQ.id)).where(RFQ.status == s))
        rfq_counts[s] = c.scalar()

    # Quote totals
    total_quoted = await db.execute(select(func.sum(Quote.total_sell_price)))
    total_cost = await db.execute(select(func.sum(Quote.total_cost)))

    # Won deals
    won_rfqs = await db.execute(select(func.count(RFQ.id)).where(RFQ.status == 'Won'))
    won_value = await db.execute(
        select(func.sum(Quote.total_sell_price)).join(RFQ, Quote.rfq_id == RFQ.id).where(RFQ.status == 'Won')
    )

    # PO stats
    po_ordered = await db.execute(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.status == DealStatus.ORDERED))
    po_shipped = await db.execute(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.status == DealStatus.SHIPPED))
    po_delivered = await db.execute(select(func.count(PurchaseOrder.id)).where(PurchaseOrder.status == DealStatus.DELIVERED))

    # Product catalog size
    prod_count = await db.execute(select(func.count(ProductCatalog.id)))
    price_points = await db.execute(select(func.count(PriceHistory.id)))

    tq = float(total_quoted.scalar() or 0)
    tc = float(total_cost.scalar() or 0)

    return {
        "rfq_pipeline": rfq_counts,
        "total_rfqs": sum(rfq_counts.values()),
        "financials": {
            "total_quoted": round(tq, 2),
            "total_cost": round(tc, 2),
            "gross_margin": round(tq - tc, 2),
            "margin_pct": round((tq - tc) / max(tq, 1) * 100, 1),
        },
        "won": {
            "count": won_rfqs.scalar(),
            "value": round(float(won_value.scalar() or 0), 2),
        },
        "fulfillment": {
            "ordered": po_ordered.scalar(),
            "shipped": po_shipped.scalar(),
            "delivered": po_delivered.scalar(),
        },
        "intelligence": {
            "products_cataloged": prod_count.scalar(),
            "price_data_points": price_points.scalar(),
        },
    }
