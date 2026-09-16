"""
Append-only identifier conflict and decision events.

When a delivery matches an existing entity (by RCE organisation OID) but
carries a different value for an identifier the registry already holds, the
pipeline RAISES a conflict here and holds the record. It never overwrites the
registered value and never discards the submitted one: both stay side by side
in this table, in Area 1 and in Area 2, until a human decides.

Decisions append a new event with the same (entity, identifier_type) and the
next sequence. Only CONFIRM_SUBMITTED and CORRECTED change the registry, only
in answer to a raised conflict, only with a value that passes the identifier
validator and is not another entity's active identifier, and only through
`apply_confirmed_submitted`, which writes a `tefca_entity_versions` row and a
registry audit row in the same transaction.
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


#: Identifier families a decision may name. Anything else is refused: the
#: decision table is about the registry's identifiers, not a free-text store.
DECIDABLE_IDENTIFIER_TYPES = ("npi", "tefcaid", "hcid", "aaid")

#: Decisions that write the chosen value into the registry.
REGISTRY_WRITING_DECISIONS = frozenset({CONFIRM_SUBMITTED, "CORRECTED"})


class NoOpenConflict(ValueError):
    """A decision was offered for an (entity, identifier) with no raised conflict."""


class IdentifierValueRefused(ValueError):
    """The value a decision would write is not a usable identifier."""


class IdentifierAlreadyRegistered(ValueError):
    """The value a decision would write is the active identifier of another entity."""


def _validate_identifier_value(identifier_type: str, value: Optional[str]) -> str:
    """The one gate every registry-writing decision passes through.

    Independent reviewers (2026-09-16) showed CONFIRM_SUBMITTED writing a
    checksum-invalid NPI, and an arbitrary string, as the ACTIVE identifier row.
    The quality rules refuse such values at promotion; a human decision must not
    be the way round them.
    """
    value = (value or "").strip()
    if not value:
        raise IdentifierValueRefused("a registry-writing decision needs a value")
    if not value.isascii() or any(ch.isspace() for ch in value):
        raise IdentifierValueRefused(
            f"{identifier_type} value must be printable ASCII without whitespace")
    if identifier_type == "npi":
        from app.services.npi_validator import validate_npi
        ok, message = validate_npi(value)
        if not ok:
            raise IdentifierValueRefused(
                f"{value!r} is not a valid NPI ({message}); it cannot be registered. "
                f"Use CONFIRM_EXISTING, CORRECTED with a valid value, or REJECTED.")
    elif len(value) > 128:
        raise IdentifierValueRefused(f"{identifier_type} value is too long")
    return value


async def _registered_elsewhere(db, *, entity_id, identifier_type: str,
                                value: str) -> Optional[uuid.UUID]:
    """Another entity's ACTIVE row with this (type, value), if any."""
    return (await db.execute(
        select(reg.TefcaEntityIdentifier.entity_id).where(
            reg.TefcaEntityIdentifier.identifier_type == identifier_type,
            reg.TefcaEntityIdentifier.identifier_value == value,
            reg.TefcaEntityIdentifier.identifier_status == "active",
            reg.TefcaEntityIdentifier.entity_id != entity_id).limit(1)
    )).scalar_one_or_none()


