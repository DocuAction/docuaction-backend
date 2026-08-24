"""
RCE pipeline API.

AREA 1 HAS NO MUTATING ROUTE
There is no PUT, PATCH or DELETE for a delivery or a source record anywhere in
this module. That is the second of the four immutability layers described in
`repository.py`, and it is enforced by absence rather than by a guard clause —
a route that does not exist cannot be called with the wrong arguments.

Issues DO mutate: an analyst resolving one is the workflow. Corrections mutate
Area 2, never Area 1.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tefca/rce", tags=["TEFCA RCE Pipeline"])


def _client_ip(request: Request):
    from app.core.security import get_client_ip
    return get_client_ip(request)


# ── P2 — deliveries ──────────────────────────────────────────────────────────

@router.post("/deliveries", summary="Upload an RCE delivery into immutable Area 1")
async def upload_delivery(
    request: Request,
    file: UploadFile = File(...),
    delivery_label: Optional[str] = Query(None),
    delimiter: Optional[str] = Query(
        None, description="Declare the delimiter explicitly: | , or tab. "
                          "Omit to detect."),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("contributor")),
):
    """Accept a delivery. Every line lands in Area 1 or the intake aborts.

    A byte-identical re-delivery is ACCEPTED as its own intake and linked to the
    earlier one — ONC may legitimately resend, and a rejected re-delivery would
    leave no record that it arrived.
    """
    from app.api.routes import _scan_upload_or_reject
    from app.tefca_registry.rce.intake import IntakeError, ingest_delivery

    raw = await file.read()
    extension = (file.filename or "").rsplit(".", 1)[-1].lower() \
        if "." in (file.filename or "") else "csv"
    await _scan_upload_or_reject(db, user, request, raw, file.filename,
                                 extension, "rce_delivery")

    declared = {"pipe": "|", "comma": ",", "tab": "\t"}.get(
        (delimiter or "").lower(), delimiter)
    try:
        return await ingest_delivery(
            db, raw, filename=file.filename or "delivery",
            delivery_label=delivery_label, declared_delimiter=declared or None,
            received_by=getattr(user, "email", None) or "SYSTEM",
            source_metadata={"ip": _client_ip(request)})
    except IntakeError as exc:
        raise HTTPException(422, str(exc))


@router.get("/deliveries", summary="List deliveries")
async def list_deliveries(
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import repository as repo

    rows = await repo.list_intakes(db, limit=limit)
    return {"items": [{
        "id": str(r.id), "delivery_label": r.delivery_label,
        "original_filename": r.original_filename, "sha256": r.sha256,
        "file_size_bytes": r.file_size_bytes, "record_count": r.record_count,
        "delimiter": r.delimiter, "encoding": r.encoding,
        "encoding_anomaly": r.encoding_anomaly, "status": r.status,
        "received_at": r.received_at, "received_by": r.received_by,
        "duplicate_content": r.duplicate_content,
        "duplicate_of_intake_id": (str(r.duplicate_of_intake_id)
                                   if r.duplicate_of_intake_id else None),
    } for r in rows]}


@router.get("/deliveries/{intake_id}", summary="Delivery metadata and provenance")
async def get_delivery(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import repository as repo

    intake = await repo.get_intake(db, intake_id)
    if intake is None:
        raise HTTPException(404, f"No delivery {intake_id}")
    return {
        "id": str(intake.id), "delivery_label": intake.delivery_label,
        "original_filename": intake.original_filename,
        "storage_path": intake.storage_path, "sha256": intake.sha256,
        "file_size_bytes": intake.file_size_bytes, "delimiter": intake.delimiter,
        "encoding": intake.encoding, "encoding_anomaly": intake.encoding_anomaly,
        "line_terminator": intake.line_terminator, "headers": intake.headers,
        "schema_fingerprint": intake.schema_fingerprint,
        "record_count": intake.record_count, "status": intake.status,
        "received_at": intake.received_at, "received_by": intake.received_by,
        "source_metadata": intake.source_metadata,
        "duplicate_content": intake.duplicate_content,
        "duplicate_of_intake_id": (str(intake.duplicate_of_intake_id)
                                   if intake.duplicate_of_intake_id else None),
        "counts": {
            "source_records": await repo.count_source_records(db, intake.id),
            "promoted": await repo.count_source_records(
                db, intake.id, promotion_status="promoted"),
        },
    }


@router.get("/deliveries/{intake_id}/records", summary="Immutable source records")
async def list_records(
    intake_id: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    parse_status: Optional[str] = None,
    promotion_status: Optional[str] = None,
    include_raw: bool = Query(False, description="Include the raw delivered line"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce import repository as repo

    rows = await repo.list_source_records(
        db, intake_id, limit=limit, offset=offset,
        parse_status=parse_status, promotion_status=promotion_status)
    return {"items": [{
        "id": str(r.id), "line_number": r.line_number,
        "record_sha256": r.record_sha256, "source_rce_id": r.source_rce_id,
        "tefcaid": r.tefcaid, "hcid": r.hcid, "npi": r.npi,
        "field_count": r.field_count, "parse_status": r.parse_status,
        "parse_note": r.parse_note, "promotion_status": r.promotion_status,
        "canonical_entity_id": (str(r.canonical_entity_id)
                                if r.canonical_entity_id else None),
        "parsed": r.parsed,
        **({"raw_line": r.raw_line} if include_raw else {}),
    } for r in rows], "count": len(rows), "offset": offset}


@router.get("/deliveries/{intake_id}/integrity",
            summary="Re-verify Area 1 has not been modified")
async def verify_integrity(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Re-hash the stored raw lines and the preserved file; report the verdict.

    VIEWER, deliberately. "Is the delivered evidence still what was delivered"
    is the question a reviewer or auditor most needs answered, and gating it
    above viewer would make it invisible to the role that exists to read
    evidence — the shape `test_no_tefca_read_endpoint_sits_above_the_viewer_floor`
    exists to prevent.

    What is NOT returned here: the storage path, the database role name, its
    privilege grid, and the remediation SQL. Those are infrastructure details
    that motivated an earlier reviewer-level floor; removing them is a better
    answer than raising the floor, because it lets the verdict reach the people
    who need it without disclosing how the store is laid out.
    `GET /deliveries/{id}` already carries the storage path for operators.
    """
    from app.tefca_registry.rce import repository as repo

    hashes = await repo.verify_record_hashes(db, intake_id)
    stored = await repo.verify_stored_file(db, intake_id)
    immutability = await repo.verify_immutable(db)
    return {
        "records_checked": hashes["records_checked"],
        "record_hash_mismatches": hashes["mismatches"],
        "raw_lines_intact": hashes["intact"],
        "original_file_checked": stored.get("checked", False),
        "original_file_intact": stored.get("intact"),
        "database_immutability_enforced": immutability.get("enforced"),
        "immutability_note": immutability.get("note"),
        "verdict": (
            "Area 1 is intact: every stored raw line still hashes to the value "
            "recorded at intake, and the preserved original file is unchanged."
            if hashes["intact"] and stored.get("intact") is not False
            else "Area 1 integrity check FAILED — see the mismatch counts."
        ),
    }


