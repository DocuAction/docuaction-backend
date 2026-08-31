"""A COR-directed priority review (Task 5), on the certified review workflow.

WHAT THIS CONNECTS — AND WHAT IT DELIBERATELY DOES NOT BUILD
────────────────────────────────────────────────────────────
    authorized COR request      -> TEFCAPriorityCase   (the request of record)
      -> canonical target resolution  (tefca_reg_entities, one ladder)
      -> review case                  (review_records)
      -> assignment / claim           (case_assignment)
      -> analyst determination        (review_decision_events)
      -> independent QA               (qa_gate)
      -> reportability                (review_records.reportable_at)
      -> D5.1 status report           (Tefca.reporting)

    **No new table. No migration.** `tefca_priority_cases` already exists and
    already carries every field the contract's D5.1 report needs — the COR
    reference, who asked, when it arrived, the deadline they set, the issue
    they described, and the root cause / severity / recommendations the report
    must contain. What it did NOT have was any connection to the certified
    maker-checker chain, so a single `senior_analyst` could PATCH a root cause,
    a severity and a resolution in one call and the report would print it.

    This module is that connection. The request row stays the request. Every
    human act on the determination goes through `review_decision_events`, which
    already owns analyst determination, independent QA, segregation of duties
    and reportability. There is no second determination system here.

SELECTION AUTHORITY IS THE WHOLE POINT
──────────────────────────────────────
    A priority review is not a statistical selection and not a data-quality
    finding. It exists because the COR asked for it, by reference. Nothing in
    this module can create a request from a HIGH DQ severity, a HELD record, a
    source conflict or a sample membership — `receive_request` requires a COR
    reference and a named requester, and there is no code path that supplies
    them from a rule.

THE DEADLINE
────────────
    Contract Task 5 (¶146): reviews are performed "within the agreed upon
    deadline", and "the deadline, and the Participants or Subparticipants
    identified for review, will be communicated by the COR."

    So there is NO fixed turnaround, and this module manufactures none. A
    request either carries the deadline the COR stated or it carries
    NO_DEADLINE, which is a state to act on (ask the COR) and not a default to
    compute from. `DUE_SOON` requires the caller to say what "soon" means,
    because the contract does not — the alternative would be inventing a
    threshold and reporting against it.

    `app/tefca_registry/sla.py` holds a `"priority": 3` day window. That window
    is a display policy for SAMPLED reviews and is explicitly not contractual;
    it must never be applied to a Task 5 request, and a test pins that this
    module never imports it.

PAST_DUE IS NOT A COMPLIANCE FINDING
────────────────────────────────────
    It is arithmetic on two timestamps. Whether a missed deadline is a
    contractual failure depends on what was agreed, what was communicated and
    when — none of which a timestamp knows. The status is reported; the
    conclusion is not drawn.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from app.tefca_registry import audit as reg_audit
from app.tefca_registry import models as reg

#: Stamped on every review case this module creates, so priority work is
#: separable from DQ exceptions and from statistical samples in the same table.
QUEUE_SOURCE = "TEFCA_ARC_PRIORITY"
SELECTION_REASON = "PRIORITY_REQUEST"
SERVICE_VERSION = "1.0.0"

#: Contract anchor for every parameter this module implements.
CONTRACT_AUTHORITY = "SOW Task 5, para 146-150 (contract 7571MN26F80064)"

# ── deadline vocabulary ──────────────────────────────────────────────────────
#: Minimal, and every value is derivable from the request alone.
NO_DEADLINE = "NO_DEADLINE"
ON_TRACK = "ON_TRACK"
DUE_SOON = "DUE_SOON"
PAST_DUE = "PAST_DUE"

# ── request lifecycle, DERIVED ───────────────────────────────────────────────
#: There is no status column this module writes. State is read from the review
#: events and the assignment column that already own it — a second copy would
#: be a second answer to the same question.
RECEIVED = "RECEIVED"
NEEDS_TARGET_RESOLUTION = "NEEDS_TARGET_RESOLUTION"
WITHDRAWN = "WITHDRAWN"

# ── audit actions ────────────────────────────────────────────────────────────
ACT_RECEIVED = "priority_request_received"
ACT_DEADLINE_AMENDED = "priority_deadline_amended"
ACT_WITHDRAWN = "priority_request_withdrawn"
ACT_CONTENT_RECORDED = "priority_finding_recorded"


class PriorityRefused(RuntimeError):
    """A priority-review act was refused, and the reason is stated."""


def _case_model():
    from app.Tefca.models import TEFCAPriorityCase
    return TEFCAPriorityCase


def request_key(cor_reference: str, target_reference: str) -> str:
    """The idempotency key: one COR request, one target.

    The COR reference is IN the key on purpose. A second submission of the same
    request for the same organisation is a transport replay and must find the
    case already made. A NEW request naming the same organisation is a new
    question the COR is entitled to ask, and must create a new case — a
    uniqueness rule keyed on the organisation alone would refuse the Government
    a second review of an organisation it has already asked about once.
    """
    return f"{QUEUE_SOURCE}:{(cor_reference or '').strip()}:{(target_reference or '').strip()}"


# ── target resolution ────────────────────────────────────────────────────────

async def resolve_target(db, reference: str) -> Dict[str, Any]:
    """Resolve one COR-named organisation against the CANONICAL registry.

    Delegates to `Tefca.entity_resolution`, which owns the single resolution
    ladder used by the evidence pipeline. Reusing it means a priority request
    and an evidence run agree about what a reference means; reimplementing the
    ladder here would guarantee they eventually would not.

    Ambiguity is returned WITH its candidates, never resolved by taking the
    first. Attaching one organisation's federal evidence to another's review is
    worse than resolving nothing, and a COR request is exactly the case where
    someone will act on the answer.
    """
    from app.Tefca import entity_resolution as er

    outcome = await er.resolve_reference_detail(db, reference)
    return {
        "reference": (reference or "").strip(),
        "state": outcome["state"],
        "entity_id": outcome["entity_id"],
        "matched_on": outcome["matched_on"],
        "candidate_entity_ids": [str(c) for c in outcome["candidates"]],
    }


async def _area1_anchor(db, entity_id, reference: str):
    """The delivered line a case should cite, and whether it was promoted.

    A canonical entity carries its source record. An UNPROMOTED delivered
    record has no entity at all — and a COR request may legitimately name one,
    because the Government asks about an organisation in its delivery, not
    about the subset AGT was able to promote. `review_records.source_record_id`
    exists precisely so such a case can be about something.
    """
    from app.tefca_registry.rce import models as m

    if entity_id is not None:
        entity = await db.get(reg.TefcaRegEntity, entity_id)
        return (getattr(entity, "source_record_id", None), False)

    # No entity: is the reference a delivered record that was never promoted?
    row = (await db.execute(
        select(m.RceCuratedRecord.source_record_id, m.RceCuratedRecord.record_status)
        .where(m.RceCuratedRecord.rce_org_oid == reference,
               m.RceCuratedRecord.canonical_entity_id.is_(None))
        .limit(2))).all()
    if len(row) == 1:
        return (row[0][0], True)
    return (None, False)


# ── request intake ───────────────────────────────────────────────────────────

async def _next_review_id(db) -> str:
    """REV-YYYY-NNNNNN. Mirrors `review_routes.generate_review_id`.

    Derived from the current maximum and retried on collision: review ids
    appear in delivered reports, so a duplicate is not something that can be
    quietly corrected afterwards.
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
    raise PriorityRefused(
        "Could not allocate a unique review id after 6 attempts; refusing "
        "rather than risking a duplicate id in a delivered report.")


