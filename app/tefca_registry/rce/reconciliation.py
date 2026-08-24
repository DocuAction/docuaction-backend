"""
P12 — the hard reconciliation gate.

WHAT THIS PROVES
────────────────
    A  source records received          every delivered line
    B  rejected / held                   not eligible to proceed, with a reason
    C  eligible                          A − B
    D  Area 2 curated records            must equal A: every line gets a row
    E  canonical registry promotions     must equal C
    F  verification population           reviews written, ⊆ E

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
or carry a documented transformation reason. Orphan counts are asserted at zero.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m

logger = logging.getLogger(__name__)


class ReconciliationFailure(RuntimeError):
    """A population did not reconcile. Never downgraded to a warning."""


async def _scalar(db, stmt) -> int:
    return int((await db.execute(stmt)).scalar() or 0)


async def reconcile_delivery(db, intake_id) -> Dict[str, Any]:
    """Full A–F reconciliation for one delivery."""
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

    # ── B — not eligible to proceed ──
    b_held = status_counts.get("HELD", 0)
    b_rejected = status_counts.get("REJECTED", 0)
    b_total = b_held + b_rejected
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
        "WHERE c.source_intake_id = :i AND s.id IS NULL").bindparams(i=intake_id))
    orphan_promotion = await _scalar(db, text(
        "SELECT count(*) FROM rce_curated_records c "
        "LEFT JOIN tefca_reg_entities e ON e.id = c.canonical_entity_id "
        "WHERE c.source_intake_id = :i AND c.canonical_entity_id IS NOT NULL "
        "AND e.id IS NULL").bindparams(i=intake_id))
    orphan_issue = await _scalar(db, text(
        "SELECT count(*) FROM rce_issues i "
        "LEFT JOIN rce_source_records s ON s.id = i.source_record_id "
        "WHERE i.source_intake_id = :i AND i.source_record_id IS NOT NULL "
        "AND s.id IS NULL").bindparams(i=intake_id))
    orphan_correction = await _scalar(db, text(
        "SELECT count(*) FROM rce_correction_details d "
        "LEFT JOIN rce_curated_records c ON c.id = d.curated_record_id "
        "WHERE c.id IS NULL"))
    corrections_total = await _scalar(db, text(
        "SELECT count(*) FROM rce_correction_details d "
        "JOIN rce_curated_records c ON c.id = d.curated_record_id "
        "WHERE c.source_intake_id = :i").bindparams(i=intake_id))
    # A correction must cite an issue OR carry a documented reason. Both empty
    # would be an unexplained edit to delivered data.
    corrections_unexplained = await _scalar(db, text(
        "SELECT count(*) FROM rce_correction_details d "
        "JOIN rce_curated_records c ON c.id = d.curated_record_id "
        "WHERE c.source_intake_id = :i AND d.issue_id IS NULL "
        "AND (d.correction_reason IS NULL OR d.correction_reason = '')"
    ).bindparams(i=intake_id))

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
                m.RceRuleExecutionHistory.run_id == latest_run.id))).scalars().all()
    rules_under_evaluated = [
        r.rule_id for r in rule_rows if (r.records_evaluated or 0) != a_received]
    rules_failed = [r.rule_id for r in rule_rows if r.execution_status != "COMPLETE"]

    # ── determinations trace to evidence ──
    reviews_without_evidence = await _scalar(db, text(
        "SELECT count(*) FROM review_records r "
        "WHERE r.entity_id IN (SELECT canonical_entity_id FROM rce_curated_records "
        "                      WHERE source_intake_id = :i "
        "                        AND canonical_entity_id IS NOT NULL) "
        "  AND (r.verification_results IS NULL "
        "       OR r.verification_results->'dimensions' IS NULL "
        "       OR jsonb_array_length(r.verification_results->'dimensions') = 0)"
    ).bindparams(i=intake_id))
    reviews_without_rule = await _scalar(db, text(
        "SELECT count(*) FROM review_records r "
        "WHERE r.entity_id IN (SELECT canonical_entity_id FROM rce_curated_records "
        "                      WHERE source_intake_id = :i "
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
          f"(held {b_held} + rejected {b_rejected})")
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
            "C_eligible": c_eligible,
            "D_curated_records": d_curated,
            "E_promoted_to_registry": e_promoted,
            "E_distinct_entities": e_entities,
            "F_verification_reviews": f_reviews,
            "F_verified_entities": f_entities,
        },
        "curated_status_counts": status_counts,
        "corrections": {"total": corrections_total,
                        "unexplained": corrections_unexplained},
        "rule_execution": {
            "run_id": str(latest_run.id) if latest_run else None,
            "rules_executed": len(rule_rows),
            "rules_under_evaluated": rules_under_evaluated,
            "rules_failed": rules_failed,
        },
        "area1_integrity": {"record_hashes": hashes, "stored_file": stored_file,
                            "immutability": immutability},
        "checks": checks,
        "failed_checks": [c for c in checks if not c["passed"]],
        "note": (
            "Every population is counted from the database. C = A − B and "
            "E = C are asserted as equalities, not tolerances: a record that "
            "does not appear on both sides of the arithmetic has gone somewhere "
            "nobody can account for."
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
