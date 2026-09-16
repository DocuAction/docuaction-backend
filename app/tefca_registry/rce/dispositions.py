"""
Append-only terminal accounting per delivered line.

    Received = Created + Updated + Matched/Unchanged + Held + Rejected
             + Missing Key + Excluded

Every source record of a delivery has, at any moment, exactly one CURRENT
disposition: the highest-sequence row in rce_disposition_events, exposed by the
view rce_current_dispositions. A new decision appends; nothing is edited.

Reason codes are short machine tokens (e.g. HELD_QUALITY_ISSUE,
UPDATED_MATERIAL_FIELDS, MATCHED_NO_MATERIAL_CHANGE); `reason` is the text a
reviewer reads.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, select, text

from app.core import request_context
from app.tefca_registry.rce import traceability_models as tm

logger = logging.getLogger(__name__)

CREATED, UPDATED, MATCHED_UNCHANGED, HELD, REJECTED, MISSING_KEY, EXCLUDED = \
    tm.DISPOSITIONS

#: Reason codes the pipeline uses. Analysts may add their own through the
#: disposition endpoint; these are the system's.
REASON_CREATED = "CREATED_NEW_ENTITY"
REASON_UPDATED = "UPDATED_MATERIAL_FIELDS"
REASON_UNCHANGED = "MATCHED_NO_MATERIAL_CHANGE"
REASON_HELD_QUALITY = "HELD_QUALITY_ISSUE"
REASON_HELD_CONFLICT = "HELD_IDENTIFIER_CONFLICT"
REASON_HELD_SCHEMA = "HELD_SCHEMA_DRIFT"
REASON_REJECTED_PARSE = "REJECTED_UNPARSEABLE"
REASON_MISSING_KEY = "MISSING_KEY_NO_OID_OR_NAME"
REASON_EXCLUDED_TEST = "EXCLUDED_TEST_RECORD"
REASON_ANALYST = "ANALYST_DISPOSITION"


async def _next_sequence(db, source_record_id) -> int:
    current = (await db.execute(
        select(func.max(tm.RceDispositionEvent.sequence)).where(
            tm.RceDispositionEvent.source_record_id == source_record_id))).scalar()
    return int(current or 0) + 1


async def record(db, *, intake_id, source_record_id, disposition: str,
                 reason_code: str, reason: Optional[str] = None,
                 curated_record_id=None, job_id=None, entity_id=None,
                 changed_fields: Optional[Iterable[str]] = None,
                 actor: str = "SYSTEM", actor_type: str = "SYSTEM",
                 reconstructed: bool = False,
                 reconstruction: Optional[Dict[str, Any]] = None,
                 commit: bool = False) -> tm.RceDispositionEvent:
    """Append one disposition event for one source record."""
    if disposition not in tm.DISPOSITIONS:
        raise ValueError(f"unknown disposition {disposition!r}")
    if actor_type not in tm.ACTOR_TYPES:
        raise ValueError(f"unknown actor_type {actor_type!r}")
    event = tm.RceDispositionEvent(
        intake_id=intake_id, source_record_id=source_record_id,
        curated_record_id=curated_record_id, job_id=job_id,
        sequence=await _next_sequence(db, source_record_id),
        disposition=disposition, reason_code=reason_code[:64], reason=reason,
        entity_id=entity_id, changed_fields=sorted(set(changed_fields or [])),
        actor=(actor or "SYSTEM")[:320], actor_type=actor_type,
        correlation_id=request_context.correlation_id()[:64],
        build_sha=request_context.build_sha(),
        reconstructed=bool(reconstructed), reconstruction=reconstruction)
    db.add(event)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return event


async def current_for_intake(db, intake_id, *, disposition: Optional[str] = None,
                             limit: int = 1000, offset: int = 0) -> List[Dict[str, Any]]:
    """Current disposition rows for one delivery, joined to the line number."""
    sql = text(f"""
        SELECT d.*, s.line_number, s.source_rce_id, s.npi AS submitted_npi,
               c.record_status, c.name AS curated_name, c.npi AS curated_npi
        FROM {tm.CURRENT_DISPOSITIONS_VIEW} d
        JOIN rce_source_records s ON s.id = d.source_record_id
        LEFT JOIN rce_curated_records c ON c.id = d.curated_record_id
        WHERE d.intake_id = CAST(:i AS uuid)
          AND (CAST(:disp AS text) IS NULL OR d.disposition = CAST(:disp AS text))
        ORDER BY s.line_number
        LIMIT :lim OFFSET :off""")
    rows = (await db.execute(sql, {"i": str(intake_id), "disp": disposition,
                                   "lim": limit, "off": offset})).mappings().all()
    return [dict(r) for r in rows]


async def counts_for_intake(db, intake_id) -> Dict[str, int]:
    """Current disposition counts, every category present (zero when absent)."""
    rows = (await db.execute(text(f"""
        SELECT disposition, count(*) AS n
        FROM {tm.CURRENT_DISPOSITIONS_VIEW}
        WHERE intake_id = CAST(:i AS uuid) GROUP BY disposition"""),
        {"i": str(intake_id)})).all()
    out = {d: 0 for d in tm.DISPOSITIONS}
    for disposition, n in rows:
        out[disposition] = int(n)
    out["total"] = sum(out[d] for d in tm.DISPOSITIONS)
    return out


async def history_for_record(db, source_record_id) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(tm.RceDispositionEvent)
        .where(tm.RceDispositionEvent.source_record_id == source_record_id)
        .order_by(tm.RceDispositionEvent.sequence))).scalars().all()
    return [r.to_dict() for r in rows]


async def records_without_disposition(db, intake_id) -> int:
    """Source records of the delivery that have NO disposition event at all."""
    return int((await db.execute(text(f"""
        SELECT count(*) FROM rce_source_records s
        LEFT JOIN {tm.CURRENT_DISPOSITIONS_VIEW} d ON d.source_record_id = s.id
        WHERE s.source_intake_id = CAST(:i AS uuid) AND d.id IS NULL"""),
        {"i": str(intake_id)})).scalar() or 0)


def equation(counts: Dict[str, int], received: int) -> Dict[str, Any]:
    """The accounting identity, evaluated. Never rounds, never tolerates."""
    accounted = sum(int(counts.get(d, 0)) for d in tm.DISPOSITIONS)
    return {
        "received": int(received), "accounted": accounted,
        **{d.lower(): int(counts.get(d, 0)) for d in tm.DISPOSITIONS},
        "holds": accounted == int(received),
        "difference": int(received) - accounted,
    }


def material_changes(existing: Dict[str, Any], incoming: Dict[str, Any],
                     fields: Iterable[str]) -> List[str]:
    """Which of `fields` differ between an existing entity and the delivery.

    Strings are compared after outer-whitespace strip; None and '' are equal.
    Used by promotion to decide UPDATED versus MATCHED_UNCHANGED.
    """
    changed = []
    for f in fields:
        a, b = existing.get(f), incoming.get(f)
        a = a.strip() if isinstance(a, str) else a
        b = b.strip() if isinstance(b, str) else b
        if (a or None) != (b or None):
            changed.append(f)
    return changed