async def _existing_case(db, key: str) -> Optional[reg.ReviewRecord]:
    return (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.verification_results["queue_source"].astext
               == QUEUE_SOURCE,
               reg.ReviewRecord.verification_results["request_key"].astext == key)
        .limit(1))).scalars().first()


async def receive_request(db, *, cor_reference: str, target_reference: str,
                          issue_description: str, requested_by: str,
                          received_at: Optional[datetime] = None,
                          deadline: Optional[datetime] = None,
                          instructions: Optional[str] = None,
                          qhin: Optional[str] = None,
                          actor: Optional[str] = None,
                          actor_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    """Log one authorized COR request for one organisation. IDEMPOTENT.

    The three things that make this a priority review — the COR reference, who
    asked, and what they described — are mandatory arguments. There is no
    default for any of them, which is what stops this from being callable by a
    rule that merely thinks something looks urgent.

    The deadline is optional and is recorded EXACTLY as supplied. Absent means
    absent; the procedure says AGT asks the COR rather than assuming, so a
    missing deadline is surfaced, never computed.
    """
    cor_reference = (cor_reference or "").strip()
    target_reference = (target_reference or "").strip()
    issue_description = (issue_description or "").strip()
    requested_by = (requested_by or "").strip()
    if not cor_reference:
        raise PriorityRefused(
            "a priority review requires the COR reference that authorises it; "
            "without one this would be an AGT-initiated review presented as a "
            "Government request")
    if not target_reference:
        raise PriorityRefused("a priority review requires the organisation the COR named")
    if not issue_description:
        raise PriorityRefused(
            "a priority review requires the issue the COR described; it is the "
            "first element the D5.1 status report must contain")
    if not requested_by:
        raise PriorityRefused("a priority review requires the person who made the request")

    key = request_key(cor_reference, target_reference)

    # One writer at a time per request. Transaction-scoped, so a crash cannot
    # strand it. Without this, two concurrent submissions of the same request
    # could both find no case and both create one.
    await db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": key})

    found = await _existing_case(db, key)
    if found is not None:
        payload = found.verification_results or {}
        return {**await get_request(db, uuid.UUID(payload["priority_case_id"])),
                "duplicate_request": True}

    resolution = await resolve_target(db, target_reference)
    source_record_id, pre_promotion = await _area1_anchor(
        db, resolution["entity_id"], target_reference)

    received_at = received_at or datetime.utcnow()
    case = _case_model()(
        case_id=uuid.uuid4(),
        cor_reference=cor_reference,
        qhin=qhin,
        # Deliberately NULL: this FK points at the legacy `tefca_entities`
        # table, and the canonical target lives on the review case. Writing a
        # legacy id here would give the request two targets that could differ.
        entity_id=None,
        assigned_by=requested_by,
        assigned_date=received_at,
        deadline_date=deadline,
        issue_description=issue_description,
        # ASSIGNED means "request logged". It is NOT the workflow state and
        # this module never advances it — see `request_state`.
        case_status=_case_status_received(),
    )
    db.add(case)
    await db.flush()

    # THE REQUEST IS LOGGED EITHER WAY. A review case needs a subject —
    # `ck_review_record_has_subject` sees to that — and an unresolvable
    # reference has none yet. Refusing the whole request would mean the
    # Government asked and AGT recorded nothing, which is the opposite of the
    # procedure. The request stands in NEEDS_TARGET_RESOLUTION with its
    # candidates preserved until a human resolves it.
    record = None
    if resolution["entity_id"] is not None or source_record_id is not None:
        record = await _create_review_case(
            db, case, resolution, source_record_id, pre_promotion,
            key=key, requested_by=requested_by, received_at=received_at,
            deadline=deadline, instructions=instructions, qhin=qhin)

    reg_audit.record(
        db, ACT_RECEIVED, resolution["entity_id"],
        actor_id=actor_id, actor_email=actor or requested_by,
        metadata={"priority_case_id": str(case.case_id),
                  "review_id": record.review_id if record else None,
                  "request_key": key,
                  "cor_reference": cor_reference,
                  "requested_by": requested_by,
                  "received_at": received_at.isoformat(),
                  # When AGT wrote the row, as distinct from when the COR's
                  # request arrived: `received_at` may be backdated to the
                  # email, and the audit row's own `created_at` is Postgres
                  # transaction time, identical for two writes in one
                  # transaction. Ordering history on either would be wrong.
                  "recorded_at": datetime.utcnow().isoformat(),
                  "target_reference": target_reference,
                  "target_resolution": resolution["state"],
                  "candidate_entity_ids": resolution["candidate_entity_ids"],
                  "instructions": instructions,
                  # The deadline as first stated. Every later amendment names
                  # this value, so the original survives the column being
                  # updated.
                  "deadline": deadline.isoformat() if deadline else None,
                  "deadline_stated": deadline is not None,
                  "contract_authority": CONTRACT_AUTHORITY})

    return {**await get_request(db, case.case_id), "duplicate_request": False}


async def _create_review_case(db, case, resolution, source_record_id,
                              pre_promotion, *, key, requested_by, received_at,
                              deadline, instructions, qhin) -> reg.ReviewRecord:
    """The review case for a request whose target is known."""
    review_id = await _next_review_id(db)
    record = reg.ReviewRecord(
        id=uuid.uuid4(),
        review_id=review_id,
        entity_id=resolution["entity_id"],
        source_record_id=source_record_id,
        verification_results={
            "queue_source": QUEUE_SOURCE,
            "selection_reason": SELECTION_REASON,
            "service_version": SERVICE_VERSION,
            "contract_authority": CONTRACT_AUTHORITY,
            "request_key": key,
            "priority_case_id": str(case.case_id),
            "cor_reference": case.cor_reference,
            "requested_by": requested_by,
            "received_at": received_at.isoformat(),
            "target_reference": resolution["reference"],
            "target_resolution": resolution["state"],
            "target_matched_on": resolution["matched_on"],
            "candidate_entity_ids": resolution["candidate_entity_ids"],
            "pre_promotion": pre_promotion,
            "deadline_at_receipt": deadline.isoformat() if deadline else None,
            "instructions": instructions,
            "qhin": qhin,
            "queued_at": datetime.utcnow().isoformat(),
            "note": ("A COR-directed review under Task 5. No determination, no "
                     "severity and no reportability is implied by this record "
                     "existing."),
        },
        # All NULL. Only a human, through the QA gate, may move them, and only
        # a standing QA APPROVE sets reportable_at.
        classification_bucket=None,
        reviewer_resolution=None,
        reportable_at=None,
    )
    db.add(record)
    await db.flush()
    return record


async def resolve_target_manually(db, case_id, *, entity_id: uuid.UUID,
                                  rationale: str, actor: str,
                                  actor_id: Optional[uuid.UUID] = None
                                  ) -> Dict[str, Any]:
    """A human names the canonical entity an ambiguous request meant.

    This is the only way an AMBIGUOUS or NOT_FOUND target acquires a review
    case. It is deliberately a separate, attributable act with a written
    rationale: automatic first-match selection would attach one organisation's
    federal evidence to another organisation's Government review, and nobody
    would ever see that it had happened.
    """
    rationale = (rationale or "").strip()
    if len(rationale) < 10:
        raise PriorityRefused(
            "resolving a Government target by hand requires a written rationale "
            "naming the evidence the choice rests on")
    if not (actor or "").strip():
        raise PriorityRefused("target resolution must name who decided it")

    case = await _case_or_refuse(db, case_id)
    if await _review_for(db, case.case_id) is not None:
        raise PriorityRefused(
            f"request {case.cor_reference} already has a review case; its "
            f"target cannot be changed after review has begun")
    if await _withdrawn_at(db, case.case_id) is not None:
        raise PriorityRefused(f"request {case.cor_reference} was withdrawn")

    entity = await db.get(reg.TefcaRegEntity, entity_id)
    if entity is None:
        raise PriorityRefused(
            f"{entity_id} is not a registry entity. A target is chosen from the "
            f"registry; one is never created to satisfy a request.")

    received = await _received_metadata(db, case.case_id)
    resolution = {"reference": received.get("target_reference"),
                  "state": "RESOLVED",
                  "entity_id": entity.id,
                  "matched_on": "human_resolution",
                  "candidate_entity_ids": received.get("candidate_entity_ids") or []}
    record = await _create_review_case(
        db, case, resolution, getattr(entity, "source_record_id", None), False,
        key=received.get("request_key"), requested_by=case.assigned_by,
        received_at=case.assigned_date, deadline=case.deadline_date,
        instructions=received.get("instructions"), qhin=case.qhin)
    payload = dict(record.verification_results or {})
    payload["target_resolved_by"] = actor
    payload["target_resolution_rationale"] = rationale
    record.verification_results = payload
    await db.flush()

    reg_audit.record(
        db, "priority_target_resolved", entity.id, actor_id=actor_id,
        actor_email=actor,
        metadata={"priority_case_id": str(case.case_id),
                  "review_id": record.review_id,
                  "entity_id": str(entity.id),
                  "rationale": rationale,
                  "candidate_entity_ids": resolution["candidate_entity_ids"]})
    return await get_request(db, case.case_id)


async def _received_metadata(db, case_id) -> Dict[str, Any]:
    """What was recorded when the request arrived. The append-only original."""
    rows = (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action == ACT_RECEIVED))).scalars().all()
    for row in rows:
        if (row.metadata_ or {}).get("priority_case_id") == str(case_id):
            return row.metadata_ or {}
    return {}


