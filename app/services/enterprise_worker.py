"""
DocuAction Enterprise State Machine & Worker
- Immutable state transitions with validation
- Append-only audit logging
- Background job processing
- HITL enforcement
- Execution queue processor
"""
import uuid
import json
import logging
import hashlib
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update

logger = logging.getLogger("docuaction.enterprise_worker")


# ═══════════════════════════════════════════════════════
# STATE MACHINE — Validates all transitions
# ═══════════════════════════════════════════════════════

class StateMachine:
    """
    Enforces valid state transitions for all entities.
    Every transition is logged to StateAuditLog (append-only).
    Invalid transitions are rejected.
    """

    # Centralized transition rules
    TRANSITIONS = {
        "job": {
            "pending": ["processing", "cancelled"],
            "processing": ["completed", "failed"],
            "failed": ["pending"],
            "completed": [],
            "cancelled": [],
        },
        "decision": {
            "draft": ["pending_review", "approved", "rejected"],
            "pending_review": ["approved", "rejected", "revision_requested"],
            "revision_requested": ["draft", "pending_review"],
            "approved": ["superseded"],
            "rejected": ["draft"],
            "superseded": [],
        },
        "action": {
            "draft": ["approved", "rejected", "cancelled"],
            "approved": ["queued"],
            "queued": ["executing"],
            "executing": ["completed", "failed"],
            "failed": ["queued"],
            "completed": [],
            "rejected": [],
            "cancelled": [],
        },
        "execution": {
            "pending": ["processing", "cancelled"],
            "processing": ["completed", "failed"],
            "failed": ["pending"],
            "completed": [],
            "cancelled": [],
        },
    }

    @classmethod
    def validate_transition(cls, entity_type: str, old_state: str, new_state: str) -> bool:
        """Check if a state transition is valid."""
        rules = cls.TRANSITIONS.get(entity_type, {})
        valid_next = rules.get(old_state, [])
        return new_state in valid_next

    @classmethod
    def get_valid_transitions(cls, entity_type: str, current_state: str) -> list:
        """Get all valid next states for current state."""
        rules = cls.TRANSITIONS.get(entity_type, {})
        return rules.get(current_state, [])


async def transition_state(
    db: AsyncSession,
    entity_type: str,
    entity_id: str,
    new_state: str,
    changed_by: str,
    tenant_id: str,
    reason: str = "",
    metadata: dict = None,
    ip_address: str = None,
) -> dict:
    """
    Execute a state transition with full audit logging.
    1. Validate transition is allowed
    2. Update entity state
    3. Log to StateAuditLog (append-only)
    4. Return result
    """
    from app.models.enterprise_models import (
        ProcessJob, Decision, Action, ExecutionQueue, StateAuditLog
    )

    # Map entity type to model and state field
    model_map = {
        "job": ProcessJob,
        "decision": Decision,
        "action": Action,
        "execution": ExecutionQueue,
    }

    Model = model_map.get(entity_type)
    if not Model:
        return {"success": False, "error": f"Unknown entity type: {entity_type}"}

    # Fetch current entity
    result = await db.execute(
        select(Model).where(and_(Model.id == entity_id, Model.tenant_id == tenant_id))
    )
    entity = result.scalar_one_or_none()
    if not entity:
        return {"success": False, "error": f"{entity_type} not found: {entity_id}"}

    old_state = entity.status

    # Validate transition
    if not StateMachine.validate_transition(entity_type, old_state, new_state):
        valid = StateMachine.get_valid_transitions(entity_type, old_state)
        return {
            "success": False,
            "error": f"Invalid transition: {old_state} → {new_state}. Valid: {valid}",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "current_state": old_state,
            "valid_transitions": valid,
        }

    # Update entity state
    entity.status = new_state

    # Set timestamps based on transition
    if new_state == "processing" and hasattr(entity, 'started_at'):
        entity.started_at = datetime.utcnow()
    if new_state in ("completed", "failed") and hasattr(entity, 'completed_at'):
        entity.completed_at = datetime.utcnow()
    if new_state == "approved":
        if hasattr(entity, 'approved_by'):
            entity.approved_by = changed_by
        if hasattr(entity, 'approved_at'):
            entity.approved_at = datetime.utcnow()
        if hasattr(entity, 'is_immutable'):
            entity.is_immutable = True
    if new_state == "failed" and hasattr(entity, 'failure_reason'):
        entity.failure_reason = reason
    if new_state == "failed" and hasattr(entity, 'last_error'):
        entity.last_error = reason

    # Log to audit (append-only)
    audit_entry = StateAuditLog(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        entity_id=entity_id,
        entity_type=entity_type,
        old_state=old_state,
        new_state=new_state,
        change_reason=reason,
        changed_by=changed_by,
        ip_address=ip_address,
        extra_data=metadata or {},
        timestamp=datetime.utcnow(),
    )
    db.add(audit_entry)

    await db.commit()

    logger.info(f"State transition: {entity_type}/{entity_id} {old_state} → {new_state} by {changed_by}")

    return {
        "success": True,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "old_state": old_state,
        "new_state": new_state,
        "changed_by": changed_by,
        "timestamp": audit_entry.timestamp.isoformat(),
    }


