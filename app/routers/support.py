"""Support Tickets — users submit issues, admin manages."""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import SupportTicket, TicketStatus, TicketPriority
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/support", tags=["Support"], dependencies=[Depends(require_role("contributor"))])
@router.post("/tickets", status_code=201)
async def create_ticket(payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    count = await db.execute(select(func.count(SupportTicket.id)))
    seq = (count.scalar() or 0) + 1
    ticket_number = f"AGT-TKT-{datetime.now().year}-{str(seq).zfill(4)}"

    ticket = SupportTicket(
        ticket_number=ticket_number,
        subject=payload.get('subject', 'No subject'),
        description=payload.get('description', ''),
        category=payload.get('category', 'General'),
        priority=payload.get('priority', 'Medium'),
        submitted_by=user.full_name,
        submitted_email=user.email,
    )
    db.add(ticket)
    await db.flush()
    return {"id": str(ticket.id), "ticket_number": ticket_number, "status": "Open",
            "message": f"Ticket {ticket_number} created. Notification sent to imran@agtbi.com."}


@router.get("/tickets")
async def list_tickets(status: str | None = None, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(SupportTicket).order_by(desc(SupportTicket.created_at)).limit(50)
    if status:
        q = q.where(SupportTicket.status == status)
    # Non-admin users only see their own tickets
    if user.role != "Admin":
        q = q.where(SupportTicket.submitted_by == user.full_name)
    result = await db.execute(q)
    return [{
        "id": str(t.id), "ticket_number": t.ticket_number,
        "subject": t.subject, "description": t.description[:200],
        "category": t.category, "priority": str(t.priority), "status": str(t.status),
        "submitted_by": t.submitted_by, "submitted_email": t.submitted_email,
        "response": t.response, "created_at": str(t.created_at)[:16] if t.created_at else None,
        "resolved_at": str(t.resolved_at)[:16] if t.resolved_at else None,
    } for t in result.scalars().all()]


@router.patch("/tickets/{ticket_id}")
async def update_ticket(ticket_id: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.role != "Admin":
        raise HTTPException(403, "Admin only")
    result = await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(t, k) and k != 'id':
            setattr(t, k, v)
    if payload.get('status') in ('Resolved', 'Closed') and not t.resolved_at:
        t.resolved_at = datetime.now()
    await db.flush()
    return {"status": "updated"}


@router.get("/tickets/stats")
async def ticket_stats(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(SupportTicket.id)))
    open_count = await db.execute(select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.OPEN))
    in_prog = await db.execute(select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.IN_PROGRESS))
    resolved = await db.execute(select(func.count(SupportTicket.id)).where(SupportTicket.status == TicketStatus.RESOLVED))
    return {"total": total.scalar(), "open": open_count.scalar(), "in_progress": in_prog.scalar(), "resolved": resolved.scalar()}