def _case_status_received():
    from app.Tefca.models import CaseStatus
    return CaseStatus.ASSIGNED


# ── deadline ─────────────────────────────────────────────────────────────────

async def amend_deadline(db, case_id, *, new_deadline: Optional[datetime],
                         reason: str, actor: str,
                         actor_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    """Record a deadline the COR changed. Append-only in the audit trail.

    The column is updated because it is what "the deadline" means today, and
    the audit log keeps every value it has ever held, with who changed it and
    why. Overwriting without that history would make an amended deadline
    indistinguishable from a misrecorded one.
    """
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise PriorityRefused(
            "amending a Government deadline requires a reason of at least 10 "
            "characters naming the authority for the change")
    if not (actor or "").strip():
        raise PriorityRefused("a deadline amendment must name who recorded it")

    case = await _case_or_refuse(db, case_id)
    previous = case.deadline_date
    case.deadline_date = new_deadline
    await db.flush()

    reg_audit.record(
        db, ACT_DEADLINE_AMENDED, None, actor_id=actor_id, actor_email=actor,
        metadata={"priority_case_id": str(case.case_id),
                  "cor_reference": case.cor_reference,
                  "deadline_from": previous.isoformat() if previous else None,
                  "deadline": new_deadline.isoformat() if new_deadline else None,
                  "reason": reason,
                  "recorded_at": datetime.utcnow().isoformat(),
                  "amended_at": datetime.utcnow().isoformat()})
    return await deadline_history(db, case_id)


async def deadline_history(db, case_id) -> Dict[str, Any]:
    """Every deadline this request has held, oldest first.

    Reconstructed from the append-only audit trail rather than from a column,
    so the original survives however many times the current value changes.
    """
    case = await _case_or_refuse(db, case_id)
    rows = (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action.in_((ACT_RECEIVED, ACT_DEADLINE_AMENDED)))
    )).scalars().all()
    mine = sorted(
        (r for r in rows
         if (r.metadata_ or {}).get("priority_case_id") == str(case.case_id)),
        key=lambda r: (r.metadata_ or {}).get("recorded_at") or "")

    history = [{
        "deadline": (r.metadata_ or {}).get("deadline"),
        "recorded_at": ((r.metadata_ or {}).get("recorded_at")
                        or (r.created_at.isoformat() if r.created_at else None)),
        "actor": r.actor_email,
        "action": r.action,
        "reason": (r.metadata_ or {}).get("reason"),
    } for r in mine]

    return {
        "priority_case_id": str(case.case_id),
        "cor_reference": case.cor_reference,
        "original_deadline": history[0]["deadline"] if history else None,
        "current_deadline": (case.deadline_date.isoformat()
                             if case.deadline_date else None),
        "amendments": max(0, len(history) - 1),
        "history": history,
    }


