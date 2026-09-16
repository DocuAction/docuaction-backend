"""
Append-only identifier conflict and decision events.

When a delivery matches an existing entity (by RCE organisation OID) but
carries a different value for an identifier the registry already holds, the
pipeline RAISES a conflict here and holds the record. It never overwrites the
registered value and never discards the submitted one: both stay side by side
in this table, in Area 1 and in Area 2, until a human decides.

Decisions append a new event with the same (entity, identifier_type) and the
next sequence. CONFIRM_SUBMITTED is the only decision that changes the
registry, and it does so through `apply_confirmed_submitted`, which writes a
`tefca_entity_versions` row and a registry audit row in the same transaction.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.core import request_context
from app.tefca_registry import models as reg
from app.tefca_registry.rce import traceability_models as tm

logger = logging.getLogger(__name__)

CONFLICT_RAISED = "CONFLICT_RAISED"
CONFIRM_EXISTING = "CONFIRM_EXISTING"
CONFIRM_SUBMITTED = "CONFIRM_SUBMITTED"
#: Decisions that release the hold on the record.
RELEASING_DECISIONS = frozenset({CONFIRM_EXISTING, CONFIRM_SUBMITTED, "CORRECTED",
                                 "REJECTED"})


async def _next_sequence(db, entity_id, identifier_type: str) -> int:
    current = (await db.execute(
        select(func.max(tm.TefcaIdentifierDecisionEvent.sequence)).where(
            tm.TefcaIdentifierDecisionEvent.entity_id == entity_id,
            tm.TefcaIdentifierDecisionEvent.identifier_type == identifier_type))).scalar()
    return int(current or 0) + 1


async def raise_conflict(db, *, entity_id, identifier_type: str,
                         submitted_value: Optional[str], existing_value: Optional[str],
                         source_record_id=None, intake_id=None, issue_id=None,
                         reason: Optional[str] = None,
                         commit: bool = False) -> tm.TefcaIdentifierDecisionEvent:
    """The pipeline's statement that submitted != registered. SYSTEM actor."""
    event = tm.TefcaIdentifierDecisionEvent(
        entity_id=entity_id, source_record_id=source_record_id, intake_id=intake_id,
        issue_id=issue_id,
        sequence=await _next_sequence(db, entity_id, identifier_type),
        identifier_type=identifier_type, submitted_value=submitted_value,
        existing_value=existing_value, verified_value=None, selected_value=None,
        decision=CONFLICT_RAISED,
        reason=reason or (f"Delivered {identifier_type} {submitted_value!r} differs "
                          f"from the registered value {existing_value!r}. The "
                          f"registered value was retained pending an analyst "
                          f"decision; the submitted value is preserved in Area 1 "
                          f"and Area 2."),
        evidence_source="rce_delivery", actor="SYSTEM", actor_type="SYSTEM",
        correlation_id=request_context.correlation_id()[:64],
        build_sha=request_context.build_sha())
    db.add(event)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return event


async def decide(db, *, entity_id, identifier_type: str, decision: str,
                 reason: str, actor: str, selected_value: Optional[str] = None,
                 verified_value: Optional[str] = None,
                 evidence_source: Optional[str] = None,
                 confidence: Optional[str] = None,
                 source_record_id=None, intake_id=None, issue_id=None,
                 actor_id: Optional[uuid.UUID] = None,
                 commit: bool = False) -> Dict[str, Any]:
    """Append a human decision. Applies a registry change only for CONFIRM_SUBMITTED."""
    if decision not in tm.IDENTIFIER_DECISIONS or decision == CONFLICT_RAISED:
        raise ValueError(f"not a human decision: {decision!r}")
    if not (reason or "").strip():
        raise ValueError("a decision reason is required")

    latest = await latest_event(db, entity_id, identifier_type)
    submitted = latest.submitted_value if latest else None
    existing = latest.existing_value if latest else None
    if decision == CONFIRM_EXISTING:
        selected_value = existing
    elif decision == CONFIRM_SUBMITTED:
        selected_value = submitted if selected_value is None else selected_value

    version_id = None
    if decision == CONFIRM_SUBMITTED:
        version_id = await apply_confirmed_submitted(
            db, entity_id=entity_id, identifier_type=identifier_type,
            new_value=selected_value, previous_value=existing, actor=actor,
            actor_id=actor_id, reason=reason)

    event = tm.TefcaIdentifierDecisionEvent(
        entity_id=entity_id, source_record_id=source_record_id or
        (latest.source_record_id if latest else None),
        intake_id=intake_id or (latest.intake_id if latest else None),
        issue_id=issue_id or (latest.issue_id if latest else None),
        sequence=await _next_sequence(db, entity_id, identifier_type),
        identifier_type=identifier_type, submitted_value=submitted,
        existing_value=existing, verified_value=verified_value,
        selected_value=selected_value, decision=decision, reason=reason.strip(),
        evidence_source=evidence_source, confidence=confidence,
        actor=actor[:320], actor_type="HUMAN", version_id=version_id,
        correlation_id=request_context.correlation_id()[:64],
        build_sha=request_context.build_sha())
    db.add(event)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return {"event": event.to_dict(), "releases_hold": decision in RELEASING_DECISIONS,
            "registry_changed": version_id is not None}