# ═══════════════════════════════════════════════════════
# JOB PROCESSOR — Background worker
# ═══════════════════════════════════════════════════════

async def process_job(db: AsyncSession, job_id: str, tenant_id: str) -> dict:
    """
    Process a single job through the AI pipeline.
    Flow: pending → processing → (AI work) → completed/failed
    Creates Decision + Action records from output.
    """
    from app.models.enterprise_models import ProcessJob, Decision, Action, Context, Traceability

    # Fetch job
    result = await db.execute(
        select(ProcessJob).where(and_(ProcessJob.id == job_id, ProcessJob.tenant_id == tenant_id))
    )
    job = result.scalar_one_or_none()
    if not job:
        return {"success": False, "error": "Job not found"}

    if job.status != "pending":
        return {"success": False, "error": f"Job is {job.status}, not pending"}

    # Transition to processing
    await transition_state(db, "job", job_id, "processing", "system", tenant_id, "Worker picked up job")

    import time
    start_time = time.time()

    try:
        # Get context (source document/transcript)
        source_text = ""
        source_name = ""
        if job.context_id:
            ctx_result = await db.execute(select(Context).where(Context.id == job.context_id))
            ctx = ctx_result.scalar_one_or_none()
            if ctx:
                source_name = ctx.source_name

                # If context links to existing document, get its text
                if ctx.source_id:
                    try:
                        from app.models.database import Document
                        doc_result = await db.execute(select(Document).where(Document.id == ctx.source_id))
                        doc = doc_result.scalar_one_or_none()
                        if doc:
                            source_text = getattr(doc, 'content', '') or getattr(doc, 'extracted_text', '') or ''
                    except Exception:
                        pass

        # Run AI processing using existing engine
        params = job.input_params or {}
        action_type = params.get("action_type", job.job_type)
        context_focus = params.get("context_focus", "")

        from app.services.ai_engine import process_document
        ai_result = await process_document(
            document_text=source_text,
            action_type=action_type,
            user_id=job.created_by,
            document_id=params.get("document_id"),
            file_path=params.get("file_path"),
        )

        processing_ms = int((time.time() - start_time) * 1000)
        job.processing_time_ms = processing_ms
        job.model_used = ai_result.get("_meta", {}).get("model_used", "")

        # Parse AI output
        content = ai_result
        if isinstance(content, dict) and "content" in content:
            try:
                content = json.loads(content["content"])
            except:
                pass

        # Extract decisions and create records
        decisions_created = []
        for dec in content.get("decisions", []):
            if isinstance(dec, dict) and dec.get("decision"):
                decision = Decision(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    context_id=job.context_id,
                    job_id=job_id,
                    decision_text=dec["decision"],
                    decided_by=dec.get("decided_by", ""),
                    rationale=dec.get("context", dec.get("rationale", "")),
                    confidence_score=dec.get("confidence", ai_result.get("confidence", 0.85)),
                    model_name=job.model_used,
                    prompt_version="v1",
                    status="draft",
                    correlation_id="DA-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper(),
                    created_by=job.created_by,
                )
                db.add(decision)
                decisions_created.append(decision.id)

                # Create traceability for this decision
                if source_text:
                    trace = Traceability(
                        id=str(uuid.uuid4()),
                        tenant_id=tenant_id,
                        decision_id=decision.id,
                        source_document=source_name,
                        source_type="document",
                        quote=dec.get("source_reference", ""),
                        confidence=dec.get("confidence", 0.85),
                        match_method="ai_extraction",
                    )
                    db.add(trace)

        # Extract actions and create records
        actions_created = []
        for task in content.get("tasks", []):
            if isinstance(task, dict) and task.get("task"):
                action = Action(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    context_id=job.context_id,
                    job_id=job_id,
                    decision_id=decisions_created[0] if decisions_created else None,
                    type="manual",
                    title=task["task"],
                    owner=task.get("owner", "TBD"),
                    priority=task.get("priority", "medium"),
                    description=task.get("source_reference", ""),
                    status="draft",
                    requires_approval=True,
                    correlation_id="DA-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper(),
                    created_by=job.created_by,
                )

                # Parse deadline
                deadline = task.get("deadline", "")
                if deadline and deadline != "TBD":
                    try:
                        from dateutil.parser import parse as parse_date
                        action.deadline = parse_date(deadline)
                    except:
                        pass

                db.add(action)
                actions_created.append(action.id)

        # Transition to completed
        await transition_state(
            db, "job", job_id, "completed", "system", tenant_id,
            f"Processed successfully: {len(decisions_created)} decisions, {len(actions_created)} actions",
            metadata={
                "decisions_created": decisions_created,
                "actions_created": actions_created,
                "processing_time_ms": processing_ms,
            },
        )

        return {
            "success": True,
            "job_id": job_id,
            "decisions_created": len(decisions_created),
            "actions_created": len(actions_created),
            "processing_time_ms": processing_ms,
            "decision_ids": decisions_created,
            "action_ids": actions_created,
        }

    except Exception as e:
        logger.error(f"Job processing failed: {e}")

        # Increment retry count
        job.retry_count = (job.retry_count or 0) + 1

        await transition_state(
            db, "job", job_id, "failed", "system", tenant_id,
            str(e)[:500],
            metadata={"retry_count": job.retry_count, "error": str(e)[:200]},
        )

        return {"success": False, "error": str(e)[:200], "retry_count": job.retry_count}