def deadline_status(deadline: Optional[datetime], *, now: Optional[datetime] = None,
                    due_soon_within_hours: Optional[float] = None) -> Dict[str, Any]:
    """Where this request stands against the deadline the COR set.

    `due_soon_within_hours` has NO default. The contract sets no standing
    turnaround and names no warning threshold, so a default here would be an
    invented service level arriving through the back door. A caller that wants
    a warning band says how wide it is, and the answer records the number it
    was given.

    PAST_DUE is arithmetic, not an assertion of contractual non-compliance.
    """
    now = now or datetime.utcnow()
    if deadline is None:
        return {"deadline": None, "status": NO_DEADLINE, "hours_remaining": None,
                "due_soon_within_hours": due_soon_within_hours,
                "note": ("The COR did not state a deadline. The procedure is to "
                         "ask, not to assume one."),
                "compliance_conclusion": None}

    remaining = (deadline - now).total_seconds() / 3600.0
    if remaining < 0:
        status = PAST_DUE
    elif due_soon_within_hours is not None and remaining <= due_soon_within_hours:
        status = DUE_SOON
    else:
        status = ON_TRACK

    return {
        "deadline": deadline.isoformat(),
        "status": status,
        "hours_remaining": round(remaining, 3),
        "due_soon_within_hours": due_soon_within_hours,
        "note": ("Measured against the deadline the COR set for this request. "
                 "No standing AGT service level is applied."),
        # Stated explicitly so no downstream consumer can read PAST_DUE as a
        # finding against the Government or against AGT.
        "compliance_conclusion": None,
    }


