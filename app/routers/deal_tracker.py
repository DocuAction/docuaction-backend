"""Deal Tracker — Dashboard, alerts, workflow tracking for deal registrations.

Problem solved: Team registers deal with OEM → OEM sends pricing to distributor →
Team never checks if distributor updated pricing → Deal expires → Lost opportunity.

This module adds:
- Dashboard showing ALL deals with countdown timers
- Alert system for expiring deals (7/14/30 day windows)
- Workflow status: Registered → OEM Approved → Supplier Notified → Price Received → Quote Created → Submitted
- Action items per deal
- Automatic expiration detection
"""
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, and_, or_, case, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import DealRegistration, RFQ, Quote, QuoteLineItem, Supplier, SupplierQuoteRequest
from app.services.auth import get_current_user

router = APIRouter(prefix="/deal-tracker", tags=["Deal Tracker"])


@router.get("/dashboard")
async def deal_dashboard(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Master dashboard — all deal registrations with alerts and workflow status."""
    today = date.today()

    result = await db.execute(
        select(DealRegistration).order_by(
            case(
                (DealRegistration.status == "Active", 0),
                (DealRegistration.status == "Expired", 1),
                else_=2
            ),
            DealRegistration.expiration_date.asc()
        )
    )
    deals = result.scalars().all()

    enriched = []
    alerts = []
    stats = {"total": 0, "active": 0, "expired": 0, "used": 0,
             "expiring_7d": 0, "expiring_14d": 0, "expiring_30d": 0,
             "needs_action": 0, "missing_quote": 0, "total_discount_value": 0}

    for deal in deals:
        stats["total"] += 1
        d = {
            "id": str(deal.id),
            "oem": deal.oem,
            "registration_id": deal.registration_id,
            "rfq_id": str(deal.rfq_id) if deal.rfq_id else None,
            "sku_list": deal.sku_list,
            "discount_pct": deal.discount_pct,
            "special_unit_price": float(deal.special_unit_price) if deal.special_unit_price else None,
            "expiration_date": str(deal.expiration_date) if deal.expiration_date else None,
            "status": deal.status.value if hasattr(deal.status, 'value') else deal.status,
            "created_at": str(deal.created_at) if deal.created_at else None,
        }

        # Calculate days remaining
        if deal.expiration_date:
            days_left = (deal.expiration_date - today).days
            d["days_left"] = days_left
            d["is_expired"] = days_left < 0

            # Auto-detect expired deals
            if days_left < 0 and deal.status == "Active":
                d["status"] = "Expired"
                d["auto_expired"] = True
                deal.status = "Expired"

            # Urgency classification
            if days_left < 0:
                d["urgency"] = "expired"
                stats["expired"] += 1
            elif days_left <= 7:
                d["urgency"] = "critical"
                stats["expiring_7d"] += 1
                alerts.append({
                    "severity": "critical",
                    "type": "expiring_deal",
                    "message": f"🔴 CRITICAL: {deal.oem} deal #{deal.registration_id} expires in {days_left} day{'s' if days_left != 1 else ''}!",
                    "deal_id": str(deal.id),
                    "days_left": days_left,
                    "oem": deal.oem,
                    "registration_id": deal.registration_id,
                })
            elif days_left <= 14:
                d["urgency"] = "warning"
                stats["expiring_14d"] += 1
                alerts.append({
                    "severity": "warning",
                    "type": "expiring_deal",
                    "message": f"🟡 WARNING: {deal.oem} deal #{deal.registration_id} expires in {days_left} days",
                    "deal_id": str(deal.id),
                    "days_left": days_left,
                    "oem": deal.oem,
                    "registration_id": deal.registration_id,
                })
            elif days_left <= 30:
                d["urgency"] = "attention"
                stats["expiring_30d"] += 1
            else:
                d["urgency"] = "ok"
                stats["active"] += 1
        else:
            d["days_left"] = None
            d["urgency"] = "unknown"
            d["is_expired"] = False

        if deal.status == "Used":
            stats["used"] += 1

        # Check workflow: does this deal have a quote created with it?
        d["workflow"] = await _get_deal_workflow(deal, db)

        # Determine action items
        d["action_items"] = _get_action_items(d)
        if d["action_items"]:
            stats["needs_action"] += 1

        # Check if RFQ has a quote
        if deal.rfq_id:
            rfq_result = await db.execute(select(RFQ).where(RFQ.id == deal.rfq_id))
            rfq = rfq_result.scalar_one_or_none()
            if rfq:
                d["rfq_title"] = rfq.title
                d["rfq_status"] = rfq.status.value if hasattr(rfq.status, 'value') else rfq.status
                d["rfq_due_date"] = str(rfq.due_date) if rfq.due_date else None
                d["rfq_agency"] = rfq.agency

                # Check if quote exists for this RFQ
                q_result = await db.execute(select(func.count(Quote.id)).where(Quote.rfq_id == deal.rfq_id))
                quote_count = q_result.scalar()
                d["has_quote"] = quote_count > 0

                if not d["has_quote"] and d["urgency"] in ("critical", "warning"):
                    stats["missing_quote"] += 1
                    alerts.append({
                        "severity": "critical" if d["urgency"] == "critical" else "warning",
                        "type": "missing_quote",
                        "message": f"📋 {deal.oem} deal #{deal.registration_id} linked to RFQ but NO QUOTE created yet!",
                        "deal_id": str(deal.id),
                        "rfq_id": str(deal.rfq_id),
                    })
            else:
                d["rfq_title"] = None
                d["has_quote"] = False
        else:
            d["rfq_title"] = None
            d["rfq_status"] = None
            d["rfq_due_date"] = None
            d["rfq_agency"] = None
            d["has_quote"] = False

            # Alert: deal not linked to any RFQ
            if deal.status == "Active" and d.get("days_left", 999) <= 14:
                alerts.append({
                    "severity": "warning",
                    "type": "no_rfq",
                    "message": f"⚠ {deal.oem} deal #{deal.registration_id} is NOT linked to any RFQ",
                    "deal_id": str(deal.id),
                })

        # Check supplier quote status
        if deal.rfq_id:
            sq_result = await db.execute(
                select(SupplierQuoteRequest).where(SupplierQuoteRequest.rfq_id == deal.rfq_id)
            )
            supplier_quotes = sq_result.scalars().all()
            d["supplier_quotes"] = [{
                "supplier_name": sq.supplier_name,
                "status": sq.status.value if hasattr(sq.status, 'value') else sq.status,
                "requested_date": str(sq.requested_date) if sq.requested_date else None,
                "received": sq.received,
                "quoted_amount": float(sq.quoted_amount) if sq.quoted_amount else None,
            } for sq in supplier_quotes]

            # Alert: supplier quote pending
            pending_sq = [sq for sq in supplier_quotes if not sq.received]
            if pending_sq and d["urgency"] in ("critical", "warning"):
                for sq in pending_sq:
                    alerts.append({
                        "severity": "critical",
                        "type": "supplier_pending",
                        "message": f"📦 Waiting on {sq.supplier_name} pricing for {deal.oem} deal #{deal.registration_id}",
                        "deal_id": str(deal.id),
                    })
        else:
            d["supplier_quotes"] = []

        enriched.append(d)

    await db.flush()  # Save any auto-expiration status changes

    # Sort alerts by severity
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))

    return {
        "deals": enriched,
        "alerts": alerts,
        "stats": stats,
    }


@router.get("/alerts")
async def get_alerts_only(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get just the alerts for header notification badge."""
    dashboard = await deal_dashboard(user=user, db=db)
    return {
        "alerts": dashboard["alerts"],
        "count": len(dashboard["alerts"]),
        "critical_count": len([a for a in dashboard["alerts"] if a["severity"] == "critical"]),
    }


@router.post("/{deal_id}/extend")
async def extend_deal(deal_id: str, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Extend deal registration expiration date."""
    result = await db.execute(select(DealRegistration).where(DealRegistration.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(404)
    new_date = payload.get("new_expiration_date")
    if new_date:
        deal.expiration_date = date.fromisoformat(new_date)
        deal.status = "Active"
    await db.flush()
    return {"status": "extended", "new_date": str(deal.expiration_date)}


@router.post("/{deal_id}/mark-used")
async def mark_deal_used(deal_id: str, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Mark deal as used (quote submitted with deal pricing)."""
    result = await db.execute(select(DealRegistration).where(DealRegistration.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(404)
    deal.status = "Used"
    await db.flush()
    return {"status": "marked_used"}


@router.post("/{deal_id}/link-rfq")
async def link_deal_to_rfq(deal_id: str, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Link a deal registration to an RFQ."""
    result = await db.execute(select(DealRegistration).where(DealRegistration.id == deal_id))
    deal = result.scalar_one_or_none()
    if not deal:
        raise HTTPException(404)
    rfq_id = payload.get("rfq_id")
    if not rfq_id:
        raise HTTPException(400, "rfq_id required")
    # Verify RFQ exists
    rfq_result = await db.execute(select(RFQ).where(RFQ.id == rfq_id))
    if not rfq_result.scalar_one_or_none():
        raise HTTPException(404, "RFQ not found")
    deal.rfq_id = rfq_id
    await db.flush()
    return {"status": "linked"}


async def _get_deal_workflow(deal, db):
    """Determine what stage the deal is in."""
    steps = [
        {"step": "registered", "label": "Deal Registered", "done": True},
        {"step": "oem_approved", "label": "OEM Approved", "done": deal.discount_pct is not None or deal.special_unit_price is not None},
    ]

    # Check if linked to RFQ
    has_rfq = deal.rfq_id is not None
    steps.append({"step": "rfq_linked", "label": "Linked to RFQ", "done": has_rfq})

    # Check if supplier was notified / quote requested
    if has_rfq:
        sq = await db.execute(select(func.count(SupplierQuoteRequest.id)).where(
            SupplierQuoteRequest.rfq_id == deal.rfq_id))
        sq_count = sq.scalar()
        sq_received = 0
        if sq_count > 0:
            sq_r = await db.execute(select(func.count(SupplierQuoteRequest.id)).where(
                and_(SupplierQuoteRequest.rfq_id == deal.rfq_id, SupplierQuoteRequest.received == True)))
            sq_received = sq_r.scalar()
        steps.append({"step": "supplier_notified", "label": "Supplier Quote Requested", "done": sq_count > 0})
        steps.append({"step": "price_received", "label": "Supplier Price Received", "done": sq_received > 0})

        # Check if quote created
        q = await db.execute(select(func.count(Quote.id)).where(Quote.rfq_id == deal.rfq_id))
        steps.append({"step": "quote_created", "label": "Quote Created", "done": q.scalar() > 0})

        # Check if quote submitted
        qs = await db.execute(select(func.count(Quote.id)).where(
            and_(Quote.rfq_id == deal.rfq_id, Quote.is_locked == True)))
        steps.append({"step": "submitted", "label": "Quote Submitted", "done": qs.scalar() > 0})
    else:
        steps.extend([
            {"step": "supplier_notified", "label": "Supplier Quote Requested", "done": False},
            {"step": "price_received", "label": "Supplier Price Received", "done": False},
            {"step": "quote_created", "label": "Quote Created", "done": False},
            {"step": "submitted", "label": "Quote Submitted", "done": False},
        ])

    return steps


def _get_action_items(deal_data):
    """Generate action items based on workflow state."""
    actions = []
    workflow = deal_data.get("workflow", [])
    urgency = deal_data.get("urgency", "ok")
    days_left = deal_data.get("days_left")

    if urgency == "expired":
        actions.append({"priority": "critical", "action": "Deal EXPIRED — request extension from OEM or mark as lost"})
        return actions

    # Check each workflow step
    step_map = {s["step"]: s["done"] for s in workflow}

    if not step_map.get("oem_approved"):
        actions.append({"priority": "high", "action": f"Follow up with {deal_data['oem']} for deal approval and special pricing"})

    if not step_map.get("rfq_linked"):
        actions.append({"priority": "high", "action": "Link this deal registration to an RFQ"})

    if step_map.get("rfq_linked") and not step_map.get("supplier_notified"):
        actions.append({"priority": "high", "action": "Request pricing from distributor/supplier with deal reg info"})

    if step_map.get("supplier_notified") and not step_map.get("price_received"):
        actions.append({"priority": "critical" if urgency in ("critical", "warning") else "medium",
                        "action": "Follow up with supplier — pricing not yet received"})

    if step_map.get("price_received") and not step_map.get("quote_created"):
        actions.append({"priority": "critical" if urgency == "critical" else "high",
                        "action": "Create quote using deal registration pricing"})

    if step_map.get("quote_created") and not step_map.get("submitted"):
        actions.append({"priority": "high" if urgency in ("critical", "warning") else "medium",
                        "action": "Submit quote to customer"})

    # Time-based urgency boost
    if days_left is not None and days_left <= 7 and not step_map.get("submitted"):
        actions.insert(0, {"priority": "critical",
                           "action": f"⏰ Only {days_left} day{'s' if days_left != 1 else ''} left — complete all pending steps NOW"})

    return actions