# ═══════════════════════════════════════════════════════
# EXECUTION QUEUE PROCESSOR
# ═══════════════════════════════════════════════════════

async def process_execution_queue(db: AsyncSession, tenant_id: str) -> dict:
    """
    Process pending items in the execution queue.
    Only processes actions that have been APPROVED (HITL enforced).
    """
    from app.models.enterprise_models import ExecutionQueue, Action

    # Fetch pending queue items
    result = await db.execute(
        select(ExecutionQueue).where(
            and_(
                ExecutionQueue.tenant_id == tenant_id,
                ExecutionQueue.status == "pending",
            )
        ).order_by(ExecutionQueue.priority, ExecutionQueue.created_at).limit(10)
    )
    queue_items = result.scalars().all()

    processed = []

    for item in queue_items:
        # Verify the action is approved (HITL check)
        action_result = await db.execute(
            select(Action).where(and_(Action.id == item.action_id, Action.tenant_id == tenant_id))
        )
        action = action_result.scalar_one_or_none()

        if not action or action.status != "queued":
            continue

        # Transition queue item to processing
        await transition_state(db, "execution", item.id, "processing", "system", tenant_id)
        await transition_state(db, "action", action.id, "executing", "system", tenant_id)

        try:
            # Execute based on action type
            exec_result = await _execute_action(action)

            action.execution_result = exec_result
            action.executed_by = "system"
            action.executed_at = datetime.utcnow()

            await transition_state(db, "action", action.id, "completed", "system", tenant_id,
                                   metadata={"result": exec_result})
            await transition_state(db, "execution", item.id, "completed", "system", tenant_id)

            processed.append({"action_id": action.id, "status": "completed", "result": exec_result})

        except Exception as e:
            item.attempts = (item.attempts or 0) + 1
            item.last_error = str(e)[:500]

            await transition_state(db, "action", action.id, "failed", "system", tenant_id, str(e)[:200])
            await transition_state(db, "execution", item.id, "failed", "system", tenant_id, str(e)[:200])

            processed.append({"action_id": action.id, "status": "failed", "error": str(e)[:100]})

    return {"processed": len(processed), "results": processed}


