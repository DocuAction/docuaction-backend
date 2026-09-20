"""The exception ledger for one delivery, read-only.

ONE LEDGER, THREE EVIDENCE TABLES
---------------------------------
An "exception" on a delivery is a finding in `rce_issues`. What a reviewer
needs beside it is spread over two more append-only tables:

    tefca_identifier_decision_events   the registered value the submitted one
                                       conflicted with, and who decided what
    rce_disposition_events             what became of the delivered line
                                       (HELD, CREATED, ...) and every later
                                       analyst decision, in sequence

This module composes the three into the row shape fixed by the remediation
contract (2026-09-17, section 6) and applies the ledger filters in SQL. It
writes nothing. The stage of a finding is DERIVED from its rule, not stored:
QUALITY for the quality-run rules, PROMOTION for identifier conflicts against
the registry (NPI-008 / IDENTIFIER_EXISTING_VALUE_CONFLICT), VERIFICATION for
the source-lookup findings (NPI-005 / 006 / 009).

Also here, because it is the same join: the record-level disposition listing
and its CSV, which the `/dispositions` routes serve.

Raw delivered values (submitted NPI, curated name) ARE in these rows. Every
route that serves them sits at the reviewer floor; a viewer never reaches
this module.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import aliased

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce import traceability_models as tm

logger = logging.getLogger(__name__)

STAGE_QUALITY = "QUALITY"
STAGE_PROMOTION = "PROMOTION"
STAGE_VERIFICATION = "VERIFICATION"
STAGES = (STAGE_QUALITY, STAGE_PROMOTION, STAGE_VERIFICATION)

PROMOTION_RULES = ("NPI-008",)
PROMOTION_TYPES = ("NPI_EXISTING_VALUE_CONFLICT", "IDENTIFIER_EXISTING_VALUE_CONFLICT")
VERIFICATION_RULES = ("NPI-005", "NPI-006", "NPI-009")
#: `INVALID_ACTIVE_IDENTIFIER` and `MATERIAL_IDENTIFIER_CONFLICT` (added
#: 2026-09-18, pre-merge review Decision 2) reuse the QUALITY rule NPI-003 and
#: the PROMOTION rule NPI-008 respectively, because they are the same kind of
#: defect discovered at a different time — but by rule_id alone they would be
#: misclassified. `stage_for`/`stage_expr` check issue_type before rule_id for
#: exactly this reason.
VERIFICATION_TYPES = ("NPI_NOT_FOUND", "NPI_DEACTIVATED", "NPI_VERIFICATION_UNAVAILABLE",
                      "INVALID_ACTIVE_IDENTIFIER", "MATERIAL_IDENTIFIER_CONFLICT")

#: `rce_issues.resolution` values that mean "still someone's work".
OPEN_RESOLUTIONS = ("OPEN", "PROPOSED", "UNDER_REVIEW")
RESOLVED_RESOLUTIONS = ("APPROVED", "REJECTED", "WAIVED", "RESOLVED")

#: Historical issue types and the rule-set 1.2.0 types that replaced them.
#: `quality_rules.LEGACY_ISSUE_TYPES` (lane P) is preferred when present.
_LEGACY_FALLBACK = {
    "NPI_MALFORMED": ["NPI_LENGTH_INVALID", "NPI_FORMAT_INVALID"],
    "NPI_CHECK_DIGIT_FAILED": ["NPI_CHECKSUM_INVALID"],
}


def legacy_issue_types() -> Dict[str, List[str]]:
    try:
        from app.tefca_registry.rce.quality_rules import LEGACY_ISSUE_TYPES
        return dict(LEGACY_ISSUE_TYPES)
    except Exception:  # noqa: BLE001 - lane P may not have landed it yet
        return dict(_LEGACY_FALLBACK)


def stage_for(rule_id: Optional[str], issue_type: Optional[str]) -> str:
    """The pipeline stage a finding belongs to, from its rule and type.

    `issue_type` is checked before `rule_id`: a post-promotion finding
    (`INVALID_ACTIVE_IDENTIFIER`, `MATERIAL_IDENTIFIER_CONFLICT`) reuses a
    QUALITY/PROMOTION rule id for a defect discovered at a different time, and
    the specific type is what actually disambiguates that.
    """
    if issue_type in VERIFICATION_TYPES:
        return STAGE_VERIFICATION
    if issue_type in PROMOTION_TYPES:
        return STAGE_PROMOTION
    if rule_id in PROMOTION_RULES:
        return STAGE_PROMOTION
    if rule_id in VERIFICATION_RULES:
        return STAGE_VERIFICATION
    return STAGE_QUALITY


def stage_expr():
    """The same derivation as `stage_for`, as a SQL expression for filtering."""
    return case(
        (m.RceIssue.issue_type.in_(VERIFICATION_TYPES), STAGE_VERIFICATION),
        (m.RceIssue.issue_type.in_(PROMOTION_TYPES), STAGE_PROMOTION),
        (m.RceIssue.rule_id.in_(PROMOTION_RULES), STAGE_PROMOTION),
        (m.RceIssue.rule_id.in_(VERIFICATION_RULES), STAGE_VERIFICATION),
        else_=STAGE_QUALITY,
    )


def _iso(value):
    # Naive datetimes are UTC by construction (rce_issues.created_at is written
    # with datetime.utcnow()); state the offset so a browser never parses the
    # value as local time (QA-010).
    if isinstance(value, datetime):
        if value.tzinfo is None:
            from datetime import timezone

            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _jsonable(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _parse_when(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    """ISO date or datetime. A date-only `to` bound covers the whole day."""
    if not value:
        return None
    text_value = value.strip()
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an ISO date or datetime") from exc
    if end_of_day and len(text_value) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def as_uuid(value) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


# -- the exception ledger -----------------------------------------------------

def _base_statement(intake_id, *, all_runs: bool):
    """Issues of the delivery joined to their delivered line and curated copy."""
    return (
        select(m.RceIssue,
               m.RceSourceRecord.line_number, m.RceSourceRecord.source_rce_id,
               m.RceSourceRecord.npi.label("submitted_npi"),
               m.RceSourceRecord.canonical_entity_id.label("source_entity_id"),
               m.RceCuratedRecord.id.label("curated_id"),
               m.RceCuratedRecord.name.label("curated_name"),
               m.RceCuratedRecord.npi.label("curated_npi"),
               m.RceCuratedRecord.canonical_entity_id.label("curated_entity_id"),
               m.RceCuratedRecord.record_status,
               stage_expr().label("stage"))
        .select_from(m.RceIssue)
        .outerjoin(m.RceSourceRecord, m.RceSourceRecord.id == m.RceIssue.source_record_id)
        .outerjoin(m.RceCuratedRecord,
                   m.RceCuratedRecord.source_record_id == m.RceIssue.source_record_id)
        .where(run_selection.issues_filter(intake_id, all_runs=all_runs))
    )


def _apply_filters(stmt, *, source_row=None, entity_name=None, npi=None,
                   rule_code=None, issue_type=None, severity=None, stage=None,
                   status=None, from_=None, to=None, assigned_to=None,
                   disposition=None):
    if source_row is not None:
        stmt = stmt.where(m.RceSourceRecord.line_number == int(source_row))
    if entity_name:
        stmt = stmt.where(m.RceCuratedRecord.name.ilike(f"%{entity_name.strip()}%"))
    if npi:
        value = npi.strip()
        stmt = stmt.where(or_(m.RceSourceRecord.npi == value,
                              m.RceCuratedRecord.npi == value,
                              m.RceIssue.original_value == value))
    if rule_code:
        stmt = stmt.where(m.RceIssue.rule_id == rule_code.strip())
    if issue_type:
        stmt = stmt.where(m.RceIssue.issue_type == issue_type.strip())
    if severity:
        stmt = stmt.where(m.RceIssue.severity == severity.strip().upper())
    if stage:
        wanted = stage.strip().upper()
        if wanted not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        stmt = stmt.where(stage_expr() == wanted)
    if status:
        stmt = stmt.where(m.RceIssue.resolution == status.strip().upper())
    if from_:
        stmt = stmt.where(m.RceIssue.created_at >= _parse_when(from_))
    if to:
        stmt = stmt.where(m.RceIssue.created_at <= _parse_when(to, end_of_day=True))
    if assigned_to:
        assignee = as_uuid(assigned_to)
        if assignee is None:
            raise ValueError("assigned_to must be a user id")
        held = select(reg.ReviewRecord.source_record_id).where(
            reg.ReviewRecord.assigned_to_user_id == assignee,
            reg.ReviewRecord.source_record_id.isnot(None))
        stmt = stmt.where(m.RceIssue.source_record_id.in_(held))
    if disposition:
        wanted = disposition.strip().upper()
        if wanted not in tm.DISPOSITIONS:
            raise ValueError(f"disposition must be one of {tm.DISPOSITIONS}")
        # The CURRENT disposition is the highest-sequence event per record
        # (the rce_current_dispositions view, expressed here in Core so it
        # composes with the rest of the statement). The outer EXISTS uses an
        # alias so the inner max() keeps rce_disposition_events as its own
        # FROM and correlates only on rce_issues.
        current_event = aliased(tm.RceDispositionEvent)
        latest_sequence = (
            select(func.max(tm.RceDispositionEvent.sequence))
            .where(tm.RceDispositionEvent.source_record_id == m.RceIssue.source_record_id)
            .correlate(m.RceIssue).scalar_subquery())
        current = (
            select(current_event.source_record_id)
            .where(current_event.source_record_id == m.RceIssue.source_record_id,
                   current_event.disposition == wanted,
                   current_event.sequence == latest_sequence)
            .correlate(m.RceIssue))
        stmt = stmt.where(current.exists())
    return stmt


async def _totals(db, filtered_subquery) -> Dict[str, Any]:
    sub = filtered_subquery
    rows_code = (await db.execute(
        select(sub.c.rule_id, func.count()).group_by(sub.c.rule_id))).all()
    rows_sev = (await db.execute(
        select(sub.c.severity, func.count()).group_by(sub.c.severity))).all()
    rows_res = (await db.execute(
        select(sub.c.resolution, func.count()).group_by(sub.c.resolution))).all()
    by_resolution = {str(r): int(n) for r, n in rows_res}
    open_count = sum(n for r, n in by_resolution.items() if r in OPEN_RESOLUTIONS)
    resolved = sum(n for r, n in by_resolution.items() if r in RESOLVED_RESOLUTIONS)
    return {
        "total": sum(by_resolution.values()),
        "open": open_count,
        "resolved": resolved,
        "by_code": {str(k): int(n) for k, n in rows_code},
        "by_severity": {str(k): int(n) for k, n in rows_sev},
        "by_resolution": by_resolution,
    }


async def _job_for_intake(db, intake_id):
    from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
    return (await db.execute(
        select(RceDeliveryJob.id).where(RceDeliveryJob.source_intake_id == intake_id)
        .order_by(RceDeliveryJob.created_at.desc()).limit(1))).scalar()


async def _disposition_histories(db, record_ids: Sequence[uuid.UUID]
                                 ) -> Dict[uuid.UUID, List[Dict[str, Any]]]:
    if not record_ids:
        return {}
    rows = (await db.execute(
        select(tm.RceDispositionEvent)
        .where(tm.RceDispositionEvent.source_record_id.in_(list(record_ids)))
        .order_by(tm.RceDispositionEvent.source_record_id,
                  tm.RceDispositionEvent.sequence))).scalars().all()
    out: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row.source_record_id, []).append(row.to_dict())
    return out


async def _identifier_events(db, issue_ids: Sequence[uuid.UUID]
                             ) -> Dict[uuid.UUID, List[Dict[str, Any]]]:
    if not issue_ids:
        return {}
    rows = (await db.execute(
        select(tm.TefcaIdentifierDecisionEvent)
        .where(tm.TefcaIdentifierDecisionEvent.issue_id.in_(list(issue_ids)))
        .order_by(tm.TefcaIdentifierDecisionEvent.issue_id,
                  tm.TefcaIdentifierDecisionEvent.sequence))).scalars().all()
    out: Dict[uuid.UUID, List[Dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row.issue_id, []).append(row.to_dict())
    return out


async def _assignees(db, record_ids: Sequence[uuid.UUID]) -> Dict[uuid.UUID, Dict[str, Any]]:
    """Who holds the DQ case for each delivered line, if anyone."""
    if not record_ids:
        return {}
    rows = (await db.execute(
        select(reg.ReviewRecord.source_record_id, reg.ReviewRecord.review_id,
               reg.ReviewRecord.assigned_to_user_id, reg.ReviewRecord.assigned_at)
        .where(reg.ReviewRecord.source_record_id.in_(list(record_ids)),
               reg.ReviewRecord.assigned_to_user_id.isnot(None)))).all()
    user_ids = {r.assigned_to_user_id for r in rows}
    emails: Dict[Any, str] = {}
    if user_ids:
        try:
            from app.models.database import User
            for uid, email in (await db.execute(
                    select(User.id, User.email).where(User.id.in_(list(user_ids))))).all():
                emails[uid] = email
        except Exception as exc:  # noqa: BLE001 - the id still identifies the holder
            logger.info("assignee email lookup skipped: %s", type(exc).__name__)
    return {r.source_record_id: {
        "user_id": str(r.assigned_to_user_id),
        "email": emails.get(r.assigned_to_user_id),
        "review_id": r.review_id,
        "assigned_at": _iso(r.assigned_at),
    } for r in rows}


IDENTIFIER_TYPES = ("npi", "tefcaid", "hcid", "aaid")


def _identifier_type(issue, latest_event) -> Optional[str]:
    """npi|tefcaid|hcid|aaid for an identifier-conflict row, else None.

    The identifier decision event names it authoritatively; a conflict row that
    has no event yet falls back to the finding's field name.
    """
    if latest_event and latest_event.get("identifier_type"):
        return str(latest_event["identifier_type"]).lower()
    if stage_for(issue.rule_id, issue.issue_type) != STAGE_PROMOTION:
        return None
    key = (issue.field_name or "").strip().lower()
    return key if key in IDENTIFIER_TYPES else None


def _normalized_value(row, field_name: Optional[str]):
    """The curated (Area 2) value for the field the finding is about."""
    if not field_name:
        return None
    key = field_name.strip().lower()
    aliases = {"npi": "curated_npi", "name": "curated_name"}
    attr = aliases.get(key)
    return getattr(row, attr, None) if attr else None


def _row(row, *, job_id, intake_id, histories, ident_events, assignees, legacy):
    issue = row.RceIssue
    events = ident_events.get(issue.id, [])
    latest_event = events[-1] if events else None
    history = histories.get(issue.source_record_id, [])
    latest_disposition = history[-1] if history else None
    legacy_types = legacy.get(issue.issue_type)
    entity_id = row.curated_entity_id or row.source_entity_id or (
        latest_event.get("entity_id") if latest_event else None)
    is_conflict = str(issue.issue_type or "").endswith("_CONFLICT")
    existing_value = (latest_event["existing_value"] if latest_event
                      else (issue.suggested_value if is_conflict else None))
    out = {
        "issue_id": str(issue.id),
        "issue_code": issue.issue_code,
        "delivery": {"job_id": str(job_id) if job_id else None,
                     "intake_id": str(intake_id)},
        "source_row": row.line_number,
        "source_record_id": str(issue.source_record_id) if issue.source_record_id else None,
        "source_rce_id": row.source_rce_id,
        "curated_record_id": str(row.curated_id) if row.curated_id else None,
        "entity_id": str(entity_id) if entity_id else None,
        "entity_name": row.curated_name,
        "identifier_type": _identifier_type(issue, latest_event),
        "record_status": row.record_status,
        "field_name": issue.field_name,
        "submitted_value": issue.original_value,
        "existing_value": existing_value,
        "normalized_value": _normalized_value(row, issue.field_name),
        "suggested_value": issue.suggested_value,
        "rule_code": issue.rule_id,
        "rule_version": issue.rule_version,
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "stage": row.stage,
        "description": issue.description,
        "correction_authority": issue.correction_authority,
        "reason_code": (latest_disposition or {}).get("reason_code"),
        "created_at": _iso(issue.created_at),
        "status": issue.resolution,
        "assignee": assignees.get(issue.source_record_id),
        "disposition": (latest_disposition or {}).get("disposition"),
        "disposition_history": history,
        "identifier_decisions": events,
        "actor": (issue.resolved_by
                  or (latest_event["actor"] if latest_event else None)),
        "decided_at": (_iso(issue.resolved_at)
                       or (latest_event["decided_at"] if latest_event else None)),
        "resolution_notes": issue.resolution_notes,
        "qa_approved_by": issue.qa_approved_by,
        "evidence_source": ((latest_event or {}).get("evidence_source")
                            or "rce_quality_rules"),
        "correlation_id": ((latest_event or {}).get("correlation_id")
                           or (latest_disposition or {}).get("correlation_id")),
        "build_sha": ((latest_event or {}).get("build_sha")
                      or (latest_disposition or {}).get("build_sha")),
    }
    if legacy_types:
        out["legacy_issue_type"] = issue.issue_type
        out["current_issue_types"] = list(legacy_types)
    return out


async def list_exceptions(db, intake_id, *, source_row: Optional[int] = None,
                          entity_name: Optional[str] = None, npi: Optional[str] = None,
                          rule_code: Optional[str] = None,
                          issue_type: Optional[str] = None,
                          severity: Optional[str] = None, stage: Optional[str] = None,
                          status: Optional[str] = None, from_: Optional[str] = None,
                          to: Optional[str] = None, assigned_to: Optional[str] = None,
                          disposition: Optional[str] = None, all_runs: bool = False,
                          limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    """The ledger page plus totals over the SAME filtered set (not just the page).

    Raises ValueError for a filter value outside its vocabulary; the route turns
    that into a 422.
    """
    intake_uuid = as_uuid(intake_id)
    if intake_uuid is None:
        raise ValueError("intake_id must be a uuid")
    stmt = _apply_filters(
        _base_statement(intake_uuid, all_runs=all_runs),
        source_row=source_row, entity_name=entity_name, npi=npi, rule_code=rule_code,
        issue_type=issue_type, severity=severity, stage=stage, status=status,
        from_=from_, to=to, assigned_to=assigned_to, disposition=disposition)

    totals = await _totals(db, stmt.subquery())
    page = (await db.execute(
        stmt.order_by(m.RceSourceRecord.line_number.nullslast(),
                      m.RceIssue.issue_code)
        .limit(limit).offset(offset))).all()

    record_ids = list({r.RceIssue.source_record_id for r in page
                       if r.RceIssue.source_record_id is not None})
    issue_ids = [r.RceIssue.id for r in page]
    histories = await _disposition_histories(db, record_ids)
    ident_events = await _identifier_events(db, issue_ids)
    assignees = await _assignees(db, record_ids)
    job_id = await _job_for_intake(db, intake_uuid)
    legacy = legacy_issue_types()

    items = [_row(r, job_id=job_id, intake_id=intake_uuid, histories=histories,
                  ident_events=ident_events, assignees=assignees, legacy=legacy)
             for r in page]
    return {
        "intake_id": str(intake_uuid),
        "job_id": str(job_id) if job_id else None,
        "run_scope": "all_runs" if all_runs else "current",
        "items": items,
        "count": len(items),
        "total": totals["total"],
        "offset": offset,
        "limit": limit,
        "totals": totals,
        "stage_derivation": {
            "PROMOTION": {"rules": list(PROMOTION_RULES), "issue_types": list(PROMOTION_TYPES)},
            "VERIFICATION": {"rules": list(VERIFICATION_RULES),
                             "issue_types": list(VERIFICATION_TYPES)},
            "QUALITY": "every other rule",
        },
    }


async def totals_for_intake(db, intake_id, *, all_runs: bool = False) -> Dict[str, Any]:
    """Counts only - what the job detail's `exceptions` block carries."""
    intake_uuid = as_uuid(intake_id)
    if intake_uuid is None:
        return {"total": 0, "open": 0, "resolved": 0, "by_code": {}, "by_severity": {},
                "by_resolution": {}}
    return await _totals(db, _base_statement(intake_uuid, all_runs=all_runs).subquery())