# ── withdrawal ───────────────────────────────────────────────────────────────

async def withdraw_request(db, case_id, *, reason: str, actor: str,
                           actor_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    """Record that the COR withdrew a request. Nothing is deleted.

    The request, the case, the evidence and every decision event stay exactly
    where they are. Withdrawal is a fact ABOUT the request, recorded once in
    the append-only trail and derived from there — not an erasure and not a
    column this module keeps in parallel.
    """
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise PriorityRefused(
            "withdrawing a Government request requires a reason naming the "
            "authority for the withdrawal")
    case = await _case_or_refuse(db, case_id)
    if await _withdrawn_at(db, case.case_id) is not None:
        raise PriorityRefused(f"request {case.cor_reference} is already withdrawn")

    reg_audit.record(
        db, ACT_WITHDRAWN, None, actor_id=actor_id, actor_email=actor,
        metadata={"priority_case_id": str(case.case_id),
                  "cor_reference": case.cor_reference,
                  "reason": reason,
                  "withdrawn_at": datetime.utcnow().isoformat()})
    await db.flush()
    return await get_request(db, case_id)


async def _withdrawn_at(db, case_id) -> Optional[str]:
    rows = (await db.execute(
        select(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.action == ACT_WITHDRAWN))).scalars().all()
    for row in rows:
        if (row.metadata_ or {}).get("priority_case_id") == str(case_id):
            return (row.metadata_ or {}).get("withdrawn_at")
    return None


# ── the analyst's finding ────────────────────────────────────────────────────

async def record_finding(db, case_id, *, user, root_cause_determination: Optional[str],
                         root_cause_description: Optional[str],
                         severity: str, recommendations: List[Dict[str, Any]],
                         prevention_recommendation: Optional[str] = None,
                         resolution_notes: Optional[str] = None,
                         rationale: str,
                         ip_address: Optional[str] = None) -> Dict[str, Any]:
    """The analyst's D5.1 content, recorded WITH a determination event.

    The five elements the contract names for a D5.1 status report — the
    identified issue, root cause if determined, severity or impact,
    recommendations to prevent reoccurrence, and resolution — already have
    columns on `tefca_priority_cases`. This writes them, and in the same call
    records the ANALYST_DETERMINATION event that makes the write attributable
    and reviewable.

    The columns are the report's content. The event chain is the authority. A
    caller cannot have one without the other, which is what stops the old
    single-PATCH determination from being possible through this path.

    "Root cause not determined" is a legitimate outcome and is recorded as
    such. The contract asks for root cause **if determined**; manufacturing one
    to fill the field would be worse than the honest blank.
    """
    from app.tefca_registry import case_assignment as assignment
    from app.tefca_registry.qa_gate import record_analyst_determination

    case = await _case_or_refuse(db, case_id)
    record = await _review_or_refuse(db, case.case_id)

    if record.reportable_at is not None:
        raise PriorityRefused(
            f"{record.review_id} has a standing QA approval; its reported "
            f"content cannot be edited afterwards. A correction is a new "
            f"determination through the QA gate, not an overwrite.")
    if await _withdrawn_at(db, case.case_id) is not None:
        raise PriorityRefused(
            f"request {case.cor_reference} was withdrawn by the COR; recording "
            f"a finding against it would answer a question no longer asked")

    # The case owner is the only one who may record its finding. `require_owner`
    # is the same check the DQ and sampling paths use.
    assignment.require_owner(record, user)

    severity = (severity or "").strip().upper()
    valid = _severities()
    if severity not in valid:
        raise PriorityRefused(f"severity must be one of {sorted(valid)}")
    if not isinstance(recommendations, list):
        raise PriorityRefused("recommendations must be a list of recorded items")

    # The determination event first: if the gate refuses (no actor, rationale
    # too short), nothing has been written to the report content.
    event = await record_analyst_determination(
        db, record.review_id, user=user, determination="CONFIRM",
        rationale=rationale, ip_address=ip_address)

    from app.Tefca.models import CaseSeverity
    case.root_cause_determination = root_cause_determination
    case.root_cause_description = root_cause_description
    case.severity = CaseSeverity[severity]
    case.recommendations = recommendations
    case.prevention_recommendation = prevention_recommendation
    case.resolution_notes = resolution_notes
    case.assigned_reviewer_id = str(getattr(user, "email", "") or "")
    await db.flush()

    reg_audit.record(
        db, ACT_CONTENT_RECORDED, record.entity_id,
        actor_id=getattr(user, "id", None),
        actor_email=str(getattr(user, "email", "") or ""),
        ip_address=ip_address,
        metadata={"priority_case_id": str(case.case_id),
                  "review_id": record.review_id,
                  "decision_event_id": event["decision_event_id"],
                  "root_cause_determined": root_cause_determination is not None,
                  "severity": severity,
                  "recommendation_count": len(recommendations)})
    return {"priority_case_id": str(case.case_id),
            "review_id": record.review_id,
            "decision_event_id": event["decision_event_id"],
            "reportable": False,
            "note": ("Recorded for independent QA. An analyst determination is "
                     "not a reportable result.")}


def _severities():
    from app.Tefca.models import CaseSeverity
    return {s.name for s in CaseSeverity}


# ── reads ────────────────────────────────────────────────────────────────────

async def _case_or_refuse(db, case_id):
    case = await db.get(_case_model(), case_id)
    if case is None:
        raise PriorityRefused(f"no priority request {case_id}")
    return case


async def _review_for(db, case_id) -> Optional[reg.ReviewRecord]:
    """The review case for a request, or None while its target is unresolved."""
    return (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.verification_results["priority_case_id"].astext
               == str(case_id)).limit(1))).scalars().first()