async def apply_confirmed_submitted(db, *, entity_id, identifier_type: str,
                                    new_value: Optional[str],
                                    previous_value: Optional[str], actor: str,
                                    actor_id: Optional[uuid.UUID], reason: str):
    """Change the registered identifier: retire the old row, add the new one,
    write a version row and an audit row. Returns the version id."""
    from app.tefca_registry import audit as reg_audit

    rows = (await db.execute(
        select(reg.TefcaEntityIdentifier).where(
            reg.TefcaEntityIdentifier.entity_id == entity_id,
            reg.TefcaEntityIdentifier.identifier_type == identifier_type))).scalars().all()
    for row in rows:
        if row.identifier_value == previous_value and row.identifier_status == "active":
            row.identifier_status = "superseded"
    if new_value:
        db.add(reg.TefcaEntityIdentifier(
            id=uuid.uuid4(), entity_id=entity_id, identifier_type=identifier_type,
            identifier_value=new_value, system_uri=_system_uri(identifier_type),
            is_primary=False, identifier_status="active"))

    entity = await db.get(reg.TefcaRegEntity, entity_id)
    version_number = int((entity.current_version if entity else 1) or 1) + 1
    version = reg.TefcaEntityVersion(
        id=uuid.uuid4(), entity_id=entity_id, version_number=version_number,
        snapshot_data={"change": "identifier", "identifier_type": identifier_type,
                       "previous_value": previous_value, "new_value": new_value,
                       "reason": reason, "decided_by": actor},
        change_reason="identifier_confirmed_submitted", changed_by=actor_id)
    db.add(version)
    if entity is not None:
        entity.current_version = version_number
        entity.updated_at = datetime.utcnow()
    await db.flush()
    reg_audit.record(
        db, "identifier_changed", entity_id, actor_id=actor_id, actor_email=actor,
        metadata={"identifier_type": identifier_type, "previous_value": previous_value,
                  "new_value": new_value, "reason": reason,
                  "version_number": version_number,
                  "correlation_id": request_context.correlation_id()})
    return version.id


def _system_uri(identifier_type: str) -> Optional[str]:
    try:
        from app.tefca_registry.rce.promotion import SYSTEM_URI
        return SYSTEM_URI.get(identifier_type)
    except Exception:  # noqa: BLE001
        return None


async def latest_event(db, entity_id, identifier_type: str):
    return (await db.execute(
        select(tm.TefcaIdentifierDecisionEvent).where(
            tm.TefcaIdentifierDecisionEvent.entity_id == entity_id,
            tm.TefcaIdentifierDecisionEvent.identifier_type == identifier_type)
        .order_by(tm.TefcaIdentifierDecisionEvent.sequence.desc()).limit(1)
    )).scalar_one_or_none()


async def history(db, entity_id, identifier_type: Optional[str] = None) -> List[Dict[str, Any]]:
    stmt = select(tm.TefcaIdentifierDecisionEvent).where(
        tm.TefcaIdentifierDecisionEvent.entity_id == entity_id)
    if identifier_type:
        stmt = stmt.where(tm.TefcaIdentifierDecisionEvent.identifier_type == identifier_type)
    rows = (await db.execute(stmt.order_by(
        tm.TefcaIdentifierDecisionEvent.identifier_type,
        tm.TefcaIdentifierDecisionEvent.sequence))).scalars().all()
    return [r.to_dict() for r in rows]


async def unresolved_for_intake(db, intake_id) -> int:
    """Conflicts whose latest event is still CONFLICT_RAISED, for one delivery."""
    from sqlalchemy import text
    return int((await db.execute(text("""
        SELECT count(*) FROM (
          SELECT DISTINCT ON (entity_id, identifier_type) decision
          FROM tefca_identifier_decision_events
          WHERE intake_id = CAST(:i AS uuid)
          ORDER BY entity_id, identifier_type, sequence DESC) x
        WHERE x.decision = 'CONFLICT_RAISED'"""), {"i": str(intake_id)})).scalar() or 0)
