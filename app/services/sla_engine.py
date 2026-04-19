"""
DocuAction SLA & Decision Lifecycle Engine
Inspired by: ServiceNow (SLA/escalation), Cloverpop (outcome tracking), Palantir (decision rights)

Features:
  1. SLA Timer: Auto-set deadline on decision creation (configurable hours per domain)
  2. Multi-Stage Escalation: 50% warning → 75% urgent → 100% breached (ServiceNow pattern)
  3. Justification Enforcement: Approval requires documented rationale
  4. Outcome Tracking: Record what actually happened vs what was decided (Cloverpop pattern)
  5. Decision Rights: Role-based approval routing (Palantir pattern)
  6. Version Chain: Superseded decisions link to predecessors
  7. Breach Tracking: Overdue decisions flagged and escalated
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict

logger = logging.getLogger("docuaction.sla_engine")


# ═══════════════════════════════════════════════════════
# SLA CONFIGURATION (per domain — ServiceNow pattern)
# ═══════════════════════════════════════════════════════

SLA_CONFIG = {
    "enterprise": {
        "review_sla_hours": 24,
        "escalation_thresholds": [0.50, 0.75, 1.0],  # % of SLA elapsed
        "escalation_labels": ["warning", "urgent", "breached"],
        "auto_escalate_to": "manager",
        "require_justification": False,
    },
    "government": {
        "review_sla_hours": 8,
        "escalation_thresholds": [0.50, 0.75, 1.0],
        "escalation_labels": ["warning", "urgent", "breached"],
        "auto_escalate_to": "compliance_officer",
        "require_justification": True,
    },
    "healthcare": {
        "review_sla_hours": 4,
        "escalation_thresholds": [0.50, 0.75, 1.0],
        "escalation_labels": ["warning", "urgent", "breached"],
        "auto_escalate_to": "compliance_officer",
        "require_justification": True,
    },
    "legal": {
        "review_sla_hours": 12,
        "escalation_thresholds": [0.50, 0.75, 1.0],
        "escalation_labels": ["warning", "urgent", "breached"],
        "auto_escalate_to": "managing_partner",
        "require_justification": True,
    },
    "financial": {
        "review_sla_hours": 4,
        "escalation_thresholds": [0.50, 0.75, 1.0],
        "escalation_labels": ["warning", "urgent", "breached"],
        "auto_escalate_to": "risk_officer",
        "require_justification": True,
    },
}

# Decision rights matrix — who can approve what (Palantir pattern)
DECISION_RIGHTS = {
    "user": {"max_approval_usd": 10000, "domains": ["enterprise"]},
    "manager": {"max_approval_usd": 100000, "domains": ["enterprise", "legal"]},
    "admin": {"max_approval_usd": 1000000, "domains": ["enterprise", "government", "legal", "healthcare"]},
    "compliance_officer": {"max_approval_usd": None, "domains": ["government", "healthcare", "financial"]},
    "super_admin": {"max_approval_usd": None, "domains": ["enterprise", "government", "healthcare", "legal", "financial"]},
}


# ═══════════════════════════════════════════════════════
# SLA TIMER MANAGEMENT
# ═══════════════════════════════════════════════════════

def compute_sla_deadline(created_at: datetime, domain: str = "enterprise") -> datetime:
    """Compute SLA deadline based on domain configuration."""
    config = SLA_CONFIG.get(domain, SLA_CONFIG["enterprise"])
    hours = config["review_sla_hours"]
    return created_at + timedelta(hours=hours)


def compute_escalation_level(created_at: datetime, deadline: datetime) -> dict:
    """
    Compute current escalation level based on SLA elapsed time.
    ServiceNow pattern: 50% = warning, 75% = urgent, 100% = breached
    """
    now = datetime.utcnow()
    total_duration = (deadline - created_at).total_seconds()
    elapsed = (now - created_at).total_seconds()

    if total_duration <= 0:
        return {"level": 3, "label": "breached", "pct_elapsed": 100, "remaining_seconds": 0, "is_overdue": True}

    pct_elapsed = min(elapsed / total_duration, 2.0)  # cap at 200%
    remaining = max(0, (deadline - now).total_seconds())

    if pct_elapsed >= 1.0:
        level, label = 3, "breached"
    elif pct_elapsed >= 0.75:
        level, label = 2, "urgent"
    elif pct_elapsed >= 0.50:
        level, label = 1, "warning"
    else:
        level, label = 0, "on_track"

    return {
        "level": level,
        "label": label,
        "pct_elapsed": round(pct_elapsed * 100, 1),
        "remaining_seconds": int(remaining),
        "remaining_human": _format_remaining(remaining),
        "is_overdue": pct_elapsed >= 1.0,
    }


def _format_remaining(seconds: float) -> str:
    """Format remaining seconds as human-readable."""
    if seconds <= 0:
        return "OVERDUE"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 24:
        days = hours // 24
        return f"{days}d {hours % 24}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def check_all_sla_status(decisions: list) -> dict:
    """
    Batch check SLA status for all pending decisions.
    Returns summary + list of decisions needing escalation.
    """
    results = {
        "total_checked": 0,
        "on_track": 0,
        "warning": 0,
        "urgent": 0,
        "breached": 0,
        "needs_escalation": [],
        "overdue_decisions": [],
    }

    for dec in decisions:
        if dec.get("status") not in ("draft", "pending_review", "revision_requested"):
            continue  # only check active decisions

        results["total_checked"] += 1
        created = dec.get("created_at")
        deadline = dec.get("deadline")

        if not created or not deadline:
            continue

        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00").replace("+00:00", ""))
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00").replace("+00:00", ""))

        esc = compute_escalation_level(created, deadline)
        current_level = dec.get("escalation_level", 0)

        if esc["label"] == "on_track":
            results["on_track"] += 1
        elif esc["label"] == "warning":
            results["warning"] += 1
        elif esc["label"] == "urgent":
            results["urgent"] += 1
        elif esc["label"] == "breached":
            results["breached"] += 1
            results["overdue_decisions"].append({
                "decision_id": dec.get("id"),
                "text": dec.get("decision_text", "")[:100],
                "status": dec.get("status"),
                "deadline": str(deadline),
                "overdue_by": esc["remaining_human"],
            })

        # Needs escalation if level increased
        if esc["level"] > current_level:
            domain = dec.get("domain", "enterprise")
            config = SLA_CONFIG.get(domain, SLA_CONFIG["enterprise"])
            results["needs_escalation"].append({
                "decision_id": dec.get("id"),
                "text": dec.get("decision_text", "")[:100],
                "current_level": current_level,
                "new_level": esc["level"],
                "label": esc["label"],
                "escalate_to": config["auto_escalate_to"],
                "remaining": esc["remaining_human"],
            })

    return results


# ═══════════════════════════════════════════════════════
# JUSTIFICATION ENFORCEMENT
# ═══════════════════════════════════════════════════════

def validate_approval(
    decision: dict,
    approver_role: str,
    justification: str,
    domain: str = "enterprise",
) -> dict:
    """
    Validate that an approval meets all requirements.
    Returns {valid: bool, errors: [...]}
    """
    errors = []
    config = SLA_CONFIG.get(domain, SLA_CONFIG["enterprise"])

    # Check justification requirement
    if config["require_justification"]:
        if not justification or len(justification.strip()) < 10:
            errors.append("Justification required (minimum 10 characters) for " + domain + " domain decisions.")

    # Check decision rights
    rights = DECISION_RIGHTS.get(approver_role, DECISION_RIGHTS.get("user"))
    if domain not in rights.get("domains", []):
        errors.append(f"Role '{approver_role}' cannot approve {domain} domain decisions. Required roles: {[r for r, v in DECISION_RIGHTS.items() if domain in v.get('domains', [])]}")

    # Check financial threshold
    threshold = decision.get("approval_threshold_usd")
    max_usd = rights.get("max_approval_usd")
    if threshold and max_usd and threshold > max_usd:
        errors.append(f"Decision value ${threshold:,.0f} exceeds approval limit ${max_usd:,.0f} for role '{approver_role}'. Escalate to higher authority.")

    # Check status allows approval
    status = decision.get("status", "")
    if status not in ("draft", "pending_review", "revision_requested"):
        errors.append(f"Cannot approve decision in '{status}' status.")

    # Check immutability
    if decision.get("is_immutable"):
        errors.append("Decision is immutable and cannot be modified.")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "approver_role": approver_role,
        "domain": domain,
        "justification_required": config["require_justification"],
        "rights_checked": True,
    }


# ═══════════════════════════════════════════════════════
# OUTCOME TRACKING (Cloverpop pattern)
# ═══════════════════════════════════════════════════════

def record_outcome(
    decision: dict,
    outcome_text: str,
    outcome_matched: bool,
    recorded_by: str,
    notes: str = "",
) -> dict:
    """
    Record the actual outcome of a decision.
    Cloverpop tracks: did the outcome match the prediction?
    This enables organizational learning over time.
    """
    if decision.get("status") != "approved":
        return {"error": "Can only record outcomes for approved decisions."}

    outcome = {
        "decision_id": decision.get("id"),
        "decision_text": decision.get("decision_text", "")[:200],
        "selected_option": decision.get("selected_option", ""),
        "outcome_text": outcome_text,
        "outcome_matched": outcome_matched,
        "outcome_date": datetime.utcnow().isoformat(),
        "recorded_by": recorded_by,
        "notes": notes,
        "confidence_at_decision": decision.get("confidence_score", 0),
        "time_to_outcome_days": None,
    }

    # Calculate time from decision to outcome
    approved_at = decision.get("approved_at")
    if approved_at:
        if isinstance(approved_at, str):
            approved_at = datetime.fromisoformat(approved_at.replace("Z", ""))
        days = (datetime.utcnow() - approved_at).days
        outcome["time_to_outcome_days"] = days

    return outcome


def compute_outcome_stats(outcomes: list) -> dict:
    """
    Aggregate outcome statistics for organizational learning.
    Shows: accuracy of decisions, average time-to-outcome, patterns.
    """
    if not outcomes:
        return {"total": 0, "accuracy_rate": 0, "avg_time_to_outcome": 0}

    total = len(outcomes)
    matched = len([o for o in outcomes if o.get("outcome_matched")])
    times = [o["time_to_outcome_days"] for o in outcomes if o.get("time_to_outcome_days") is not None]

    return {
        "total_outcomes_recorded": total,
        "outcomes_matched": matched,
        "outcomes_unmatched": total - matched,
        "accuracy_rate": round(matched / total, 3) if total > 0 else 0,
        "avg_time_to_outcome_days": round(sum(times) / len(times), 1) if times else 0,
        "fastest_outcome_days": min(times) if times else 0,
        "slowest_outcome_days": max(times) if times else 0,
        "insight": _outcome_insight(matched, total),
    }


def _outcome_insight(matched: int, total: int) -> str:
    if total < 3:
        return "Not enough data for insights. Record more outcomes."
    rate = matched / total
    if rate >= 0.85:
        return "HIGH decision accuracy. AI-assisted decisions are reliable."
    elif rate >= 0.65:
        return "MODERATE accuracy. Review low-confidence decisions before approval."
    else:
        return "LOW accuracy. Consider stricter governance thresholds or more human review."


# ═══════════════════════════════════════════════════════
# VERSION CHAIN (Decision History)
# ═══════════════════════════════════════════════════════

def build_version_chain(decision_id: str, all_decisions: dict) -> list:
    """
    Build the full version history of a decision.
    Follows supersedes/superseded_by chain.
    """
    chain = []
    current = all_decisions.get(decision_id)
    if not current:
        return chain

    # Walk backward to find the original
    visited = set()
    while current and current.get("id") not in visited:
        visited.add(current["id"])
        chain.insert(0, {
            "version": current.get("version", 1),
            "decision_id": current["id"],
            "text": current.get("decision_text", "")[:200],
            "status": current.get("status"),
            "decided_by": current.get("decided_by"),
            "approved_by": current.get("approved_by"),
            "created_at": str(current.get("created_at", "")),
            "is_current": current["id"] == decision_id,
        })
        prev_id = current.get("supersedes")
        current = all_decisions.get(prev_id) if prev_id else None

    # Walk forward from original
    current = all_decisions.get(decision_id)
    if current:
        next_id = current.get("superseded_by")
        while next_id and next_id not in visited:
            visited.add(next_id)
            nxt = all_decisions.get(next_id)
            if not nxt:
                break
            chain.append({
                "version": nxt.get("version", 1),
                "decision_id": nxt["id"],
                "text": nxt.get("decision_text", "")[:200],
                "status": nxt.get("status"),
                "decided_by": nxt.get("decided_by"),
                "approved_by": nxt.get("approved_by"),
                "created_at": str(nxt.get("created_at", "")),
                "is_current": False,
            })
            next_id = nxt.get("superseded_by")

    return chain


# ═══════════════════════════════════════════════════════
# NOTIFICATION RECORDS (in-app notifications)
# ═══════════════════════════════════════════════════════

_notifications = []

def create_notification(
    tenant_id: str,
    recipient: str,
    notification_type: str,
    title: str,
    message: str,
    entity_type: str = "decision",
    entity_id: str = "",
    severity: str = "info",
) -> dict:
    """Create an in-app notification."""
    import uuid
    notif = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "recipient": recipient,
        "type": notification_type,
        "title": title,
        "message": message,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "severity": severity,  # info, warning, urgent, critical
        "read": False,
        "created_at": datetime.utcnow().isoformat(),
    }
    _notifications.append(notif)
    logger.info(f"Notification: [{severity}] {recipient}: {title}")
    return notif


def get_notifications(recipient: str, unread_only: bool = True) -> list:
    """Get notifications for a user."""
    notifs = [n for n in _notifications if n["recipient"] == recipient]
    if unread_only:
        notifs = [n for n in notifs if not n["read"]]
    return sorted(notifs, key=lambda n: n["created_at"], reverse=True)[:50]


def mark_notification_read(notification_id: str) -> bool:
    for n in _notifications:
        if n["id"] == notification_id:
            n["read"] = True
            return True
    return False


def trigger_escalation_notifications(escalation_results: dict, tenant_id: str) -> int:
    """
    Create notifications for all escalation events.
    ServiceNow pattern: different severity per escalation level.
    """
    count = 0
    for esc in escalation_results.get("needs_escalation", []):
        severity_map = {1: "warning", 2: "urgent", 3: "critical"}
        severity = severity_map.get(esc["new_level"], "info")

        create_notification(
            tenant_id=tenant_id,
            recipient=esc["escalate_to"],
            notification_type="sla_escalation",
            title=f"Decision SLA {esc['label'].upper()}: {esc['remaining']} remaining",
            message=f"Decision requires review: {esc['text']}",
            entity_type="decision",
            entity_id=esc["decision_id"],
            severity=severity,
        )
        count += 1

    for overdue in escalation_results.get("overdue_decisions", []):
        create_notification(
            tenant_id=tenant_id,
            recipient="admin",
            notification_type="sla_breach",
            title=f"BREACHED: Decision overdue",
            message=f"Decision has exceeded SLA: {overdue['text']}",
            entity_type="decision",
            entity_id=overdue["decision_id"],
            severity="critical",
        )
        count += 1

    return count