async def _review_or_refuse(db, case_id) -> reg.ReviewRecord:
    record = await _review_for(db, case_id)
    if record is None:
        raise PriorityRefused(
            f"priority request {case_id} has no review case: its target is not "
            f"resolved to one canonical entity. Resolve the target first — "
            f"there is nothing yet for an analyst to be right or wrong about.")
    return record


async def request_state(db, case_id) -> str:
    """The request's operational state, DERIVED — never a stored column."""
    from app.tefca_registry import case_assignment as assignment

    case = await _case_or_refuse(db, case_id)
    if await _withdrawn_at(db, case.case_id) is not None:
        return WITHDRAWN
    record = await _review_for(db, case.case_id)
    if record is None:
        return NEEDS_TARGET_RESOLUTION
    return await assignment.case_state(db, record.review_id)


async def get_request(db, case_id) -> Dict[str, Any]:
    """One request, its case, its deadline standing and its workflow state."""
    from app.tefca_registry.qa_gate import _events, is_reportable

    case = await _case_or_refuse(db, case_id)
    record = await _review_for(db, case.case_id)
    received = await _received_metadata(db, case.case_id)
    payload = (record.verification_results or {}) if record is not None else received
    events = await _events(db, record.review_id) if record is not None else []

    return {
        "priority_case_id": str(case.case_id),
        "cor_reference": case.cor_reference,
        "requested_by": case.assigned_by,
        "received_at": case.assigned_date.isoformat() if case.assigned_date else None,
        "issue_description": case.issue_description,
        "instructions": payload.get("instructions"),
        "qhin": case.qhin,
        "target_reference": payload.get("target_reference"),
        "target_resolution": payload.get("target_resolution"),
        "target_matched_on": payload.get("target_matched_on"),
        "candidate_entity_ids": payload.get("candidate_entity_ids") or [],
        "entity_id": (str(record.entity_id)
                      if record is not None and record.entity_id else None),
        "source_record_id": (str(record.source_record_id)
                             if record is not None and record.source_record_id
                             else None),
        "pre_promotion": payload.get("pre_promotion"),
        "review_id": record.review_id if record is not None else None,
        "assigned_to_user_id": (str(record.assigned_to_user_id)
                                if record is not None and record.assigned_to_user_id
                                else None),
        "state": await request_state(db, case.case_id),
        "withdrawn_at": await _withdrawn_at(db, case.case_id),
        "deadline": (case.deadline_date.isoformat() if case.deadline_date else None),
        "deadline_at_receipt": payload.get("deadline_at_receipt"),
        "severity": case.severity.value if case.severity else None,
        "root_cause_determination": case.root_cause_determination,
        "decision_events": len(events),
        "reportable": is_reportable(events),
        "contract_authority": CONTRACT_AUTHORITY,
    }