async def _execute_action(action) -> dict:
    """Execute a single action based on its type."""
    action_type = action.type
    payload = action.payload or {}

    if action_type == "email":
        # Future: integrate with SendGrid / Outlook Graph API
        return {"status": "draft_created", "type": "email", "note": "Email queued for delivery"}

    elif action_type == "jira_ticket":
        # Future: integrate with Jira REST API
        return {"status": "ticket_drafted", "type": "jira", "note": "Jira ticket prepared"}

    elif action_type == "servicenow":
        return {"status": "incident_drafted", "type": "servicenow", "note": "ServiceNow record prepared"}

    elif action_type == "notification":
        return {"status": "sent", "type": "notification", "note": "Notification dispatched"}

    elif action_type == "manual":
        return {"status": "tracked", "type": "manual", "note": "Manual action tracked — requires human execution"}

    else:
        return {"status": "unknown_type", "type": action_type, "note": "Action type not yet supported"}


# ═══════════════════════════════════════════════════════
# HITL ENFORCEMENT
# ═══════════════════════════════════════════════════════

async def approve_decision(
    db: AsyncSession,
    decision_id: str,
    tenant_id: str,
    approved_by: str,
    notes: str = "",
) -> dict:
    """
    Approve a decision — makes it immutable.
    Also queues any linked actions for execution.
    """
    from app.models.enterprise_models import Decision, Action, ExecutionQueue

    result = await transition_state(
        db, "decision", decision_id, "approved", approved_by, tenant_id,
        notes or "Approved via HITL review",
    )

    if not result.get("success"):
        return result

    # Queue linked actions
    actions_result = await db.execute(
        select(Action).where(and_(Action.decision_id == decision_id, Action.tenant_id == tenant_id))
    )
    actions = actions_result.scalars().all()

    queued = []
    for action in actions:
        if action.status == "draft" and action.requires_approval:
            # Auto-approve actions when decision is approved
            await transition_state(db, "action", action.id, "approved", approved_by, tenant_id, "Auto-approved with decision")
            await transition_state(db, "action", action.id, "queued", "system", tenant_id, "Queued for execution")

            # Add to execution queue
            queue_item = ExecutionQueue(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                action_id=action.id,
                priority={"critical": 1, "high": 3, "medium": 5, "low": 7}.get(action.priority, 5),
            )
            db.add(queue_item)
            queued.append(action.id)

    await db.commit()

    return {
        "success": True,
        "decision_id": decision_id,
        "status": "approved",
        "is_immutable": True,
        "actions_queued": len(queued),
        "action_ids": queued,
    }


async def reject_decision(
    db: AsyncSession,
    decision_id: str,
    tenant_id: str,
    rejected_by: str,
    reason: str,
) -> dict:
    """Reject a decision with documented reason."""
    return await transition_state(
        db, "decision", decision_id, "rejected", rejected_by, tenant_id,
        reason, metadata={"rejection_reason": reason},
    )
