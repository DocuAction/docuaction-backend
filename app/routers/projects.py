from uuid import UUID
from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import require_role
from app.database import get_db
from app.models import DevProject, ProjectStage, ProjectType, SupplierQuoteRequest, SupplierQuoteStatus
from app.services.auth import get_current_user

# Router-level auth. app/routers/ is dormant (see __init__.py) and this
# dependency is the precondition recorded there for ever mounting it: every
# route inherits the check, so a handler added later cannot arrive unguarded.
router = APIRouter(prefix="/projects", tags=["Pipeline & Projects"], dependencies=[Depends(require_role("contributor"))])
class ProjectCreate(BaseModel):
    title: str
    project_type: str = "Development"
    agency: str | None = None
    solicitation_number: str | None = None
    description: str | None = None
    due_date: date | None = None
    start_date: date | None = None
    estimated_value: Decimal | None = None
    assigned_to: str | None = None
    source: str | None = None
    contract_number: str | None = None
    amendment: str | None = None


class SupplierQuoteCreate(BaseModel):
    rfq_id: UUID | None = None
    project_id: UUID | None = None
    supplier_name: str
    requested_date: date
    notes: str | None = None


# ── Projects CRUD ──

@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    # Store source/contract_number/amendment/start_date if model supports them
    project = DevProject(
        title=data['title'],
        project_type=data.get('project_type', 'Development'),
        agency=data.get('agency'),
        solicitation_number=data.get('solicitation_number'),
        description=data.get('description'),
        due_date=data.get('due_date'),
        estimated_value=data.get('estimated_value'),
        assigned_to=data.get('assigned_to'),
    )
    # Set extra fields via setattr for migrated columns
    for field in ('source', 'contract_number', 'amendment', 'start_date'):
        if data.get(field):
            try:
                setattr(project, field, data[field])
            except:
                pass
    project.created_by = user.full_name
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return _proj_dict(project)


@router.get("")
async def list_projects(
    search: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(DevProject).order_by(desc(DevProject.created_at))
    if search:
        q = q.where(or_(
            DevProject.title.ilike(f"%{search}%"),
            DevProject.agency.ilike(f"%{search}%"),
            DevProject.solicitation_number.ilike(f"%{search}%"),
            DevProject.assigned_to.ilike(f"%{search}%"),
        ))
    if stage:
        q = q.where(DevProject.stage == stage)

    # Filter active vs history
    if status == 'active':
        q = q.where(or_(DevProject.due_date == None, DevProject.due_date >= date.today()))
        q = q.where(DevProject.stage.notin_(['Completed', 'Lost', 'Expired']))
    elif status == 'history':
        q = q.where(or_(
            DevProject.due_date < date.today(),
            DevProject.stage.in_(['Completed', 'Lost', 'Expired'])
        ))

    result = await db.execute(q)
    return [_proj_dict(p) for p in result.scalars().all()]


@router.get("/stats/summary")
async def project_stats(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(DevProject.id)))
    active = await db.execute(select(func.count(DevProject.id)).where(
        DevProject.stage.notin_(['Completed', 'Lost', 'Expired'])
    ))
    pipeline_val = await db.execute(select(func.sum(DevProject.estimated_value)).where(
        DevProject.stage.notin_(['Completed', 'Lost', 'Expired'])
    ))

    # Stage counts
    stages = {}
    for s in ['Intake', 'Review', 'Proposal', 'Submitted', 'Awarded', 'Active', 'Completed', 'Lost']:
        c = await db.execute(select(func.count(DevProject.id)).where(DevProject.stage == s))
        stages[s] = c.scalar()

    return {
        "total": total.scalar(),
        "active": active.scalar(),
        "pipeline_value": float(pipeline_val.scalar() or 0),
        "stages": stages,
    }


