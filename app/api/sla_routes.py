"""
DocuAction SLA & Decision Lifecycle API Routes
- SLA status check and escalation
- Outcome tracking
- In-app notifications
- Decision rights validation
- Version history
"""
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.security import get_current_user

logger = logging.getLogger("docuaction.sla_routes")
router = APIRouter(prefix="/api/sla", tags=["SLA & Lifecycle"])


# ═══ REQUEST MODELS ═══

class ApprovalRequest(BaseModel):
    decision_id: str
    justification: str = ""
    approver_role: str = "admin"

class OutcomeRequest(BaseModel):
    decision_id: str
    outcome_text: str
    outcome_matched: bool
    notes: str = ""

class NotificationMarkRead(BaseModel):
    notification_id: str


# ═══ SLA STATUS ═══

@router.get("/check")
async def check_sla_status(
    user=Depends(get_current_user),
):
    """
    Check SLA status for all pending decisions.
    Returns escalation levels and overdue items.
    ServiceNow pattern: 50% warning → 75% urgent → 100% breached
    """
    from app.services.sla_engine import check_all_sla_status, trigger_escalation_notifications

    # Get decisions from Decision Bank
    try:
        from app.api.decision_intel_routes import _decision_bank
        decisions = list(_decision_bank.values())
    except:
        decisions = []

    # Also try enterprise decisions
    try:
        from app.api.enterprise_routes import _get_enterprise_decisions
        enterprise_decisions = _get_enterprise_decisions()
        decisions.extend(enterprise_decisions)
    except:
        pass

    results = check_all_sla_status(decisions)

    # Trigger notifications for new escalations
    if results["needs_escalation"]:
        notif_count = trigger_escalation_notifications(results, tenant_id=getattr(user, "tenant_id", "default"))
        results["notifications_sent"] = notif_count

    return results


@router.get("/decision/{decision_id}")
async def get_decision_sla(
    decision_id: str,
    user=Depends(get_current_user),
):
    """Get SLA status for a specific decision."""
    from app.services.sla_engine import compute_escalation_level

    # Find decision
    try:
        from app.api.decision_intel_routes import _decision_bank
        decision = _decision_bank.get(decision_id)
    except:
        decision = None

    if not decision:
        raise HTTPException(404, "Decision not found")

    created = decision.get("created_at") or decision.get("timestamp")
    deadline = decision.get("deadline")

    if not created:
        return {"decision_id": decision_id, "sla_status": "no_sla", "message": "No creation timestamp found"}

    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", ""))
    if not deadline:
        from app.services.sla_engine import compute_sla_deadline
        domain = decision.get("domain", "enterprise")
        deadline = compute_sla_deadline(created, domain)

    if isinstance(deadline, str):
        deadline = datetime.fromisoformat(deadline.replace("Z", ""))

    esc = compute_escalation_level(created, deadline)

    return {
        "decision_id": decision_id,
        "status": decision.get("status"),
        "domain": decision.get("domain", "enterprise"),
        "created_at": str(created),
        "deadline": str(deadline),
        "sla": esc,
    }


# ═══ APPROVAL WITH JUSTIFICATION ═══

@router.post("/approve")
async def approve_with_justification(
    req: ApprovalRequest,
    user=Depends(get_current_user),
):
    """
    Approve a decision with mandatory justification (for gov/healthcare/legal domains).
    Validates decision rights before allowing approval.
    """
    from app.services.sla_engine import validate_approval
    from app.api.decision_intel_routes import _decision_bank

    decision = _decision_bank.get(req.decision_id)
    if not decision:
        raise HTTPException(404, "Decision not found")

    domain = decision.get("domain", "enterprise")

    # Validate approval rights
    validation = validate_approval(
        decision=decision,
        approver_role=req.approver_role,
        justification=req.justification,
        domain=domain,
    )

    if not validation["valid"]:
        raise HTTPException(400, {"errors": validation["errors"], "rights_checked": True})

    # Perform approval
    decision["status"] = "approved"
    decision["approved_by"] = user.email
    decision["approved_at"] = datetime.utcnow().isoformat()
    decision["approval_justification"] = req.justification
    decision["is_immutable"] = True
    decision["escalation_level"] = 0  # clear escalation

    _decision_bank[req.decision_id] = decision

    return {
        "decision_id": req.decision_id,
        "status": "approved",
        "approved_by": user.email,
        "justification_recorded": True,
        "is_immutable": True,
        "validation": validation,
    }


# ═══ OUTCOME TRACKING ═══

_outcomes = {}

