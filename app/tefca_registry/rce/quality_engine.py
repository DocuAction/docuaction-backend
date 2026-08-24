"""
P3 + P4 + P5 — run the rule set over a delivery and write the Issue Ledger.

ONE PASS, EVERY RECORD, EVERY RULE
──────────────────────────────────
`records_evaluated` is recorded per rule, not per run, so the question "did all
records get evaluated?" has a per-rule answer. A rule that found nothing and a
rule that never executed produce identical issue counts — zero — and only the
execution history distinguishes them.

CROSS-RECORD FACTS ARE COMPUTED ONCE
Duplicate identifier maps and the set of known source ids are built in a single
pass before rule evaluation and handed to every rule through `RecordContext
.dataset`. Deriving them per record would be 23,566 × 23,566 comparisons; doing
it once is a single pass.

DETERMINISM
Same delivery + same rule config = same issues, in the same order, with the same
issue codes. Issue codes are assigned by (line number, rule id) ordering rather
than by insertion timing, so a re-run is diffable against the previous one.
"""

from __future__ import annotations

import collections
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.tefca_registry.rce import models as m
from app.tefca_registry.rce.field_map import (
    FIELD_MAP_VERSION,
    OBSERVED_QHIN_OIDS,
    RCE_FIELDS,
    empty_in_delivery,
)
from app.tefca_registry.rce.quality_rules import (
    RULE_SET_VERSION,
    RULES,
    Finding,
    RecordContext,
    rule_config_hash,
)

logger = logging.getLogger(__name__)

#: Records pulled from the database at a time. Sized so a 23,566-record delivery
#: never materialises entirely in memory as ORM objects.
BATCH_SIZE = 2000

#: Issues inserted per statement.
ISSUE_INSERT_BATCH = 2000


