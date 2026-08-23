"""Turn a triaged Phase-6 exception into an analyst work item.

THE ONLY THING MISSING FROM THE CANONICAL PATH
    Everything downstream of a review already exists and is already correct:

        tefca_reg_entities
              -> review_records            (reportable_at, set ONLY by QA APPROVE)
              -> review_decision_events    (append-only; APPROVE / RETURN / ESCALATE,
                                            supersession, segregation of duties)

    `app.tefca_registry.qa_gate` owns every human act on that chain and
    `app.tefca_registry.review_routes` exposes it. This module supplies the one
    missing edge: a Phase-6 observation that triage marked READY_FOR_ANALYST
    becomes a `review_records` row that the existing machinery can carry.

WHY NOT THE OTHER QUEUE
    `tefca_analyst_queue` hangs off `tefca_evidence_records` (0 rows) which hangs
    off `tefca_entities` (2 rows) — a parallel, effectively unused model still
    wired to 15 endpoints. Pointing it at canonical evidence would either break
    those endpoints or require a bridge table whose only purpose is keeping two
    evidence models alive. Neither is worth doing when the canonical chain
    already runs from registry entity to reportability.

WHAT THIS REFUSES TO DO
    It creates a QUESTION, never an answer. `classification_bucket`,
    `reviewer_resolution` and `reportable_at` are all left NULL — the first
    because triage is not a B1-B4 classification, the last two because a human
    has not acted. A review row created here is not reportable and cannot become
    reportable except through a QA APPROVE event.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, func, select, text
from sqlalchemy import cast as sa_cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.tefca_registry import models as reg
from app.Tefca.exception_triage import TRIAGE_VERSION, Triage

#: Recorded on every review this module creates, so a work item can always be
#: traced back to the run and the rules that raised it.
QUEUE_SOURCE = "PHASE6_EXCEPTION_TRIAGE"


class QueueRefused(RuntimeError):
    """A work item was not created, and the reason is stated."""


async def _next_review_id(db: AsyncSession) -> str:
    """REV-YYYY-NNNNNN. Mirrors `review_routes.generate_review_id`.

    Derived from the current maximum rather than a counter table, and retried on
    collision: review ids appear in delivered reports, so a duplicate is not
    something that can be quietly corrected afterwards.
    """
    year = datetime.utcnow().year
    prefix = f"REV-{year}-"
    for _attempt in range(6):
        top = (await db.execute(
            select(func.max(reg.ReviewRecord.review_id))
            .where(reg.ReviewRecord.review_id.like(f"{prefix}%")))).scalar()
        nxt = (int(top.rsplit("-", 1)[1]) + 1) if top else 1
        candidate = f"{prefix}{nxt:06d}"
        clash = (await db.execute(
            select(reg.ReviewRecord.id)
            .where(reg.ReviewRecord.review_id == candidate).limit(1))).scalar()
        if clash is None:
            return candidate
    raise QueueRefused(
        "Could not allocate a unique review id after 6 attempts; refusing "
        "rather than risking a duplicate id in a delivered report.")


async def create_work_item(
    db: AsyncSession,
    *,
    entity_id: uuid.UUID,
    observation_ids: List[uuid.UUID],
    reason: str,
    priority: int = 50,
    source_intake_id: Optional[uuid.UUID] = None,
    source_record_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create ONE analyst work item for one entity's exception.

    The observations are LINKED, never copied: `tefca_dimension_evidence.review_id`
    is stamped with the new review id so the evidence stays the single record of
    what each source said. Duplicating it into the review would create a second
    version of the truth that could drift from the first.
    """
    if not observation_ids:
        raise QueueRefused(
            "A work item must cite the observations that justify it; an "
            "exception with no evidence is not reviewable.")
    if not (reason or "").strip():
        raise QueueRefused("A work item must state why it exists.")

    entity = await db.get(reg.TefcaRegEntity, entity_id)
    if entity is None:
        raise QueueRefused(f"No canonical registry entity {entity_id}")

    review_id = await _next_review_id(db)
    record = reg.ReviewRecord(
        id=uuid.uuid4(),
        review_id=review_id,
        entity_id=entity_id,
        verification_results={
            # A SNAPSHOT of why this was queued, not a pointer to live state.
            "queue_source": QUEUE_SOURCE,
            "triage_version": TRIAGE_VERSION,
            "triage_disposition": Triage.READY_FOR_ANALYST.value,
            "triage_reason": reason,
            "observation_ids": [str(o) for o in observation_ids],
            "source_intake_id": str(source_intake_id) if source_intake_id else None,
            "source_record_id": source_record_id,
            "priority": priority,
            "queued_at": datetime.utcnow().isoformat(),
            # Named explicitly so nobody reads the absence as an oversight.
            "note": ("Triage raises a QUESTION. No classification, no "
                     "determination and no reportability is implied."),
        },
        # All three stay NULL on purpose — see the module docstring.
        classification_bucket=None,
        reviewer_resolution=None,
        reportable_at=None,
    )
    db.add(record)
    await db.flush()

    # Link the evidence to the review. The observation rows themselves are
    # otherwise untouched: their observation_result, hash and provenance are the
    # Phase-6 record and must remain byte-identical to what the source said.
    await db.execute(
        text("update tefca_dimension_evidence set review_id = :rid "
             "where id = any(:ids)"),
        {"rid": review_id, "ids": [str(o) for o in observation_ids]})

    return {"review_id": review_id, "entity_id": str(entity_id),
            "observation_count": len(observation_ids), "priority": priority,
            "reportable": False}


async def open_work_items(db: AsyncSession, limit: int = 100) -> List[Dict[str, Any]]:
    """Work items raised by triage that no human has resolved yet.

    Ordered by the priority triage assigned, then oldest first — an exception
    that has waited longer is not less urgent than an identical newer one.
    """
    # Ordered in SQL, not afterwards: sorting a page that LIMIT already chose
    # would order the wrong rows and quietly hide the highest-priority items
    # behind whichever ones happened to be oldest.
    priority = sa_cast(
        reg.ReviewRecord.verification_results["priority"].astext, Integer)
    rows = (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.reviewer_resolution.is_(None))
        .where(reg.ReviewRecord.verification_results["queue_source"].astext
               == QUEUE_SOURCE)
        .order_by(priority.desc().nullslast(),
                  reg.ReviewRecord.created_at.asc())
        .limit(limit))).scalars().all()
    items = [{
        "review_id": r.review_id,
        "entity_id": str(r.entity_id),
        "priority": (r.verification_results or {}).get("priority", 50),
        "reason": (r.verification_results or {}).get("triage_reason"),
        "observation_ids": (r.verification_results or {}).get("observation_ids", []),
        "reportable": r.reportable_at is not None,
    } for r in rows]
    return items