@router.post("/outcome")
async def record_decision_outcome(
    req: OutcomeRequest,
    user=Depends(get_current_user),
):
    """
    Record the actual outcome of a decision (Cloverpop pattern).
    Tracks: did the outcome match the prediction?
    """
    from app.services.sla_engine import record_outcome
    from app.api.decision_intel_routes import _decision_bank

    decision = _decision_bank.get(req.decision_id)
    if not decision:
        raise HTTPException(404, "Decision not found")

    result = record_outcome(
        decision=decision,
        outcome_text=req.outcome_text,
        outcome_matched=req.outcome_matched,
        recorded_by=user.email,
        notes=req.notes,
    )

    if "error" in result:
        raise HTTPException(400, result["error"])

    _outcomes[req.decision_id] = result
    return result


@router.get("/outcomes")
async def get_outcome_stats(user=Depends(get_current_user)):
    """Get aggregated outcome statistics for organizational learning."""
    from app.services.sla_engine import compute_outcome_stats

    outcomes = list(_outcomes.values())
    stats = compute_outcome_stats(outcomes)
    return stats


@router.get("/outcome/{decision_id}")
async def get_decision_outcome(
    decision_id: str,
    user=Depends(get_current_user),
):
    """Get the recorded outcome for a specific decision."""
    outcome = _outcomes.get(decision_id)
    if not outcome:
        raise HTTPException(404, "No outcome recorded for this decision")
    return outcome


# ═══ VERSION HISTORY ═══

@router.get("/history/{decision_id}")
async def get_decision_history(
    decision_id: str,
    user=Depends(get_current_user),
):
    """Get the full version chain of a decision (supersedes/superseded_by)."""
    from app.services.sla_engine import build_version_chain
    from app.api.decision_intel_routes import _decision_bank

    chain = build_version_chain(decision_id, _decision_bank)
    if not chain:
        raise HTTPException(404, "Decision not found")

    return {
        "decision_id": decision_id,
        "total_versions": len(chain),
        "versions": chain,
    }


# ═══ NOTIFICATIONS ═══

@router.get("/notifications")
async def get_my_notifications(
    unread_only: bool = Query(True),
    user=Depends(get_current_user),
):
    """Get notifications for the current user."""
    from app.services.sla_engine import get_notifications

    # Check by email and role
    notifs_email = get_notifications(user.email, unread_only)
    notifs_role = get_notifications(getattr(user, "role", "user"), unread_only)
    notifs_admin = get_notifications("admin", unread_only) if getattr(user, "role", "") == "admin" else []

    # Deduplicate
    seen = set()
    all_notifs = []
    for n in notifs_email + notifs_role + notifs_admin:
        if n["id"] not in seen:
            seen.add(n["id"])
            all_notifs.append(n)

    all_notifs.sort(key=lambda n: n["created_at"], reverse=True)

    return {
        "total": len(all_notifs),
        "unread": len([n for n in all_notifs if not n["read"]]),
        "notifications": all_notifs[:30],
    }


@router.post("/notifications/read")
async def mark_read(
    req: NotificationMarkRead,
    user=Depends(get_current_user),
):
    """Mark a notification as read."""
    from app.services.sla_engine import mark_notification_read
    success = mark_notification_read(req.notification_id)
    return {"success": success}


# ═══ DECISION RIGHTS ═══

@router.get("/rights")
async def get_decision_rights(user=Depends(get_current_user)):
    """Get the decision rights matrix showing who can approve what."""
    from app.services.sla_engine import DECISION_RIGHTS, SLA_CONFIG

    return {
        "rights_matrix": DECISION_RIGHTS,
        "sla_config": SLA_CONFIG,
        "current_user": {
            "email": user.email,
            "role": getattr(user, "role", "user"),
            "rights": DECISION_RIGHTS.get(getattr(user, "role", "user"), DECISION_RIGHTS["user"]),
        },
    }


# ═══ STATUS ═══

@router.get("/status")
async def sla_engine_status(user=Depends(get_current_user)):
    """SLA Engine status."""
    from app.services.sla_engine import SLA_CONFIG, DECISION_RIGHTS

    return {
        "engine": "active",
        "version": "1.0.0",
        "features": {
            "sla_timers": True,
            "multi_stage_escalation": True,
            "justification_enforcement": True,
            "outcome_tracking": True,
            "decision_rights": True,
            "version_chain": True,
            "notifications": True,
        },
        "domains_configured": list(SLA_CONFIG.keys()),
        "roles_configured": list(DECISION_RIGHTS.keys()),
        "total_outcomes": len(_outcomes),
    }
