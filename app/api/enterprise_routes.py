"""
DocuAction Enterprise API Routes
EXTENDS existing routes — backward compatible.

Endpoints:
  Jobs:       POST /create, GET /list, POST /process
  Decisions:  GET /list, GET /{id}, POST /approve, POST /reject
  Actions:    GET /list, GET /{id}, POST /approve, POST /execute
  Audit:      GET /log, GET /entity/{id}
  Tenant:     POST /setup, GET /info
  Queue:      GET /status, POST /process
"""
import uuid
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, and_, func
from pydantic import BaseModel

from app.core.database import get_db
from app.core.security import get_current_user

logger = logging.getLogger("docuaction.enterprise")
router = APIRouter(prefix="/api/enterprise", tags=["Enterprise System"])


# ═══ REQUEST MODELS ═══

class JobCreate(BaseModel):
    document_id: Optional[str] = None
    job_type: str = "summary"  # summary, actions, insights, email, brief, meeting
    context_focus: Optional[str] = None
    domain: str = "enterprise"
    priority: int = 5


class DecisionAction(BaseModel):
    notes: str = ""


class ActionApproval(BaseModel):
    notes: str = ""


class TenantSetup(BaseModel):
    name: str
    domain: str = "enterprise"
    plan: str = "free"
    governance_policy: str = "enterprise"


# ═══ HELPER: Get or create tenant ═══

async def _get_tenant_id(db: AsyncSession, user) -> str:
    """Get tenant_id for user. Auto-creates default tenant if needed."""
    from app.models.enterprise_models import Tenant, TenantUser

    tenant_id = getattr(user, 'tenant_id', None)
    if tenant_id:
        return tenant_id

    # Check if user has a tenant mapping
    result = await db.execute(
        select(TenantUser).where(TenantUser.user_id == str(user.id))
    )
    mapping = result.scalar_one_or_none()
    if mapping:
        return mapping.tenant_id

    # Auto-create default tenant
    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=getattr(user, 'company', '') or user.email.split('@')[1],
        domain="enterprise",
        plan=getattr(user, 'plan', 'free') or 'free',
    )
    db.add(tenant)

    tu = TenantUser(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        user_id=str(user.id),
        role="owner",
    )
    db.add(tu)
    await db.commit()

    return tenant.id


# ═══════════════════════════════════════════════════════
# JOBS
# ═══════════════════════════════════════════════════════