# -- record-level dispositions ------------------------------------------------

_DISPOSITION_SQL = f"""
    SELECT d.id, d.intake_id, d.source_record_id,
           COALESCE(d.curated_record_id, c.id) AS curated_record_id, d.job_id,
           d.sequence, d.disposition, d.reason_code, d.reason, d.entity_id,
           d.changed_fields, d.actor, d.actor_type, d.decided_at,
           d.correlation_id, d.build_sha, d.reconstructed,
           s.line_number, s.source_rce_id, s.npi AS submitted_npi,
           c.record_status, c.name AS curated_name, c.npi AS curated_npi
    FROM {tm.CURRENT_DISPOSITIONS_VIEW} d
    JOIN rce_source_records s ON s.id = d.source_record_id
    LEFT JOIN rce_curated_records c ON c.source_record_id = d.source_record_id
    WHERE d.intake_id = CAST(:i AS uuid)
      AND (CAST(:disp AS TEXT) IS NULL OR d.disposition = :disp)
      AND (CAST(:row AS INTEGER) IS NULL OR s.line_number = :row)
      AND (CAST(:name AS TEXT) IS NULL OR c.name ILIKE :name)
      AND (CAST(:npi AS TEXT) IS NULL OR s.npi = :npi OR c.npi = :npi)
"""


