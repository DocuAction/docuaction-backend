"""HUMAN_REQUIRED DQ issues become review cases in the EXISTING architecture.

WHAT THIS CONNECTS
──────────────────
    current quality run
        -> HUMAN_REQUIRED issues            (rce_issues)
        -> review case                      (review_records)
        -> analyst determination / QA        (review_decision_events)
        -> reportability                     (review_records.reportable_at)

    There is no new case table, and there must never be one. `review_records`
    plus `review_decision_events` already own every human act on a
    determination, and `app.Tefca.exception_queue.create_work_item` already
    demonstrates the pattern for a DIFFERENT trigger (Phase-6 observations).
    This module is the second caller of that same pattern, for the DQ ledger.

THE CASE BOUNDARY, DECIDED FROM THE DATA
────────────────────────────────────────
    One case = (current run, source record, case classification).

    Not one case per issue: two address findings on one record are one
    question and would fragment an analyst's work for no reason.

    Not one case per source record either. Measured on the delivered
    population: 134 source records carry HUMAN_REQUIRED findings, 130 with one
    finding and 4 with two — and every one of those 4 pairs findings from
    DIFFERENT classes (a test-record suspicion beside an address mismatch, a
    malformed NPI beside a test-record suspicion). A `review_record` carries ONE
    determination and ONE QA decision, so folding two materially different
    questions into it would force one answer onto both.

    The classification is derived from the RULE that raised the issue, not
    invented: every rule that can produce a HUMAN_REQUIRED finding today is
    listed in `RULE_CLASSIFICATION`, and an unlisted rule refuses rather than
    defaulting into a bucket that would misroute the work.

WHAT THIS REFUSES TO DO
───────────────────────
    * It creates a QUESTION, never an answer: `classification_bucket`,
      `reviewer_resolution` and `reportable_at` are all left NULL, exactly as
      `create_work_item` leaves them. Only a QA APPROVE can set the last one.
    * It never resolves, edits or annotates the issue it cites. The issue
      ledger is the record of what the rules found.
    * It never copies Government values into the case. The case stores
      REFERENCES — intake, source record, issue ids — and the analyst reads the
      delivered values through Area 1, which stays the single source of truth.
    * It never touches historical runs. Only the current run raises new work;
      a superseded run's findings stay history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Integer, func, select, text
from sqlalchemy import cast as sa_cast

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection

#: Stamped on every case this module creates, so DQ work is distinguishable
#: from the Phase-6 exception queue and from ARC classification recommendations.
QUEUE_SOURCE = "RCE_DQ_HUMAN_REQUIRED"

BRIDGE_VERSION = "1.0.0"

#: Correction authorities that require a human. AUTO_SAFE is applied
#: deterministically and NO_CORRECTION is recorded and preserved — neither is a
#: question for an analyst, and queueing them would bury the ones that are.
HUMAN_AUTHORITIES = ("HUMAN_REQUIRED", "QA_REQUIRED")

#: Rule -> case classification. Derived from what each rule actually asks, and
#: deliberately NOT a superset: ENROLMENT, EXCLUSION and SOURCE_DEPENDENCY are
#: real classifications elsewhere in the ARC, but no DQ rule raises them, so
#: listing them here would advertise routing that nothing produces.
RULE_CLASSIFICATION: Dict[str, str] = {
    "FMT-001": "DQ", "FMT-002": "DQ", "FMT-003": "DQ",
    "FMT-004": "DQ", "FMT-005": "DQ", "FMT-006": "DQ",
    "FMT-007": "DQ",
    "REQ-001": "DQ", "REQ-002": "DQ", "REQ-003": "DQ",
    "SCH-001": "DQ", "SCH-002": "DQ",
    "ID-001": "IDENTITY", "ID-002": "IDENTITY", "ID-003": "IDENTITY",
    "ID-004": "IDENTITY", "ID-005": "IDENTITY", "ID-006": "IDENTITY",
    "NPI-001": "IDENTITY", "NPI-002": "IDENTITY", "NPI-003": "IDENTITY",
    "INT-001": "RELATIONSHIP", "INT-002": "RELATIONSHIP",
    "INT-003": "RELATIONSHIP",
    "BUS-001": "METHODOLOGY", "BUS-002": "METHODOLOGY",
    "BUS-003": "METHODOLOGY",
    "CON-001": "METHODOLOGY", "CON-002": "METHODOLOGY",
    "CON-003": "METHODOLOGY", "CON-004": "METHODOLOGY",
    "CON-005": "METHODOLOGY",
}

#: Higher sorts first in the analyst queue. A statement about how soon a human
#: should look, never about the entity.
SEVERITY_PRIORITY = {"CRITICAL": 90, "HIGH": 80, "MEDIUM": 50,
                     "LOW": 30, "INFORMATIONAL": 10}


class BridgeRefused(RuntimeError):
    """Work was not created, and the reason is stated."""


def classification_for(rule_id: str) -> str:
    """The case class a rule's finding belongs to.

    Refuses an unknown rule rather than defaulting. A new rule that nobody has
    classified would otherwise be routed by accident into whichever bucket the
    default happened to be, and an analyst would be handed a question their
    queue was not chosen for.
    """
    try:
        return RULE_CLASSIFICATION[rule_id]
    except KeyError:
        raise BridgeRefused(
            f"rule {rule_id!r} has no case classification. Add it to "
            f"RULE_CLASSIFICATION deliberately — defaulting would misroute the "
            f"work and hide that the decision was never made.")


def case_key(run_id: Any, source_record_id: Any, classification: str) -> str:
    """The idempotency key. Stable, deterministic, and carries its own run.

    The run is part of the key on purpose: a NEW quality run is a new
    assessment and may legitimately raise the same question again, while a
    second bridge pass over the SAME run must find the case it already made.
    """
    return f"{run_id}:{source_record_id}:{classification}"


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
    raise BridgeRefused(
        "Could not allocate a unique review id after 6 attempts; refusing "
        "rather than risking a duplicate id in a delivered report.")


async def _existing_case(db, key: str) -> Optional[reg.ReviewRecord]:
    """The case this key already made, if any. Resolved or not."""
    return (await db.execute(
        select(reg.ReviewRecord)
        .where(reg.ReviewRecord.verification_results["queue_source"].astext
               == QUEUE_SOURCE,
               reg.ReviewRecord.verification_results["case_key"].astext == key)
        .limit(1))).scalars().first()


async def plan_cases(db, intake_id, *, run_id=None) -> Dict[str, Any]:
    """What the bridge WOULD create. Read-only; writes nothing.

    Used both by the operational bridge and, on its own, to forecast a delivery
    before any case exists.
    """
    scope = run_selection.issues_filter(intake_id, run_id=run_id)
    rows = (await db.execute(
        select(m.RceIssue.id, m.RceIssue.issue_code, m.RceIssue.rule_id,
               m.RceIssue.issue_type, m.RceIssue.severity,
               m.RceIssue.source_record_id, m.RceIssue.run_id,
               m.RceCuratedRecord.canonical_entity_id,
               m.RceCuratedRecord.record_status)
        .join(m.RceCuratedRecord,
              m.RceCuratedRecord.source_record_id == m.RceIssue.source_record_id,
              isouter=True)
        .where(scope,
               m.RceIssue.correction_authority.in_(HUMAN_AUTHORITIES),
               m.RceIssue.source_record_id.isnot(None)))).all()

    groups: Dict[str, Dict[str, Any]] = {}
    unmappable: List[Dict[str, Any]] = []
    for (issue_id, code, rule_id, issue_type, severity, source_record_id,
         issue_run_id, entity_id, record_status) in rows:
        if source_record_id is None:
            # No Area 1 anchor and no entity: nothing to review. Reported,
            # never silently dropped.
            unmappable.append({
                "issue_code": code, "rule_id": rule_id,
                "issue_type": issue_type, "severity": severity,
                "record_status": record_status,
                "reason": "the issue names no source record, so a case would "
                          "have no subject at all",
            })
            continue
        classification = classification_for(rule_id)
        key = case_key(issue_run_id, source_record_id, classification)
        group = groups.setdefault(key, {
            "case_key": key, "classification": classification,
            "entity_id": entity_id, "source_record_id": source_record_id,
            "record_status": record_status,
            # A case for a record that was never promoted. The subject is the
            # delivered line, not an entity that does not exist yet.
            "pre_promotion": entity_id is None,
            "run_id": issue_run_id, "issue_ids": [], "issue_codes": [],
            "rule_ids": [], "issue_types": [], "severities": [],
        })
        group["issue_ids"].append(issue_id)
        group["issue_codes"].append(code)
        group["rule_ids"].append(rule_id)
        group["issue_types"].append(issue_type)
        group["severities"].append(severity)

    for group in groups.values():
        group["priority"] = max(
            SEVERITY_PRIORITY.get(s, 50) for s in group["severities"])
        group["severity"] = min(
            group["severities"],
            key=lambda s: -SEVERITY_PRIORITY.get(s, 50))

    planned = sorted(groups.values(), key=lambda g: g["case_key"])
    return {
        "intake_id": str(intake_id),
        "run_id": str(planned[0]["run_id"]) if planned else None,
        "human_required_issues": len(rows),
        "planned_cases": len(planned),
        "single_issue_cases": sum(1 for g in planned if len(g["issue_ids"]) == 1),
        "multi_issue_cases": sum(1 for g in planned if len(g["issue_ids"]) > 1),
        "by_classification": _tally(g["classification"] for g in planned),
        "by_severity": _tally(g["severity"] for g in planned),
        "entity_backed_cases": sum(1 for g in planned if not g["pre_promotion"]),
        "pre_promotion_cases": sum(1 for g in planned if g["pre_promotion"]),
        "unmappable_issues": len(unmappable),
        "unmappable": unmappable,
        "cases": planned,
    }


def _tally(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


async def build_cases(db, intake_id, *, run_id=None,
                      actor: Optional[str] = None) -> Dict[str, Any]:
    """Create the review cases the current run's HUMAN_REQUIRED issues justify.

    IDEMPOTENT. A second pass over the same run finds every case by its
    `case_key` and creates nothing. Concurrency is serialised on a
    transaction-scoped advisory lock keyed by the intake, so two bridge runs
    cannot both find "no existing case" for the same key and both insert —
    a check-then-insert race that no constraint currently prevents, because the
    key lives in JSONB and nothing indexes it uniquely.
    """
    plan = await plan_cases(db, intake_id, run_id=run_id)

    # One writer at a time per delivery. Transaction-scoped: released on commit
    # or rollback, so a crash cannot strand it. Not a sleep, not a retry, not a
    # process-local flag — the database arbitrates.
    await db.execute(
        text("select pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"dq_review_bridge:{intake_id}"})

    created, existing = [], []
    for group in plan["cases"]:
        found = await _existing_case(db, group["case_key"])
        if found is not None:
            existing.append(found.review_id)
            continue

        review_id = await _next_review_id(db)
        record = reg.ReviewRecord(
            id=uuid.uuid4(),
            review_id=review_id,
            # A case is anchored to the delivered line ALWAYS, and to an entity
            # only when one exists. A HELD record is held precisely because it
            # needs human judgement; requiring an entity would have made the
            # records most needing review the only unreviewable ones.
            entity_id=group["entity_id"],
            source_record_id=group["source_record_id"],
            verification_results={
                # A SNAPSHOT of WHY this was queued. References, not copies:
                # the delivered values stay in Area 1 and are read from there.
                "queue_source": QUEUE_SOURCE,
                "bridge_version": BRIDGE_VERSION,
                "case_key": group["case_key"],
                "case_classification": group["classification"],
                "source_intake_id": str(intake_id),
                "source_record_id": str(group["source_record_id"]),
                "pre_promotion": group["pre_promotion"],
                "record_status": group["record_status"],
                "quality_run_id": str(group["run_id"]),
                "issue_ids": [str(i) for i in group["issue_ids"]],
                "issue_codes": sorted(group["issue_codes"]),
                "rule_ids": sorted(set(group["rule_ids"])),
                "issue_types": sorted(set(group["issue_types"])),
                "severity": group["severity"],
                "priority": group["priority"],
                "queued_at": datetime.utcnow().isoformat(),
                "note": ("A data-quality finding requiring human judgement. No "
                         "classification, no determination and no "
                         "reportability is implied by this record existing."),
            },
            # All three stay NULL. Only a human, through the QA gate, may move
            # them — and only a standing QA APPROVE sets reportable_at.
            classification_bucket=None,
            reviewer_resolution=None,
            reportable_at=None,
        )
        db.add(record)
        await db.flush()
        created.append(review_id)

        reg_audit_record(db, record, group, actor)

    return {
        "intake_id": str(intake_id),
        "run_id": plan["run_id"],
        "human_required_issues": plan["human_required_issues"],
        "cases_created": len(created),
        "cases_already_present": len(existing),
        "planned_cases": plan["planned_cases"],
        "entity_backed_cases": plan["entity_backed_cases"],
        "pre_promotion_cases": plan["pre_promotion_cases"],
        "unmappable_issues": plan["unmappable_issues"],
        "by_classification": plan["by_classification"],
        "created_review_ids": created,
    }


def reg_audit_record(db, record, group, actor) -> None:
    """One audit row per case created, naming what justified it."""
    from app.tefca_registry import audit as reg_audit

    reg_audit.record(
        db, "review_case_created", record.entity_id,
        actor_email=actor,
        metadata={"review_id": record.review_id,
                  "queue_source": QUEUE_SOURCE,
                  "case_key": group["case_key"],
                  "case_classification": group["classification"],
                  "issue_codes": sorted(group["issue_codes"]),
                  "quality_run_id": str(group["run_id"])})


async def open_cases(db, intake_id=None, *, limit: int = 100,
                     classification: Optional[str] = None) -> List[Dict[str, Any]]:
    """DQ cases no human has resolved, highest priority first, then oldest.

    Ordered in SQL, not afterwards: sorting a page that LIMIT already chose
    would order the wrong rows and hide the highest-priority items behind
    whichever happened to be oldest.
    """
    priority = sa_cast(
        reg.ReviewRecord.verification_results["priority"].astext, Integer)
    stmt = (select(reg.ReviewRecord)
            .where(reg.ReviewRecord.verification_results["queue_source"].astext
                   == QUEUE_SOURCE,
                   reg.ReviewRecord.reviewer_resolution.is_(None)))
    if intake_id is not None:
        stmt = stmt.where(
            reg.ReviewRecord.verification_results["source_intake_id"].astext
            == str(intake_id))
    if classification:
        stmt = stmt.where(
            reg.ReviewRecord.verification_results["case_classification"].astext
            == classification)
    rows = (await db.execute(
        stmt.order_by(priority.desc().nullslast(),
                      reg.ReviewRecord.created_at.asc())
        .limit(limit))).scalars().all()
    return [_case_dto(r) for r in rows]


def _case_dto(record) -> Dict[str, Any]:
    payload = record.verification_results or {}
    return {
        "review_id": record.review_id,
        # NULL for a pre-promotion case. Never str(None).
        "entity_id": str(record.entity_id) if record.entity_id else None,
        "source_record_id": (str(record.source_record_id)
                             if record.source_record_id else None),
        "case_classification": payload.get("case_classification"),
        "severity": payload.get("severity"),
        "priority": payload.get("priority", 50),
        "issue_codes": payload.get("issue_codes", []),
        "issue_types": payload.get("issue_types", []),
        "rule_ids": payload.get("rule_ids", []),
        "source_record_id": payload.get("source_record_id"),
        "source_intake_id": payload.get("source_intake_id"),
        "quality_run_id": payload.get("quality_run_id"),
        "created_at": record.created_at,
        "reportable": record.reportable_at is not None,
    }


async def workload_summary(db, intake_id=None) -> Dict[str, Any]:
    """Aggregate counts for a supervisor view.

    `operational_age_days` is an INTERNAL OPERATIONAL measure. It is not a
    contractual deadline and must never be presented as one: nothing in the
    contract or COR direction sets a due date for a DQ case.
    """
    stmt = select(reg.ReviewRecord).where(
        reg.ReviewRecord.verification_results["queue_source"].astext
        == QUEUE_SOURCE)
    if intake_id is not None:
        stmt = stmt.where(
            reg.ReviewRecord.verification_results["source_intake_id"].astext
            == str(intake_id))
    records = (await db.execute(stmt)).scalars().all()

    now = datetime.utcnow()
    by_classification: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    ages: List[float] = []
    unresolved = reportable = 0
    for record in records:
        payload = record.verification_results or {}
        key = payload.get("case_classification") or "(none)"
        by_classification[key] = by_classification.get(key, 0) + 1
        sev = payload.get("severity") or "(none)"
        by_severity[sev] = by_severity.get(sev, 0) + 1
        if record.reviewer_resolution is None:
            unresolved += 1
        if record.reportable_at is not None:
            reportable += 1
        if record.created_at:
            ages.append((now - record.created_at).total_seconds() / 86400.0)

    return {
        "total_cases": len(records),
        "unresolved": unresolved,
        "reportable": reportable,
        "by_classification": dict(sorted(by_classification.items(),
                                         key=lambda kv: -kv[1])),
        "by_severity": dict(sorted(by_severity.items(), key=lambda kv: -kv[1])),
        "operational_age_days": {
            "oldest": round(max(ages), 2) if ages else None,
            "median": round(sorted(ages)[len(ages) // 2], 2) if ages else None,
        },
        "note": ("operational_age_days is an internal operational measure, not "
                 "a contractual SLA or deadline."),
    }
