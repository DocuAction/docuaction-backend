"""
Delivery Processing Report — data service.

READ-ONLY, DETERMINISTIC, AND BUILT FROM PERSISTED EVIDENCE ONLY.

The report answers, for ONE registered delivery: what arrived, what happened to
every received line, when each stage ran, what the reconciliation result was at
the time, which identifiers conflicted and how they were decided, what the
analysts did, and what could NOT be reconstructed. Nothing here re-runs a rule,
re-reconciles, or looks anything up; every value is read from a row that the
pipeline, an analyst or the reconciliation writer persisted earlier.

THE DELIVERY IS ALWAYS NAMED. The caller must supply `job_id` or `intake_id`.
There is no "newest delivery" default: a report that quietly picked a delivery
would describe the wrong one the day two arrive close together, and nothing on
the page would show it.

THE SNAPSHOT IS PINNED. The reconciliation block is read from ONE persisted
snapshot (`rce_reconciliation_snapshots`): the latest for the job unless the
caller names `snapshot_id` for a regeneration. The dataset carries
`snapshot_id`, `snapshot_hash` and `snapshot_created_at`, and the template
prints them on page one, so a regenerated report can never silently rest on
newer live data. When the CURRENT disposition table (which analysts may have
appended to since) no longer sums to the snapshot, that is reported as a fact,
not smoothed over.

Every timestamp is emitted as an ISO-8601 string so the stored dataset (JSONB)
round-trips byte-for-byte and the CSV regenerated from it says what the HTML
said.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, select, text

logger = logging.getLogger(__name__)

DELIVERY_PROCESSING_DATA_SERVICE_VERSION = "1.0.0"

#: Severities that count as a FINDING on a record; the rest are warnings.
FINDING_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM")
WARNING_SEVERITIES = ("LOW", "INFORMATIONAL")
SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")

#: Verification sources the report always lists, so an absent one reads
#: "Not Run" rather than vanishing.
VERIFICATION_SOURCES = ("nppes", "pecos", "leie", "sam")

#: Page size for the record-level disposition read. All rows are read; this
#: only bounds one round trip.
_PAGE = 5000

#: Lineage rows printed in the report (the totals are always complete).
_LINEAGE_ROW_CAP = 200


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _s(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _uuid(value: Any, *, what: str):
    """Parse an identifier, refusing malformed input as a parameter error."""
    from app.reports.generator import ReportParameterError

    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise ReportParameterError(
            f"{what} {value!r} is not a valid identifier.",
            code="DELIVERY_IDENTIFIER_INVALID", status=422)


# ── resolution ───────────────────────────────────────────────────────────────

async def resolve_delivery(db, *, job_id=None, intake_id=None) -> Dict[str, Any]:
    """Name the delivery. Returns {job, intake, resolved_from}.

    `job_id` wins; `intake_id` alone is accepted when exactly one job (or no
    job at all - a legacy synchronous upload) references the intake. Two jobs
    for one intake is ambiguous and is refused with the candidates, matching
    the job-keyed detail API.
    """
    from app.reports.generator import ReportParameterError
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

    if not job_id and not intake_id:
        raise ReportParameterError(
            "This report describes ONE delivery and needs parameters.job_id or "
            "parameters.intake_id. It never defaults to the newest delivery.",
            code="DELIVERY_IDENTIFIER_REQUIRED", status=422)

    job = intake = None
    resolved_from = None
    if job_id:
        job = await db.get(RceDeliveryJob, _uuid(job_id, what="job_id"))
        if job is None:
            raise ReportParameterError(
                f"No delivery job exists with id {job_id}.",
                code="DELIVERY_NOT_FOUND", status=404)
        resolved_from = "job_id"
        if job.source_intake_id is not None:
            intake = await db.get(m.RceSourceIntake, job.source_intake_id)
        if intake_id and str(_uuid(intake_id, what="intake_id")) != str(job.source_intake_id):
            raise ReportParameterError(
                f"parameters.intake_id {intake_id} does not belong to job {job_id} "
                f"(its intake is {job.source_intake_id}).",
                code="DELIVERY_IDENTIFIER_MISMATCH", status=422)
    else:
        intake = await db.get(m.RceSourceIntake, _uuid(intake_id, what="intake_id"))
        if intake is None:
            raise ReportParameterError(
                f"No delivery intake exists with id {intake_id}.",
                code="DELIVERY_NOT_FOUND", status=404)
        resolved_from = "intake_id"
        jobs = (await db.execute(
            select(RceDeliveryJob)
            .where(RceDeliveryJob.source_intake_id == intake.id)
            .order_by(RceDeliveryJob.created_at.desc()))).scalars().all()
        if len(jobs) == 1:
            job = jobs[0]
        elif len(jobs) > 1:
            raise ReportParameterError(
                f"Intake {intake_id} is referenced by {len(jobs)} delivery jobs; "
                f"name the job with parameters.job_id. Candidates: "
                f"{[str(j.id) for j in jobs]}",
                code="DELIVERY_AMBIGUOUS", status=409)
    return {"job": job, "intake": intake, "resolved_from": resolved_from}


def _snapshot_dict(obj: Any) -> Optional[Dict[str, Any]]:
    if obj is None:
        return None
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


async def select_snapshot(db, *, job, intake, snapshot_id=None
                          ) -> Tuple[Optional[Dict[str, Any]], int, bool]:
    """The pinned snapshot as a dict, the job's snapshot count, and whether the
    pinned one is the latest.

    Lane P's `reconciliation.latest_snapshot(db, job_id)` is used when present;
    otherwise the same row is read directly. A named `snapshot_id` must belong
    to the delivery, or the request is refused.
    """
    from app.reports.generator import ReportParameterError
    from app.tefca_registry.rce import traceability_models as tm

    S = tm.RceReconciliationSnapshot
    history = 0
    if job is not None:
        history = int((await db.execute(
            select(func.count()).select_from(S).where(S.job_id == job.id))).scalar() or 0)
    elif intake is not None:
        history = int((await db.execute(
            select(func.count()).select_from(S).where(S.intake_id == intake.id))).scalar() or 0)

    latest = None
    if job is not None:
        latest = await _latest_snapshot_for_job(db, job.id)
    elif intake is not None:
        latest = _snapshot_dict((await db.execute(
            select(S).where(S.intake_id == intake.id)
            .order_by(S.created_at.desc(), S.sequence.desc()).limit(1))).scalars().first())

    if snapshot_id:
        row = await db.get(S, _uuid(snapshot_id, what="snapshot_id"))
        if row is None:
            raise ReportParameterError(
                f"No reconciliation snapshot exists with id {snapshot_id}.",
                code="SNAPSHOT_NOT_FOUND", status=404)
        if job is not None and str(row.job_id) != str(job.id):
            raise ReportParameterError(
                f"Snapshot {snapshot_id} belongs to job {row.job_id}, not to the "
                f"requested delivery (job {job.id}).",
                code="SNAPSHOT_NOT_FOR_JOB", status=422)
        if job is None and intake is not None and str(row.intake_id) != str(intake.id):
            raise ReportParameterError(
                f"Snapshot {snapshot_id} belongs to intake {row.intake_id}, not to "
                f"the requested delivery (intake {intake.id}).",
                code="SNAPSHOT_NOT_FOR_JOB", status=422)
        pinned = row.to_dict()
        return pinned, history, bool(latest and latest.get("id") == pinned.get("id"))
    return latest, history, latest is not None


async def _latest_snapshot_for_job(db, job_id) -> Optional[Dict[str, Any]]:
    try:
        from app.tefca_registry.rce import reconciliation
        fn = getattr(reconciliation, "latest_snapshot", None)
    except Exception:  # noqa: BLE001 - lane P module may be mid-change
        fn = None
    if callable(fn):
        try:
            return _snapshot_dict(await fn(db, job_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("reconciliation.latest_snapshot failed (%s); reading the "
                           "snapshot table directly", exc)
    from app.tefca_registry.rce import traceability_models as tm

    S = tm.RceReconciliationSnapshot
    row = (await db.execute(
        select(S).where(S.job_id == job_id)
        .order_by(S.sequence.desc()).limit(1))).scalars().first()
    return _snapshot_dict(row)


# ── the service ──────────────────────────────────────────────────────────────

class DeliveryProcessingDataService:
    """Canonical read-only queries behind the Delivery Processing Report."""

    version = DELIVERY_PROCESSING_DATA_SERVICE_VERSION

    def __init__(self, db, *, job_id=None, intake_id=None, snapshot_id=None):
        self.db = db
        self.job_id = job_id
        self.intake_id = intake_id
        self.snapshot_id = snapshot_id
        self.limitations: List[str] = []

    def limit(self, message: str) -> None:
        if message not in self.limitations:
            self.limitations.append(message)

    # -- identity ------------------------------------------------------------

    def _delivery(self, job, intake, resolved_from) -> Dict[str, Any]:
        return {
            "resolved_from": resolved_from,
            "job_id": _s(job.id) if job else None,
            "intake_id": _s(intake.id) if intake else (_s(job.source_intake_id) if job else None),
            "delivery_label": (job.delivery_label if job else None) or (intake.delivery_label if intake else None),
            "filename": (job.original_filename if job else None) or (intake.original_filename if intake else None),
            "sha256": (job.sha256 if job else None) or (intake.sha256 if intake else None),
            "file_size_bytes": (job.file_size_bytes if job else None) or (intake.file_size_bytes if intake else None),
            "registrant": (job.registered_by if job else None) or (intake.received_by if intake else None),
            "received_date": _iso(job.received_date) if job else None,
            "registered_at": _iso(job.created_at) if job else (_iso(intake.received_at) if intake else None),
            "started_at": _iso(job.started_at) if job else None,
            "completed_at": _iso(job.completed_at) if job else None,
            "failed_at": _iso(job.failed_at) if job else None,
            "government_reference": job.government_reference if job else None,
            "source_name": job.source_name if job else None,
            "state": job.state if job else None,
            "stage": job.stage if job else None,
            "attempt_count": job.attempt_count if job else None,
            "error_reason": job.error_reason if job else None,
            "records_received_by_job": job.records_received if job else None,
            "records_processed_by_job": job.records_processed if job else None,
            "record_count": intake.record_count if intake else None,
            "intake_status": intake.status if intake else None,
            "intake_received_at": _iso(intake.received_at) if intake else None,
            "schema_fingerprint": intake.schema_fingerprint if intake else None,
            "encoding": intake.encoding if intake else None,
            "delimiter": intake.delimiter if intake else None,
            "duplicate_content": bool(intake.duplicate_content) if intake else False,
        }

    async def _build(self) -> Dict[str, Any]:
        from app.core import request_context

        build = dict(request_context.build_identity())
        try:
            rev = (await self.db.execute(text("select version_num from alembic_version"))).scalars().all()
            build["migration_revision"] = rev[0] if len(rev) == 1 else (",".join(rev) or "unknown")
        except Exception as exc:  # noqa: BLE001
            logger.info("alembic_version unreadable: %s", exc)
            build["migration_revision"] = "unknown"
        return build

    # -- evidence blocks -----------------------------------------------------

    async def _timeline(self, job) -> Dict[str, Any]:
        if job is None:
            self.limit("No delivery job exists for this intake (legacy synchronous "
                       "upload), so no stage timeline was recorded.")
            return {"available": False, "events": [], "summary": {
                "failed_stage": None, "completed_stages": [], "attempts": 0}}
        from app.tefca_registry.rce import stage_events

        events = await stage_events.timeline(self.db, job.id)
        summary = stage_events.summarise(events)
        if not events:
            self.limit("No stage events were recorded for this job: it was processed "
                       "before durable stage events existed. Stage timings cannot "
                       "be reconstructed.")
        return {
            "available": bool(events),
            "events": events,
            "summary": {"failed_stage": summary["failed_stage"],
                        "completed_stages": summary["completed_stages"],
                        "attempts": summary["attempts"]},
        }

    async def _received(self, intake) -> int:
        from app.tefca_registry.rce import models as m

        if intake is None:
            return 0
        return int((await self.db.execute(
            select(func.count()).select_from(m.RceSourceRecord)
            .where(m.RceSourceRecord.source_intake_id == intake.id))).scalar() or 0)

    async def _issue_counts_by_record(self, intake) -> Dict[str, Tuple[int, int]]:
        """(finding_count, warning_count) per source record, current run."""
        from app.tefca_registry.rce import models as m
        from app.tefca_registry.rce import run_selection

        rows = (await self.db.execute(
            select(m.RceIssue.source_record_id, m.RceIssue.severity, func.count())
            .where(run_selection.current_issues_filter(intake.id),
                   m.RceIssue.source_record_id.isnot(None))
            .group_by(m.RceIssue.source_record_id, m.RceIssue.severity))).all()
        out: Dict[str, Tuple[int, int]] = {}
        for record_id, severity, n in rows:
            f, w = out.get(str(record_id), (0, 0))
            if severity in WARNING_SEVERITIES:
                w += int(n)
            else:
                f += int(n)
            out[str(record_id)] = (f, w)
        return out

    async def _dispositions(self, intake, job, snapshot: Optional[Dict[str, Any]],
                            received: int) -> Dict[str, Any]:
        from app.tefca_registry.rce import dispositions as disp
        from app.tefca_registry.rce import traceability_models as tm

        empty_counts = {d: 0 for d in tm.DISPOSITIONS}
        empty_counts["total"] = 0
        if intake is None:
            self.limit("The job produced no intake (it failed before Area 1 was "
                       "written), so there are no records to account for.")
            return {"available": False, "counts": empty_counts,
                    "equation": disp.equation(empty_counts, 0), "records_received": 0,
                    "records_without_disposition": 0, "agrees_with_snapshot": None,
                    "rows": [], "human_count": 0, "reconstructed_count": 0}

        counts = await disp.counts_for_intake(self.db, intake.id)
        without = await disp.records_without_disposition(self.db, intake.id)
        per_record = await self._issue_counts_by_record(intake)

        rows: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = await disp.current_for_intake(self.db, intake.id, limit=_PAGE, offset=offset)
            for r in page:
                f, w = per_record.get(str(r.get("source_record_id")), (0, 0))
                rows.append({
                    "line_number": r.get("line_number"),
                    "source_record_id": _s(r.get("source_record_id")),
                    "source_rce_id": r.get("source_rce_id"),
                    "name": r.get("curated_name"),
                    "submitted_npi": r.get("submitted_npi"),
                    "curated_npi": r.get("curated_npi"),
                    "record_status": r.get("record_status"),
                    "disposition": r.get("disposition"),
                    "reason_code": r.get("reason_code"),
                    "reason": r.get("reason"),
                    "entity_id": _s(r.get("entity_id")),
                    "changed_fields": list(r.get("changed_fields") or []),
                    "finding_count": f,
                    "warning_count": w,
                    "sequence": r.get("sequence"),
                    "actor": r.get("actor"),
                    "actor_type": r.get("actor_type"),
                    "decided_at": _iso(r.get("decided_at")),
                    "reconstructed": bool(r.get("reconstructed")),
                })
            if len(page) < _PAGE:
                break
            offset += _PAGE

        equation = disp.equation(counts, received)
        agrees = None
        if snapshot is not None:
            seq = snapshot.get("equation") or {}
            agrees = all(int(seq.get(d.lower(), 0)) == int(counts.get(d, 0))
                         for d in tm.DISPOSITIONS) and int(seq.get("received", -1)) == received
            if not agrees:
                self.limit("The CURRENT disposition table no longer sums to the pinned "
                           "reconciliation snapshot: decisions were appended after the "
                           "snapshot was written, or the snapshot is not the latest. "
                           "The snapshot is the reconciliation of record; the table "
                           "shows the current state.")
        if not rows:
            self.limit("No disposition events exist for this delivery: it was processed "
                       "before record-level accounting was persisted. Per-record "
                       "outcomes cannot be stated from evidence; see "
                       "docs/rce/HISTORICAL_RECONSTRUCTION_POLICY.md for the dry-run "
                       "reconstruction tool.")
        elif without:
            self.limit(f"{without} received record(s) have no disposition event; the "
                       f"accounting equation does not close on the current table.")
        return {
            "available": bool(rows),
            "counts": counts,
            "equation": equation,
            "records_received": received,
            "records_without_disposition": without,
            "agrees_with_snapshot": agrees,
            "rows": rows,
            "human_count": sum(1 for r in rows if r["actor_type"] == "HUMAN"),
            "reconstructed_count": sum(1 for r in rows if r["reconstructed"]),
        }

    async def _findings(self, intake) -> Dict[str, Any]:
        from app.tefca_registry.rce import models as m
        from app.tefca_registry.rce import run_selection

        base = {"available": False, "total": 0, "by_severity": {s: 0 for s in SEVERITY_ORDER},
                "by_code": [], "by_resolution": {}, "rows": [], "run_id": None,
                "rule_set_version": None, "open_high": 0}
        if intake is None:
            return base
        scope = run_selection.current_issues_filter(intake.id)
        run = (await self.db.execute(
            select(m.RceIngestionRun.id, m.RceIngestionRun.rule_set_version)
            .where(m.RceIngestionRun.id == run_selection.current_run_id_subquery(intake.id)))).first()
        by_code = (await self.db.execute(
            select(m.RceIssue.rule_id, m.RceIssue.issue_type, m.RceIssue.severity, func.count())
            .where(scope)
            .group_by(m.RceIssue.rule_id, m.RceIssue.issue_type, m.RceIssue.severity)
            .order_by(func.count().desc(), m.RceIssue.rule_id))).all()
        by_res = dict((k or "(none)", int(v)) for k, v in (await self.db.execute(
            select(m.RceIssue.resolution, func.count()).where(scope)
            .group_by(m.RceIssue.resolution))).all())
        rows = (await self.db.execute(
            select(m.RceIssue, m.RceSourceRecord.line_number)
            .join(m.RceSourceRecord, m.RceSourceRecord.id == m.RceIssue.source_record_id,
                  isouter=True)
            .where(scope)
            .order_by(m.RceSourceRecord.line_number, m.RceIssue.rule_id))).all()
        by_severity = {s: 0 for s in SEVERITY_ORDER}
        for _rule, _type, severity, n in by_code:
            by_severity[severity] = by_severity.get(severity, 0) + int(n)
        open_high = sum(1 for issue, _ in rows
                        if issue.resolution == "OPEN" and issue.severity in ("HIGH", "CRITICAL"))
        if run is None:
            self.limit("No completed quality run exists for this delivery; findings "
                       "cannot be stated (quality never ran, or never completed).")
        return {
            "available": run is not None,
            "total": sum(by_severity.values()),
            "by_severity": by_severity,
            "by_code": [{"rule_id": r, "issue_type": t, "severity": s, "count": int(n)}
                        for r, t, s, n in by_code],
            "by_resolution": by_res,
            "rows": [{
                "issue_code": issue.issue_code, "line_number": line,
                "source_record_id": _s(issue.source_record_id),
                "rule_id": issue.rule_id, "rule_version": issue.rule_version,
                "issue_type": issue.issue_type, "severity": issue.severity,
                "field_name": issue.field_name, "resolution": issue.resolution,
                "correction_authority": issue.correction_authority,
                "description": issue.description,
                "resolved_by": issue.resolved_by, "resolved_at": _iso(issue.resolved_at),
            } for issue, line in rows],
            "run_id": _s(run[0]) if run else None,
            "rule_set_version": run[1] if run else None,
            "open_high": open_high,
        }

    async def _identifiers(self, intake) -> Dict[str, Any]:
        from app.tefca_registry.rce import traceability_models as tm

        if intake is None:
            return {"available": False, "conflicts": [], "unresolved": 0, "events_total": 0}
        E = tm.TefcaIdentifierDecisionEvent
        events = (await self.db.execute(
            select(E).where(E.intake_id == intake.id)
            .order_by(E.entity_id, E.identifier_type, E.sequence))).scalars().all()
        conflicts: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for ev in events:
            key = (str(ev.entity_id), ev.identifier_type)
            item = conflicts.setdefault(key, {
                "entity_id": str(ev.entity_id), "identifier_type": ev.identifier_type,
                "source_record_id": _s(ev.source_record_id), "issue_id": _s(ev.issue_id),
                "submitted_value": ev.submitted_value, "existing_value": ev.existing_value,
                "verified_value": None, "selected_value": None,
                "current_decision": None, "decided_at": None, "actor": None,
                "reason": None, "events": []})
            item["events"].append(ev.to_dict())
            item["current_decision"] = ev.decision
            item["decided_at"] = _iso(ev.decided_at)
            item["actor"] = ev.actor
            item["reason"] = ev.reason
            item["verified_value"] = ev.verified_value or item["verified_value"]
            item["selected_value"] = ev.selected_value
        items = list(conflicts.values())
        unresolved = sum(1 for c in items if c["current_decision"] in ("CONFLICT_RAISED",
                                                                       "REQUEST_EVIDENCE",
                                                                       "DEFERRED", "ESCALATED"))
        return {"available": True, "conflicts": items, "unresolved": unresolved,
                "events_total": len(events)}

    async def _verification(self, intake) -> Dict[str, Any]:
        """Lane A's coverage_for_intake when present; otherwise "Not Run"."""
        fallback = {
            "available": False, "state": "Not Run", "source": "fallback",
            "sources": [{"name": s, "state": "Not Run", "eligible": None, "attempted": None,
                         "verified": None, "not_found": None, "unavailable": None,
                         "failed": None, "coverage_pct": None,
                         "note": "No coverage evidence was read for this source."}
                        for s in VERIFICATION_SOURCES],
            "reason": None, "failed_required": 0,
        }
        if intake is None:
            fallback["reason"] = "no intake"
            return fallback
        try:
            from app.tefca_registry.rce import verification_coverage as vc
            fn = getattr(vc, "coverage_for_intake")
        except Exception as exc:  # noqa: BLE001 - lane A module not present yet
            fallback["reason"] = f"verification coverage service unavailable ({type(exc).__name__})"
            self.limit("Verification coverage could not be read (service unavailable); "
                       "every source is reported as Not Run.")
            return fallback
        try:
            raw = await fn(self.db, intake.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("coverage_for_intake failed: %s", exc)
            fallback["reason"] = f"verification coverage query failed ({type(exc).__name__})"
            self.limit("Verification coverage could not be read (query failed); "
                       "every source is reported as Not Run.")
            return fallback
        return _normalise_coverage(raw)

    async def _analyst(self, intake, dispositions: Dict[str, Any]) -> Dict[str, Any]:
        from app.tefca_registry import models as reg
        from app.tefca_registry.rce import models as m
        from app.tefca_registry.rce import run_selection
        from app.tefca_registry.rce import traceability_models as tm

        empty = {"available": False, "disposition_events": [], "issue_resolutions": [],
                 "review_records": [], "counts": {"disposition_events": 0,
                                                  "issue_resolutions": 0,
                                                  "review_records": 0, "open": 0,
                                                  "claimed": 0, "qa_pending": 0,
                                                  "qa_approved": 0}}
        if intake is None:
            return empty
        D = tm.RceDispositionEvent
        human = (await self.db.execute(
            select(D, m.RceSourceRecord.line_number)
            .join(m.RceSourceRecord, m.RceSourceRecord.id == D.source_record_id)
            .where(D.intake_id == intake.id, D.actor_type == "HUMAN")
            .order_by(D.decided_at))).all()
        resolved = (await self.db.execute(
            select(m.RceIssue)
            .where(run_selection.current_issues_filter(intake.id),
                   m.RceIssue.resolution != "OPEN")
            .order_by(m.RceIssue.resolved_at))).scalars().all()
        record_ids = select(m.RceSourceRecord.id).where(
            m.RceSourceRecord.source_intake_id == intake.id)
        reviews = (await self.db.execute(
            select(reg.ReviewRecord)
            .where(reg.ReviewRecord.source_record_id.in_(record_ids))
            .order_by(reg.ReviewRecord.created_at))).scalars().all()
        open_items = sum(1 for r in reviews
                         if r.assigned_to_user_id is None and r.reviewer_resolution is None)
        claimed = sum(1 for r in reviews
                      if r.assigned_to_user_id is not None and r.reviewer_resolution is None)
        qa_pending = sum(1 for r in reviews
                         if r.reviewer_resolution is not None and r.reportable_at is None)
        qa_approved = sum(1 for r in reviews if r.reportable_at is not None)
        return {
            "available": bool(human or resolved or reviews),
            "disposition_events": [{**ev.to_dict(), "line_number": line} for ev, line in human],
            "issue_resolutions": [{
                "issue_code": i.issue_code, "rule_id": i.rule_id, "issue_type": i.issue_type,
                "severity": i.severity, "resolution": i.resolution,
                "resolved_by": i.resolved_by, "resolved_at": _iso(i.resolved_at),
                "resolution_notes": i.resolution_notes,
                "qa_approved_by": i.qa_approved_by, "qa_approved_at": _iso(i.qa_approved_at),
            } for i in resolved],
            "review_records": [{
                "review_id": r.review_id, "source_record_id": _s(r.source_record_id),
                "entity_id": _s(r.entity_id), "bucket": r.classification_bucket,
                "resolution": r.reviewer_resolution, "reclassified_to": r.reclassified_to,
                "assigned": r.assigned_to_user_id is not None,
                "reviewed_at": _iso(r.reviewed_at), "reportable_at": _iso(r.reportable_at),
                "created_at": _iso(r.created_at),
            } for r in reviews],
            "counts": {"disposition_events": len(human), "issue_resolutions": len(resolved),
                       "review_records": len(reviews), "open": open_items,
                       "claimed": claimed, "qa_pending": qa_pending,
                       "qa_approved": qa_approved},
        }

    async def _lineage(self, intake, job) -> Dict[str, Any]:
        from app.tefca_registry import models as reg
        from app.tefca_registry.rce import models as m

        empty = {"available": False, "corrections_total": 0, "by_authority": {},
                 "by_column": {}, "rows": [], "rows_shown": 0,
                 "entities_promoted": 0, "entity_versions_in_window": None}
        if intake is None:
            return empty
        C, R = m.RceCorrectionDetail, m.RceCuratedRecord
        scoped = (select(C, m.RceSourceRecord.line_number)
                  .join(R, R.id == C.curated_record_id)
                  .join(m.RceSourceRecord, m.RceSourceRecord.id == C.source_record_id, isouter=True)
                  .where(R.source_intake_id == intake.id)
                  .order_by(m.RceSourceRecord.line_number, C.column_name))
        rows = (await self.db.execute(scoped)).all()
        by_authority: Dict[str, int] = {}
        by_column: Dict[str, int] = {}
        for c, _ in rows:
            by_authority[c.correction_authority] = by_authority.get(c.correction_authority, 0) + 1
            by_column[c.column_name] = by_column.get(c.column_name, 0) + 1
        promoted_ids = select(R.canonical_entity_id).where(
            R.source_intake_id == intake.id, R.canonical_entity_id.isnot(None))
        entities = int((await self.db.execute(
            select(func.count(func.distinct(R.canonical_entity_id)))
            .where(R.source_intake_id == intake.id, R.canonical_entity_id.isnot(None)))).scalar() or 0)
        versions = None
        if job is not None and job.started_at is not None:
            V = reg.TefcaEntityVersion
            stmt = select(func.count()).select_from(V).where(
                V.entity_id.in_(promoted_ids), V.created_at >= job.started_at)
            if job.completed_at is not None:
                stmt = stmt.where(V.created_at <= job.completed_at)
            versions = int((await self.db.execute(stmt)).scalar() or 0)
        else:
            self.limit("Entity version history could not be scoped to the job window "
                       "(no job start time), so registry changes made by this "
                       "delivery are not counted.")
        return {
            "available": True,
            "corrections_total": len(rows),
            "by_authority": dict(sorted(by_authority.items())),
            "by_column": dict(sorted(by_column.items(), key=lambda kv: (-kv[1], kv[0]))),
            "rows": [{
                "line_number": line, "column_name": c.column_name,
                "original_value": c.original_value, "corrected_value": c.corrected_value,
                "correction_rule_id": c.correction_rule_id,
                "correction_authority": c.correction_authority,
                "corrected_by": c.corrected_by, "confidence": c.confidence,
                "qa_status": c.qa_status, "created_at": _iso(c.created_at),
            } for c, line in rows[:_LINEAGE_ROW_CAP]],
            "rows_shown": min(len(rows), _LINEAGE_ROW_CAP),
            "entities_promoted": entities,
            "entity_versions_in_window": versions,
        }

    async def _invalid_identifiers_promoted(self, intake) -> int:
        """Active NPI identifier rows on this delivery's entities that fail validation."""
        if intake is None:
            return 0
        try:
            from app.services.npi_validator import validate_npi
            from app.tefca_registry import models as reg
            from app.tefca_registry.rce import models as m
        except Exception:  # noqa: BLE001
            return 0
        promoted_ids = select(m.RceCuratedRecord.canonical_entity_id).where(
            m.RceCuratedRecord.source_intake_id == intake.id,
            m.RceCuratedRecord.canonical_entity_id.isnot(None))
        I = reg.TefcaEntityIdentifier
        values = (await self.db.execute(
            select(I.identifier_value).where(
                I.entity_id.in_(promoted_ids), func.lower(I.identifier_type) == "npi",
                func.coalesce(I.identifier_status, "active") == "active"))).scalars().all()
        bad = 0
        for v in values:
            try:
                ok, _ = validate_npi(v)
            except Exception:  # noqa: BLE001
                ok = False
            bad += 0 if ok else 1
        return bad

    # -- assembly ------------------------------------------------------------

    async def build_dataset(self) -> Dict[str, Any]:
        from app.reports.engine.template_engine import TEMPLATE_VERSION
        from app.tefca_registry.rce import status_model

        resolved = await resolve_delivery(self.db, job_id=self.job_id, intake_id=self.intake_id)
        job, intake = resolved["job"], resolved["intake"]

        snapshot, history, is_latest = await select_snapshot(
            self.db, job=job, intake=intake, snapshot_id=self.snapshot_id)
        if snapshot is None:
            self.limit("No reconciliation snapshot is persisted for this delivery: "
                       "reconciliation never ran, never completed, or ran before "
                       "snapshots were persisted. No reconciliation of record exists.")
        elif not is_latest:
            self.limit(f"This report was regenerated from snapshot "
                       f"{snapshot.get('id')} (sequence {snapshot.get('sequence')}), "
                       f"which is NOT the latest of {history} for this job. It states "
                       f"the reconciliation as it stood then.")
        if snapshot is not None and snapshot.get("reconstructed"):
            self.limit("The pinned reconciliation snapshot is marked RECONSTRUCTED: it "
                       "was derived after the fact under the historical reconstruction "
                       "policy, not written by the pipeline at processing time.")

        received = await self._received(intake)
        timeline = await self._timeline(job)
        dispositions = await self._dispositions(intake, job, snapshot, received)
        findings = await self._findings(intake)
        identifiers = await self._identifiers(intake)
        verification = await self._verification(intake)
        analyst = await self._analyst(intake, dispositions)
        lineage = await self._lineage(intake, job)
        invalid_promoted = await self._invalid_identifiers_promoted(intake)

        outcome = status_model.processing_outcome(
            job_state=job.state if job else ("SUCCEEDED" if intake else None),
            job_stage=job.stage if job else (intake.status if intake else None),
            failed_stage=timeline["summary"]["failed_stage"],
            error_reason=job.error_reason if job else None,
            snapshot=snapshot,
            stages_completed=timeline["summary"]["completed_stages"] or None,
            unresolved_findings=findings["open_high"],
            unresolved_conflicts=identifiers["unresolved"],
            invalid_identifiers_promoted=invalid_promoted,
            failed_required_verification=int(verification.get("failed_required") or 0),
            unexplained_records=dispositions["records_without_disposition"])
        counts = analyst["counts"]
        review = status_model.review_state(
            outcome_code=outcome["code"],
            snapshot_passed=bool(snapshot and snapshot.get("passed")),
            open_work_items=counts["open"] + findings["open_high"],
            claimed_work_items=counts["claimed"],
            determined_items=0, qa_pending=counts["qa_pending"],
            qa_approved=counts["qa_approved"])
        if job is None and intake is not None:
            self.limit("Processing outcome is derived from the intake alone (no job "
                       "row); the job lifecycle (queued/running/failed) is unknown.")

        build = await self._build()
        reconciliation = {
            "available": snapshot is not None,
            "history_count": history,
            "is_latest": is_latest,
            **({k: snapshot.get(k) for k in (
                "id", "sequence", "passed", "failure_reason", "equation", "dimensions",
                "checks", "source_evidence", "actor", "trigger", "created_at", "hash",
                "build_sha", "migration_revision", "correlation_id", "reconstructed")}
               if snapshot else {
                   "id": None, "sequence": None, "passed": None, "failure_reason": None,
                   "equation": {}, "dimensions": {}, "checks": [], "source_evidence": {},
                   "actor": None, "trigger": None, "created_at": None, "hash": None,
                   "build_sha": None, "migration_revision": None, "correlation_id": None,
                   "reconstructed": False}),
        }

        return {
            "service_version": self.version,
            "template_version": TEMPLATE_VERSION,
            "charts": {}, "chart_list": [],
            "delivery": self._delivery(job, intake, resolved["resolved_from"]),
            "build": build,
            "snapshot_id": reconciliation["id"],
            "snapshot_created_at": reconciliation["created_at"],
            "snapshot_hash": reconciliation["hash"],
            "snapshot_sequence": reconciliation["sequence"],
            "snapshot_is_latest": is_latest,
            "reconciliation": reconciliation,
            "outcome": outcome,
            "review": review,
            "timeline": timeline,
            "dispositions": dispositions,
            "findings": findings,
            "identifiers": identifiers,
            "verification": verification,
            "analyst": analyst,
            "lineage": lineage,
            "limitations": list(self.limitations),
            "audit_note": (
                "Generation of this report is recorded in audit_logs "
                "(action=report_generated) and linked to the job, intake and snapshot "
                "in rce_delivery_report_links. Every download is recorded as "
                "report_downloaded. The report reads persisted evidence only: no rule "
                "is re-run, no reconciliation is recomputed and no external lookup is "
                "performed while it is produced."),
        }


def _normalise_coverage(raw: Any) -> Dict[str, Any]:
    """Shape Lane A's coverage payload into the list the template prints."""
    if not isinstance(raw, dict):
        return {"available": False, "state": "Not Run", "source": "verification_coverage",
                "sources": [], "reason": "unexpected payload shape", "failed_required": 0}
    sources_raw = raw.get("sources") or {}
    items: List[Dict[str, Any]] = []
    if isinstance(sources_raw, dict):
        iterable: Iterable[Tuple[str, Any]] = sources_raw.items()
    else:
        iterable = ((s.get("name") or s.get("source") or f"source_{i}", s)
                    for i, s in enumerate(sources_raw))
    for name, s in iterable:
        s = s or {}
        items.append({
            "name": name,
            "state": s.get("coverage_state") or s.get("state") or "Not Run",
            "eligible": s.get("eligible"), "attempted": s.get("attempted"),
            "verified": s.get("verified"), "not_found": s.get("not_found"),
            "unavailable": s.get("unavailable"), "failed": s.get("failed"),
            "coverage_pct": s.get("coverage_pct"),
            "note": s.get("note") or s.get("detail"),
        })
    present = {i["name"] for i in items}
    for name in VERIFICATION_SOURCES:
        if name not in present:
            items.append({"name": name, "state": "Not Run", "eligible": None,
                          "attempted": None, "verified": None, "not_found": None,
                          "unavailable": None, "failed": None, "coverage_pct": None,
                          "note": "No coverage evidence was read for this source."})
    return {
        "available": True,
        "state": raw.get("state") or "Not Run",
        "source": "verification_coverage",
        "sources": items,
        "reason": None,
        "failed_required": int(raw.get("failed_required") or 0),
    }