async def list_dispositions(db, intake_id, *, disposition: Optional[str] = None,
                            source_row: Optional[int] = None,
                            entity_name: Optional[str] = None,
                            npi: Optional[str] = None,
                            limit: int = 200, offset: int = 0
                            ) -> Tuple[List[Dict[str, Any]], int]:
    """Current disposition per delivered line, filtered; returns (rows, total)."""
    intake_uuid = as_uuid(intake_id)
    if intake_uuid is None:
        raise ValueError("intake_id must be a uuid")
    if disposition and disposition.strip().upper() not in tm.DISPOSITIONS:
        raise ValueError(f"disposition must be one of {tm.DISPOSITIONS}")
    params = {
        "i": str(intake_uuid),
        "disp": disposition.strip().upper() if disposition else None,
        "row": int(source_row) if source_row is not None else None,
        "name": f"%{entity_name.strip()}%" if entity_name else None,
        "npi": npi.strip() if npi else None,
    }
    total = int((await db.execute(
        text(f"SELECT count(*) FROM ({_DISPOSITION_SQL}) x"), params)).scalar() or 0)
    rows = (await db.execute(
        text(f"{_DISPOSITION_SQL} ORDER BY s.line_number LIMIT :lim OFFSET :off"),
        {**params, "lim": limit, "off": offset})).mappings().all()
    return [_jsonable(dict(r)) for r in rows], total


DISPOSITION_CSV_COLUMNS = (
    "line_number", "source_rce_id", "curated_name", "submitted_npi", "curated_npi",
    "record_status", "disposition", "reason_code", "reason", "entity_id",
    "changed_fields", "sequence", "actor", "actor_type", "decided_at",
    "correlation_id", "build_sha", "reconstructed",
)


def dispositions_csv(rows: Iterable[Dict[str, Any]]) -> str:
    from app.reports.engine.csv_engine import neutralise_row

    """RFC 4180 CSV of the disposition rows, columns fixed by DISPOSITION_CSV_COLUMNS."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(DISPOSITION_CSV_COLUMNS)
    for row in rows:
        out = []
        for col in DISPOSITION_CSV_COLUMNS:
            value = row.get(col)
            if isinstance(value, list):
                value = ";".join(str(v) for v in value)
            elif isinstance(value, dict):
                value = str(value)
            out.append("" if value is None else value)
        writer.writerow(neutralise_row(out))
    return buf.getvalue()