async def analyst_package(db, case_id) -> Dict[str, Any]:
    """Everything the analyst needs to work the case, in one read.

    References and observations, not a dump of internals: the delivered values
    stay in Area 1 and are read from there, so the package cannot drift from
    the source of truth it describes.
    """
    from app.tefca_registry.qa_gate import history as qa_history, _events
    from app.tefca_registry.rce import models as m

    request = await get_request(db, case_id)
    record = await _review_or_refuse(db, case_id)

    entity = None
    if record.entity_id:
        row = await db.get(reg.TefcaRegEntity, record.entity_id)
        if row is not None:
            entity = {"entity_id": str(row.id), "name": row.name,
                      "entity_level": row.entity_level,
                      "operational_status": row.operational_status,
                      "verification_status": row.verification_status,
                      "rce_org_oid": getattr(row, "rce_org_oid", None)}

    qhin = None
    if record.entity_id:
        parent = (await db.execute(
            select(reg.TefcaRegEntity.id, reg.TefcaRegEntity.name)
            .join(reg.TefcaEntityRelationship,
                  reg.TefcaEntityRelationship.parent_entity_id == reg.TefcaRegEntity.id)
            .where(reg.TefcaEntityRelationship.child_entity_id == record.entity_id,
                   reg.TefcaEntityRelationship.relationship_type == "managed_by_qhin",
                   reg.TefcaEntityRelationship.status == "active"))).all()
        if len(parent) == 1:
            qhin = {"entity_id": str(parent[0][0]), "name": parent[0][1]}

    curated = None
    if record.source_record_id:
        row = (await db.execute(
            select(m.RceCuratedRecord)
            .where(m.RceCuratedRecord.source_record_id == record.source_record_id)
            .limit(1))).scalars().first()
        if row is not None:
            curated = {"record_status": row.record_status,
                       "issue_count": row.issue_count,
                       "correction_count": row.correction_count,
                       "source_intake_id": str(row.source_intake_id)}

    # Historical review context: prior reviews of the same subject. Shown, and
    # explicitly NOT treated as a current answer — a new Government request
    # asks a new question and must be reviewed on its own scope.
    prior = []
    if record.entity_id is not None:
        rows = (await db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.entity_id == record.entity_id,
                   reg.ReviewRecord.review_id != record.review_id)
            .order_by(reg.ReviewRecord.created_at.desc()).limit(10))).scalars().all()
        for row in rows:
            other = row.verification_results or {}
            prior.append({"review_id": row.review_id,
                          "queue_source": other.get("queue_source"),
                          "selection_reason": other.get("selection_reason"),
                          "reportable_at": (row.reportable_at.isoformat()
                                            if row.reportable_at else None),
                          "created_at": (row.created_at.isoformat()
                                         if row.created_at else None)})

    # Verification observations already recorded for this entity. READ ONLY:
    # this module collects no evidence of its own and runs no connector. A
    # source that could not answer stays `unavailable` — priority does not
    # convert an outage into an answer, and a short deadline is exactly when
    # that temptation arrives.
    verifications = []
    if record.entity_id is not None:
        rows = (await db.execute(
            select(reg.TefcaVerification)
            .where(reg.TefcaVerification.entity_id == record.entity_id)
            .order_by(reg.TefcaVerification.verified_at.desc())
            .limit(50))).scalars().all()
        verifications = [{"source": r.source,
                          "verification_status": r.verification_status,
                          "lookup_identifier": r.lookup_identifier,
                          "detail": r.detail,
                          "data_source_label": r.data_source_label,
                          "verified_at": (r.verified_at.isoformat()
                                          if r.verified_at else None)}
                         for r in rows]
    unavailable = sorted({v["source"] for v in verifications
                          if v["verification_status"] == "unavailable"})

    return {
        "request": request,
        "entity": entity,
        "qhin": qhin,
        "delivered_record": curated,
        "verifications": verifications,
        "source_limitations": [
            {"source": source,
             "status": "unavailable",
             "meaning": ("The source could not answer. This is not evidence "
                         "for or against the entity and must not be reported "
                         "as a clear result.")}
            for source in unavailable],
        "government_restricted_identifiers": _restricted_identifiers(),
        "prior_reviews": prior,
        "decision_history": qa_history(await _events(db, record.review_id)),
        "note": ("Prior reviews are context. A previous approval is not a "
                 "current answer: this request has its own scope and requires "
                 "its own determination and QA."),
    }