@router.get("/alerts")
async def get_alerts(user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Get alerts: due dates approaching, expired RFQs, missing supplier quotes."""
    alerts = []
    today = date.today()
    tomorrow = today + timedelta(days=1)
    two_days = today + timedelta(days=2)

    # Due date alerts
    due_soon = await db.execute(
        select(DevProject).where(
            DevProject.due_date != None,
            DevProject.due_date <= two_days,
            DevProject.due_date >= today,
            DevProject.stage.notin_(['Completed', 'Lost', 'Expired'])
        )
    )
    for p in due_soon.scalars().all():
        days_left = (p.due_date - today).days
        alerts.append({
            "type": "due_date",
            "severity": "critical" if days_left == 0 else "warning",
            "message": f"'{p.title}' due {'TODAY' if days_left == 0 else f'in {days_left} day(s)'} ({p.due_date})",
            "project_id": str(p.id),
        })

    # Expired
    expired = await db.execute(
        select(DevProject).where(
            DevProject.due_date != None,
            DevProject.due_date < today,
            DevProject.stage.notin_(['Completed', 'Lost', 'Expired'])
        )
    )
    for p in expired.scalars().all():
        alerts.append({
            "type": "expired",
            "severity": "critical",
            "message": f"'{p.title}' EXPIRED on {p.due_date}",
            "project_id": str(p.id),
        })

    # Missing supplier quotes
    pending_quotes = await db.execute(
        select(SupplierQuoteRequest).where(SupplierQuoteRequest.status == SupplierQuoteStatus.PENDING)
    )
    for sq in pending_quotes.scalars().all():
        days_waiting = (today - sq.requested_date).days
        if days_waiting >= 2:
            alerts.append({
                "type": "supplier_quote",
                "severity": "warning" if days_waiting < 5 else "critical",
                "message": f"Supplier quote from '{sq.supplier_name}' pending {days_waiting} days",
                "project_id": str(sq.project_id) if sq.project_id else None,
            })

    return {"alerts": alerts, "count": len(alerts)}


@router.get("/{project_id}")
async def get_project(project_id: UUID, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DevProject).where(DevProject.id == project_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404)
    return _proj_dict(p)


@router.patch("/{project_id}")
async def update_project(project_id: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DevProject).where(DevProject.id == project_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(p, k) and k != 'id':
            setattr(p, k, v)
    p.updated_by = user.full_name
    await db.flush()
    return {"status": "updated"}


def _proj_dict(p):
    today = date.today()
    days_left = None
    is_expired = False
    if p.due_date:
        days_left = (p.due_date - today).days
        is_expired = days_left < 0

    return {
        "id": str(p.id), "title": p.title,
        "project_type": p.project_type,
        "agency": p.agency,
        "solicitation_number": p.solicitation_number,
        "description": p.description,
        "due_date": str(p.due_date) if p.due_date else None,
        "start_date": str(getattr(p, 'start_date', None)) if getattr(p, 'start_date', None) else None,
        "estimated_value": float(p.estimated_value) if p.estimated_value else None,
        "stage": p.stage,
        "assigned_to": p.assigned_to,
        "source": getattr(p, 'source', None),
        "contract_number": getattr(p, 'contract_number', None),
        "amendment": getattr(p, 'amendment', None),
        "created_by": p.created_by,
        "updated_by": p.updated_by,
        "created_at": str(p.created_at) if p.created_at else None,
        "days_left": days_left,
        "is_expired": is_expired,
    }


# ── Supplier Quote Tracking ──

@router.post("/supplier-quotes", status_code=201)
async def create_supplier_quote_request(payload: SupplierQuoteCreate, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    sq = SupplierQuoteRequest(**payload.model_dump())
    db.add(sq)
    await db.flush()
    await db.refresh(sq)
    return {"id": str(sq.id), "supplier": sq.supplier_name, "status": sq.status}


@router.get("/supplier-quotes")
async def list_supplier_quotes(
    project_id: UUID | None = None,
    status: str | None = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(SupplierQuoteRequest).order_by(desc(SupplierQuoteRequest.created_at))
    if project_id:
        q = q.where(SupplierQuoteRequest.project_id == project_id)
    if status:
        q = q.where(SupplierQuoteRequest.status == status)
    result = await db.execute(q)
    return [{
        "id": str(sq.id),
        "supplier_name": sq.supplier_name,
        "requested_date": str(sq.requested_date),
        "received": sq.received,
        "received_date": str(sq.received_date) if sq.received_date else None,
        "status": sq.status,
        "quoted_amount": float(sq.quoted_amount) if sq.quoted_amount else None,
        "notes": sq.notes,
        "project_id": str(sq.project_id) if sq.project_id else None,
        "rfq_id": str(sq.rfq_id) if sq.rfq_id else None,
    } for sq in result.scalars().all()]


@router.patch("/supplier-quotes/{sq_id}")
async def update_supplier_quote(sq_id: UUID, payload: dict, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SupplierQuoteRequest).where(SupplierQuoteRequest.id == sq_id))
    sq = result.scalar_one_or_none()
    if not sq:
        raise HTTPException(404)
    for k, v in payload.items():
        if hasattr(sq, k) and k != 'id':
            setattr(sq, k, v)
    if payload.get('status') == 'Received':
        sq.received = True
        sq.received_date = date.today()
    await db.flush()
    return {"status": "updated"}
