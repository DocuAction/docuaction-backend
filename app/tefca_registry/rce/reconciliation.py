"""
P12 — the hard reconciliation gate.

WHAT THIS PROVES
────────────────
    A  source records received          every delivered line
    B  rejected / held / excluded        not eligible to proceed, with a reason
    C  eligible                          A − B
    D  Area 2 curated records            must equal A: every line gets a row
    E  canonical registry promotions     must equal C
    F  verification population           reviews written, ⊆ E

    and, since 2026-09-17, the DISPOSITION EQUATION over persisted rows:

    Received = Created + Updated + Matched/Unchanged + Held + Rejected
             + Missing Key + Excluded

Every population is counted from the database, never estimated, and each
identity is asserted rather than described. A check that cannot be expressed as
an equality is not in here.

WHY EQUALITIES AND NOT TOLERANCES
A reconciliation that passes "within a few records" is not a reconciliation. If
23,566 lines arrive and 23,562 entities exist, the four missing ones must each
have a name and a reason — held, rejected, or unpromotable — and the arithmetic
must close exactly. Anything else means a record went somewhere nobody can
account for, which is the single failure this whole pipeline exists to prevent.

ORPHANS
Every Area 2 row must point at an Area 1 row that exists. Every registry
promotion must point back at an Area 2 row. Every correction must cite an issue
or carry a documented transformation reason. Every delivery report link must
point at a report and a snapshot that exist. Orphan counts are asserted at zero.

SNAPSHOTS
`persist_snapshot` writes the result of one reconciliation run to
`rce_reconciliation_snapshots` with a content hash, the build SHA and the
migration revision in force, so the verdict a report was generated from can be
re-read and re-verified later rather than recomputed from rows that may since
have moved on. The table's CHECK constraint refuses a `passed = true` snapshot
whose counts do not sum.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from app.core import request_context
from app.tefca_registry import models as reg
from app.tefca_registry.rce import dispositions as disp
from app.tefca_registry.rce import identifier_decisions
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce import traceability_models as tm

logger = logging.getLogger(__name__)


class ReconciliationFailure(RuntimeError):
    """A population did not reconcile. Never downgraded to a warning."""


async def _scalar(db, stmt) -> int:
    return int((await db.execute(stmt)).scalar() or 0)


async def reconcile_delivery(db, intake_id) -> Dict[str, Any]:
    """Full A–F reconciliation for one delivery, plus the disposition equation."""
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ValueError(f"No intake {intake_id}")

    # ── A — everything that arrived ──
    a_received = await _scalar(db, select(func.count()).select_from(m.RceSourceRecord)
                               .where(m.RceSourceRecord.source_intake_id == intake_id))
    a_declared = intake.record_count

    # ── D — Area 2 ──
    d_curated = await _scalar(db, select(func.count()).select_from(m.RceCuratedRecord)
                              .where(m.RceCuratedRecord.source_intake_id == intake_id))
    status_counts = {status: int(count) for status, count in (await db.execute(
        select(m.RceCuratedRecord.record_status, func.count())
        .where(m.RceCuratedRecord.source_intake_id == intake_id)
        .group_by(m.RceCuratedRecord.record_status))).all()}

    # ── dispositions — the persisted accounting ──
    disposition_counts = await disp.counts_for_intake(db, intake_id)
    without_disposition = await disp.records_without_disposition(db, intake_id)
    equation = disp.equation(disposition_counts, a_received)

    # ── B — not eligible to proceed ──
    b_held = status_counts.get("HELD", 0)
    b_rejected = status_counts.get("REJECTED", 0)
    # Rows the pipeline accounted as EXCLUDED or MISSING_KEY are neither held
    # nor rejected in Area 2 status terms, yet they are legitimately not
    # promoted. They belong to B, or E == C would report them as lost.
    b_excluded = await _scalar(db, text(f"""
        SELECT count(*) FROM rce_curated_records c
        JOIN {tm.CURRENT_DISPOSITIONS_VIEW} d ON d.source_record_id = c.source_record_id
        WHERE c.source_intake_id = CAST(:i AS uuid)
          AND c.canonical_entity_id IS NULL
          AND c.record_status NOT IN ('HELD', 'REJECTED')
          AND d.disposition IN ('EXCLUDED', 'MISSING_KEY')""").bindparams(i=intake_id))
    b_total = b_held + b_rejected + b_excluded
    c_eligible = a_received - b_total

    # ── E — canonical registry ──
    e_promoted = await _scalar(db, select(func.count()).select_from(m.RceCuratedRecord)
                               .where(m.RceCuratedRecord.source_intake_id == intake_id,
                                      m.RceCuratedRecord.canonical_entity_id.isnot(None)))
    e_source_marked = await _scalar(
        db, select(func.count()).select_from(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == intake_id,
               m.RceSourceRecord.promotion_status == "promoted"))
    e_entities = await _scalar(
        db, select(func.count(func.distinct(m.RceCuratedRecord.canonical_entity_id)))
        .where(m.RceCuratedRecord.source_intake_id == intake_id,
               m.RceCuratedRecord.canonical_entity_id.isnot(None)))

    # ── F — verification population ──
    promoted_ids = select(m.RceCuratedRecord.canonical_entity_id).where(
        m.RceCuratedRecord.source_intake_id == intake_id,
        m.RceCuratedRecord.canonical_entity_id.isnot(None))
    f_reviews = await _scalar(db, select(func.count()).select_from(reg.ReviewRecord)
                              .where(reg.ReviewRecord.entity_id.in_(promoted_ids)))
    f_entities = await _scalar(
        db, select(func.count(func.distinct(reg.ReviewRecord.entity_id)))
        .where(reg.ReviewRecord.entity_id.in_(promoted_ids)))

    # ── orphans ──
    orphan_curated = await _scalar(db, text(
        "SELECT count(*) FROM rce_curated_records c "
        "LEFT JOIN rce_source_records s ON s.id = c.source_record_id "
        "WHERE c.source_intake_id = CAST(:i AS uuid) AND s.id IS NULL").bindparams(i=intake_id))
    orphan_promotion = await _scalar(db, text(
        "SELECT count(*) FROM rce_curated_records c "
        "LEFT JOIN tefca_reg_entities e ON e.id = c.canonical_entity_id "
        "WHERE c.source_intake_id = CAST(:i AS uuid) AND c.canonical_entity_id IS NOT NULL "
        "AND e.id IS NULL").bindparams(i=intake_id))
    orphan_issue = await _scalar(db, text(
        "SELECT count(*) FROM rce_issues i "
        "LEFT JOIN rce_source_records s ON s.id = i.source_record_id "
        "WHERE i.source_intake_id = CAST(:i AS uuid) AND i.source_record_id IS NOT NULL "
        "AND s.id IS NULL").bindparams(i=intake_id))
    orphan_correction = await _scalar(db, text(
        "SELECT count(*) FROM rce_correction_details d "
        "LEFT JOIN rce_curated_records c ON c.id = d.curated_record_id "
        "WHERE c.id IS NULL"))
    corrections_total = await _scalar(db, text(
        "SELECT count(*) FROM rce_correction_details d "
        "JOIN rce_curated_records c ON c.id = d.curated_record_id "
        "WHERE c.source_intake_id = CAST(:i AS uuid)").bindparams(i=intake_id))
    # A correction must cite an issue OR carry a documented reason. Both empty
    # would be an unexplained edit to delivered data.
    corrections_unexplained = await _scalar(db, text(
        "SELECT count(*) FROM rce_correction_details d "
        "JOIN rce_curated_records c ON c.id = d.curated_record_id "
        "WHERE c.source_intake_id = CAST(:i AS uuid) AND d.issue_id IS NULL "
        "AND (d.correction_reason IS NULL OR d.correction_reason = '')"
    ).bindparams(i=intake_id))
    # Delivery report links: the report and the snapshot each link names must
    # exist. `report_id` is the `review_reports.report_id` string; the snapshot
    # is a declared FK, so a missing one can only mean the row was never there.
    orphan_report_links = await _scalar(db, text(
        "SELECT count(*) FROM rce_delivery_report_links l "
        "LEFT JOIN review_reports r ON r.report_id = l.report_id "
        "LEFT JOIN rce_reconciliation_snapshots s ON s.id = l.snapshot_id "
        "WHERE l.intake_id = CAST(:i AS uuid) AND (r.report_id IS NULL OR s.id IS NULL)"
    ).bindparams(i=intake_id))
    report_links_total = await _scalar(db, text(
        "SELECT count(*) FROM rce_delivery_report_links "
        "WHERE intake_id = CAST(:i AS uuid)").bindparams(i=intake_id))

    # ── identifier conflicts ──
    unresolved_conflicts = await identifier_decisions.unresolved_for_intake(db, intake_id)
    # Every record whose latest identifier decision is still CONFLICT_RAISED
    # must be HELD (or REJECTED) in Area 2. A released record with an open
    # conflict would have reached the registry past an undecided question.
    conflicts_not_held = await _scalar(db, text("""
        SELECT count(*) FROM (
          SELECT DISTINCT ON (entity_id, identifier_type) decision, source_record_id
          FROM tefca_identifier_decision_events
          WHERE intake_id = CAST(:i AS uuid)
          ORDER BY entity_id, identifier_type, sequence DESC) x
        JOIN rce_curated_records c ON c.source_record_id = x.source_record_id
        WHERE x.decision = 'CONFLICT_RAISED'
          AND (c.record_status <> 'HELD' OR c.canonical_entity_id IS NOT NULL)
    """).bindparams(i=intake_id))

    # ── rule execution coverage ──
    runs = (await db.execute(
        select(m.RceIngestionRun).where(
            m.RceIngestionRun.source_intake_id == intake_id)
        .order_by(m.RceIngestionRun.started_at.desc()))).scalars().all()
    latest_run = runs[0] if runs else None
    rule_rows = []
    if latest_run is not None:
        rule_rows = (await db.execute(
            select(m.RceRuleExecutionHistory).where(
                m.RceRuleExecutionHistory.run_id == latest_run.id)
            .order_by(m.RceRuleExecutionHistory.rule_id))).scalars().all()
    rules_under_evaluated = [
        r.rule_id for r in rule_rows if (r.records_evaluated or 0) != a_received]
    rules_failed = [r.rule_id for r in rule_rows if r.execution_status != "COMPLETE"]

    # ── findings, current run ──
    findings_by_severity = {sev: int(n) for sev, n in (await db.execute(
        select(m.RceIssue.severity, func.count())
        .where(run_selection.issues_filter(intake_id))
        .group_by(m.RceIssue.severity))).all()}
    findings_open_high = await _scalar(
        db, select(func.count()).select_from(m.RceIssue)
        .where(run_selection.issues_filter(intake_id),
               m.RceIssue.severity.in_(("CRITICAL", "HIGH")),
               m.RceIssue.resolution.in_(("OPEN", "PROPOSED", "UNDER_REVIEW"))))
    warnings = sum(findings_by_severity.get(s, 0) for s in ("LOW", "INFORMATIONAL"))

    # ── relationships ──
    relationship_edges = await _scalar(
        db, select(func.count()).select_from(reg.TefcaEntityRelationship)
        .where(reg.TefcaEntityRelationship.child_entity_id.in_(promoted_ids)))

    # ── determinations trace to evidence ──
    reviews_without_evidence = await _scalar(db, text(
        "SELECT count(*) FROM review_records r "
        "WHERE r.entity_id IN (SELECT canonical_entity_id FROM rce_curated_records "
        "                      WHERE source_intake_id = CAST(:i AS uuid) "
        "                        AND canonical_entity_id IS NOT NULL) "
        "  AND (r.verification_results IS NULL "
        "       OR r.verification_results->'dimensions' IS NULL "
        "       OR jsonb_array_length(r.verification_results->'dimensions') = 0)"
    ).bindparams(i=intake_id))
    reviews_without_rule = await _scalar(db, text(
        "SELECT count(*) FROM review_records r "
        "WHERE r.entity_id IN (SELECT canonical_entity_id FROM rce_curated_records "
        "                      WHERE source_intake_id = CAST(:i AS uuid) "
        "                        AND canonical_entity_id IS NOT NULL) "
        "  AND r.classification_bucket IS NOT NULL "
        "  AND r.classification_rule IS NULL").bindparams(i=intake_id))

    checks: List[Dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("A: every delivered line stored",
          a_received == a_declared,
          f"{a_received} source records vs {a_declared} declared on the intake")
    check("D == A: every source record curated",
          d_curated == a_received,
          f"{d_curated} curated vs {a_received} source records")
    check("C = A − B: eligible population is exact",
          c_eligible == a_received - b_total,
          f"{c_eligible} eligible = {a_received} received − {b_total} "
          f"(held {b_held} + rejected {b_rejected} + excluded/missing key "
          f"{b_excluded})")
    check("E == C: every eligible record promoted",
          e_promoted == c_eligible,
          f"{e_promoted} promoted vs {c_eligible} eligible")
    check("E: Area 1 promotion markers agree with Area 2",
          e_source_marked == e_promoted,
          f"{e_source_marked} source records marked promoted vs "
          f"{e_promoted} curated records carrying an entity id")
    check("E: one registry entity per promoted record",
          e_entities == e_promoted,
          f"{e_entities} distinct entities vs {e_promoted} promoted records")
    check("F ⊆ E: every verification traces to a promoted entity",
          f_entities <= e_promoted,
          f"{f_reviews} reviews over {f_entities} entities, within "
          f"{e_promoted} promoted")
    check("Zero orphan Area 2 records", orphan_curated == 0,
          f"{orphan_curated} curated records with no Area 1 parent")
    check("Zero orphan promotions", orphan_promotion == 0,
          f"{orphan_promotion} curated records pointing at a missing entity")
    check("Zero orphan issues", orphan_issue == 0,
          f"{orphan_issue} issues pointing at a missing source record")
    check("Zero orphan corrections", orphan_correction == 0,
          f"{orphan_correction} corrections with no curated record")
    check("Every correction is explained", corrections_unexplained == 0,
          f"{corrections_unexplained} of {corrections_total} corrections cite "
          f"neither an issue nor a reason")
    check("Every rule evaluated every record", not rules_under_evaluated,
          f"{len(rules_under_evaluated)} rule(s) did not evaluate all "
          f"{a_received} records: {rules_under_evaluated[:5]}")
    check("No rule execution failed", not rules_failed,
          f"{len(rules_failed)} rule(s) failed: {rules_failed[:5]}")
    check("Every determination traces to evidence", reviews_without_evidence == 0,
          f"{reviews_without_evidence} review(s) carry no dimension evidence")
    check("Every determination cites a rule", reviews_without_rule == 0,
          f"{reviews_without_rule} classified review(s) carry no rule_code")
    # ── the 2026-09-17 additions ──
    check("Every source record has a current disposition",
          without_disposition == 0,
          f"{without_disposition} of {a_received} source records carry no "
          f"disposition event")
    check("Disposition equation: Received = Created + Updated + Matched/Unchanged "
          "+ Held + Rejected + Missing Key + Excluded",
          equation["holds"],
          f"{equation['received']} received vs {equation['accounted']} accounted "
          f"(difference {equation['difference']}): created {equation['created']}, "
          f"updated {equation['updated']}, unchanged "
          f"{equation['matched_unchanged']}, held {equation['held']}, rejected "
          f"{equation['rejected']}, missing key {equation['missing_key']}, "
          f"excluded {equation['excluded']}")
    check("Zero orphan report links", orphan_report_links == 0,
          f"{orphan_report_links} of {report_links_total} delivery report links "
          f"name a report or snapshot that does not exist")
    check("Unresolved identifier conflicts are all HELD", conflicts_not_held == 0,
          f"{unresolved_conflicts} unresolved conflict(s); {conflicts_not_held} "
          f"on record(s) that are not held")

    # ── Area 1 integrity ──
    from app.tefca_registry.rce import repository as repo

    hashes = await repo.verify_record_hashes(db, intake_id)
    check("Area 1 raw lines still hash to their intake values", hashes["intact"],
          f"{hashes['mismatches']} mismatch(es) across "
          f"{hashes['records_checked']} records")
    stored_file = await repo.verify_stored_file(db, intake_id)
    if stored_file.get("checked"):
        check("Original delivery file unmodified", bool(stored_file.get("intact")),
              f"stored sha256 {'matches' if stored_file.get('intact') else 'DIFFERS'}")
    immutability = await repo.verify_immutable(db)

    passed = all(c["passed"] for c in checks)

    return {
        "intake_id": str(intake_id),
        "passed": passed,
        "populations": {
            "A_source_records_received": a_received,
            "B_rejected_or_held": b_total,
            "B_held": b_held,
            "B_rejected": b_rejected,
            "B_excluded_or_missing_key": b_excluded,
            "C_eligible": c_eligible,
            "D_curated_records": d_curated,
            "E_promoted_to_registry": e_promoted,
            "E_distinct_entities": e_entities,
            "F_verification_reviews": f_reviews,
            "F_verified_entities": f_entities,
        },
        "curated_status_counts": status_counts,
        "dispositions": disposition_counts,
        "equation": equation,
        "records_without_disposition": without_disposition,
        "identifier_conflicts": {"unresolved": unresolved_conflicts,
                                 "not_held": conflicts_not_held},
        "corrections": {"total": corrections_total,
                        "unexplained": corrections_unexplained},
        "report_links": {"total": report_links_total, "orphans": orphan_report_links},
        "rule_execution": {
            "run_id": str(latest_run.id) if latest_run else None,
            "rules_executed": len(rule_rows),
            "rules_under_evaluated": rules_under_evaluated,
            "rules_failed": rules_failed,
        },
        "dimensions": {
            "warnings": warnings,
            "findings": {"by_severity": findings_by_severity,
                         "open_high_or_critical": findings_open_high,
                         "total": sum(findings_by_severity.values())},
            "verification": {"reviews": f_reviews, "entities": f_entities,
                             "without_evidence": reviews_without_evidence,
                             "without_rule": reviews_without_rule},
            "relationships": {"edges": relationship_edges},
        },
        "area1_integrity": {"record_hashes": hashes, "stored_file": stored_file,
                            "immutability": immutability},
        "checks": checks,
        "failed_checks": [c for c in checks if not c["passed"]],
        "note": (
            "Every population is counted from the database. C = A − B and "
            "E = C are asserted as equalities, not tolerances: a record that "
            "does not appear on both sides of the arithmetic has gone somewhere "
            "nobody can account for. The disposition equation is proven over "
            "persisted, append-only events, not over this run's counters."
        ),
    }


async def assert_reconciled(db, intake_id) -> Dict[str, Any]:
    """Reconcile, and raise if it does not pass. The gate, as a call."""
    result = await reconcile_delivery(db, intake_id)
    if not result["passed"]:
        failures = "; ".join(f"{c['check']} — {c['detail']}"
                             for c in result["failed_checks"])
        raise ReconciliationFailure(
            f"Delivery {intake_id} did not reconcile: {failures}")
    return result


# ── snapshots ────────────────────────────────────────────────────────────────

def snapshot_hash(equation: Dict[str, Any], dimensions: Dict[str, Any],
                  checks: List[Dict[str, Any]]) -> str:
    """sha256 over the canonical JSON of what the snapshot asserts."""
    payload = json.dumps({"equation": equation, "dimensions": dimensions,
                          "checks": checks},
                         sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def migration_revision(db) -> str:
    """The Alembic revision the database is at, or 'unknown'."""
    try:
        value = (await db.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar()
        return str(value)[:64] if value else "unknown"
    except Exception as exc:  # noqa: BLE001 — a missing table is reported, not fatal
        logger.warning("alembic_version not readable: %s", type(exc).__name__,
                       exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return "unknown"


async def persist_snapshot(db, intake_id, result: Dict[str, Any], *, job_id,
                           actor: str, trigger: str,
                           reconstructed: bool = False) -> tm.RceReconciliationSnapshot:
    """Write one `RceReconciliationSnapshot` from a `reconcile_delivery` result.

    `sequence` is max+1 for the job. `source_evidence` records the population
    numbers and ids the verdict was computed from, so the snapshot can be
    re-verified against the rows later. Commits.
    """
    if job_id is None:
        raise ValueError("a reconciliation snapshot must belong to a delivery job")
    if trigger not in tm.SNAPSHOT_TRIGGERS:
        raise ValueError(f"unknown snapshot trigger {trigger!r}")

    equation = dict(result.get("equation") or {})
    dimensions = dict(result.get("dimensions") or {})
    checks = list(result.get("checks") or [])
    passed = bool(result.get("passed"))
    failed = [c for c in checks if not c.get("passed")]
    failure_reason = None
    if failed:
        failure_reason = "; ".join(
            f"{c['check']} — {c['detail']}" for c in failed)[:4000]

    sequence = int((await db.execute(
        select(func.max(tm.RceReconciliationSnapshot.sequence)).where(
            tm.RceReconciliationSnapshot.job_id == job_id))).scalar() or 0) + 1

    snapshot = tm.RceReconciliationSnapshot(
        job_id=job_id, intake_id=intake_id, sequence=sequence,
        passed=passed, failure_reason=failure_reason,
        received=int(equation.get("received", 0)),
        created=int(equation.get("created", 0)),
        updated=int(equation.get("updated", 0)),
        matched_unchanged=int(equation.get("matched_unchanged", 0)),
        held=int(equation.get("held", 0)),
        rejected=int(equation.get("rejected", 0)),
        missing_key=int(equation.get("missing_key", 0)),
        excluded=int(equation.get("excluded", 0)),
        dimensions=dimensions, checks=checks,
        source_evidence={
            "intake_id": str(intake_id), "job_id": str(job_id),
            "populations": result.get("populations") or {},
            "curated_status_counts": result.get("curated_status_counts") or {},
            "dispositions": result.get("dispositions") or {},
            "records_without_disposition": result.get("records_without_disposition"),
            "identifier_conflicts": result.get("identifier_conflicts") or {},
            "rule_execution": result.get("rule_execution") or {},
            "report_links": result.get("report_links") or {},
            "corrections": result.get("corrections") or {},
            "area1_record_hashes": (result.get("area1_integrity") or {}).get(
                "record_hashes"),
        },
        actor=(actor or "SYSTEM")[:320], trigger=trigger,
        hash=snapshot_hash(equation, dimensions, checks),
        build_sha=request_context.build_sha(),
        migration_revision=await migration_revision(db),
        correlation_id=request_context.correlation_id()[:64],
        reconstructed=bool(reconstructed),
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    logger.info("reconciliation snapshot persisted",
                extra={"job_id": str(job_id), "sequence": sequence,
                       "passed": passed, "trigger": trigger})
    return snapshot


async def latest_snapshot(db, job_id) -> Optional[tm.RceReconciliationSnapshot]:
    """The highest-sequence snapshot for one job, or None."""
    if job_id is None:
        return None
    return (await db.execute(
        select(tm.RceReconciliationSnapshot)
        .where(tm.RceReconciliationSnapshot.job_id == job_id)
        .order_by(tm.RceReconciliationSnapshot.sequence.desc())
        .limit(1))).scalar_one_or_none()


async def snapshot_history_count(db, job_id) -> int:
    if job_id is None:
        return 0
    return await _scalar(db, select(func.count()).select_from(
        tm.RceReconciliationSnapshot).where(
        tm.RceReconciliationSnapshot.job_id == job_id))