def _restricted_identifiers() -> List[Dict[str, Any]]:
    """Identifiers AGT has no authorized mechanism to verify.

    Quoted from `Tefca.identifier_boundary`, which owns the boundary. A COR
    request asking for taxpayer-identity confirmation is answered with the
    boundary, not with a manufactured PASS and not by inferring identity from
    an NPI.
    """
    from app.Tefca.identifier_boundary import (AUTHORITIES,
                                               GOVERNMENT_RESTRICTED)

    return [{"identifier": name,
             "state": "PENDING_GOVERNMENT_VERIFICATION",
             "reason": (AUTHORITIES[name].access_note
                        if name in AUTHORITIES else None)}
            for name in sorted(GOVERNMENT_RESTRICTED)]


async def reportable_result(db, case_id) -> Dict[str, Any]:
    """The D5.1 content, and whether it may be reported at all.

    Refuses to present a determination as a result until a QA approval stands.
    The request existing is not a finding; an analyst determination is not a
    finding; only an independently approved determination is.
    """
    from app.tefca_registry.qa_gate import _events, effective_determination, is_reportable

    case = await _case_or_refuse(db, case_id)
    record = await _review_or_refuse(db, case.case_id)
    events = await _events(db, record.review_id)
    reportable = is_reportable(events)

    return {
        "priority_case_id": str(case.case_id),
        "cor_reference": case.cor_reference,
        "review_id": record.review_id,
        "reportable": reportable,
        "reportable_at": (record.reportable_at.isoformat()
                          if record.reportable_at else None),
        "effective_determination": effective_determination(events),
        "identified_issue": case.issue_description,
        "root_cause_determination": case.root_cause_determination if reportable else None,
        "root_cause_description": case.root_cause_description if reportable else None,
        "severity": (case.severity.value if (reportable and case.severity) else None),
        "recommendations": case.recommendations if reportable else None,
        "prevention_recommendation": case.prevention_recommendation if reportable else None,
        "resolution_notes": case.resolution_notes if reportable else None,
        "withheld_reason": None if reportable else (
            "No standing QA approval. Under the release gate a determination is "
            "not a reportable result until an independent reviewer approves it."),
    }


async def open_requests(db, *, limit: int = 100,
                        include_withdrawn: bool = False) -> List[Dict[str, Any]]:
    """Priority work, newest request first.

    Driven from the REQUEST rows, not from the review cases: a request whose
    target is still unresolved has no case yet, and it is exactly the one an
    operator most needs to see.
    """
    cases = (await db.execute(
        select(_case_model())
        .order_by(_case_model().assigned_date.desc())
        .limit(limit))).scalars().all()

    out = []
    for case in cases:
        if await _review_for(db, case.case_id) is None \
                and not await _received_metadata(db, case.case_id):
            # Not created by this service (no receipt record, no case). Left
            # alone rather than reinterpreted through a workflow it never
            # entered.
            continue
        item = await get_request(db, case.case_id)
        if item["state"] == WITHDRAWN and not include_withdrawn:
            continue
        out.append(item)
    return out


async def workload_summary(db, *, now: Optional[datetime] = None,
                           due_soon_within_hours: Optional[float] = None
                           ) -> Dict[str, Any]:
    """Capacity view for the ~20 requests a month the contract anticipates.

    Counting only, and no cap: the contract's twenty-per-month is a planning
    figure describing expected workload, not a quota to enforce and not a
    threshold whose breach is a finding.
    """
    requests = await open_requests(db, limit=1000, include_withdrawn=True)
    by_state: Dict[str, int] = {}
    by_deadline: Dict[str, int] = {}
    unresolved_targets = 0
    for item in requests:
        by_state[item["state"]] = by_state.get(item["state"], 0) + 1
        deadline = (datetime.fromisoformat(item["deadline"])
                    if item["deadline"] else None)
        status = deadline_status(deadline, now=now,
                                 due_soon_within_hours=due_soon_within_hours)["status"]
        by_deadline[status] = by_deadline.get(status, 0) + 1
        if item["target_resolution"] != "RESOLVED":
            unresolved_targets += 1

    return {
        "total_requests": len(requests),
        "by_state": by_state,
        "by_deadline_status": by_deadline,
        "unresolved_targets": unresolved_targets,
        "reportable": sum(1 for i in requests if i["reportable"]),
        "due_soon_within_hours": due_soon_within_hours,
        "note": ("Contract Task 5 anticipates an average of twenty reviews per "
                 "month with capability to exceed. That is a capacity "
                 "expectation, not a cap, a quota or a compliance threshold."),
    }