async def decide(db, *, entity_id, identifier_type: str, decision: str,
                 reason: str, actor: str, selected_value: Optional[str] = None,
                 verified_value: Optional[str] = None,
                 evidence_source: Optional[str] = None,
                 confidence: Optional[str] = None,
                 source_record_id=None, intake_id=None, issue_id=None,
                 actor_id: Optional[uuid.UUID] = None,
                 commit: bool = False) -> Dict[str, Any]:
    """Append a human decision about a RAISED conflict.

    Preconditions (each a `ValueError`, surfaced as 422 by the routes):
      * `identifier_type` is one of DECIDABLE_IDENTIFIER_TYPES;
      * the latest event for (entity, identifier_type) is CONFLICT_RAISED -
        a decision is an answer to a question the pipeline asked, never a
        free-standing write to the registry;
      * CONFIRM_SUBMITTED takes the submitted value as raised; a different
        `selected_value` is refused (that is what CORRECTED is for);
      * CORRECTED requires a `selected_value`;
      * a value that CONFIRM_SUBMITTED or CORRECTED would write passes
        `_validate_identifier_value` and is not another entity's active
        identifier (`IdentifierAlreadyRegistered`, surfaced as 409).

    Registry change: CONFIRM_SUBMITTED and CORRECTED (when the chosen value
    differs from the registered one) go through `apply_confirmed_submitted`,
    which writes a version row and an audit row in the same transaction.
    """
    if decision not in tm.IDENTIFIER_DECISIONS or decision == CONFLICT_RAISED:
        raise ValueError(f"not a human decision: {decision!r}")
    if not (reason or "").strip():
        raise ValueError("a decision reason is required")
    identifier_type = (identifier_type or "").strip().lower()
    if identifier_type not in DECIDABLE_IDENTIFIER_TYPES:
        raise ValueError(f"identifier_type must be one of {list(DECIDABLE_IDENTIFIER_TYPES)}")

    latest = await latest_event(db, entity_id, identifier_type)
    if latest is None or latest.decision != CONFLICT_RAISED:
        raise NoOpenConflict(
            f"No open identifier conflict for {identifier_type} on this entity; "
            f"a decision must answer a conflict the pipeline raised.")
    submitted = latest.submitted_value
    existing = latest.existing_value

    if decision == CONFIRM_EXISTING:
        selected_value = existing
    elif decision == CONFIRM_SUBMITTED:
        if selected_value is not None and (selected_value or "").strip() != (submitted or ""):
            raise ValueError(
                "CONFIRM_SUBMITTED confirms the submitted value as raised; to register "
                "a different value use CORRECTED with selected_value.")
        selected_value = submitted
    elif decision == "CORRECTED":
        if selected_value is None or not str(selected_value).strip():
            raise ValueError("CORRECTED requires selected_value")
    else:
        # REQUEST_EVIDENCE / DEFERRED / ESCALATED / REJECTED: nothing chosen yet;
        # the registry keeps the existing value.
        selected_value = None if selected_value is None else str(selected_value).strip()

    version_id = None
    if decision in REGISTRY_WRITING_DECISIONS:
        chosen = _validate_identifier_value(identifier_type, selected_value)
        selected_value = chosen
        if chosen != (existing or ""):
            other = await _registered_elsewhere(
                db, entity_id=entity_id, identifier_type=identifier_type, value=chosen)
            if other is not None:
                raise IdentifierAlreadyRegistered(
                    f"{identifier_type} {chosen!r} is already the active identifier of "
                    f"another registry entity; resolve that entity first.")
            version_id = await apply_confirmed_submitted(
                db, entity_id=entity_id, identifier_type=identifier_type,
                new_value=chosen, previous_value=existing, actor=actor,
                actor_id=actor_id, reason=reason, decision=decision)

    event = tm.TefcaIdentifierDecisionEvent(
        entity_id=entity_id, source_record_id=source_record_id or latest.source_record_id,
        intake_id=intake_id or latest.intake_id,
        issue_id=issue_id or latest.issue_id,
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


#: Entity columns that mirror the identifier rows, kept in step on a change.
_ENTITY_COLUMNS = {"npi": None, "tefcaid": "rce_tefcaid", "hcid": "rce_hcid",
                   "aaid": "rce_aaid"}


async def apply_confirmed_submitted(db, *, entity_id, identifier_type: str,
                                    new_value: Optional[str],
                                    previous_value: Optional[str], actor: str,
                                    actor_id: Optional[uuid.UUID], reason: str,
                                    decision: str = CONFIRM_SUBMITTED):
    """Change the registered identifier: retire the old row, add the new one,
    keep the entity's mirror column in step, write a version row and an audit
    row. Returns the version id.

    Callers must have validated `new_value` (see `decide`); this function is
    the mechanism, `decide` is the gate, and it re-validates so that no other
    caller can use the mechanism without the gate.
    """
    from app.tefca_registry import audit as reg_audit

    new_value = _validate_identifier_value(identifier_type, new_value)
    rows = (await db.execute(
        select(reg.TefcaEntityIdentifier).where(
            reg.TefcaEntityIdentifier.entity_id == entity_id,
            reg.TefcaEntityIdentifier.identifier_type == identifier_type))).scalars().all()
    for row in rows:
        if row.identifier_status == "active" and row.identifier_value != new_value:
            row.identifier_status = "superseded"
    if not any(r.identifier_value == new_value and r.identifier_status == "active"
               for r in rows):
        db.add(reg.TefcaEntityIdentifier(
            id=uuid.uuid4(), entity_id=entity_id, identifier_type=identifier_type,
            identifier_value=new_value, system_uri=_system_uri(identifier_type),
            is_primary=False, identifier_status="active"))

    entity = await db.get(reg.TefcaRegEntity, entity_id)
    column = _ENTITY_COLUMNS.get(identifier_type)
    if entity is not None and column and hasattr(entity, column):
        setattr(entity, column, new_value)
    version_number = int((entity.current_version if entity else 1) or 1) + 1
    change_reason = ("identifier_confirmed_submitted" if decision == CONFIRM_SUBMITTED
                     else "identifier_corrected")
    version = reg.TefcaEntityVersion(
        id=uuid.uuid4(), entity_id=entity_id, version_number=version_number,
        snapshot_data={"change": "identifier", "identifier_type": identifier_type,
                       "previous_value": previous_value, "new_value": new_value,
                       "decision": decision, "reason": reason, "decided_by": actor},
        change_reason=change_reason, changed_by=actor_id)
    db.add(version)
    if entity is not None:
        entity.current_version = version_number
        entity.updated_at = datetime.utcnow()
    await db.flush()
    reg_audit.record(
        db, "identifier_changed", entity_id, actor_id=actor_id, actor_email=actor,
        metadata={"identifier_type": identifier_type, "previous_value": previous_value,
                  "new_value": new_value, "decision": decision, "reason": reason,
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
