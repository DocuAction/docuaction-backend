"""
Deal Workspace — Unified RFQ lifecycle view
Global search, communication logging, task automation, agency metrics
"""
from uuid import UUID
from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_, desc, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.security import require_role
from app.database import get_db
from app.models import (
    RFQ, Quote, QuoteLineItem, BOMItem, Supplier, PurchaseOrder, DealStatus,
    PriceHistory, SupplierQuoteRequest, SupplierQuoteStatus,
    CommunicationLog, Task, TaskStatus, AgencyMetric
)
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/deals", tags=["Deal Workspace"], dependencies=[Depends(require_role("contributor"))])
# ══════════════════════════════════════════════════════════════
# DEAL WORKSPACE — Single unified view for an RFQ
# ══════════════════════════════════════════════════════════════

@router.get("/workspace/{rfq_id}")
async def get_deal_workspace(rfq_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Complete deal view — RFQ + BOM + quotes + supplier quotes + POs + comms + tasks."""

    # RFQ
    rfq_r = await db.execute(select(RFQ).where(RFQ.id == rfq_id))
    rfq = rfq_r.scalar_one_or_none()
    if not rfq:
        raise HTTPException(404, "RFQ not found")

    today = date.today()
    days_left = (rfq.due_date - today).days if rfq.due_date else None

    rfq_data = {
        "id": str(rfq.id), "title": rfq.title, "agency": rfq.agency,
        "solicitation_number": rfq.solicitation_number, "status": str(rfq.status),
        "customer_type": str(rfq.customer_type), "due_date": str(rfq.due_date) if rfq.due_date else None,
        "days_left": days_left, "is_expired": days_left is not None and days_left < 0,
        "estimated_value": float(rfq.estimated_value) if rfq.estimated_value else None,
        "contract_officer_name": getattr(rfq, 'contract_officer_name', None),
        "contract_officer_email": getattr(rfq, 'contract_officer_email', None),
        "contract_officer_phone": getattr(rfq, 'contract_officer_phone', None),
        "department": getattr(rfq, 'department', None),
        "source": getattr(rfq, 'source', None),
        "created_by": rfq.created_by, "created_at": str(rfq.created_at) if rfq.created_at else None,
    }

    # BOM
    bom_r = await db.execute(select(BOMItem).where(BOMItem.rfq_id == rfq_id).order_by(BOMItem.created_at))
    bom_items = [{
        "id": str(b.id), "part_number": b.part_number, "manufacturer": b.manufacturer,
        "description": b.description, "quantity": b.quantity,
        "unit_cost": float(b.unit_cost) if b.unit_cost else None,
    } for b in bom_r.scalars().all()]

    # Quotes (all versions)
    quotes_r = await db.execute(
        select(Quote).where(Quote.rfq_id == rfq_id)
        .options(selectinload(Quote.line_items))
        .order_by(desc(Quote.version))
    )
    quotes = []
    for q in quotes_r.scalars().unique().all():
        quotes.append({
            "id": str(q.id), "quote_number": q.quote_number, "version": q.version,
            "status": str(q.status), "is_locked": q.is_locked,
            "total_sell": float(q.total_sell_price or 0), "total_cost": float(q.total_cost or 0),
            "margin_pct": float(q.overall_margin_pct or 0), "total_tax": float(q.total_tax or 0),
            "line_count": len(q.line_items) if q.line_items else 0,
            "created_at": str(q.created_at) if q.created_at else None,
        })

    # Supplier quote requests
    sq_r = await db.execute(select(SupplierQuoteRequest).where(SupplierQuoteRequest.rfq_id == rfq_id))
    supplier_quotes = [{
        "id": str(sq.id), "supplier_name": sq.supplier_name, "status": str(sq.status),
        "requested_date": str(sq.requested_date), "received": sq.received,
        "received_date": str(sq.received_date) if sq.received_date else None,
        "quoted_amount": float(sq.quoted_amount) if sq.quoted_amount else None,
    } for sq in sq_r.scalars().all()]

    # Purchase orders
    po_r = await db.execute(select(PurchaseOrder).where(PurchaseOrder.rfq_id == rfq_id))
    pos = [{
        "id": str(po.id), "po_number": po.po_number, "supplier_name": po.supplier_name,
        "total_cost": float(po.total_cost) if po.total_cost else None,
        "total_sell": float(po.total_sell) if po.total_sell else None,
        "status": str(po.status),
        "ordered_date": str(po.ordered_date) if po.ordered_date else None,
        "shipped_date": str(po.shipped_date) if po.shipped_date else None,
        "delivered_date": str(po.delivered_date) if po.delivered_date else None,
        "tracking_number": po.tracking_number,
    } for po in po_r.scalars().all()]

    # Communications
    comm_r = await db.execute(select(CommunicationLog).where(CommunicationLog.rfq_id == rfq_id).order_by(desc(CommunicationLog.created_at)))
    comms = [{
        "id": str(c.id), "direction": c.direction, "type": c.comm_type,
        "recipient_name": c.recipient_name, "recipient_email": c.recipient_email,
        "subject": c.subject, "status": c.status, "sent_by": c.sent_by,
        "date": str(c.created_at) if c.created_at else None,
    } for c in comm_r.scalars().all()]

    # Tasks
    task_r = await db.execute(select(Task).where(Task.rfq_id == rfq_id).order_by(Task.due_date))
    tasks = [{
        "id": str(t.id), "title": t.title, "status": str(t.status),
        "assigned_to": t.assigned_to, "due_date": str(t.due_date) if t.due_date else None,
        "task_type": t.task_type,
    } for t in task_r.scalars().all()]

    # Alerts for this deal
    alerts = []
    if days_left is not None:
        if days_left < 0:
            alerts.append({"type": "expired", "severity": "critical", "message": f"RFQ expired {abs(days_left)} days ago"})
        elif days_left <= 2:
            alerts.append({"type": "due_soon", "severity": "warning", "message": f"Due in {days_left} day(s)"})

    for sq in supplier_quotes:
        if sq['status'] == 'Pending':
            days_waiting = (today - date.fromisoformat(sq['requested_date'])).days
            if days_waiting >= 2:
                alerts.append({"type": "supplier_pending", "severity": "warning", "message": f"Quote from {sq['supplier_name']} pending {days_waiting} days"})

    if quotes:
        latest = quotes[0]
        if latest['margin_pct'] < 5:
            alerts.append({"type": "low_margin", "severity": "critical", "message": f"Margin {latest['margin_pct']:.1f}% below 5% minimum"})

    return {
        "rfq": rfq_data, "bom": bom_items, "quotes": quotes,
        "supplier_quotes": supplier_quotes, "purchase_orders": pos,
        "communications": comms, "tasks": tasks, "alerts": alerts,
    }


# ══════════════════════════════════════════════════════════════
# GLOBAL SEARCH
# ══════════════════════════════════════════════════════════════

@router.get("/search")
async def global_search(q: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Search across RFQs, quotes, suppliers, agencies, part numbers."""
    results = {"rfqs": [], "quotes": [], "suppliers": [], "products": []}

    # RFQs
    rfqs = await db.execute(select(RFQ).where(or_(
        RFQ.title.ilike(f"%{q}%"), RFQ.solicitation_number.ilike(f"%{q}%"),
        RFQ.agency.ilike(f"%{q}%"),
    )).order_by(desc(RFQ.created_at)).limit(10))
    for r in rfqs.scalars().all():
        results["rfqs"].append({"id": str(r.id), "title": r.title, "solicitation": r.solicitation_number, "agency": r.agency, "status": str(r.status)})

    # Quotes
    quotes = await db.execute(select(Quote).where(Quote.quote_number.ilike(f"%{q}%")).limit(10))
    for qr in quotes.scalars().all():
        results["quotes"].append({"id": str(qr.id), "quote_number": qr.quote_number, "total": float(qr.total_sell_price or 0)})

    # Suppliers
    sups = await db.execute(select(Supplier).where(or_(
        Supplier.name.ilike(f"%{q}%"), Supplier.manufacturer_focus.ilike(f"%{q}%"),
    )).limit(10))
    for s in sups.scalars().all():
        results["suppliers"].append({"id": str(s.id), "name": s.name, "type": getattr(s, 'supplier_type', None)})

    # Products/BOM
    from app.models import ProductCatalog
    prods = await db.execute(select(ProductCatalog).where(or_(
        ProductCatalog.part_number.ilike(f"%{q}%"), ProductCatalog.manufacturer.ilike(f"%{q}%"),
    )).limit(10))
    for p in prods.scalars().all():
        results["products"].append({"part_number": p.part_number, "manufacturer": p.manufacturer, "last_cost": float(p.last_known_cost) if p.last_known_cost else None})

    results["total"] = sum(len(v) for v in results.values())
    return results


# ══════════════════════════════════════════════════════════════
# COMMUNICATION LOG
# ══════════════════════════════════════════════════════════════

@router.post("/comms", status_code=201)
async def log_communication(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    comm = CommunicationLog(
        rfq_id=payload.get('rfq_id'),
        direction=payload.get('direction', 'outbound'),
        comm_type=payload.get('type', 'email'),
        recipient_name=payload.get('recipient_name'),
        recipient_email=payload.get('recipient_email'),
        subject=payload.get('subject'),
        body_preview=payload.get('body', '')[:500],
        status=payload.get('status', 'sent'),
        sent_by=user.full_name,
    )
    db.add(comm)
    await db.flush()
    return {"id": str(comm.id), "status": "logged"}


@router.get("/comms")
async def list_comms(rfq_id: UUID | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(CommunicationLog).order_by(desc(CommunicationLog.created_at)).limit(50)
    if rfq_id:
        q = q.where(CommunicationLog.rfq_id == rfq_id)
    result = await db.execute(q)
    return [{
        "id": str(c.id), "rfq_id": str(c.rfq_id) if c.rfq_id else None,
        "direction": c.direction, "type": c.comm_type,
        "recipient_name": c.recipient_name, "recipient_email": c.recipient_email,
        "subject": c.subject, "status": c.status, "sent_by": c.sent_by,
        "date": str(c.created_at) if c.created_at else None,
    } for c in result.scalars().all()]


# ══════════════════════════════════════════════════════════════
# TASKS
# ══════════════════════════════════════════════════════════════

@router.post("/tasks", status_code=201)
async def create_task(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    task = Task(
        rfq_id=payload.get('rfq_id'),
        title=payload['title'],
        description=payload.get('description'),
        assigned_to=payload.get('assigned_to', user.full_name),
        due_date=payload.get('due_date'),
        task_type=payload.get('task_type', 'general'),
    )
    db.add(task)
    await db.flush()
    return {"id": str(task.id), "status": "created"}


@router.get("/tasks")
async def list_tasks(status: str | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(Task).order_by(Task.due_date).limit(50)
    if status:
        q = q.where(Task.status == status)
    result = await db.execute(q)
    return [{
        "id": str(t.id), "title": t.title, "status": str(t.status),
        "assigned_to": t.assigned_to, "due_date": str(t.due_date) if t.due_date else None,
        "task_type": t.task_type, "rfq_id": str(t.rfq_id) if t.rfq_id else None,
    } for t in result.scalars().all()]


@router.patch("/tasks/{task_id}")
async def update_task(task_id: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).where(Task.id == task_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(t, k) and k != 'id':
            setattr(t, k, v)
    await db.flush()
    return {"status": "updated"}


# ══════════════════════════════════════════════════════════════
# AGENCY METRICS
# ══════════════════════════════════════════════════════════════

@router.get("/agency-metrics")
async def get_agency_metrics(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Calculate live agency metrics from RFQ data."""
    agencies = await db.execute(select(distinct(RFQ.agency)).where(RFQ.agency != None))
    metrics = []
    for (agency_name,) in agencies.all():
        if not agency_name:
            continue
        total = await db.execute(select(func.count(RFQ.id)).where(RFQ.agency == agency_name))
        won = await db.execute(select(func.count(RFQ.id)).where(RFQ.agency == agency_name, RFQ.status == 'Won'))
        lost = await db.execute(select(func.count(RFQ.id)).where(RFQ.agency == agency_name, RFQ.status == 'Lost'))

        won_value = await db.execute(
            select(func.sum(Quote.total_sell_price))
            .join(RFQ, Quote.rfq_id == RFQ.id)
            .where(RFQ.agency == agency_name, RFQ.status == 'Won')
        )
        quoted_value = await db.execute(
            select(func.sum(Quote.total_sell_price))
            .join(RFQ, Quote.rfq_id == RFQ.id)
            .where(RFQ.agency == agency_name)
        )

        t = total.scalar()
        w = won.scalar()
        metrics.append({
            "agency": agency_name, "total_rfqs": t, "won": w, "lost": lost.scalar(),
            "win_rate": round(w / max(t, 1) * 100, 1),
            "total_quoted": round(float(quoted_value.scalar() or 0), 2),
            "total_won_value": round(float(won_value.scalar() or 0), 2),
        })
    metrics.sort(key=lambda x: x['total_rfqs'], reverse=True)
    return metrics


# ══════════════════════════════════════════════════════════════
# DASHBOARD ALERTS (aggregated)
# ══════════════════════════════════════════════════════════════

@router.get("/alerts")
async def get_all_alerts(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Aggregated alerts across all deals."""
    alerts = []
    today = date.today()

    # Due date alerts
    rfqs = await db.execute(select(RFQ).where(
        RFQ.due_date != None,
        RFQ.status.notin_(['Won', 'Lost', 'Cancelled']),
    ))
    for r in rfqs.scalars().all():
        days = (r.due_date - today).days
        if days < 0:
            alerts.append({"type": "expired", "severity": "critical", "message": f"'{r.title}' expired {abs(days)}d ago", "rfq_id": str(r.id)})
        elif days <= 2:
            alerts.append({"type": "due_soon", "severity": "warning", "message": f"'{r.title}' due in {days}d", "rfq_id": str(r.id)})

    # Pending supplier quotes
    pending = await db.execute(select(SupplierQuoteRequest).where(SupplierQuoteRequest.status == SupplierQuoteStatus.PENDING))
    for sq in pending.scalars().all():
        days_waiting = (today - sq.requested_date).days
        if days_waiting >= 2:
            alerts.append({"type": "supplier_pending", "severity": "warning", "message": f"'{sq.supplier_name}' quote pending {days_waiting}d", "rfq_id": str(sq.rfq_id) if sq.rfq_id else None})

    # Low margin quotes
    low_margin = await db.execute(select(Quote).where(Quote.overall_margin_pct < 5, Quote.overall_margin_pct != None))
    for q in low_margin.scalars().all():
        alerts.append({"type": "low_margin", "severity": "critical", "message": f"Quote {q.quote_number} margin {float(q.overall_margin_pct):.1f}% below 5%", "rfq_id": str(q.rfq_id)})

    # Overdue tasks
    overdue = await db.execute(select(Task).where(Task.due_date < today, Task.status != TaskStatus.COMPLETED))
    for t in overdue.scalars().all():
        alerts.append({"type": "task_overdue", "severity": "warning", "message": f"Task '{t.title}' overdue", "rfq_id": str(t.rfq_id) if t.rfq_id else None})

    alerts.sort(key=lambda a: 0 if a['severity'] == 'critical' else 1)
    return {"alerts": alerts, "count": len(alerts)}


# ══════════════════════════════════════════════════════════════
# AUTO-TASK on RFQ creation hook
# ══════════════════════════════════════════════════════════════

@router.post("/auto-tasks/{rfq_id}")
async def generate_auto_tasks(rfq_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Auto-generate standard tasks for a new RFQ."""
    rfq_r = await db.execute(select(RFQ).where(RFQ.id == rfq_id))
    rfq = rfq_r.scalar_one_or_none()
    if not rfq:
        raise HTTPException(404)

    due = rfq.due_date or (date.today() + timedelta(days=7))
    tasks_to_create = [
        {"title": "Request supplier quotes", "task_type": "supplier_quote", "due_date": due - timedelta(days=5)},
        {"title": "Enter BOM line items", "task_type": "bom_entry", "due_date": due - timedelta(days=4)},
        {"title": "Complete pricing", "task_type": "pricing", "due_date": due - timedelta(days=3)},
        {"title": "Generate and review quote", "task_type": "quote_review", "due_date": due - timedelta(days=2)},
        {"title": "Submit quote to customer", "task_type": "submission", "due_date": due - timedelta(days=1)},
    ]
    created = 0
    for t in tasks_to_create:
        # Skip if already exists
        existing = await db.execute(select(Task).where(Task.rfq_id == rfq_id, Task.task_type == t['task_type']))
        if existing.scalar_one_or_none():
            continue
        db.add(Task(rfq_id=rfq_id, title=t['title'], task_type=t['task_type'],
                     due_date=max(t['due_date'], date.today()), assigned_to=user.full_name))
        created += 1
    await db.flush()
    return {"created": created, "total_tasks": len(tasks_to_create)}