@router.post("/jobs/create")
async def create_job(
    req: JobCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Create a processing job. Idempotent — same input won't create duplicate."""
    from app.models.enterprise_models import ProcessJob, Context

    tenant_id = await _get_tenant_id(db, user)

    # Build idempotency key
    idem_key = hashlib.sha256(
        f"{tenant_id}:{req.document_id}:{req.job_type}:{req.context_focus or ''}".encode()
    ).hexdigest()[:32]

    # Check for existing job
    existing = await db.execute(
        select(ProcessJob).where(ProcessJob.idempotency_key == idem_key)
    )
    if existing.scalar_one_or_none():
        return {"status": "already_exists", "idempotency_key": idem_key}

    # Create context if document_id provided
    context_id = None
    if req.document_id:
        try:
            from app.models.database import Document
            doc = await db.execute(select(Document).where(Document.id == req.document_id))
            doc = doc.scalar_one_or_none()
            if doc:
                ctx = Context(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    type="document",
                    source_name=doc.filename,
                    source_id=str(doc.id),
                    word_count=len((getattr(doc, 'content', '') or '').split()),
                    created_by=str(user.id),
                )
                db.add(ctx)
                context_id = ctx.id
        except Exception as e:
            logger.warning(f"Context creation: {e}")

    job = ProcessJob(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        context_id=context_id,
        idempotency_key=idem_key,
        job_type=req.job_type,
        priority=req.priority,
        input_params={
            "document_id": req.document_id,
            "action_type": req.job_type,
            "context_focus": req.context_focus,
            "domain": req.domain,
        },
        created_by=str(user.id),
    )
    db.add(job)

    # Log creation
    from app.models.enterprise_models import StateAuditLog
    db.add(StateAuditLog(
        id=str(uuid.uuid4()), tenant_id=tenant_id,
        entity_id=job.id, entity_type="job",
        old_state=None, new_state="pending",
        changed_by=str(user.id), change_reason="Job created",
    ))

    await db.commit()

    return {
        "job_id": job.id,
        "status": "pending",
        "job_type": req.job_type,
        "idempotency_key": idem_key,
        "context_id": context_id,
    }


@router.post("/jobs/{job_id}/process")
async def process_job_endpoint(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Trigger processing of a pending job."""
    tenant_id = await _get_tenant_id(db, user)

    from app.services.enterprise_worker import process_job
    result = await process_job(db, job_id, tenant_id)
    return result


@router.get("/jobs")
async def list_jobs(
    status: str = Query(""),
    limit: int = Query(20),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List processing jobs for this tenant."""
    from app.models.enterprise_models import ProcessJob

    tenant_id = await _get_tenant_id(db, user)
    query = select(ProcessJob).where(ProcessJob.tenant_id == tenant_id)
    if status:
        query = query.where(ProcessJob.status == status)
    query = query.order_by(desc(ProcessJob.created_at)).limit(limit)

    result = await db.execute(query)
    jobs = result.scalars().all()

    return {
        "total": len(jobs),
        "jobs": [
            {
                "id": j.id, "type": j.job_type, "status": j.status,
                "priority": j.priority, "retry_count": j.retry_count,
                "processing_time_ms": j.processing_time_ms, "model_used": j.model_used,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in jobs
        ],
    }


# ═══════════════════════════════════════════════════════
# DECISIONS
# ═══════════════════════════════════════════════════════

@router.get("/decisions")
async def list_decisions(
    status: str = Query(""),
    days: int = Query(90),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all decisions in the Decision System of Record."""
    from app.models.enterprise_models import Decision

    tenant_id = await _get_tenant_id(db, user)
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = select(Decision).where(and_(Decision.tenant_id == tenant_id, Decision.created_at >= cutoff))
    if status:
        query = query.where(Decision.status == status)
    query = query.order_by(desc(Decision.created_at)).limit(50)

    result = await db.execute(query)
    decisions = result.scalars().all()

    return {
        "total": len(decisions),
        "decisions": [
            {
                "id": d.id, "text": d.decision_text, "decided_by": d.decided_by,
                "status": d.status, "confidence": d.confidence_score,
                "accuracy": d.accuracy_score, "reliability": d.reliability,
                "version": d.version, "is_immutable": d.is_immutable,
                "model": d.model_name, "correlation_id": d.correlation_id,
                "approved_by": d.approved_by,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "approved_at": d.approved_at.isoformat() if d.approved_at else None,
            }
            for d in decisions
        ],
    }


@router.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get a single decision with full traceability."""
    from app.models.enterprise_models import Decision, Action, Traceability, StateAuditLog

    tenant_id = await _get_tenant_id(db, user)

    result = await db.execute(
        select(Decision).where(and_(Decision.id == decision_id, Decision.tenant_id == tenant_id))
    )
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(404, "Decision not found")

    # Get linked actions
    actions_result = await db.execute(
        select(Action).where(and_(Action.decision_id == decision_id, Action.tenant_id == tenant_id))
    )
    actions = actions_result.scalars().all()

    # Get traceability
    trace_result = await db.execute(
        select(Traceability).where(and_(Traceability.decision_id == decision_id, Traceability.tenant_id == tenant_id))
    )
    traces = trace_result.scalars().all()

    # Get audit trail
    audit_result = await db.execute(
        select(StateAuditLog).where(
            and_(StateAuditLog.entity_id == decision_id, StateAuditLog.entity_type == "decision")
        ).order_by(StateAuditLog.timestamp)
    )
    audits = audit_result.scalars().all()

    return {
        "decision": {
            "id": decision.id, "text": decision.decision_text,
            "decided_by": decision.decided_by, "rationale": decision.rationale,
            "options_considered": decision.options_considered,
            "status": decision.status, "confidence": decision.confidence_score,
            "accuracy": decision.accuracy_score, "reliability": decision.reliability,
            "version": decision.version, "is_immutable": decision.is_immutable,
            "model": decision.model_name, "correlation_id": decision.correlation_id,
            "stakeholders": decision.stakeholders, "alignment_score": decision.alignment_score,
            "approved_by": decision.approved_by,
            "created_at": decision.created_at.isoformat() if decision.created_at else None,
        },
        "actions": [
            {"id": a.id, "title": a.title, "owner": a.owner, "status": a.status, "priority": a.priority, "type": a.type}
            for a in actions
        ],
        "traceability": [
            {"source": t.source_document, "page": t.page, "quote": t.quote, "confidence": t.confidence, "verified": t.verified}
            for t in traces
        ],
        "audit_trail": [
            {"old": a.old_state, "new": a.new_state, "by": a.changed_by, "reason": a.change_reason, "at": a.timestamp.isoformat()}
            for a in audits
        ],
        "valid_transitions": {
            "decision": ["pending_review", "approved", "rejected"] if decision.status == "draft" else
                       ["approved", "rejected"] if decision.status == "pending_review" else [],
        },
    }


@router.post("/decisions/{decision_id}/approve")
async def approve_decision_endpoint(
    decision_id: str,
    req: DecisionAction,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Approve a decision — makes it immutable, queues linked actions."""
    tenant_id = await _get_tenant_id(db, user)

    from app.services.enterprise_worker import approve_decision
    result = await approve_decision(db, decision_id, tenant_id, user.email, req.notes)
    return result


@router.post("/decisions/{decision_id}/reject")
async def reject_decision_endpoint(
    decision_id: str,
    req: DecisionAction,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Reject a decision with documented reason."""
    tenant_id = await _get_tenant_id(db, user)

    from app.services.enterprise_worker import reject_decision
    result = await reject_decision(db, decision_id, tenant_id, user.email, req.notes)
    return result


@router.post("/decisions/{decision_id}/review")
async def send_for_review(
    decision_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Send a draft decision for HITL review."""
    tenant_id = await _get_tenant_id(db, user)

    from app.services.enterprise_worker import transition_state
    result = await transition_state(
        db, "decision", decision_id, "pending_review",
        str(user.id), tenant_id, "Sent for HITL review",
    )
    return result


# ═══════════════════════════════════════════════════════
# ACTIONS
# ═══════════════════════════════════════════════════════

@router.get("/actions")
async def list_actions(
    status: str = Query(""),
    days: int = Query(30),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """List all actions across the tenant."""
    from app.models.enterprise_models import Action

    tenant_id = await _get_tenant_id(db, user)
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = select(Action).where(and_(Action.tenant_id == tenant_id, Action.created_at >= cutoff))
    if status:
        query = query.where(Action.status == status)
    query = query.order_by(desc(Action.created_at)).limit(50)

    result = await db.execute(query)
    actions = result.scalars().all()

    return {
        "total": len(actions),
        "by_status": {
            "draft": len([a for a in actions if a.status == "draft"]),
            "approved": len([a for a in actions if a.status == "approved"]),
            "queued": len([a for a in actions if a.status == "queued"]),
            "completed": len([a for a in actions if a.status == "completed"]),
            "failed": len([a for a in actions if a.status == "failed"]),
        },
        "actions": [
            {
                "id": a.id, "title": a.title, "type": a.type,
                "owner": a.owner, "status": a.status, "priority": a.priority,
                "decision_id": a.decision_id, "requires_approval": a.requires_approval,
                "deadline": a.deadline.isoformat() if a.deadline else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in actions
        ],
    }


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    req: ActionApproval,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Approve an action for execution."""
    tenant_id = await _get_tenant_id(db, user)

    from app.services.enterprise_worker import transition_state
    from app.models.enterprise_models import ExecutionQueue

    result = await transition_state(db, "action", action_id, "approved", user.email, tenant_id, req.notes)
    if not result.get("success"):
        return result

    # Queue for execution
    await transition_state(db, "action", action_id, "queued", "system", tenant_id, "Queued for execution")

    queue_item = ExecutionQueue(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        action_id=action_id,
        priority=5,
    )
    db.add(queue_item)
    await db.commit()

    return {"success": True, "action_id": action_id, "status": "queued"}


# ═══════════════════════════════════════════════════════
# EXECUTION QUEUE
# ═══════════════════════════════════════════════════════

@router.get("/queue")
async def get_queue_status(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get execution queue status."""
    from app.models.enterprise_models import ExecutionQueue

    tenant_id = await _get_tenant_id(db, user)

    result = await db.execute(
        select(ExecutionQueue).where(ExecutionQueue.tenant_id == tenant_id).order_by(ExecutionQueue.created_at.desc()).limit(20)
    )
    items = result.scalars().all()

    return {
        "total": len(items),
        "pending": len([i for i in items if i.status == "pending"]),
        "processing": len([i for i in items if i.status == "processing"]),
        "completed": len([i for i in items if i.status == "completed"]),
        "items": [
            {
                "id": i.id, "action_id": i.action_id, "status": i.status,
                "priority": i.priority, "attempts": i.attempts,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in items
        ],
    }


@router.post("/queue/process")
async def process_queue(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Process pending items in the execution queue."""
    tenant_id = await _get_tenant_id(db, user)

    from app.services.enterprise_worker import process_execution_queue
    result = await process_execution_queue(db, tenant_id)
    return result


# ═══════════════════════════════════════════════════════
# AUDIT LOG
# ═══════════════════════════════════════════════════════

@router.get("/audit")
async def get_audit_log(
    entity_type: str = Query(""),
    days: int = Query(30),
    limit: int = Query(50),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get immutable audit log entries."""
    from app.models.enterprise_models import StateAuditLog

    tenant_id = await _get_tenant_id(db, user)
    cutoff = datetime.utcnow() - timedelta(days=days)

    query = select(StateAuditLog).where(
        and_(StateAuditLog.tenant_id == tenant_id, StateAuditLog.timestamp >= cutoff)
    )
    if entity_type:
        query = query.where(StateAuditLog.entity_type == entity_type)
    query = query.order_by(desc(StateAuditLog.timestamp)).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    return {
        "total": len(entries),
        "entries": [
            {
                "entity_id": e.entity_id, "entity_type": e.entity_type,
                "old_state": e.old_state, "new_state": e.new_state,
                "changed_by": e.changed_by, "reason": e.change_reason,
                "timestamp": e.timestamp.isoformat(),
                "metadata": e.metadata,
            }
            for e in entries
        ],
    }


@router.get("/audit/entity/{entity_id}")
async def get_entity_audit(
    entity_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get complete audit trail for a specific entity."""
    from app.models.enterprise_models import StateAuditLog

    tenant_id = await _get_tenant_id(db, user)

    result = await db.execute(
        select(StateAuditLog).where(
            and_(StateAuditLog.entity_id == entity_id, StateAuditLog.tenant_id == tenant_id)
        ).order_by(StateAuditLog.timestamp)
    )
    entries = result.scalars().all()

    return {
        "entity_id": entity_id,
        "total_transitions": len(entries),
        "trail": [
            {
                "old_state": e.old_state, "new_state": e.new_state,
                "changed_by": e.changed_by, "reason": e.change_reason,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in entries
        ],
    }


# ═══════════════════════════════════════════════════════
# TENANT
# ═══════════════════════════════════════════════════════

@router.post("/tenant/setup")
async def setup_tenant(
    req: TenantSetup,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Set up a new tenant (organization)."""
    from app.models.enterprise_models import Tenant, TenantUser

    tenant = Tenant(
        id=str(uuid.uuid4()),
        name=req.name,
        domain=req.domain,
        plan=req.plan,
        governance_policy=req.governance_policy,
    )
    db.add(tenant)

    tu = TenantUser(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        user_id=str(user.id),
        role="owner",
    )
    db.add(tu)
    await db.commit()

    return {"tenant_id": tenant.id, "name": req.name, "plan": req.plan}


@router.get("/tenant")
async def get_tenant_info(
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Get current tenant info."""
    from app.models.enterprise_models import Tenant, Decision, Action, ProcessJob

    tenant_id = await _get_tenant_id(db, user)

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()

    if not tenant:
        return {"tenant_id": tenant_id, "status": "not_found"}

    # Counts
    dec_count = await db.execute(select(func.count()).select_from(Decision).where(Decision.tenant_id == tenant_id))
    act_count = await db.execute(select(func.count()).select_from(Action).where(Action.tenant_id == tenant_id))
    job_count = await db.execute(select(func.count()).select_from(ProcessJob).where(ProcessJob.tenant_id == tenant_id))

    return {
        "tenant_id": tenant.id,
        "name": tenant.name,
        "domain": tenant.domain,
        "plan": tenant.plan,
        "governance_policy": tenant.governance_policy,
        "strict_mode": tenant.strict_mode,
        "stats": {
            "decisions": dec_count.scalar() or 0,
            "actions": act_count.scalar() or 0,
            "jobs": job_count.scalar() or 0,
        },
    }


# ═══════════════════════════════════════════════════════
# ENTERPRISE STATUS
# ═══════════════════════════════════════════════════════

@router.get("/status")
async def enterprise_status(user=Depends(get_current_user)):
    """Get enterprise system status."""
    return {
        "engine": "active",
        "version": "1.0.0",
        "components": {
            "state_machine": True,
            "decision_system": True,
            "action_system": True,
            "execution_queue": True,
            "audit_log": True,
            "tenant_isolation": True,
            "hitl_enforcement": True,
            "policy_validation": True,
            "traceability": True,
            "idempotent_jobs": True,
        },
        "state_transitions": {
            "job": ["pending", "processing", "completed", "failed", "cancelled"],
            "decision": ["draft", "pending_review", "approved", "rejected", "superseded", "revision_requested"],
            "action": ["draft", "approved", "queued", "executing", "completed", "failed", "rejected", "cancelled"],
        },
    }