# ── P3/P4 — processing ───────────────────────────────────────────────────────

@router.post("/deliveries/{intake_id}/quality-run",
             summary="Run the data-quality rule set over a delivery")
async def run_quality(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("contributor")),
):
    from app.tefca_registry.rce.quality_engine import run_quality_engine

    return await run_quality_engine(
        db, intake_id, executed_by=getattr(user, "email", None) or "SYSTEM")


@router.get("/deliveries/{intake_id}/runs", summary="Ingestion runs and rule history")
async def list_runs(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from sqlalchemy import select
    from app.tefca_registry.rce import models as m

    runs = (await db.execute(
        select(m.RceIngestionRun)
        .where(m.RceIngestionRun.source_intake_id == intake_id)
        .order_by(m.RceIngestionRun.started_at.desc()))).scalars().all()
    out = []
    for run in runs:
        rules = (await db.execute(
            select(m.RceRuleExecutionHistory)
            .where(m.RceRuleExecutionHistory.run_id == run.id)
            .order_by(m.RceRuleExecutionHistory.rule_id))).scalars().all()
        out.append({
            "id": str(run.id), "rule_set_version": run.rule_set_version,
            "rule_config_hash": run.rule_config_hash,
            "field_map_version": run.field_map_version,
            "started_at": run.started_at, "completed_at": run.completed_at,
            "records_evaluated": run.records_evaluated,
            "issues_generated": run.issues_generated,
            "run_status": run.run_status, "executed_by": run.executed_by,
            "rules": [{
                "rule_id": r.rule_id, "rule_version": r.rule_version,
                "category": r.rule_category,
                "records_evaluated": r.records_evaluated,
                "issues_generated": r.issues_generated,
                "execution_status": r.execution_status,
                "duration_ms": r.execution_duration_ms, "error": r.error,
            } for r in rules],
        })
    return {"items": out}


# ── P5 — Issue Ledger ────────────────────────────────────────────────────────

@router.get("/deliveries/{intake_id}/issues", summary="Issue Ledger for a delivery")
async def list_issues(
    intake_id: str,
    severity: Optional[str] = None,
    resolution: Optional[str] = None,
    rule_id: Optional[str] = None,
    correction_authority: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from sqlalchemy import select
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.quality_engine import issue_summary

    stmt = select(m.RceIssue).where(m.RceIssue.source_intake_id == intake_id)
    for column, value in (("severity", severity), ("resolution", resolution),
                          ("rule_id", rule_id),
                          ("correction_authority", correction_authority)):
        if value:
            stmt = stmt.where(getattr(m.RceIssue, column) == value)
    rows = (await db.execute(
        stmt.order_by(m.RceIssue.issue_code).limit(limit).offset(offset)
    )).scalars().all()
    return {
        "summary": await issue_summary(db, intake_id),
        "items": [{
            "id": str(r.id), "issue_code": r.issue_code,
            "source_record_id": str(r.source_record_id) if r.source_record_id else None,
            "rule_id": r.rule_id, "rule_version": r.rule_version,
            "issue_type": r.issue_type, "severity": r.severity,
            "field_name": r.field_name, "original_value": r.original_value,
            "suggested_value": r.suggested_value,
            "suggested_confidence": r.suggested_confidence,
            "correction_authority": r.correction_authority,
            "description": r.description, "resolution": r.resolution,
            "resolved_by": r.resolved_by, "resolved_at": r.resolved_at,
            "qa_approved_by": r.qa_approved_by,
        } for r in rows],
        "count": len(rows), "offset": offset,
    }


class IssueTransition(BaseModel):
    to_status: str = Field(..., description="PROPOSED | UNDER_REVIEW | APPROVED | "
                                            "REJECTED | WAIVED | RESOLVED")
    notes: Optional[str] = None
    qa_actor: Optional[str] = Field(
        None, description="Required for QA_REQUIRED issues, and must differ "
                          "from the reviewer.")
    apply_now: bool = Field(
        False, description="Apply the correction immediately after APPROVED.")


@router.patch("/issues/{issue_id}", summary="Resolve or acknowledge an issue")
async def patch_issue(
    issue_id: str,
    body: IssueTransition,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    from app.tefca_registry.rce.curation import (
        CorrectionRefused, apply_correction, transition_issue)

    actor = getattr(user, "email", None) or "SYSTEM"
    try:
        result = await transition_issue(
            db, issue_id, to_status=body.to_status, actor=actor,
            notes=body.notes, qa_actor=body.qa_actor)
        if body.apply_now and body.to_status == "APPROVED":
            result["applied"] = await apply_correction(db, issue_id, actor=actor)
        return result
    except CorrectionRefused as exc:
        raise HTTPException(409, str(exc))


# ── P6/P7 — curation ─────────────────────────────────────────────────────────

@router.post("/deliveries/{intake_id}/curate",
             summary="Build Area 2 and apply AUTO_SAFE corrections")
async def curate(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("contributor")),
):
    from app.tefca_registry.rce.curation import curate_delivery

    return await curate_delivery(
        db, intake_id, curated_by=getattr(user, "email", None) or "SYSTEM")


@router.get("/deliveries/{intake_id}/curated", summary="Curated Working Dataset")
async def list_curated(
    intake_id: str,
    record_status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from sqlalchemy import select
    from app.tefca_registry.rce import models as m

    stmt = select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == intake_id)
    if record_status:
        stmt = stmt.where(m.RceCuratedRecord.record_status == record_status)
    rows = (await db.execute(
        stmt.order_by(m.RceCuratedRecord.rce_org_oid).limit(limit).offset(offset)
    )).scalars().all()
    return {"items": [{
        "id": str(r.id), "source_record_id": str(r.source_record_id),
        "record_status": r.record_status, "status_reason": r.status_reason,
        "issue_count": r.issue_count, "correction_count": r.correction_count,
        "rce_org_oid": r.rce_org_oid, "tefcaid": r.tefcaid, "hcid": r.hcid,
        "npi": r.npi, "name": r.name, "entity_level": r.entity_level,
        "sequoia_org_type": r.sequoia_org_type,
        "org_node_type": r.org_node_type,
        "operational_status": r.operational_status,
        "is_test_record": r.is_test_record,
        "exchange_purposes": r.exchange_purposes,
        "canonical_entity_id": (str(r.canonical_entity_id)
                                if r.canonical_entity_id else None),
        "transformation_version": r.transformation_version,
    } for r in rows], "count": len(rows), "offset": offset}


@router.get("/curated/{curated_id}/lineage",
            summary="Full lineage: Area 1 → issues → corrections → Area 2 → registry")
async def lineage(
    curated_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from sqlalchemy import select
    from app.tefca_registry.rce import models as m

    curated = await db.get(m.RceCuratedRecord, curated_id)
    if curated is None:
        raise HTTPException(404, f"No curated record {curated_id}")
    source = await db.get(m.RceSourceRecord, curated.source_record_id)
    issues = (await db.execute(
        select(m.RceIssue).where(
            m.RceIssue.source_record_id == curated.source_record_id))).scalars().all()
    corrections = (await db.execute(
        select(m.RceCorrectionDetail).where(
            m.RceCorrectionDetail.curated_record_id == curated.id))).scalars().all()
    return {
        "area1": {
            "id": str(source.id), "line_number": source.line_number,
            "raw_line": source.raw_line, "record_sha256": source.record_sha256,
            "parse_status": source.parse_status,
        } if source else None,
        "issues": [{"issue_code": i.issue_code, "rule_id": i.rule_id,
                    "severity": i.severity, "resolution": i.resolution,
                    "correction_authority": i.correction_authority}
                   for i in issues],
        "corrections": [{"column": c.column_name,
                         "original_value": c.original_value,
                         "corrected_value": c.corrected_value,
                         "authority": c.correction_authority,
                         "reason": c.correction_reason,
                         "corrected_by": c.corrected_by,
                         "approval_actor": c.approval_actor}
                        for c in corrections],
        "area2": {"id": str(curated.id), "record_status": curated.record_status,
                  "transformation_version": curated.transformation_version},
        "registry_entity_id": (str(curated.canonical_entity_id)
                               if curated.canonical_entity_id else None),
    }


# ── P8 — promotion ───────────────────────────────────────────────────────────

@router.post("/deliveries/{intake_id}/promote",
             summary="Promote approved Area 2 records to the canonical registry")
async def promote(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    from app.tefca_registry.rce.promotion import promote_delivery

    try:
        return await promote_delivery(
            db, intake_id, actor=getattr(user, "email", None) or "SYSTEM",
            actor_id=getattr(user, "id", None))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


# ── P9/P10 — verification and classification ─────────────────────────────────

class VerifyRequest(BaseModel):
    entity_refs: List[str] = Field(default_factory=list)
    limit: int = Field(10, ge=1, le=500)


@router.post("/deliveries/{intake_id}/verify",
             summary="Run D1-D6, B1-B4 and tier routing over promoted entities")
async def verify(
    intake_id: str,
    body: VerifyRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("contributor")),
):
    from sqlalchemy import select
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    refs = list(body.entity_refs)
    if not refs:
        refs = list((await db.execute(
            select(m.RceCuratedRecord.rce_org_oid)
            .where(m.RceCuratedRecord.source_intake_id == intake_id,
                   m.RceCuratedRecord.canonical_entity_id.isnot(None))
            .order_by(m.RceCuratedRecord.rce_org_oid)
            .limit(body.limit))).scalars().all())
    return await verify_and_classify(
        db, refs, intake_id=intake_id,
        actor=getattr(user, "email", None) or "SYSTEM")


# ── P12 — reconciliation ─────────────────────────────────────────────────────

@router.get("/deliveries/{intake_id}/reconciliation",
            summary="Hard reconciliation gate: A–F populations must close exactly")
async def reconciliation(
    intake_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.tefca_registry.rce.reconciliation import reconcile_delivery

    return await reconcile_delivery(db, intake_id)


# ── P0/P1 — profile and field map ────────────────────────────────────────────

@router.get("/field-map", summary="The locked 41-field RCE mapping")
async def field_map(user=Depends(require_role("viewer"))):
    from app.tefca_registry.rce import field_map as fm

    return {
        "version": fm.FIELD_MAP_VERSION,
        "profiled_file": fm.PROFILED_FILE,
        "profiled_sha256": fm.PROFILED_SHA256,
        "profiled_record_count": fm.PROFILED_RECORD_COUNT,
        "expected_schema_fingerprint": fm.EXPECTED_SCHEMA_FINGERPRINT,
        "field_count": fm.RCE_FIELD_COUNT,
        "fields": fm.mapping_table(),
        "empty_in_delivery": fm.empty_in_delivery(),
        "observed_vocabularies": {
            "sequoiaorgtype": list(fm.OBSERVED_SEQUOIA_ORG_TYPES),
            "organizationNodeType": list(fm.OBSERVED_ORG_NODE_TYPES),
            "hl7orgrole": list(fm.OBSERVED_HL7_ORG_ROLES),
            "purposesofuse_tokens": list(fm.OBSERVED_PURPOSE_TOKENS),
            "qhin_oids": list(fm.OBSERVED_QHIN_OIDS),
        },
        "note": ("OBSERVED facts, DOCUMENTED meaning and DOCUACTION "
                 "interpretation are carried separately on every field. A "
                 "column name is never treated as its definition."),
    }