def issue_code(sequence: int, when: Optional[datetime] = None) -> str:
    """DQ-YYYYMMDD-NNNNNN."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"DQ-{stamp}-{sequence:06d}"


async def _build_dataset_context(db, intake_id) -> Dict[str, Any]:
    """Cross-record facts, computed in one pass over the delivery."""
    rows = (await db.execute(
        select(m.RceSourceRecord.source_rce_id,
               m.RceSourceRecord.tefcaid,
               m.RceSourceRecord.hcid,
               m.RceSourceRecord.npi)
        .where(m.RceSourceRecord.source_intake_id == intake_id))).all()

    known_ids = set()
    tefcaid_counts: collections.Counter = collections.Counter()
    hcid_counts: collections.Counter = collections.Counter()
    npi_counts: collections.Counter = collections.Counter()
    for source_id, tefcaid, hcid, npi in rows:
        if source_id:
            known_ids.add(source_id)
        if tefcaid:
            tefcaid_counts[tefcaid] += 1
        if hcid:
            hcid_counts[hcid] += 1
        if npi:
            npi_counts[npi] += 1

    return {
        "expected_field_count": len(RCE_FIELDS),
        "known_source_ids": known_ids,
        "qhin_oids": set(OBSERVED_QHIN_OIDS),
        "tefcaid_duplicates": {v: n for v, n in tefcaid_counts.items() if n > 1},
        "hcid_duplicates": {v: n for v, n in hcid_counts.items() if n > 1},
        "npi_duplicates": {v: n for v, n in npi_counts.items() if n > 1},
    }


def _dataset_level_findings(intake) -> List[Finding]:
    """Findings about the DELIVERY rather than about any one record.

    Empty columns are reported once here. Reporting them per record would write
    23,566 identical issues for each of the six columns the delivery leaves
    blank — 141,396 rows that say one thing — and would bury every finding that
    is actually about a record.
    """
    findings: List[Finding] = []
    headers = list(intake.headers or [])
    always_empty = empty_in_delivery()
    delivered_empty = [c for c in always_empty if c in headers]
    if delivered_empty:
        findings.append(Finding(
            "SCH-002", "COLUMN_EMPTY_IN_DELIVERY", "INFORMATIONAL",
            f"{len(delivered_empty)} columns are present in the header but "
            f"empty on every record: {', '.join(delivered_empty)}. Structurally "
            f"delivered, semantically absent. Recorded once for the delivery "
            f"rather than once per record.",
            "NO_CORRECTION", field_name=", ".join(delivered_empty)))
    metadata = intake.source_metadata or {}
    if metadata.get("schema_drift"):
        findings.append(Finding(
            "SCH-003", "SCHEMA_DRIFT", "CRITICAL",
            "The delivered header does not match the locked 41-field map. "
            "Records are preserved, but promotion is held: parsing an unknown "
            "schema against a stale map would mis-assign values.",
            "QA_REQUIRED", field_name="__header__"))
    if metadata.get("mojibake_cells"):
        findings.append(Finding(
            "SCH-004", "ENCODING_ANOMALY", "MEDIUM",
            f"{metadata['mojibake_cells']} cells carry UTF-8-through-CP1252 "
            f"corruption markers. Values are preserved exactly as delivered; "
            f"nothing is re-decoded, because a guessed re-decode would put a "
            f"value in the record that the RCE never sent.",
            "HUMAN_REQUIRED", field_name="__encoding__"))
    return findings


async def run_quality_engine(
    db,
    intake_id,
    *,
    executed_by: str = "SYSTEM",
) -> Dict[str, Any]:
    """Evaluate every rule against every record of a delivery.

    Returns the run summary. Issues are written to `rce_issues`; per-rule
    execution is written to `rce_rule_execution_history`.
    """
    intake = await db.get(m.RceSourceIntake, intake_id)
    if intake is None:
        raise ValueError(f"No intake {intake_id}")

    config_hash = rule_config_hash()
    run = m.RceIngestionRun(
        source_intake_id=intake.id,
        rule_set_version=RULE_SET_VERSION,
        rule_config_hash=config_hash,
        field_map_version=FIELD_MAP_VERSION,
        started_at=datetime.utcnow(),
        run_status="RUNNING",
        executed_by=executed_by,
    )
    db.add(run)
    await db.flush()

    dataset = await _build_dataset_context(db, intake.id)

    per_rule_evaluated: Dict[str, int] = {r.rule_id: 0 for r in RULES}
    per_rule_issues: Dict[str, int] = {r.rule_id: 0 for r in RULES}
    per_rule_ms: Dict[str, float] = {r.rule_id: 0.0 for r in RULES}
    per_rule_error: Dict[str, Optional[str]] = {r.rule_id: None for r in RULES}

    total_records = 0
    pending: List[Dict[str, Any]] = []
    sequence = 0
    now = datetime.utcnow()
    stamp = datetime.now(timezone.utc)

    total = int((await db.execute(
        select(func.count()).select_from(m.RceSourceRecord)
        .where(m.RceSourceRecord.source_intake_id == intake.id))).scalar() or 0)

    # ── dataset-level findings first, so they carry the lowest issue codes ──
    for finding in _dataset_level_findings(intake):
        sequence += 1
        pending.append(_issue_row(finding, intake.id, None, run.id, sequence,
                                  stamp, now))
        per_rule_issues[finding.rule_id] = per_rule_issues.get(finding.rule_id, 0) + 1

    # ── per-record evaluation, in line order for determinism ──
    for offset in range(0, total, BATCH_SIZE):
        records = (await db.execute(
            select(m.RceSourceRecord)
            .where(m.RceSourceRecord.source_intake_id == intake.id)
            .order_by(m.RceSourceRecord.line_number)
            .limit(BATCH_SIZE).offset(offset))).scalars().all()

        for record in records:
            total_records += 1
            ctx = RecordContext(
                line_number=record.line_number,
                parse_status=record.parse_status,
                field_count=record.field_count,
                values=dict(record.parsed or {}),
                dataset=dataset,
            )
            for rule in RULES:
                started = time.perf_counter()
                try:
                    findings = rule.evaluate(ctx) or []
                except Exception as exc:  # noqa: BLE001
                    # One rule failing must not stop the run or lose the other
                    # rules' findings for this record. The failure is recorded
                    # against that rule so it cannot pass as "found nothing".
                    per_rule_error[rule.rule_id] = f"{type(exc).__name__}: {exc}"
                    logger.warning("rule %s raised on line %s: %s",
                                   rule.rule_id, record.line_number, exc)
                    findings = []
                per_rule_ms[rule.rule_id] += (time.perf_counter() - started) * 1000
                per_rule_evaluated[rule.rule_id] += 1
                for finding in findings:
                    sequence += 1
                    severity = rule.severity() if finding.severity is None else finding.severity
                    finding.severity = severity
                    pending.append(_issue_row(finding, intake.id, record.id,
                                              run.id, sequence, stamp, now))
                    per_rule_issues[rule.rule_id] += 1

        if len(pending) >= ISSUE_INSERT_BATCH:
            await _flush_issues(db, pending)
            pending = []

    if pending:
        await _flush_issues(db, pending)

    # ── per-rule execution history ──
    for rule in RULES:
        db.add(m.RceRuleExecutionHistory(
            run_id=run.id,
            rule_id=rule.rule_id,
            rule_version=rule.version,
            rule_category=rule.category,
            records_evaluated=per_rule_evaluated[rule.rule_id],
            issues_generated=per_rule_issues[rule.rule_id],
            execution_status="FAILED" if per_rule_error[rule.rule_id] else "COMPLETE",
            execution_duration_ms=int(per_rule_ms[rule.rule_id]),
            error=per_rule_error[rule.rule_id],
            executed_by=executed_by,
        ))

    run.completed_at = datetime.utcnow()
    run.records_evaluated = total_records
    run.issues_generated = sequence
    run.run_status = "COMPLETE"
    await db.commit()

    return {
        "run_id": str(run.id),
        "intake_id": str(intake.id),
        "rule_set_version": RULE_SET_VERSION,
        "rule_config_hash": config_hash,
        "field_map_version": FIELD_MAP_VERSION,
        "records_evaluated": total_records,
        "records_in_delivery": total,
        "every_record_evaluated": total_records == total,
        "issues_generated": sequence,
        "rules_executed": len(RULES),
        "rules_failed": [r for r, e in per_rule_error.items() if e],
        "issues_by_rule": {r: n for r, n in per_rule_issues.items() if n},
    }


def _issue_row(finding: Finding, intake_id, record_id, run_id, sequence: int,
               stamp: datetime, now: datetime) -> Dict[str, Any]:
    from app.tefca_registry.rce.quality_rules import RULE_BY_ID

    rule = RULE_BY_ID.get(finding.rule_id)
    return {
        "issue_code": issue_code(sequence, stamp),
        "source_intake_id": intake_id,
        "source_record_id": record_id,
        "run_id": run_id,
        "rule_id": finding.rule_id,
        "rule_version": rule.version if rule else None,
        "issue_type": finding.issue_type,
        "severity": finding.severity,
        "field_name": (finding.field_name or "")[:100] or None,
        "original_value": finding.original_value,
        "suggested_value": finding.suggested_value,
        "suggested_source": finding.suggested_source,
        "suggested_confidence": finding.suggested_confidence,
        "correction_authority": finding.correction_authority,
        "description": finding.description,
        "resolution": "OPEN",
        "created_at": now,
    }


async def _flush_issues(db, rows: List[Dict[str, Any]]) -> None:
    if rows:
        await db.execute(m.RceIssue.__table__.insert(), rows)


async def issue_summary(db, intake_id) -> Dict[str, Any]:
    """Counts by severity, rule and resolution for one delivery."""
    by_severity = dict((await db.execute(
        select(m.RceIssue.severity, func.count())
        .where(m.RceIssue.source_intake_id == intake_id)
        .group_by(m.RceIssue.severity))).all())
    by_rule = dict((await db.execute(
        select(m.RceIssue.rule_id, func.count())
        .where(m.RceIssue.source_intake_id == intake_id)
        .group_by(m.RceIssue.rule_id))).all())
    by_resolution = dict((await db.execute(
        select(m.RceIssue.resolution, func.count())
        .where(m.RceIssue.source_intake_id == intake_id)
        .group_by(m.RceIssue.resolution))).all())
    by_authority = dict((await db.execute(
        select(m.RceIssue.correction_authority, func.count())
        .where(m.RceIssue.source_intake_id == intake_id)
        .group_by(m.RceIssue.correction_authority))).all())
    total = sum(by_severity.values())
    return {
        "total": total,
        "by_severity": {k: int(v) for k, v in by_severity.items()},
        "by_rule": {k: int(v) for k, v in sorted(
            by_rule.items(), key=lambda kv: -kv[1])},
        "by_resolution": {k: int(v) for k, v in by_resolution.items()},
        "by_correction_authority": {k: int(v) for k, v in by_authority.items()},
    }
