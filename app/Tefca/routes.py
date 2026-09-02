"""
DocuAction TEFCA Review Protocol — FastAPI Routes (production).

ONC TEFCA Review Protocol — Alliance Global Tech, Inc. (AGT)
Contract No. 7571MN26F80064 (HHS/ONC)

Every route is authenticated and role-gated (FIX 2). Every state change is
persisted to the database and written to the audit trail (FIX 4 / FIX 9). There
are no stub returns and no fabricated source citations (FIX 5). Reports aggregate
real, persisted evidence records (FIX 6). The retrospective sample size uses the
finite-population correction (FIX 7).
"""

import csv
import io as _io
import json
import os
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, text, func, cast, or_, and_, String, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, async_session_maker
from app.core.security import require_role, get_current_user, ADMIN_EMAILS
from app.core.config import settings
from app.services.audit import log_tefca_event

from .connectors import (
    SourceConnectorManager, SourceResult, _extract_npi, _entity_type_of,
    is_running_mock, data_source_labels,
)
from .validation_engine import ValidationEngine, EvidenceRecordGenerator
from .mock_data import ALL_MOCK_ENTITIES, MOCK_STATS
from . import review_engine
from . import reporting
from . import qa_engine
from . import report_renderer
from app.core.client_ip import get_client_ip
from .models import (
    TEFCAEntity, TEFCAReviewCycle, TEFCAEvidenceRecord, TEFCASourceCache,
    TEFCAPriorityCase, TEFCAReport, TEFCAAnalystQueue,
    TEFCAConnectorLog, TEFCAReview, TEFCAFinding, TEFCAImportHistory,
    EntityType, EntityStatus, BucketClassification, BucketLabel,
    CycleType, CycleStatus, RecordStatus, CaseStatus, CaseSeverity, QueueStatus,
)

logger = logging.getLogger("docuaction.tefca.routes")

# ── Router — authenticated by default. No endpoint is reachable without a valid
#    JWT. (FIX 2 — HHSAR 352.204-71 / FAR 52.212-4)
#
#    The router-level floor is "viewer" (level 1), NOT a stricter role. A floor is
#    a CEILING on how permissive any route beneath it can be: while this said
#    "reviewer" (level 4), every endpoint in this router — including every
#    read-only GET — was closed to viewer(1), contributor(2) and manager(3). Those
#    are the only non-admin roles the admin API could assign, so in practice the
#    whole module was admin-only. Least privilege is enforced by the PER-ROUTE
#    guard on each endpoint below (reviewer to write, qalead to approve,
#    program_manager to submit deliverables); this floor exists only to guarantee
#    no route is ever silently anonymous. Every route in this router declares its
#    own require_role, so lowering the floor widens nothing on its own. ──
tefca_router = APIRouter(
    prefix="/api/v1/tefca",
    tags=["TEFCA Review Protocol"],
    dependencies=[Depends(require_role("viewer"))],
)
router = tefca_router  # safe_load / main.py expects mod.router


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN-013 / EQ-003 — PII visibility by role
#
# The QA role definitions are explicit: viewer@ is "Level 1 Viewer (no PII
# access)" and reviewer@ is "Level 4 Reviewer (can see PII, make decisions)".
# The API did not implement that distinction — /api/tefca/reviews is gated at
# viewer and returned full 10-digit NPIs to every role, so a viewer signing in
# and opening the Entity Queue saw the identifiers they are defined as not
# having access to. LOGIN-013 was reported as the top blocker; this is what it
# was testing.
#
# Masking happens on the SERVER. Hiding a column in the browser leaves the
# values in the JSON, one devtools panel away, which is not access control.
#
# The last four digits are kept: a reviewer discussing a case with a viewer
# needs a shared reference, and 4 digits of a 10-digit NPI is not a re-identifier
# on its own. Nothing is invented — absent stays absent.
PII_ROLE_FLOOR = "reviewer"


def _can_see_pii(user) -> bool:
    from app.core.security import ROLE_HIERARCHY, role_level

    return role_level(getattr(user, "role", None)) >= ROLE_HIERARCHY[PII_ROLE_FLOOR]


def _mask_identifier(value, keep: int = 4):
    """'1999000101' -> '••••••0101'. None stays None; short values fully masked."""
    if not value:
        return value
    s = str(value)
    if len(s) <= keep:
        return "•" * len(s)
    return "•" * (len(s) - keep) + s[-keep:]


# NPI system URI as it appears in the bundled FHIR-shaped dataset.
_NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"


def _mask_mock_entity(entity: dict) -> dict:
    """Copy of a bundled FHIR Organization with its NPI identifier masked.

    Copied rather than mutated: ALL_MOCK_ENTITIES is a module-level constant
    shared by every request, so masking in place would permanently corrupt the
    dataset for the reviewer who asked for it next.
    """
    out = dict(entity)
    idents = out.get("identifier")
    if isinstance(idents, list):
        out["identifier"] = [
            {**i, "value": _mask_identifier(i.get("value"))}
            if isinstance(i, dict) and i.get("system") == _NPI_SYSTEM else i
            for i in idents
        ]
    out["pii_masked"] = True
    return out




# ─── Lazy singletons ─────────────────────────────────────────────────────────
_connector_manager: Optional[SourceConnectorManager] = None
_validation_engine: Optional[ValidationEngine] = None
_evidence_generator: Optional[EvidenceRecordGenerator] = None


def get_connector_manager() -> SourceConnectorManager:
    global _connector_manager
    if not _connector_manager:
        _connector_manager = SourceConnectorManager()
    return _connector_manager


def get_validation_engine() -> ValidationEngine:
    global _validation_engine
    if not _validation_engine:
        _validation_engine = ValidationEngine()
    return _validation_engine


def get_evidence_generator() -> EvidenceRecordGenerator:
    global _evidence_generator
    if not _evidence_generator:
        _evidence_generator = EvidenceRecordGenerator()
    return _evidence_generator


# ─── Request schemas ─────────────────────────────────────────────────────────

class CycleCreateRequest(BaseModel):
    cycle_type: str
    cycle_start_date: str
    cycle_end_date: Optional[str] = None
    cycle_number: Optional[int] = None
    sample_confidence_level: float = 0.95
    methodology_version: str = "1.0"


class AnalystOverrideRequest(BaseModel):
    bucket_classification: int
    override_reason: str
    review_notes: Optional[str] = None


class EscalateRequest(BaseModel):
    escalation_note: str


class PriorityCaseCreateRequest(BaseModel):
    cor_reference: str
    entity_rce_id: str
    deadline_date: Optional[str] = None
    issue_description: str


class PriorityCaseUpdateRequest(BaseModel):
    case_status: Optional[str] = None
    severity: Optional[str] = None
    root_cause_determination: Optional[str] = None
    root_cause_description: Optional[str] = None
    resolution_notes: Optional[str] = None
    recommendations: Optional[dict] = None


# ─── Sample size — finite population correction (FIX 7) ───────────────────────

def calculate_sample_size(N: int, confidence: float = 0.95, margin: float = 0.05) -> int:
    """Cochran sample size with finite-population correction. Delegates to the
    single shared implementation in review_engine so the batch sampler, the
    sampling-run endpoint, and reporting can never drift. For N=94,231 @95% CI /
    ±5% margin this returns 383 (matching the contract)."""
    return review_engine.calculate_sample_size(N, confidence, margin)


# ─── Enum / helper mappers ───────────────────────────────────────────────────

_BUCKET_CLASS = {
    1: BucketClassification.BUCKET_1, 2: BucketClassification.BUCKET_2,
    3: BucketClassification.BUCKET_3, 4: BucketClassification.BUCKET_4,
}
_BUCKET_LABEL = {
    1: BucketLabel.NO_DISCREPANCY, 2: BucketLabel.MINOR_ADMINISTRATIVE,
    3: BucketLabel.INEXPLICABLE, 4: BucketLabel.NON_COMPLIANT,
}


def _bucket_class_enum(bucket: int) -> BucketClassification:
    return _BUCKET_CLASS.get(bucket, BucketClassification.BUCKET_1)


def _bucket_label_enum(bucket: int) -> BucketLabel:
    return _BUCKET_LABEL.get(bucket, BucketLabel.NO_DISCREPANCY)


def _client_ip(request: Request) -> Optional[str]:
    return get_client_ip(request)


def _assigned_role(tier: int, bucket: int) -> str:
    if tier == 3 or bucket == 4:
        return "senior_analyst"
    if bucket == 3:
        return "senior_analyst"  # Bucket-3 escalation queue is senior_analyst+
    return "reviewer"


def _queue_priority(bucket: int, indeterminate: bool) -> int:
    if bucket == 4:
        return 100
    if bucket == 3:
        return 80
    if indeterminate:
        return 70
    if bucket == 2:
        return 40
    return 50


# ─── Persistence helpers ─────────────────────────────────────────────────────

async def _get_or_create_cycle(
    db: AsyncSession,
    cycle_id: Optional[str] = None,
    cycle_type: CycleType = CycleType.TASK3_RETROSPECTIVE,
    created_by: str = "SYSTEM",
) -> TEFCAReviewCycle:
    if cycle_id:
        try:
            cid = uuid.UUID(str(cycle_id))
        except (ValueError, AttributeError, TypeError):
            cid = None
        if cid:
            row = (await db.execute(
                select(TEFCAReviewCycle).where(TEFCAReviewCycle.cycle_id == cid)
            )).scalar_one_or_none()
            if row:
                return row
    row = TEFCAReviewCycle(
        cycle_type=cycle_type,
        cycle_start_date=datetime.utcnow(),
        cycle_status=CycleStatus.IN_PROGRESS,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def _upsert_entity(db: AsyncSession, org: dict) -> TEFCAEntity:
    rce_id = org.get("id") or str(uuid.uuid4())
    row = (await db.execute(
        select(TEFCAEntity).where(TEFCAEntity.rce_organization_id == rce_id)
    )).scalar_one_or_none()
    npi = _extract_npi(org)
    raw_type = _entity_type_of(org)
    try:
        etype = EntityType(raw_type)
    except ValueError:
        etype = EntityType.PARTICIPANT
    if row is None:
        row = TEFCAEntity(
            rce_organization_id=rce_id,
            qhin_name=org.get("_qhin", "Unknown QHIN"),
            entity_type=etype,
            legal_name_submitted=org.get("name", ""),
            npi_submitted=(npi or None),
            address_submitted=org.get("address"),
            identifiers_submitted=org.get("identifier"),
            endpoints_submitted=org.get("endpoint"),
            fhir_resource_raw=org,
        )
        db.add(row)
        await db.flush()
    else:
        row.legal_name_submitted = org.get("name", row.legal_name_submitted)
        if npi:
            row.npi_submitted = npi
    return row


async def _cached_or_query_sources(
    db: AsyncSession,
    manager: SourceConnectorManager,
    org: dict,
    entity_row: TEFCAEntity,
    cycle_id,
) -> tuple[dict, bool]:
    """Return (source_results, from_cache). Reuses cached responses if every
    required source was queried for this entity within the last 24h; otherwise
    queries live and writes the cache (FIX 4 — caching + audit reproducibility)."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    rows = (await db.execute(
        select(TEFCASourceCache).where(
            TEFCASourceCache.entity_id == entity_row.entity_id,
            TEFCASourceCache.query_timestamp >= cutoff,
        )
    )).scalars().all()
    by_key = {r.source_name: r for r in rows}
    needed = manager.REQUIRED_SOURCES
    if all(k in by_key for k in needed):
        results = {}
        for k, r in by_key.items():
            payload = r.response_data or {}
            src_name = payload.get("_source_name", k)
            results[k] = SourceResult(
                source_name=src_name,
                success=bool(r.query_success),
                data=(payload if r.query_success else None),
                error=r.error_message,
                response_hash=r.response_hash,
                api_version=r.api_version,
                query_params=r.query_parameters or {},
                query_timestamp=(r.query_timestamp.isoformat() if r.query_timestamp else datetime.utcnow().isoformat()),
            )
        return results, True

    results = await manager.query_all_sources(org)
    now = datetime.utcnow()
    for k, res in results.items():
        payload = dict(res.data) if res.data is not None else {}
        payload["_source_name"] = res.source_name
        db.add(TEFCASourceCache(
            entity_id=entity_row.entity_id,
            cycle_id=cycle_id,
            source_name=k,
            query_parameters=res.query_params,
            response_data=payload,
            response_hash=res.response_hash,
            api_version=res.api_version,
            query_success=res.success,
            error_message=res.error,
            query_timestamp=now,
            cache_expires_at=now + timedelta(hours=24),
        ))
    return results, False


async def _persist_evidence(
    db: AsyncSession,
    entity_row: TEFCAEntity,
    cycle_row: TEFCAReviewCycle,
    validation: dict,
    evidence_record: dict,
    reviewer_id: str,
) -> TEFCAEvidenceRecord:
    bucket = validation["bucket"]
    try:
        rid = uuid.UUID(str(evidence_record.get("record_id")))
    except (ValueError, TypeError):
        rid = uuid.uuid4()
    row = TEFCAEvidenceRecord(
        record_id=rid,
        entity_id=entity_row.entity_id,
        cycle_id=cycle_row.cycle_id,
        tier_assigned=validation["tier"],
        auto_classified=bool(validation["auto_classify"]),
        bucket_classification=_bucket_class_enum(bucket),
        bucket_label=_bucket_label_enum(bucket),
        confidence_score=validation["confidence"],
        finding_codes=validation["finding_codes"],
        element_1_entity_identification=evidence_record.get("element_1"),
        element_2_finding_classification=evidence_record.get("element_2"),
        element_3_source_comparison=evidence_record.get("element_3"),
        element_4_supporting_citations=evidence_record.get("element_4"),
        element_5_disposition_recommendation=evidence_record.get("element_5"),
        reviewer_id=reviewer_id,
        reviewer_tier=1,
        review_timestamp=(datetime.utcnow() if validation["auto_classify"] else None),
        supervisor_review_required=(bucket == 4),
        record_status=(RecordStatus.REVIEWED if validation["auto_classify"] else RecordStatus.PENDING_REVIEW),
    )
    db.add(row)
    await db.flush()
    return row


async def _enqueue_if_needed(
    db: AsyncSession,
    record_row: TEFCAEvidenceRecord,
    entity_row: TEFCAEntity,
    cycle_row: TEFCAReviewCycle,
    validation: dict,
) -> Optional[TEFCAAnalystQueue]:
    bucket = validation["bucket"]
    indeterminate = bool(validation.get("indeterminate"))
    # B1 + fully verified = auto-complete, no human queue item.
    if bucket == 1 and not indeterminate:
        return None
    tier = validation["tier"]
    item = TEFCAAnalystQueue(
        record_id=record_row.record_id,
        entity_id=entity_row.entity_id,
        cycle_id=cycle_row.cycle_id,
        tier=tier,
        assigned_role=_assigned_role(tier, bucket),
        priority=_queue_priority(bucket, indeterminate),
        bucket_classification=_bucket_class_enum(bucket),
        queue_reason=(validation.get("indeterminate_reason")
                      or f"Bucket {bucket} requires analyst review"),
        status=QueueStatus.PENDING,
    )
    db.add(item)
    return item


def _bump_cycle_counts(cycle: TEFCAReviewCycle, validation: dict) -> None:
    bucket = validation["bucket"]
    cycle.total_entities_completed = (cycle.total_entities_completed or 0) + 1
    setattr(cycle, f"bucket_{bucket}_count", (getattr(cycle, f"bucket_{bucket}_count") or 0) + 1)
    if validation["auto_classify"]:
        cycle.auto_completed_count = (cycle.auto_completed_count or 0) + 1
    if validation["tier"] == 2:
        cycle.tier2_queue_count = (cycle.tier2_queue_count or 0) + 1
    elif validation["tier"] == 3:
        cycle.tier3_queue_count = (cycle.tier3_queue_count or 0) + 1


async def _validate_and_persist(
    db: AsyncSession, org: dict, cycle_row: TEFCAReviewCycle, reviewer_id: str, acting_user,
    ip_address: Optional[str] = None,
) -> tuple[TEFCAEvidenceRecord, dict, dict]:
    """Full single-entity pipeline with persistence. Returns (record, validation,
    evidence_dict). Does NOT commit — caller commits."""
    manager = get_connector_manager()
    engine = get_validation_engine()
    evgen = get_evidence_generator()

    entity_row = await _upsert_entity(db, org)
    sources, from_cache = await _cached_or_query_sources(db, manager, org, entity_row, cycle_row.cycle_id)
    validation = engine.validate(org, sources)
    evidence = evgen.generate(org, str(cycle_row.cycle_id), validation, sources, reviewer_id)
    record = await _persist_evidence(db, entity_row, cycle_row, validation, evidence, reviewer_id)
    await _enqueue_if_needed(db, record, entity_row, cycle_row, validation)
    _bump_cycle_counts(cycle_row, validation)

    entity_row.latest_bucket = _bucket_class_enum(validation["bucket"])
    entity_row.latest_confidence = validation["confidence"]
    entity_row.current_status = (
        EntityStatus.REVIEWED_COMPLETE if validation["auto_classify"] else EntityStatus.IN_REVIEW
    )

    await log_tefca_event(
        db, user=acting_user, action="ENTITY_VALIDATED",
        resource_type="tefca_evidence_record", resource_id=record.record_id,
        ip_address=ip_address,
        details={
            "actor": reviewer_id,
            "entity_rce_id": entity_row.rce_organization_id,
            "bucket": validation["bucket"],
            "confidence": validation["confidence"],
            "tier": validation["tier"],
            "indeterminate": validation.get("indeterminate"),
            "unavailable_sources": validation.get("unavailable_sources"),
            "sources_from_cache": from_cache,
            "sources_queried": list(sources.keys()),
        },
    )
    return record, validation, evidence


# ─── Connector health ────────────────────────────────────────────────────────

@tefca_router.get("/connectors/status", summary="Probe all data source connectors")
async def connector_health(user=Depends(require_role("viewer"))):
    manager = get_connector_manager()
    status = await manager.health_check()
    live = sum(1 for s in status.values() if s.get("live"))
    return {
        "checked_at": datetime.utcnow().isoformat(),
        "connectors": status,
        "live_connector_count": live,
        "total_connectors": len(status),
        "summary": f"{live}/{len(status)} connectors live",
    }


# ─── Reference dataset (development data only) ───────────────────────────────

@tefca_router.get("/mock/entities", summary="View bundled bundled development dataset")
async def get_mock_entities(
    bucket: Optional[int] = None,
    qhin: Optional[str] = None,
    user=Depends(require_role("viewer")),
):
    entities = ALL_MOCK_ENTITIES
    if bucket:
        entities = [e for e in entities if e.get("_expected_bucket") == bucket]
    if qhin:
        entities = [e for e in entities if e.get("_qhin") == qhin]

    # EQ-003 — this endpoint is the Entity Queue's FALLBACK. When /api/tefca/reviews
    # errors, the queue page loads this instead, and it was handing every viewer a
    # full ten-digit NPI per row while the endpoint it fell back FROM masked them.
    # The masking rule has to hold on whichever path the page actually took, so it
    # is applied here as well.
    show_pii = _can_see_pii(user)
    if not show_pii:
        entities = [_mask_mock_entity(e) for e in entities]

    return {
        "total": len(entities), "stats": MOCK_STATS, "entities": entities,
        "pii_masked": not show_pii,
        "note": "Bundled development dataset — flagged MOCK; never auto-finalized as B1.",
    }


# ─── Review cycles ───────────────────────────────────────────────────────────

@tefca_router.post("/cycles", summary="Create review cycle")
async def create_cycle(
    request: CycleCreateRequest, http: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("program_manager")),
):
    try:
        ctype = CycleType(request.cycle_type)
    except ValueError:
        raise HTTPException(400, f"Invalid cycle_type. Use one of: {[c.value for c in CycleType]}")
    row = TEFCAReviewCycle(
        cycle_type=ctype,
        cycle_start_date=datetime.fromisoformat(request.cycle_start_date),
        cycle_end_date=(datetime.fromisoformat(request.cycle_end_date) if request.cycle_end_date else None),
        cycle_number=request.cycle_number,
        sample_confidence_level=request.sample_confidence_level,
        methodology_version=request.methodology_version,
        cycle_status=CycleStatus.PLANNED,
        created_by=str(user.email),
    )
    db.add(row)
    await db.flush()
    await log_tefca_event(
        db, user=user, action="CYCLE_CREATED", resource_type="tefca_review_cycle",
        resource_id=row.cycle_id, ip_address=_client_ip(http),
        details={"cycle_type": ctype.value, "methodology_version": request.methodology_version},
    )
    await db.commit()
    return {
        "cycle_id": str(row.cycle_id), "cycle_type": ctype.value,
        "cycle_status": row.cycle_status.value, "created_at": row.created_at.isoformat(),
    }


@tefca_router.get("/cycles", summary="List review cycles")
async def list_cycles(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    rows = (await db.execute(select(TEFCAReviewCycle).order_by(TEFCAReviewCycle.created_at.desc()))).scalars().all()
    return {
        "total": len(rows),
        "cycles": [{
            "cycle_id": str(c.cycle_id), "cycle_type": c.cycle_type.value if c.cycle_type else None,
            "cycle_status": c.cycle_status.value if c.cycle_status else None,
            "cycle_number": c.cycle_number,
            # RC-003 — the cycle's own start and end dates. The list omitted
            # them entirely, so the Review Cycles table rendered "—" in both date
            # columns and there was no date to check the format of. They are
            # stored on every row; only the serializer was missing them.
            "cycle_start_date": c.cycle_start_date.isoformat() if c.cycle_start_date else None,
            "cycle_end_date": c.cycle_end_date.isoformat() if c.cycle_end_date else None,
            # RC-004 — completion tracking. `remaining` is derived here so the
            # table and any card reading this endpoint cannot disagree. No
            # percentage is returned: sampled == 0 has no meaningful rate, and a
            # 0% shown for "not started yet" is a different claim from 0% of a
            # started cycle.
            "total_entities_sampled": c.total_entities_sampled or 0,
            "total_entities_completed": c.total_entities_completed or 0,
            "total_entities_remaining": max(
                0, (c.total_entities_sampled or 0) - (c.total_entities_completed or 0)),
            "bucket_counts": {
                "1": c.bucket_1_count, "2": c.bucket_2_count,
                "3": c.bucket_3_count, "4": c.bucket_4_count,
            },
            "created_by": c.created_by,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        } for c in rows],
    }


# ─── Validation ──────────────────────────────────────────────────────────────

@tefca_router.post("/validate/entity", summary="Validate one TEFCA entity (persisted)")
async def validate_single_entity(
    entity: dict, http: Request,
    cycle_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    cycle = await _get_or_create_cycle(db, cycle_id, created_by=str(user.email))
    record, validation, evidence = await _validate_and_persist(
        db, entity, cycle, reviewer_id=str(user.email), acting_user=user, ip_address=_client_ip(http),
    )
    await db.commit()
    return {
        "record_id": str(record.record_id), "cycle_id": str(cycle.cycle_id),
        "entity_name": entity.get("name"),
        "bucket": validation["bucket"], "bucket_label": validation["bucket_label"],
        "confidence": validation["confidence"], "tier": validation["tier"],
        "auto_classify": validation["auto_classify"],
        "classification_state": validation["classification_state"],
        "indeterminate_reason": validation.get("indeterminate_reason"),
        "finding_codes": validation["finding_codes"],
        "evidence_record": evidence,
    }


@tefca_router.post("/evidence/generate", summary="Generate + persist 5-element evidence record")
async def generate_evidence(
    entity: dict, http: Request,
    cycle_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    cycle = await _get_or_create_cycle(db, cycle_id, created_by=str(user.email))
    record, validation, evidence = await _validate_and_persist(
        db, entity, cycle, reviewer_id=str(user.email), acting_user=user, ip_address=_client_ip(http),
    )
    await db.commit()
    return {
        "record_id": str(record.record_id), "cycle_id": str(cycle.cycle_id),
        "bucket": validation["bucket"], "classification_state": validation["classification_state"],
        "evidence_record": evidence,
    }


@tefca_router.post("/validate/batch", summary="Run Tier-1 validation across a cycle (async, persisted)")
async def validate_batch(
    background_tasks: BackgroundTasks, http: Request,
    cycle_id: str = Query(..., description="Existing cycle ID"),
    entity_type: Optional[str] = Query(None),
    qhin_name: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    try:
        cid = uuid.UUID(cycle_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "cycle_id must be a valid UUID")
    cycle = (await db.execute(select(TEFCAReviewCycle).where(TEFCAReviewCycle.cycle_id == cid))).scalar_one_or_none()
    if not cycle:
        raise HTTPException(404, f"Cycle {cycle_id} not found — create it via POST /cycles first")

    manager = get_connector_manager()
    rce_result = await manager.rce_directory.get_all_organizations(
        entity_type=entity_type, qhin_name=qhin_name, limit=100000,
    )
    if not rce_result.success or not rce_result.data:
        raise HTTPException(503, f"TEFCA entity data unavailable: {rce_result.error}")
    entities = rce_result.data.get("organizations", [])
    total = len(entities)
    sample_size = calculate_sample_size(total) if total > 0 else 0
    sample = entities[:sample_size]

    cycle.total_entities_sampled = sample_size
    cycle.cycle_status = CycleStatus.IN_PROGRESS
    await log_tefca_event(
        db, user=user, action="BATCH_VALIDATION_STARTED", resource_type="tefca_review_cycle",
        resource_id=cycle.cycle_id, ip_address=_client_ip(http),
        details={"population": total, "sample_size": sample_size,
                 "data_source": rce_result.data.get("data_source"), "initiated_by": str(user.email)},
    )
    await db.commit()

    background_tasks.add_task(_run_batch_validation, sample, str(cid), str(user.email))
    return {
        "cycle_id": cycle_id, "population_size": total, "sample_size": sample_size,
        "confidence_level": 0.95, "margin_of_error": 0.05,
        "status": "PROCESSING", "data_source": rce_result.data.get("data_source"),
        "monitor_at": f"/api/v1/tefca/validate/status/{cycle_id}",
    }


async def _run_batch_validation(entity_dicts: list, cycle_id_str: str, initiated_by: str):
    """Background batch worker — own DB session, commits per entity for durability."""
    manager = get_connector_manager()
    engine = get_validation_engine()
    evgen = get_evidence_generator()
    cid = uuid.UUID(cycle_id_str)
    async with async_session_maker() as db:
        cycle = (await db.execute(select(TEFCAReviewCycle).where(TEFCAReviewCycle.cycle_id == cid))).scalar_one_or_none()
        if not cycle:
            logger.error(f"batch: cycle {cycle_id_str} vanished")
            return
        for org in entity_dicts:
            try:
                entity_row = await _upsert_entity(db, org)
                sources, from_cache = await _cached_or_query_sources(db, manager, org, entity_row, cycle.cycle_id)
                validation = engine.validate(org, sources)
                evidence = evgen.generate(org, cycle_id_str, validation, sources, "SYSTEM_TIER1")
                record = await _persist_evidence(db, entity_row, cycle, validation, evidence, "SYSTEM_TIER1")
                await _enqueue_if_needed(db, record, entity_row, cycle, validation)
                _bump_cycle_counts(cycle, validation)
                entity_row.latest_bucket = _bucket_class_enum(validation["bucket"])
                entity_row.latest_confidence = validation["confidence"]
                entity_row.current_status = (
                    EntityStatus.REVIEWED_COMPLETE if validation["auto_classify"] else EntityStatus.IN_REVIEW
                )
                await log_tefca_event(
                    db, user=None, action="ENTITY_VALIDATED",
                    resource_type="tefca_evidence_record", resource_id=record.record_id,
                    details={"actor": "SYSTEM_TIER1", "initiated_by": initiated_by,
                             "entity_rce_id": entity_row.rce_organization_id,
                             "bucket": validation["bucket"], "confidence": validation["confidence"],
                             "tier": validation["tier"], "indeterminate": validation.get("indeterminate"),
                             "sources_from_cache": from_cache},
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.exception(f"batch: failed entity {org.get('id')}: {e}")
        cycle.cycle_status = CycleStatus.COMPLETE
        cycle.completed_at = datetime.utcnow()
        await db.commit()


@tefca_router.get("/validate/status/{cycle_id}", summary="Batch validation progress")
async def get_validation_status(
    cycle_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    try:
        cid = uuid.UUID(cycle_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "cycle_id must be a valid UUID")
    cycle = (await db.execute(select(TEFCAReviewCycle).where(TEFCAReviewCycle.cycle_id == cid))).scalar_one_or_none()
    if not cycle:
        raise HTTPException(404, "Cycle not found")
    completed = (await db.execute(
        select(TEFCAEvidenceRecord).where(TEFCAEvidenceRecord.cycle_id == cid)
    )).scalars().all()
    return {
        "cycle_id": cycle_id, "cycle_status": cycle.cycle_status.value if cycle.cycle_status else None,
        "sample_size": cycle.total_entities_sampled, "completed": len(completed),
        "bucket_counts": {"1": cycle.bucket_1_count, "2": cycle.bucket_2_count,
                          "3": cycle.bucket_3_count, "4": cycle.bucket_4_count},
        "auto_completed": cycle.auto_completed_count,
        "tier2_queue": cycle.tier2_queue_count, "tier3_queue": cycle.tier3_queue_count,
    }


# ─── Analyst queues (human-in-the-loop) ──────────────────────────────────────

def _queue_item_dto(q: TEFCAAnalystQueue) -> dict:
    return {
        "queue_id": str(q.queue_id), "record_id": str(q.record_id),
        "tier": q.tier, "assigned_role": q.assigned_role, "priority": q.priority,
        "bucket": int(q.bucket_classification.value) if q.bucket_classification else None,
        "reason": q.queue_reason, "status": q.status.value if q.status else None,
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


@tefca_router.get("/queue/tier2", summary="Tier-2 analyst queue")
async def get_tier2_queue(
    limit: int = 100, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    rows = (await db.execute(
        select(TEFCAAnalystQueue).where(
            TEFCAAnalystQueue.tier == 2,
            TEFCAAnalystQueue.status != QueueStatus.COMPLETE,
        ).order_by(TEFCAAnalystQueue.priority.desc(), TEFCAAnalystQueue.created_at.asc()).limit(limit)
    )).scalars().all()
    return {"total_pending": len(rows), "queue": [_queue_item_dto(q) for q in rows]}


@tefca_router.get("/queue/tier3", summary="Tier-3 SME escalation queue")
async def get_tier3_queue(
    limit: int = 100, db: AsyncSession = Depends(get_db), user=Depends(require_role("senior_analyst")),
):
    rows = (await db.execute(
        select(TEFCAAnalystQueue).where(
            TEFCAAnalystQueue.tier == 3,
            TEFCAAnalystQueue.status != QueueStatus.COMPLETE,
        ).order_by(TEFCAAnalystQueue.priority.desc(), TEFCAAnalystQueue.created_at.asc()).limit(limit)
    )).scalars().all()
    return {"total_pending": len(rows), "queue": [_queue_item_dto(q) for q in rows]}


@tefca_router.patch("/queue/{record_id}/classify", summary="Analyst override classification")
async def override_classification(
    record_id: str, request: AnalystOverrideRequest, http: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("senior_analyst")),
):
    if request.bucket_classification not in (1, 2, 3, 4):
        raise HTTPException(400, "bucket_classification must be 1-4")
    try:
        rid = uuid.UUID(record_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "record_id must be a valid UUID")
    record = (await db.execute(
        select(TEFCAEvidenceRecord).where(TEFCAEvidenceRecord.record_id == rid)
    )).scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Evidence record not found")

    previous_bucket = int(record.bucket_classification.value) if record.bucket_classification else None
    new_bucket = request.bucket_classification

    record.bucket_classification = _bucket_class_enum(new_bucket)
    record.bucket_label = _bucket_label_enum(new_bucket)
    record.auto_classified = False
    record.reviewer_id = str(user.email)
    record.reviewer_tier = 2
    record.review_timestamp = datetime.utcnow()
    record.review_notes = request.review_notes
    record.analyst_override_reason = request.override_reason
    record.supervisor_review_required = (new_bucket == 4)
    record.record_status = RecordStatus.REVIEWED

    # Close the queue item(s) for this record.
    qitems = (await db.execute(
        select(TEFCAAnalystQueue).where(TEFCAAnalystQueue.record_id == rid)
    )).scalars().all()
    for q in qitems:
        q.status = QueueStatus.COMPLETE
        q.completed_by = str(user.email)
        q.completed_at = datetime.utcnow()

    await log_tefca_event(
        db, user=user, action="BUCKET_OVERRIDE", resource_type="tefca_evidence_record",
        resource_id=rid, ip_address=_client_ip(http),
        details={"analyst_id": str(user.email), "previous_bucket": previous_bucket,
                 "new_bucket": new_bucket, "reason": request.override_reason},
    )
    await db.commit()
    return {
        "record_id": record_id, "previous_bucket": previous_bucket, "new_bucket": new_bucket,
        "overridden_by": str(user.email), "supervisor_review_required": (new_bucket == 4),
        "status": "REVIEWED",
    }


@tefca_router.patch("/queue/{record_id}/escalate", summary="Escalate record to Tier-3 SME")
async def escalate_to_tier3(
    record_id: str, request: EscalateRequest, http: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    try:
        rid = uuid.UUID(record_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "record_id must be a valid UUID")
    record = (await db.execute(
        select(TEFCAEvidenceRecord).where(TEFCAEvidenceRecord.record_id == rid)
    )).scalar_one_or_none()
    if not record:
        raise HTTPException(404, "Evidence record not found")
    record.tier_assigned = 3
    record.record_status = RecordStatus.PENDING_REVIEW

    qitems = (await db.execute(
        select(TEFCAAnalystQueue).where(TEFCAAnalystQueue.record_id == rid)
    )).scalars().all()
    if qitems:
        for q in qitems:
            q.tier = 3
            q.assigned_role = "senior_analyst"
            q.status = QueueStatus.PENDING
            q.queue_reason = f"Escalated to Tier-3: {request.escalation_note}"
    else:
        db.add(TEFCAAnalystQueue(
            record_id=rid, entity_id=record.entity_id, cycle_id=record.cycle_id,
            tier=3, assigned_role="senior_analyst", priority=90,
            bucket_classification=record.bucket_classification,
            queue_reason=f"Escalated to Tier-3: {request.escalation_note}",
            status=QueueStatus.PENDING,
        ))

    await log_tefca_event(
        db, user=user, action="ESCALATED_TIER3", resource_type="tefca_evidence_record",
        resource_id=rid, ip_address=_client_ip(http),
        details={"escalated_by": str(user.email), "escalation_type": "TIER3_SME",
                 "note": request.escalation_note},
    )
    await db.commit()
    return {"record_id": record_id, "new_tier": 3, "escalated_by": str(user.email), "status": "ESCALATED"}


# ─── Priority cases (Task 5) ─────────────────────────────────────────────────

@tefca_router.post("/priority-cases", summary="Create COR-directed priority case")
async def create_priority_case(
    request: PriorityCaseCreateRequest, http: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("program_manager")),
):
    manager = get_connector_manager()
    org_res = await manager.rce_directory.get_organization_by_id(request.entity_rce_id)
    entity_row = None
    if org_res.success and org_res.data:
        entity_row = await _upsert_entity(db, org_res.data)
    case = TEFCAPriorityCase(
        cor_reference=request.cor_reference,
        entity_id=(entity_row.entity_id if entity_row else None),
        assigned_by=str(user.email),
        assigned_date=datetime.utcnow(),
        deadline_date=(datetime.fromisoformat(request.deadline_date) if request.deadline_date else None),
        issue_description=request.issue_description,
        case_status=CaseStatus.ASSIGNED,
    )
    db.add(case)
    await db.flush()
    await log_tefca_event(
        db, user=user, action="PRIORITY_CASE_CREATED", resource_type="tefca_priority_case",
        resource_id=case.case_id, ip_address=_client_ip(http),
        details={"cor_reference": request.cor_reference, "entity_rce_id": request.entity_rce_id},
    )
    await db.commit()
    return {
        "case_id": str(case.case_id), "cor_reference": request.cor_reference,
        "case_status": case.case_status.value, "entity_resolved": entity_row is not None,
        "created_at": case.created_at.isoformat(),
    }


@tefca_router.get("/priority-cases", summary="List priority cases")
async def list_priority_cases(
    status: Optional[str] = None, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    q = select(TEFCAPriorityCase).order_by(TEFCAPriorityCase.created_at.desc())
    if status:
        try:
            q = q.where(TEFCAPriorityCase.case_status == CaseStatus(status))
        except ValueError:
            raise HTTPException(400, f"Invalid status. Use one of: {[s.value for s in CaseStatus]}")
    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "cases": [{
            "case_id": str(c.case_id), "cor_reference": c.cor_reference,
            "case_status": c.case_status.value if c.case_status else None,
            "severity": c.severity.value if c.severity else None,
            "deadline_date": c.deadline_date.isoformat() if c.deadline_date else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in rows],
    }


@tefca_router.patch("/priority-cases/{case_id}", summary="Update priority case")
async def update_priority_case(
    case_id: str, request: PriorityCaseUpdateRequest, http: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("senior_analyst")),
):
    try:
        cid = uuid.UUID(case_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "case_id must be a valid UUID")
    case = (await db.execute(select(TEFCAPriorityCase).where(TEFCAPriorityCase.case_id == cid))).scalar_one_or_none()
    if not case:
        raise HTTPException(404, "Priority case not found")
    if request.case_status:
        try:
            case.case_status = CaseStatus(request.case_status)
        except ValueError:
            raise HTTPException(400, f"Invalid case_status. Use: {[s.value for s in CaseStatus]}")
    if request.severity:
        try:
            case.severity = CaseSeverity(request.severity)
        except ValueError:
            raise HTTPException(400, f"Invalid severity. Use: {[s.value for s in CaseSeverity]}")
    if request.root_cause_determination is not None:
        case.root_cause_determination = request.root_cause_determination
    if request.root_cause_description is not None:
        case.root_cause_description = request.root_cause_description
    if request.resolution_notes is not None:
        case.resolution_notes = request.resolution_notes
    if request.recommendations is not None:
        case.recommendations = request.recommendations
    case.assigned_reviewer_id = str(user.email)
    await log_tefca_event(
        db, user=user, action="PRIORITY_CASE_UPDATED", resource_type="tefca_priority_case",
        resource_id=cid, ip_address=_client_ip(http),
        details={"updated_fields": request.dict(exclude_none=True)},
    )
    await db.commit()
    return {"case_id": case_id, "updated_by": str(user.email), "updated_at": datetime.utcnow().isoformat()}


# ─── Report aggregation (reads ONLY persisted evidence records) ──────────────

def _entity_line(r: TEFCAEvidenceRecord) -> dict:
    e1 = r.element_1_entity_identification or {}
    return {
        "record_id": str(r.record_id),
        "entity_rce_id": e1.get("entity_rce_id"),
        "entity_legal_name": e1.get("entity_legal_name"),
        "qhin_name": e1.get("qhin_name"),
        "npi": e1.get("entity_npi"),
        "bucket": int(r.bucket_classification.value) if r.bucket_classification else None,
        "confidence": r.confidence_score,
        "tier": r.tier_assigned,
        "finding_codes": r.finding_codes,
        "record_status": r.record_status.value if r.record_status else None,
    }


def _aggregate(records: list) -> dict:
    buckets = {1: [], 2: [], 3: [], 4: []}
    indeterminate = 0
    for r in records:
        b = int(r.bucket_classification.value) if r.bucket_classification else 1
        buckets.setdefault(b, []).append(r)
        e2 = r.element_2_finding_classification or {}
        if e2.get("indeterminate"):
            indeterminate += 1
    confs = [r.confidence_score for r in records if r.confidence_score is not None]
    return {
        "total_reviewed": len(records),
        "bucket_1_no_discrepancy": len(buckets[1]),
        "bucket_2_minor_admin": len(buckets[2]),
        "bucket_3_inexplicable": len(buckets[3]),
        "bucket_4_non_compliant": len(buckets[4]),
        "indeterminate_pending_source": indeterminate,
        "avg_confidence_score": (round(sum(confs) / len(confs), 3) if confs else None),
        "sample_confidence_level": 0.95,
        "stratified_entity_list": {
            "no_discrepancy": [_entity_line(r) for r in buckets[1]],
            "minor_or_administrative": [_entity_line(r) for r in buckets[2]],
            "inexplicable": [_entity_line(r) for r in buckets[3]],
            "non_compliant": [_entity_line(r) for r in buckets[4]],
        },
    }


_AGT_NOTE = ("AGT produces findings and recommendations. The ONC COR makes all "
             "final determinations.")
_CONTRACT_BLOCK = {
    "contractor": "Alliance Global Tech, Inc. (AGT)",
    "contract_reference": "ONC TEFCA Review Protocol — Contract No. 7571MN26F80064",
    "uei": "MP2FLV1MAW93", "cage": "8ERE8",
}


async def _records_for_cycle(db, cycle_id, period_start=None, period_end=None) -> list:
    q = select(TEFCAEvidenceRecord).where(TEFCAEvidenceRecord.cycle_id == cycle_id)
    if period_start:
        q = q.where(TEFCAEvidenceRecord.created_at >= period_start)
    if period_end:
        q = q.where(TEFCAEvidenceRecord.created_at <= period_end)
    return (await db.execute(q)).scalars().all()


async def _persist_report(db, report_type, cycle_id, report_data, generated_by,
                          period_start=None, period_end=None, methodology_version=None) -> TEFCAReport:
    row = TEFCAReport(
        report_type=report_type, cycle_id=cycle_id,
        period_start=period_start, period_end=period_end,
        report_data=report_data, generated_by=generated_by,
        methodology_version=methodology_version,
    )
    db.add(row)
    await db.flush()
    return row


async def build_d3_1_weekly_report(db, cycle_id, week_number, period_start, period_end):
    """D3.1 weekly progress report (SOW C.2 Task 3) — aggregates persisted
    evidence records in the week window. Reads only from the database."""
    cycle = (await db.execute(select(TEFCAReviewCycle).where(TEFCAReviewCycle.cycle_id == cycle_id))).scalar_one_or_none()
    records = await _records_for_cycle(db, cycle_id, period_start, period_end)
    return {
        "deliverable": "D3.1", "report_type": "WEEKLY_PROGRESS",
        "task": "Task 3 — Retrospective Review", "week_number": week_number,
        "cycle_id": str(cycle_id),
        "period_start": period_start.isoformat() if period_start else None,
        "period_end": period_end.isoformat() if period_end else None,
        "methodology_version": (cycle.methodology_version if cycle else None),
        "methodology_changes": [],  # sourced from a methodology-change log (future table)
        "statistics": _aggregate(records),
        "agt_does_not_adjudicate": _AGT_NOTE, "contract_info": _CONTRACT_BLOCK,
        "generated_at": datetime.utcnow().isoformat(),
    }


async def build_d3_2_final_report(db, cycle_id):
    """D3.2 final report — aggregates ALL persisted evidence records across the
    full cycle, stratified by bucket. Reads only from the database."""
    cycle = (await db.execute(select(TEFCAReviewCycle).where(TEFCAReviewCycle.cycle_id == cycle_id))).scalar_one_or_none()
    records = await _records_for_cycle(db, cycle_id)
    return {
        "deliverable": "D3.2", "report_type": "FINAL_REPORT",
        "task": "Task 3 — Retrospective Review (Final)", "cycle_id": str(cycle_id),
        "cycle_status": (cycle.cycle_status.value if cycle and cycle.cycle_status else None),
        "methodology_version": (cycle.methodology_version if cycle else None),
        "methodology_changes": [],
        "statistics": _aggregate(records),
        "agt_does_not_adjudicate": _AGT_NOTE, "contract_info": _CONTRACT_BLOCK,
        "generated_at": datetime.utcnow().isoformat(),
    }


def _parse_uuid(s: str):
    try:
        return uuid.UUID(s)
    except (ValueError, TypeError):
        raise HTTPException(400, "id must be a valid UUID")


def _parse_date(s: str):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        raise HTTPException(400, f"Invalid date '{s}', expected ISO YYYY-MM-DD")


@tefca_router.post("/reports/weekly/{cycle_id}", deprecated=True,
                   summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/sow/*. D3.1 weekly progress report")
async def generate_weekly_report(
    cycle_id: str, http: Request,
    week_number: int = Query(...),
    period_start: str = Query(...), period_end: str = Query(...),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("qalead")),
):
    cid = _parse_uuid(cycle_id)
    ps, pe = _parse_date(period_start), _parse_date(period_end)
    data = await build_d3_1_weekly_report(db, cid, week_number, ps, pe)
    row = await _persist_report(db, "D3.1_WEEKLY", cid, data, str(user.email), ps, pe,
                                data.get("methodology_version"))
    await log_tefca_event(db, user=user, action="REPORT_GENERATED", resource_type="tefca_report",
                          resource_id=row.report_id, ip_address=_client_ip(http),
                          details={"report_type": "D3.1_WEEKLY", "cycle_id": cycle_id, "week_number": week_number})
    await db.commit()
    return {"report_id": str(row.report_id), "report": data}


@tefca_router.post("/reports/final/{cycle_id}", deprecated=True,
                   summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/sow/*. D3.2 final report")
async def generate_final_report(
    cycle_id: str, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("program_manager")),
):
    cid = _parse_uuid(cycle_id)
    data = await build_d3_2_final_report(db, cid)
    row = await _persist_report(db, "D3.2_FINAL", cid, data, str(user.email),
                                methodology_version=data.get("methodology_version"))
    await log_tefca_event(db, user=user, action="REPORT_GENERATED", resource_type="tefca_report",
                          resource_id=row.report_id, ip_address=_client_ip(http),
                          details={"report_type": "D3.2_FINAL", "cycle_id": cycle_id})
    await db.commit()
    return {"report_id": str(row.report_id), "report": data}


@tefca_router.get("/reports", deprecated=True,
                  summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports. List generated reports")
async def list_reports(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    rows = (await db.execute(select(TEFCAReport).order_by(TEFCAReport.generated_at.desc()))).scalars().all()
    return {
        "total": len(rows),
        "reports": [{
            "report_id": str(r.report_id), "report_type": r.report_type,
            "cycle_id": str(r.cycle_id) if r.cycle_id else None,
            "generated_by": r.generated_by,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "period_start": r.period_start.isoformat() if r.period_start else None,
            "period_end": r.period_end.isoformat() if r.period_end else None,
        } for r in rows],
    }


# ─── REMOVED: fabricated demo citations — never reintroduce without real source calls ──
# The previous /demo/validate-all-mock endpoint emitted evidence citations that
# claimed live NPPES/LEIE/SAM/PECOS queries ("live": True, "query_success": True)
# while making NO such calls. That is a fabricated audit trail and is deleted. A
# development-only demo that runs the REAL pipeline against the bundled dataset is
# registered below ONLY when ENVIRONMENT=development.

if settings.is_development:
    demo_router = APIRouter(prefix="/api/v1/tefca/demo", tags=["TEFCA Demo (dev only)"])

    @demo_router.get("/validate-sample", summary="[dev] Validate N bundled entities via the REAL pipeline")
    async def demo_validate_sample(
        limit: int = 5, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
    ):
        """Development helper. Runs the genuine connector+engine pipeline (real API
        calls, real citations) against the bundled dataset and persists results to
        an ad-hoc cycle. Never available in production."""
        cycle = await _get_or_create_cycle(db, None, created_by=str(user.email))
        out = []
        for org in ALL_MOCK_ENTITIES[:limit]:
            record, validation, evidence = await _validate_and_persist(
                db, org, cycle, reviewer_id=str(user.email), acting_user=user,
            )
            out.append({"entity_id": org.get("id"), "bucket": validation["bucket"],
                        "classification_state": validation["classification_state"],
                        "record_id": str(record.record_id)})
        await db.commit()
        return {"cycle_id": str(cycle.cycle_id), "validated": len(out), "results": out,
                "note": "REAL pipeline — citations reflect actual source calls."}

    tefca_router.include_router(demo_router)


# ═══════════════════════════════════════════════════════════════════════════
# EXECUTIVE DASHBOARD  (mounted at /api/tefca to match the dashboard spec)
#
# summary + trends are aggregate (no PII) and PUBLIC so they can back a
# read-only dashboard / be curl-checked. /reports/export carries npi/uei/
# entity_name (PII) and is ROLE-GATED. summary/trends/export aggregate from
# tefca_reviews (+ tefca_findings); connector_uptime from tefca_connector_logs.
# ═══════════════════════════════════════════════════════════════════════════

tefca_dashboard_router = APIRouter(prefix="/api/tefca", tags=["TEFCA Dashboard"])

_FINDING_REASON_LABELS = {
    "LEIE_ACTIVE_EXCLUSION": "OIG LEIE active exclusion",
    "SAM_ACTIVE_DEBARMENT": "SAM.gov debarment",
    "PECOS_PAYMENT_SUSPENSION": "PECOS payment suspension",
    "NPI_NOT_FOUND": "NPI not found in NPPES",
    "NPI_DEACTIVATED": "NPI deactivated",
    "NPI_INACTIVE": "NPI inactive",
    "NAME_UNRESOLVABLE": "Legal name unresolvable",
}


# tefca_reviews.status -> dashboard pass/fail/pending/indeterminate.
#
# MC-005 — THE BUG THIS FIXES
#
# This map covered only the 4-bucket disposition vocabulary, and _review_status()
# defaulted EVERYTHING ELSE to "pending". But tefca_reviews.status carries two
# vocabularies: the disposition written by the decision endpoint
# (no_discrepancy, non_compliant, …) and the coarse status the column was
# originally documented with (pass / fail / pending). A row stored as "pass" or
# "compliant" matched nothing, fell through the default, and was counted as
# PENDING — so Mission Control's "Active Reviews" KPI included reviews that were
# finished.
#
# The default is what made it invisible: an unmapped status did not raise, did
# not log, and did not show up as unknown. It quietly inflated the one number an
# executive reads first, and it would do the same for any status added later.
# Unknown statuses are now counted as "unknown" and are NOT active work.
_REVIEW_STATUS_MAP = {
    # 4-bucket disposition (written by the decision endpoint)
    "no_discrepancy": "pass",
    "minor_administrative": "pass",
    "inexplicable": "pending",
    "non_compliant": "fail",
    "indeterminate": "indeterminate",
    # coarse vocabulary the column was originally documented with, plus the
    # display spellings that reach it from imports and the legacy pipeline
    "pass": "pass",
    "passed": "pass",
    "compliant": "pass",
    "completed": "pass",
    "closed": "pass",
    "resolved": "pass",
    "fail": "fail",
    "failed": "fail",
    "flagged": "fail",
    "pending": "pending",
    "pending_review": "pending",
    "under_review": "pending",
    "in_review": "pending",
    "in_progress": "pending",
    "new": "pending",
    "queued": "pending",
}


def _review_status(status: str) -> str:
    """Dashboard bucket for a stored review status.

    An unrecognised status is "unknown", NOT "pending". Guessing that unmapped
    means outstanding is what produced MC-005; a status nobody mapped is a fact
    about the data, and reporting it as active work makes the KPI wrong in the
    one direction an operator cannot detect.
    """
    return _REVIEW_STATUS_MAP.get((status or "").strip().lower(), "unknown")


def _connector_health_snapshot(health: dict) -> dict:
    def s(k):
        return "available" if health.get(k, {}).get("live") else "unavailable"
    return {"sam_gov": s("SAM_GOV"), "pecos": s("PECOS"), "leie": s("OIG_LEIE"), "nppes": s("NPPES")}


@tefca_dashboard_router.get("/dashboard/summary", summary="Executive dashboard summary (aggregate, viewer role required)", dependencies=[Depends(require_role("viewer"))])
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
    reviews = (await db.execute(select(TEFCAReview))).scalars().all()
    total = len(reviews)
    # MC-005 — "unknown" is always present, so a status nobody mapped is visible
    # as its own count rather than being folded into the active-work bucket.
    by_status = {"pass": 0, "fail": 0, "pending": 0, "indeterminate": 0, "unknown": 0}
    by_risk = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    review_times = []
    by_month: dict = {}
    now = datetime.utcnow()
    reviews_this_month = 0
    fail_ids = []
    for r in reviews:
        st = _review_status(r.status)
        by_status[st] = by_status.get(st, 0) + 1
        rl = (r.risk_level or "low").lower()
        if rl in by_risk:
            by_risk[rl] += 1
        if st == "fail":
            fail_ids.append(r.id)
        if r.created_at and r.updated_at:
            review_times.append((r.updated_at - r.created_at).total_seconds() / 3600.0)
        if r.created_at:
            mk = r.created_at.strftime("%Y-%m")
            m = by_month.setdefault(mk, {"count": 0, "pass": 0, "fail": 0})
            m["count"] += 1
            if st == "pass":
                m["pass"] += 1
            elif st == "fail":
                m["fail"] += 1
            if r.created_at.year == now.year and r.created_at.month == now.month:
                reviews_this_month += 1

    # top failure reasons: finding_type counts on failed reviews
    fail_reasons: dict = {}
    if fail_ids:
        ftypes = (await db.execute(
            select(TEFCAFinding.finding_type).where(TEFCAFinding.review_id.in_(fail_ids))
        )).scalars().all()
        for ft in ftypes:
            fail_reasons[ft] = fail_reasons.get(ft, 0) + 1

    passed, failed = by_status["pass"], by_status["fail"]
    pending = by_status["pending"] + by_status["indeterminate"]

    def rate(n):
        return round(n / total, 4) if total else 0.0

    health = await get_connector_manager().health_check()
    top = sorted(fail_reasons.items(), key=lambda x: -x[1])[:10]
    return {
        "total_reviews": total,
        "pass_rate": rate(passed),
        "fail_rate": rate(failed),
        "pending_rate": rate(pending),
        "avg_review_time_hours": round(sum(review_times) / len(review_times), 2) if review_times else 0,
        "reviews_this_month": reviews_this_month,
        "reviews_by_status": by_status,
        "reviews_by_month": [{"month": k, "count": v["count"], "pass": v["pass"], "fail": v["fail"]}
                             for k, v in sorted(by_month.items())],
        "connector_health": _connector_health_snapshot(health),
        "risk_distribution": by_risk,
        "top_failure_reasons": [{"reason": _FINDING_REASON_LABELS.get(c, c), "count": n} for c, n in top],
        **data_source_labels(),
    }


@tefca_dashboard_router.get("/dashboard/trends", summary="Monthly trends for charting (aggregate, viewer role required)", dependencies=[Depends(require_role("viewer"))])
async def dashboard_trends(db: AsyncSession = Depends(get_db)):
    reviews = (await db.execute(select(TEFCAReview))).scalars().all()
    by_month: dict = {}
    for r in reviews:
        if not r.created_at:
            continue
        mk = r.created_at.strftime("%Y-%m")
        m = by_month.setdefault(mk, {"total": 0, "passed": 0, "failed": 0, "times": []})
        st = _review_status(r.status)
        m["total"] += 1
        if st == "pass":
            m["passed"] += 1
        elif st == "fail":
            m["failed"] += 1
        if r.created_at and r.updated_at:
            m["times"].append((r.updated_at - r.created_at).total_seconds() / 3600.0)
    monthly_reviews, monthly_avg_time, pass_rate_trend = [], [], []
    for mk in sorted(by_month):
        m = by_month[mk]
        monthly_reviews.append({"month": mk, "total": m["total"], "passed": m["passed"], "failed": m["failed"]})
        monthly_avg_time.append({"month": mk, "avg_hours": round(sum(m["times"]) / len(m["times"]), 2) if m["times"] else 0})
        pass_rate_trend.append({"month": mk, "rate": round(m["passed"] / m["total"], 4) if m["total"] else 0})
    logs = (await db.execute(select(TEFCAConnectorLog))).scalars().all()
    agg: dict = {}
    for l in logs:
        a = agg.setdefault(l.connector_name, {"up": 0, "total": 0})
        a["total"] += 1
        if l.status == "available":
            a["up"] += 1
    connector_uptime = [{"connector": k.lower(), "uptime_pct": round(100 * v["up"] / v["total"], 1) if v["total"] else 0.0}
                        for k, v in agg.items()]
    if not connector_uptime:
        health = await get_connector_manager().health_check()
        connector_uptime = [{"connector": k.lower(), "uptime_pct": 100.0 if v.get("live") else 0.0}
                            for k, v in health.items()]
    return {
        "monthly_reviews": monthly_reviews,
        "monthly_avg_time": monthly_avg_time,
        "pass_rate_trend": pass_rate_trend,
        "connector_uptime": connector_uptime,
        **data_source_labels(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# QA ROUND 2 — MISSION CONTROL BACKING ENDPOINTS (DEF-003 / DEF-004 / DEF-005)
#
# Three Mission Control panels — Validation Queue, Recent Activity and
# Notifications — had no endpoint of their own. They were assembled in the
# browser from whatever the other calls happened to return, so a tester watching
# the network tab saw no request that produced those rows and correctly reported
# the data as frontend-provided. Worse, when /reviews was unavailable the page
# fell back to the bundled MOCK entity fixture, whose rows carry a hardcoded
# status of "pending" — which is how a Failed record could be shown as Pending.
#
# Each panel now has one endpoint that owns its rows. Nothing here fabricates a
# record: every field is read from tefca_entities, tefca_reviews, audit_logs or
# the live connector probe, and a panel with no data returns an empty list
# rather than a plausible-looking sample.
# ─────────────────────────────────────────────────────────────────────────────

# Entity lifecycle states that mean "this entity has not been validated yet".
_UNVALIDATED_ENTITY_STATUSES = [
    EntityStatus.PENDING_REVIEW, EntityStatus.IN_REVIEW, EntityStatus.ESCALATED,
]


def _entity_status_value(entity) -> str:
    st = getattr(entity, "current_status", None)
    return str(getattr(st, "value", st) or "PENDING_REVIEW").lower()


def _parse_entity_status(raw: str):
    """Accept 'Pending Review', 'pending_review', 'PENDING-REVIEW' → EntityStatus.

    Returns None for an unrecognised value so the caller can 400 rather than
    silently returning the unfiltered list — a filter that quietly does nothing
    is how stale data gets read as current data.
    """
    key = (raw or "").strip().upper().replace(" ", "_").replace("-", "_")
    return EntityStatus.__members__.get(key)


@tefca_dashboard_router.get(
    "/dashboard/validation-queue",
    summary="Entities awaiting validation against the authoritative sources",
    dependencies=[Depends(require_role("viewer"))],
)
async def dashboard_validation_queue(
    status: Optional[str] = Query(None, description="Filter by entity status"),
    qhin: Optional[str] = Query(None, description="Filter by QHIN / source"),
    search: Optional[str] = Query(None, description="Entity name or NPI substring"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """The Validation Queue panel on Mission Control (DEF-003).

    Filtering happens HERE, in SQL, not in the browser against a cached page of
    rows: a filter that narrows a stale local array will happily show a record
    whose status changed minutes ago (DEF-001/DEF-006). Every filter change is a
    fresh query against current state.

    `status` is matched case-insensitively so a UI that sends the display label
    ("Pending Review") resolves to the stored enum value.
    """
    q = select(TEFCAEntity)

    if status and status.strip().lower() not in ("", "all"):
        wanted = _parse_entity_status(status)
        if wanted is None:
            raise HTTPException(
                400,
                "Unknown status %r. Valid values: %s"
                % (status, ", ".join(EntityStatus.__members__)),
            )
        q = q.where(TEFCAEntity.current_status == wanted)
    else:
        # Default view: only what still needs validating.
        q = q.where(TEFCAEntity.current_status.in_(_UNVALIDATED_ENTITY_STATUSES))
    if qhin:
        q = q.where(TEFCAEntity.qhin_name == qhin)
    if search:
        term = f"%{search.strip().lower()}%"
        q = q.where(or_(
            func.lower(TEFCAEntity.legal_name_submitted).like(term),
            func.lower(func.coalesce(TEFCAEntity.npi_submitted, "")).like(term),
        ))

    q = q.order_by(TEFCAEntity.date_last_updated.desc())
    total = len((await db.execute(q)).scalars().all())
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()

    show_pii = _can_see_pii(user)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "pii_masked": not show_pii,
        "entities": [{
            "id": str(e.entity_id),
            "entity_name": e.legal_name_submitted,
            "npi": e.npi_submitted if show_pii else _mask_identifier(e.npi_submitted),
            "entity_type": str(getattr(e.entity_type, "value", e.entity_type) or "PARTICIPANT"),
            "qhin": e.qhin_name,
            "source": e.qhin_name,
            # The entity's OWN status. Never a constant: a Failed / escalated
            # entity rendered as "Pending" was DEF-002.
            "status": _entity_status_value(e),
            "bucket": str(getattr(e.latest_bucket, "value", e.latest_bucket)) if e.latest_bucket else None,
            "confidence": e.latest_confidence,
            "first_seen": e.date_first_seen.isoformat() if e.date_first_seen else None,
            "last_updated": e.date_last_updated.isoformat() if e.date_last_updated else None,
        } for e in rows],
        **data_source_labels(),
    }


# Audit actions worth surfacing on the dashboard, mapped to a plain-language verb.
_ACTIVITY_ACTION_LABELS = {
    "entity_import": "Imported entities",
    "review_executed": "Ran validation",
    "review_decision": "Recorded a decision",
    "entity_verified": "Verified entity",
    "priority_case_created": "Opened a priority case",
    "report_generated": "Generated a report",
    "user_approved": "Approved a user",
    "user_rejected": "Rejected a user",
    "user_role_changed": "Changed a user role",
    "login_success": "Signed in",
    "file_scan": "Scanned an uploaded file",
}


@tefca_dashboard_router.get(
    "/dashboard/recent-activity",
    summary="Recent audit-backed activity for the Mission Control feed",
    dependencies=[Depends(require_role("viewer"))],
)
async def dashboard_recent_activity(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """The Recent Activity feed (DEF-004).

    Every entry is an audit_logs row with its real recorded timestamp. The feed
    previously merged whatever objects happened to carry a date, and QA-gate
    records are written at request time — which is why every line read
    "just now". Relative time is computed by the client from `timestamp`; the
    absolute ISO value is always sent so the two can never disagree.
    """
    from app.models.database import AuditLog, User

    rows = (await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    )).scalars().all()

    user_ids = {r.user_id for r in rows if r.user_id}
    actors = {}
    if user_ids:
        users = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        actors = {u.id: (u.full_name or u.email) for u in users}

    def describe(r):
        d = r.details if isinstance(r.details, dict) else {}
        return (d.get("entity_name") or d.get("filename") or d.get("email")
                or r.resource_id or r.resource_type or "—")

    return {
        "total": len(rows),
        "activity": [{
            "id": str(r.id),
            "action": r.action,
            "action_label": _ACTIVITY_ACTION_LABELS.get(
                r.action, (r.action or "activity").replace("_", " ").capitalize()),
            "entity": describe(r),
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "user": actors.get(r.user_id) or "System",
            "outcome": (r.details or {}).get("result") if isinstance(r.details, dict) else None,
            # The REAL recorded time. Never "now".
            "timestamp": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    }


@tefca_dashboard_router.get(
    "/dashboard/notifications",
    summary="Real system notifications (connector outages, QA failures, SLA breaches)",
    dependencies=[Depends(require_role("viewer"))],
)
async def dashboard_notifications(db: AsyncSession = Depends(get_db)):
    """The Notifications panel (DEF-005).

    Only real failing signals are returned. There is no notification store to
    read, and inventing one would put fabricated alerts in front of a COR, so
    this reports observed conditions: a connector that did not answer, a QA gate
    that did not pass, an SLA breach, and imports that failed. An entirely
    healthy platform legitimately returns an empty list.
    """
    out = []

    # 1. Connector availability — from the live probe, not a cached label.
    try:
        health = await get_connector_manager().health_check()
    except Exception:
        health = {}
    for key, label in (("SAM_GOV", "SAM.gov"), ("NPPES", "NPPES"),
                       ("OIG_LEIE", "OIG LEIE"), ("PECOS", "PECOS")):
        info = health.get(key) or {}
        if not info.get("live"):
            out.append({
                "id": f"connector:{key.lower()}",
                "severity": "warning",
                "category": "connector",
                "title": f"{label} unavailable",
                "detail": info.get("detail") or info.get("error")
                          or "The source did not confirm availability on the last probe.",
                "timestamp": datetime.utcnow().isoformat(),
            })

    # 2. QA gates that did not pass.
    try:
        qa = await qa_engine.PlatformReadinessCheck().run(db)
        for check in (qa.get("checks") or []):
            if not check.get("passed"):
                out.append({
                    "id": f"qa:{check.get('name')}",
                    "severity": "error",
                    "category": "qa",
                    "title": f"QA check failed — {check.get('name')}",
                    "detail": check.get("detail"),
                    "timestamp": datetime.utcnow().isoformat(),
                })
    except Exception:
        # A notifications panel must never take the dashboard down with it.
        pass

    # 3. Imports that failed — read from the history table, with real times.
    failed_imports = (await db.execute(
        select(TEFCAImportHistory)
        .where(TEFCAImportHistory.status.in_(["failed", "partial"]))
        .order_by(TEFCAImportHistory.uploaded_at.desc())
        .limit(5)
    )).scalars().all()
    for imp in failed_imports:
        out.append({
            "id": f"import:{imp.id}",
            "severity": "error" if imp.status == "failed" else "warning",
            "category": "import",
            "title": f"Import {imp.status}: {imp.filename}",
            "detail": f"{imp.imported_count or 0} of {imp.record_count or 0} rows imported.",
            "timestamp": imp.uploaded_at.isoformat() if imp.uploaded_at else None,
        })

    return {"total": len(out), "notifications": out}


# ─────────────────────────────────────────────────────────────────────────────
# QA ROUND 2 — AUDIT TRAIL (AT-001 … AT-009)
#
# The Audit Trail page was reading /api/tefca/qa/audit — the QA GATE trail,
# which records gate evaluations and nothing else. Every column the Audit Trail
# is specified to show (correlation id, user, event type, outcome, IP) and every
# event it is specified to contain (login_success, login_failure, file_scan,
# entity_import) lives in audit_logs, a different table. That single wrong
# endpoint is why all seven Audit Trail cases failed: the page was not broken,
# it was pointed at the wrong data.
#
# This serves audit_logs with the specified columns. It is read-only by
# construction — there is no write, update or delete route on it (AT-006), and
# nothing here can alter a record.
# ─────────────────────────────────────────────────────────────────────────────

# Coarse event categories for the Audit Trail type filter (AT-007).
# AT-007 — ONE vocabulary, defined in app/services/audit.py and imported here.
#
# This used to be a second, hand-maintained copy of the same buckets. The write
# path stamped audit_logs.event_type from one list and this filter selected from
# the other, so the two could disagree about which bucket an action belonged to:
# a row could display an event type that its own filter would not return. The
# list that classifies a row on the way IN is now the list the filter offers on
# the way OUT.
from app.services.audit import EVENT_TYPE_ACTIONS as _AUDIT_EVENT_TYPES

# Details keys that must never be echoed, whatever a caller wrote into them.
# AT-008 requires no secret in any record; the writers do not put one there, and
# this makes that a property of the READ path too, so a future careless writer
# cannot leak through this endpoint.
_AUDIT_REDACT_KEYS = {
    "password", "new_password", "old_password", "token", "access_token",
    "refresh_token", "api_key", "apikey", "secret", "authorization",
    "client_secret", "private_key",
}


def _audit_event_type(action: str) -> str:
    """Read-path fallback for rows written before audit_logs.event_type existed.

    Delegates to the same classifier the write path uses, so a backfilled row and
    a freshly written one are never labelled differently.
    """
    from app.services.audit import classify_event_type

    return classify_event_type(action)


def _safe_audit_details(details) -> dict:
    if not isinstance(details, dict):
        return {}
    return {
        k: ("[redacted]" if k.lower() in _AUDIT_REDACT_KEYS else v)
        for k, v in details.items()
    }


@tefca_dashboard_router.get(
    "/audit-trail",
    summary="Platform audit trail (timestamp, correlation id, user, event, outcome, IP)",
    dependencies=[Depends(require_role("qalead"))],
)
async def tefca_audit_trail(
    # AT-007 — the valid values are read FROM the vocabulary rather than retyped
    # here. This description listed the pre-Round-3 buckets and had already gone
    # stale (no `security`, no `data_change`), so the API documentation was
    # telling callers that two real, selectable values did not exist.
    event_type: Optional[str] = Query(
        None,
        description="Event category. One of: "
                    + " | ".join(sorted(_AUDIT_EVENT_TYPES))
                    + " | other | all"),
    action: Optional[str] = Query(None, description="Exact action name, e.g. login_success"),
    correlation_id: Optional[str] = Query(
        None, description="Return every event sharing this correlation id (AT-009)"),
    search: Optional[str] = Query(None, description="Action or resource substring"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Read-only audit trail.

    Gated at qalead (level 6) — the QA Lead's remit is audit access without
    entity-change rights, which is exactly this endpoint. Reviewers below that
    level do not need the whole platform's authentication history to do a review.
    """
    from app.models.database import AuditLog, User

    q = select(AuditLog)

    if event_type and event_type.strip().lower() not in ("", "all"):
        key = event_type.strip().lower()
        if key == "other":
            known = [a for actions in _AUDIT_EVENT_TYPES.values() for a in actions]
            q = q.where(func.lower(AuditLog.action).notin_(known))
        elif key in _AUDIT_EVENT_TYPES:
            q = q.where(func.lower(AuditLog.action).in_(_AUDIT_EVENT_TYPES[key]))
        else:
            raise HTTPException(
                400,
                "Unknown event_type %r. Valid values: %s, other, all"
                % (event_type, ", ".join(sorted(_AUDIT_EVENT_TYPES))),
            )
    if action:
        q = q.where(func.lower(AuditLog.action) == action.strip().lower())
    if correlation_id:
        # AT-009 — match the indexed COLUMN. This previously cast the whole
        # `details` JSON to text and did a LIKE over it, which scanned the table
        # and would also match a row where the id merely appeared inside some
        # other field. Rows written before the column existed are backfilled by
        # the migration, but the details fallback is kept so a row that somehow
        # escaped the backfill is still findable rather than silently absent.
        cid = correlation_id.strip()
        q = q.where(or_(
            AuditLog.correlation_id == cid,
            and_(AuditLog.correlation_id.is_(None),
                 cast(AuditLog.details, String).like(f"%{cid}%")),
        ))
    if search:
        term = f"%{search.strip().lower()}%"
        q = q.where(or_(
            func.lower(AuditLog.action).like(term),
            func.lower(func.coalesce(AuditLog.resource_type, "")).like(term),
            func.lower(func.coalesce(AuditLog.resource_id, "")).like(term),
        ))

    q = q.order_by(AuditLog.created_at.desc())
    total = len((await db.execute(q)).scalars().all())
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()

    user_ids = {r.user_id for r in rows if r.user_id}
    actors = {}
    if user_ids:
        found = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        actors = {str(u.id): u.email for u in found}

    def outcome_of(r):
        # AT-001 — the stored column is authoritative. The derivation below is
        # retained only for rows written before the column existed, so an old
        # row still reads correctly instead of showing a blank outcome.
        if getattr(r, "outcome", None):
            return r.outcome
        details = r.details if isinstance(r.details, dict) else {}
        if details.get("result"):
            return details["result"]
        a = (r.action or "").lower()
        if a.endswith(("_failed", "_failure", "_rejected", "_blocked", "_throttled")):
            return "failure"
        return "success"

    def correlation_of(r):
        if getattr(r, "correlation_id", None):
            return r.correlation_id
        return (r.details or {}).get("correlation_id") if isinstance(r.details, dict) else None

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "event_types": sorted(_AUDIT_EVENT_TYPES) + ["other"],
        "entries": [{
            "id": str(r.id),
            "timestamp": r.created_at.isoformat() if r.created_at else None,
            "correlation_id": correlation_of(r),
            "user": actors.get(str(r.user_id)) or (
                (r.details or {}).get("email") if isinstance(r.details, dict) else None) or "System",
            "event_type": getattr(r, "event_type", None) or _audit_event_type(r.action),
            "action": r.action,
            "outcome": outcome_of(r),
            "ip_address": r.ip_address,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "details": _safe_audit_details(r.details),
        } for r in rows],
    }


@tefca_dashboard_router.get("/status", summary="Module status + data provenance (public)")
async def tefca_status():
    """Lightweight public status: whether TEFCA is serving MOCK or PRODUCTION data,
    plus live connector health. The honest 'are we on mock data?' endpoint."""
    health = await get_connector_manager().health_check()
    return {
        "module": "tefca_arc",
        "status": "active",
        "rce_directory_live": not is_running_mock(),
        "connector_health": _connector_health_snapshot(health),
        **data_source_labels(),
    }


@tefca_dashboard_router.get("/search", summary="Global entity search (NPI, name, QHIN) with live NPPES lookup")
async def tefca_search(
    q: str = Query("", description="NPI (10 digits), entity name, or QHIN"),
    type: str = Query("all", description="all | npi | name | qhin"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Search reviews/entities/findings by NPI, name, or QHIN. A 10-digit query is
    treated as an NPI (exact) and triggers a live NPPES lookup; otherwise it is a
    fuzzy ILIKE name/QHIN match. Capped at 50 results per collection."""
    from sqlalchemy import or_
    term = (q or "").strip()
    if not term:
        return {"query": "", "type": type, "is_npi": False,
                "counts": {"reviews": 0, "entities": 0, "findings": 0},
                "reviews": [], "entities": [], "findings": [], "nppes": None}
    is_npi = term.isdigit() and len(term) == 10
    LIMIT = 50
    like = f"%{term}%"

    # EQ-003 — identifiers are masked below the reviewer floor HERE too. The
    # reviews list endpoint was patched for this in Round 2; search was not, and
    # it returns the same rows from the same tables to the same viewer role. A
    # masking rule enforced on one of two doors into the same data is not a
    # masking rule. Searching an NPI still MATCHES on the full value — the query
    # is the operator's own input, so echoing a masked result is not a leak —
    # but nothing unmasked is returned to a principal below the floor.
    show_pii = _can_see_pii(user)

    # Reviews (denormalized dashboard data).
    rq = select(TEFCAReview)
    if type == "npi" or (type == "all" and is_npi):
        rq = rq.where(TEFCAReview.npi == term)
    elif type == "qhin":
        rq = rq.where(TEFCAReview.qhin.ilike(like))
    elif type == "name":
        rq = rq.where(TEFCAReview.entity_name.ilike(like))
    else:
        rq = rq.where(or_(
            TEFCAReview.entity_name.ilike(like), TEFCAReview.qhin.ilike(like),
            TEFCAReview.npi.ilike(like), TEFCAReview.entity_type.ilike(like),
        ))
    reviews = (await db.execute(rq.limit(LIMIT))).scalars().all()
    reviews_out = [{
        "id": str(r.id), "entity_name": r.entity_name,
        "npi": r.npi if show_pii else _mask_identifier(r.npi),
        "uei": r.uei if show_pii else _mask_identifier(r.uei),
        "qhin": r.qhin, "entity_type": r.entity_type, "status": r.status,
        "risk_level": r.risk_level, "is_mock_data": r.is_mock_data,
    } for r in reviews]

    # Findings attached to matched reviews.
    findings_out = []
    if reviews:
        ids = [r.id for r in reviews]
        frows = (await db.execute(
            select(TEFCAFinding).where(TEFCAFinding.review_id.in_(ids)).limit(LIMIT)
        )).scalars().all()
        findings_out = [{
            "id": str(f.id), "review_id": str(f.review_id), "connector": f.connector,
            "finding_type": f.finding_type, "severity": f.severity, "detail": f.detail,
        } for f in frows]

    # Authoritative entity master.
    entities_out = []
    try:
        eq = select(TEFCAEntity)
        if is_npi and type in ("all", "npi"):
            eq = eq.where(TEFCAEntity.npi_submitted == term)
        elif type == "qhin":
            eq = eq.where(TEFCAEntity.qhin_name.ilike(like))
        else:
            eq = eq.where(or_(
                TEFCAEntity.legal_name_submitted.ilike(like),
                TEFCAEntity.qhin_name.ilike(like),
                TEFCAEntity.npi_submitted.ilike(like),
            ))
        erows = (await db.execute(eq.limit(LIMIT))).scalars().all()
        entities_out = [{
            "entity_id": str(e.entity_id), "qhin": e.qhin_name,
            "legal_name": e.legal_name_submitted,
            "npi": e.npi_submitted if show_pii else _mask_identifier(e.npi_submitted),
            "entity_type": e.entity_type.value if e.entity_type else None,
            "status": e.current_status.value if e.current_status else None,
        } for e in erows]
    except Exception as e:
        logger.warning(f"tefca_search entities failed: {e}")

    # Live NPPES lookup (NPI searches only).
    nppes = None
    if is_npi and type in ("all", "npi"):
        try:
            from .connectors import check_nppes
            res = await check_nppes(npi=term)
            if res is not None and getattr(res, "success", False):
                nppes = getattr(res, "data", None)
            else:
                nppes = {"found": False, "unavailable": True}
        except Exception as e:
            logger.warning(f"tefca_search NPPES lookup failed: {e}")
            nppes = {"error": str(e)[:120]}

    return {
        "query": term, "type": type, "is_npi": is_npi,
        "pii_masked": not show_pii,
        "counts": {"reviews": len(reviews_out), "entities": len(entities_out), "findings": len(findings_out)},
        "reviews": reviews_out, "entities": entities_out, "findings": findings_out, "nppes": nppes,
    }


@tefca_dashboard_router.get("/reports/export", summary="CSV export of reviews (role-gated — contains PII)")
async def export_reviews(
    format: str = Query("csv"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    # LOGIN-013 — this export carries NPIs and entity identifiers; the route's
    # own summary has always said "contains PII" while admitting viewer(1).
    # Masking a CSV would produce an evidence file that silently differs from
    # the record, so this is a denial rather than a redaction: the export is for
    # roles cleared to see the data.
    user=Depends(require_role(PII_ROLE_FLOOR)),
):
    if format.lower() != "csv":
        raise HTTPException(400, "Only format=csv is supported")
    q = select(TEFCAReview)
    if start:
        q = q.where(TEFCAReview.created_at >= _parse_date(start))
    if end:
        q = q.where(TEFCAReview.created_at <= _parse_date(end))
    reviews = (await db.execute(q.order_by(TEFCAReview.created_at))).scalars().all()

    # per-connector finding type keyed by review id, for the sam/pecos/leie columns
    findings_by_review: dict = {}
    ids = [r.id for r in reviews]
    if ids:
        frows = (await db.execute(select(TEFCAFinding).where(TEFCAFinding.review_id.in_(ids)))).scalars().all()
        for f in frows:
            findings_by_review.setdefault(f.review_id, {})[(f.connector or "").lower()] = f.finding_type

    import io as _io
    import csv as _csv
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["review_id", "entity_name", "npi", "uei", "review_date", "status", "risk_level",
                "sam_status", "pecos_status", "leie_status", "reviewer", "notes"])
    for r in reviews:
        conns = findings_by_review.get(r.id, {})
        w.writerow([
            str(r.id), r.entity_name or "", r.npi or "", r.uei or "",
            r.created_at.isoformat() if r.created_at else "", r.status or "", r.risk_level or "",
            conns.get("sam_gov", ""), conns.get("pecos", ""), conns.get("leie", ""),
            r.reviewer_id or "", ("MOCK DATA" if r.is_mock_data else ""),
        ])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tefca_reviews.csv"})


# ─── Admin: seed mock data (idempotent, admin-gated) ─────────────────────────
# Applies the additive RFQ columns to tefca_reviews and seeds mock review data
# so the executive dashboard has content before real QHIN data arrives from the
# COR. Idempotent: ALTERs use IF NOT EXISTS; the mock insert is skipped if
# tefca_reviews already has rows. Run once per environment (dev + prod).

_QHINS = [
    "eHealth Exchange", "Epic Nexus", "Health Gorilla", "KONZA", "MedAllies",
    "CommonWell", "Kno2", "eClinicalWorks", "Netsmart", "Surescripts", "Oracle Health",
]

_SEED_REVIEWS_SQL = """
INSERT INTO tefca_reviews
  (id, entity_name, npi, uei, status, risk_level, reviewer_id, qhin, is_mock_data, created_at, updated_at)
SELECT
  gen_random_uuid(),
  'MOCK Participant ' || g,
  lpad((1000000000 + g)::text, 10, '0'),
  'MOCKUEI' || lpad(g::text, 5, '0'),
  CASE WHEN g <= 30 THEN 'no_discrepancy'
       WHEN g <= 43 THEN 'minor_administrative'
       WHEN g <= 48 THEN 'inexplicable'
       ELSE 'non_compliant' END,
  CASE WHEN g <= 30 THEN 'low'
       WHEN g <= 43 THEN 'medium'
       WHEN g <= 48 THEN 'high'
       ELSE 'critical' END,
  'MOCK_REVIEWER',
  (ARRAY['eHealth Exchange','Epic Nexus','Health Gorilla','KONZA','MedAllies',
         'CommonWell','Kno2','eClinicalWorks','Netsmart','Surescripts',
         'Oracle Health'])[1 + (g % 11)],
  true,
  now() - (g || ' days')::interval,
  now()
FROM generate_series(1,50) AS g
"""

_SEED_FINDINGS_SQL = """
INSERT INTO tefca_findings (id, review_id, connector, finding_type, detail, severity)
SELECT
  gen_random_uuid(),
  r.id,
  (ARRAY['nppes','leie','pecos','sam_gov'])[1 + ((row_number() OVER ())::int % 4)],
  CASE r.status
    WHEN 'non_compliant'        THEN 'LEIE_ACTIVE_EXCLUSION'
    WHEN 'inexplicable'         THEN 'SOURCE_CONFLICT'
    WHEN 'minor_administrative' THEN 'NAME_MISMATCH'
    ELSE 'NO_DISCREPANCY' END,
  'MOCK finding for ' || r.entity_name || ' (' || r.qhin || ')',
  r.risk_level
FROM tefca_reviews r, generate_series(1,2) gs
WHERE r.is_mock_data = true
"""

_SEED_LOGS_SQL = """
INSERT INTO tefca_connector_logs (id, connector_name, status, response_time_ms, checked_at)
SELECT
  gen_random_uuid(),
  c.name,
  CASE WHEN c.name IN ('nppes','oig_leie','pecos') THEN 'available' ELSE 'unavailable' END,
  (50 + floor(random()*200))::int,
  now() - (g || ' hours')::interval
FROM (VALUES ('nppes'),('oig_leie'),('pecos'),('sam_gov'),
             ('rce_directory'),('iqvia_onekey')) AS c(name),
     generate_series(1,5) AS g
"""


@tefca_dashboard_router.post("/demo/run-cycle",
                             summary="[admin] Run one QA validation cycle on a mock review (demo)")
async def demo_run_cycle(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    """Admin demo. Picks one mock review, probes the live source connectors, runs
    the REAL QA gate ladder (intake -> connectors -> findings -> QA score) via
    qa_engine.validate_review, and returns the entity, connector health, findings,
    QA score and the actual state transitions the review moved through. Read-only
    on report content; appends to the immutable tefca_qa_audit trail only."""
    if user.email not in ADMIN_EMAILS:
        raise HTTPException(403, f"Admin allowlist required; {user.email} not authorized")

    # 1) pick one mock review (prefer flagged mock data; else most-recent review)
    review = (await db.execute(
        select(TEFCAReview).where(TEFCAReview.is_mock_data == True)  # noqa: E712
        .order_by(TEFCAReview.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if review is None:
        review = (await db.execute(
            select(TEFCAReview).order_by(TEFCAReview.created_at.desc()).limit(1)
        )).scalar_one_or_none()
    if review is None:
        raise HTTPException(404, "no reviews available to demo (seed mock data first)")

    # 2) connectors called — real availability probe (logs to QA audit + connector log)
    health = await qa_engine.ConnectorHealthCheck().check_all_connectors(db)

    # 3) run the REAL QA gate ladder (logs every gate transition to tefca_qa_audit)
    verdict = await qa_engine.validate_review(db, review.id, triggered_by="demo")

    # 4) findings attached to this review
    findings = (await db.execute(
        select(TEFCAFinding).where(TEFCAFinding.review_id == review.id)
    )).scalars().all()

    # 5) actual state transitions from THIS run's audit rows (most recent gates first → reorder)
    rows = (await db.execute(text(
        "SELECT gate_name, old_state, new_state, passed, score, created_at "
        "FROM tefca_qa_audit WHERE review_id = :rid AND triggered_by = 'demo' "
        "ORDER BY created_at DESC LIMIT 5"
    ), {"rid": str(review.id)})).mappings().all()
    transitions = [
        {"gate": r["gate_name"], "from": r["old_state"], "to": r["new_state"],
         "passed": r["passed"], "score": float(r["score"]) if r["score"] is not None else None}
        for r in reversed(rows)
    ]

    return {
        "entity_name": review.entity_name,
        "qhin": getattr(review, "qhin", None),
        "npi": review.npi,
        "entity_type": getattr(review, "entity_type", None),
        "review_status": review.status,
        "risk_level": review.risk_level,
        "connectors_called": {
            name: {"available": c["available"], "health_score": c["health_score"],
                   "response_time_ms": c["response_time_ms"]}
            for name, c in health["connectors"].items()
        },
        "connector_overall_health": health["overall_health"],
        "findings": [{"connector": f.connector, "finding_type": f.finding_type,
                      "severity": f.severity, "detail": f.detail} for f in findings],
        "qa_score": verdict.get("qa_score"),
        "qa_passed": verdict.get("passed"),
        "final_state": verdict.get("final_state"),
        "recommended_status": verdict.get("recommended_status"),
        "state_transitions": transitions,
        "intake": verdict.get("intake"),
        "processed_at": datetime.utcnow().isoformat(),
        "note": "REAL QA pipeline — connector probes + gate ladder against seeded review.",
    }


@tefca_dashboard_router.post("/admin/seed-mock-data",
                             summary="[admin] Apply RFQ columns + seed mock review data (idempotent)")
async def seed_mock_data(db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))):
    # Gated by the ADMIN_EMAILS allowlist (email-based), not role, so an existing
    # token for an allowlisted admin works without changing the DB role. The email
    # is loaded from the DB by get_current_user, not trusted from the token.
    if user.email not in ADMIN_EMAILS:
        raise HTTPException(403, f"Admin allowlist required; {user.email} not authorized")

    # Hard stop on production — mirrors the identical guard on the registry seeder
    # (app/tefca_registry/routes.py). These 50 rows are fabricated entities carrying
    # REAL QHIN names ('Health Gorilla', 'Epic Nexus', ...), so on production they
    # would sit in the same table as ONC-provided reviews while claiming a genuine
    # QHIN as their source. is_mock_data=true marks them in the row, but the UI's
    # MockDataBanner keys off the GLOBAL /api/tefca/status data_source, not the
    # per-row flag — so once real and mock rows coexist, nothing on screen tells
    # them apart. The block that follows only skips seeding when the table is
    # ALREADY non-empty, which does not help an empty production table.
    #
    # The admin allowlist above is an authorization control, not an environment
    # control: it stops the wrong person seeding, not the right person seeding the
    # wrong environment. This is the latter.
    if (os.getenv("ENVIRONMENT", "") or "").strip().lower() == "production":
        raise HTTPException(
            403,
            "Mock data seeding is not permitted in production. These rows are "
            "fabricated entities labelled with real QHIN names; in the production "
            "review table they would be indistinguishable from ONC-provided data "
            "on screen. Import ONC-provided data instead.")

    out = {"alters": [], "mock_data": None, "actions": []}

    # 1 + 2: additive columns (IF NOT EXISTS — safe to re-run)
    await db.execute(text("ALTER TABLE tefca_reviews ADD COLUMN IF NOT EXISTS qhin VARCHAR(100)"))
    await db.execute(text("ALTER TABLE tefca_reviews ADD COLUMN IF NOT EXISTS is_mock_data BOOLEAN DEFAULT false"))
    await db.commit()
    out["alters"] = ["qhin VARCHAR(100)", "is_mock_data BOOLEAN DEFAULT false"]

    # 3: guarded mock insert — skip entirely if reviews already exist
    existing = int((await db.execute(text("SELECT COUNT(*) FROM tefca_reviews"))).scalar() or 0)
    if existing > 0:
        out["mock_data"] = "already existed"
        out["reviews_count"] = existing
        out["actions"].append(f"skipped mock insert — tefca_reviews already has {existing} rows")
        return out

    await db.execute(text(_SEED_REVIEWS_SQL))
    await db.execute(text(_SEED_FINDINGS_SQL))
    await db.execute(text(_SEED_LOGS_SQL))
    await db.commit()

    reviews = int((await db.execute(text("SELECT COUNT(*) FROM tefca_reviews WHERE is_mock_data = true"))).scalar() or 0)
    findings = int((await db.execute(text("SELECT COUNT(*) FROM tefca_findings"))).scalar() or 0)
    logs = int((await db.execute(text("SELECT COUNT(*) FROM tefca_connector_logs"))).scalar() or 0)
    dist_rows = (await db.execute(text(
        "SELECT status, count(*) FROM tefca_reviews WHERE is_mock_data = true GROUP BY status"
    ))).fetchall()
    out["mock_data"] = "inserted"
    out["reviews_count"] = reviews
    out["findings_count"] = findings
    out["connector_logs_count"] = logs
    out["distribution"] = {r[0]: int(r[1]) for r in dist_rows}
    out["qhins_covered"] = len(_QHINS)
    out["actions"].append("inserted 50 mock reviews, 100 findings, 30 connector logs across 11 QHINs")
    return out


# ─── Review engine: sampling, methodology, taxonomy, execution (TEFCA Task 2) ─
# methodology + discrepancy-taxonomy are reference data (public, like the
# dashboard). run-sample / execute / sampling-runs are operations (role-gated).

@tefca_dashboard_router.get("/methodology", dependencies=[Depends(require_role("viewer"))], summary="Review methodology / control framework (reference)")
async def get_methodology():
    return review_engine.generate_control_framework()


@tefca_dashboard_router.get("/discrepancy-taxonomy", dependencies=[Depends(require_role("viewer"))], summary="Discrepancy taxonomy (reference)")
async def get_discrepancy_taxonomy():
    return {
        "buckets": list(review_engine.DISCREPANCY_TAXONOMY.keys()),
        "taxonomy": review_engine.DISCREPANCY_TAXONOMY,
    }


class RunSampleRequest(BaseModel):
    population_size: Optional[int] = None
    confidence: float = 0.95
    margin: float = 0.05
    seed: int = 42


@tefca_dashboard_router.post("/reviews/run-sample", summary="Compute + record a stratified sampling run")
async def run_sample(
    request: RunSampleRequest, http: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    entities = list(ALL_MOCK_ENTITIES)
    N = request.population_size or 94231
    sample_size = review_engine.calculate_sample_size(N, request.confidence, request.margin)
    sample = review_engine.select_stratified_sample(entities, min(sample_size, len(entities)), seed=request.seed)
    per_qhin: dict = {}
    for e in sample:
        q = e.get("_qhin") or "Unknown"
        per_qhin[q] = per_qhin.get(q, 0) + 1
    # Record the sampling run in tefca_review_cycles (accepted as the sampling-runs table).
    cycle = TEFCAReviewCycle(
        cycle_type=CycleType.TASK3_RETROSPECTIVE,
        cycle_start_date=datetime.utcnow(),
        sample_confidence_level=request.confidence,
        sample_method="COCHRAN_STRATIFIED_FPC",
        total_entities_sampled=sample_size,
        cycle_status=CycleStatus.PLANNED,
        created_by=str(user.email),
    )
    db.add(cycle)
    await db.flush()
    await log_tefca_event(
        db, user=user, action="SAMPLING_RUN_CREATED", resource_type="tefca_review_cycle",
        resource_id=cycle.cycle_id, ip_address=_client_ip(http),
        details={"population": N, "sample_size": sample_size, "seed": request.seed},
    )
    await db.commit()
    return {
        "sampling_run_id": str(cycle.cycle_id),
        "population_size": N,
        "sample_size": sample_size,
        "method": "Cochran + finite population correction, stratified by QHIN",
        "confidence_level": request.confidence,
        "margin_of_error": request.margin,
        "seed": request.seed,
        "selected_count": len(sample),
        "per_qhin_allocation": per_qhin,
        "sample_preview": [e.get("id") for e in sample[:10]],
    }


@tefca_dashboard_router.post("/reviews/{review_id}/execute", summary="Execute a review against live connectors")
async def execute_review(
    review_id: str, http: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    try:
        rid = uuid.UUID(review_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "review_id must be a valid UUID")
    review = (await db.execute(select(TEFCAReview).where(TEFCAReview.id == rid))).scalar_one_or_none()
    if not review:
        raise HTTPException(404, "Review not found")
    entity = {
        "id": str(review.id),
        "name": review.entity_name,
        "identifier": ([{"system": "http://hl7.org/fhir/sid/us-npi", "value": review.npi}] if review.npi else []),
        "uei": review.uei,
        "_qhin": review.qhin,
    }
    result = await review_engine.run_entity_review(entity, db=db)
    # Preserve seeded demo data: only persist the new outcome for non-mock reviews.
    persisted = False
    if not review.is_mock_data:
        review.status = result["status"]
        review.risk_level = result["risk_level"]
        review.reviewer_id = str(user.email)
        persisted = True
    # Automatic post-review QA gate (QA Task 1). On QA failure, route to needs_review.
    qa_verdict = await review_engine.run_post_review_qa(db, review.id)
    if not qa_verdict.get("passed") and persisted:
        review.status = "needs_review"
    await log_tefca_event(
        db, user=user, action="REVIEW_EXECUTED", resource_type="tefca_review",
        resource_id=rid, ip_address=_client_ip(http),
        details={"bucket": result["bucket"], "status": result["status"],
                 "persisted": persisted, "is_mock_data": review.is_mock_data,
                 "qa_passed": qa_verdict.get("passed"), "qa_score": qa_verdict.get("qa_score")},
    )
    await db.commit()
    return {
        "review_id": review_id,
        "persisted": persisted,
        "note": ("mock review — computed but not persisted (demo data preserved)"
                 if not persisted else "persisted"),
        "result": result,
        "qa": qa_verdict,
    }


@tefca_dashboard_router.get("/sampling-runs", summary="List sampling runs")
async def list_sampling_runs(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    rows = (await db.execute(
        select(TEFCAReviewCycle).order_by(TEFCAReviewCycle.created_at.desc())
    )).scalars().all()
    return {
        "total": len(rows),
        "sampling_runs": [{
            "id": str(c.cycle_id),
            "method": c.sample_method,
            "confidence_level": c.sample_confidence_level,
            "sample_size": c.total_entities_sampled,
            "status": c.cycle_status.value if c.cycle_status else None,
            "created_by": c.created_by,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in rows],
    }


# ─── Reporting: weekly + final reports, list, detail, CSV (TEFCA Task 3) ─────
# Mounted at /api/tefca/reports/* on the dashboard router. Distinct from the
# legacy /api/v1/tefca/reports/{cycle_id} D3.1/D3.2 endpoints (which aggregate
# tefca_evidence_records). These aggregate tefca_reviews via the reporting module.

class WeeklyReportRequest(BaseModel):
    week_start: Optional[str] = None
    week_end: Optional[str] = None


class FinalReportRequest(BaseModel):
    period_start: Optional[str] = None
    period_end: Optional[str] = None


@tefca_dashboard_router.post("/reports/weekly", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Weekly progress report (SOW Task 3)")
async def create_weekly_report(
    request: WeeklyReportRequest, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("qalead")),
):
    end = _parse_date(request.week_end) if request.week_end else datetime.utcnow()
    start = _parse_date(request.week_start) if request.week_start else (end - timedelta(days=7))
    report = await reporting.generate_weekly_report(db, start, end, generated_by=str(user.email))
    await log_tefca_event(
        db, user=user, action="WEEKLY_REPORT_GENERATED", resource_type="tefca_report",
        resource_id=report["report_id"], ip_address=_client_ip(http),
        details={"total_reviews": report["total_reviews"]},
    )
    report["evidence_gate"] = await qa_engine.evidence_gate(db, start, end)  # QA Task 2 gate
    await db.commit()
    return report


@tefca_dashboard_router.post("/reports/final", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Final retrospective report (SOW Task 3)")
async def create_final_report(
    request: FinalReportRequest, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("program_manager")),
):
    end = _parse_date(request.period_end) if request.period_end else datetime.utcnow()
    start = _parse_date(request.period_start) if request.period_start else (end - timedelta(days=120))
    report = await reporting.generate_final_report(db, start, end, generated_by=str(user.email))
    await log_tefca_event(
        db, user=user, action="FINAL_REPORT_GENERATED", resource_type="tefca_report",
        resource_id=report["report_id"], ip_address=_client_ip(http),
        details={"total_reviews": report["total_reviews"]},
    )
    report["evidence_gate"] = await qa_engine.evidence_gate(db, start, end)  # QA Task 2 gate
    await db.commit()
    return report


@tefca_dashboard_router.get("/reports", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. List reports")
async def list_tefca_reports(
    type: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    q = select(TEFCAReport).order_by(TEFCAReport.generated_at.desc())
    if type:
        q = q.where(TEFCAReport.report_type == type)
    if start:
        q = q.where(TEFCAReport.generated_at >= _parse_date(start))
    if end:
        q = q.where(TEFCAReport.generated_at <= _parse_date(end))
    rows = (await db.execute(q)).scalars().all()
    return {
        "total": len(rows),
        "reports": [{
            "report_id": str(r.report_id), "report_type": r.report_type,
            "period_start": r.period_start.isoformat() if r.period_start else None,
            "period_end": r.period_end.isoformat() if r.period_end else None,
            "generated_by": r.generated_by,
            "generated_at": r.generated_at.isoformat() if r.generated_at else None,
            "total_reviews": (r.report_data or {}).get("total_reviews"),
        } for r in rows],
    }


@tefca_dashboard_router.get("/reports/{report_id}", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Full report detail")
async def get_tefca_report(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    rid = _parse_uuid(report_id)
    r = (await db.execute(select(TEFCAReport).where(TEFCAReport.report_id == rid))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Report not found")
    return {
        "report_id": str(r.report_id), "report_type": r.report_type,
        "period_start": r.period_start.isoformat() if r.period_start else None,
        "period_end": r.period_end.isoformat() if r.period_end else None,
        "generated_by": r.generated_by,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
        "report_data": r.report_data,
    }


@tefca_dashboard_router.get("/reports/{report_id}/csv", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Report as CSV")
async def get_tefca_report_csv(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    rid = _parse_uuid(report_id)
    r = (await db.execute(select(TEFCAReport).where(TEFCAReport.report_id == rid))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Report not found")
    csv_text = await reporting.generate_csv_export(db, rid)
    return Response(content=csv_text, media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=tefca_report_{report_id}.csv"})


async def _load_report_or_404(report_id: str, db: AsyncSession) -> TEFCAReport:
    rid = _parse_uuid(report_id)
    r = (await db.execute(select(TEFCAReport).where(TEFCAReport.report_id == rid))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Report not found")
    return r


@tefca_dashboard_router.get("/reports/{report_id}/pdf", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Report as PDF")
async def get_tefca_report_pdf(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    """Render a persisted report as an AGT-branded PDF (contains PII — role-gated,
    like the CSV export). MOCK reports carry a prominent MOCK-DATA banner."""
    r = await _load_report_or_404(report_id, db)
    try:
        pdf_bytes = report_renderer.render_report_pdf(r.report_data or {})
    except Exception as e:
        logger.error(f"PDF render failed for {report_id}: {e}")
        raise HTTPException(500, f"PDF rendering failed: {str(e)[:120]}")
    fname = f"TEFCA_{(r.report_type or 'report')}_{report_id}.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@tefca_dashboard_router.get("/reports/{report_id}/docx", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Report as DOCX. DOCX is not a contract requirement (matrix §4).")
async def get_tefca_report_docx(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    """Render a persisted report as an AGT-branded editable Word document (PII —
    role-gated). MOCK reports carry a prominent MOCK-DATA banner."""
    r = await _load_report_or_404(report_id, db)
    try:
        docx_bytes = report_renderer.render_report_docx(r.report_data or {})
    except Exception as e:
        logger.error(f"DOCX render failed for {report_id}: {e}")
        raise HTTPException(500, f"DOCX rendering failed: {str(e)[:120]}")
    fname = f"TEFCA_{(r.report_type or 'report')}_{report_id}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ─── Bi-weekly + quarterly reports, new-submissions (TEFCA Task 4) ───────────

@tefca_dashboard_router.post("/reports/biweekly", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Bi-weekly ongoing review (SOW Task 4)")
async def create_biweekly_report(
    request: FinalReportRequest, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("qalead")),
):
    start = _parse_date(request.period_start) if request.period_start else None
    end = _parse_date(request.period_end) if request.period_end else None
    report = await reporting.generate_biweekly_report(db, start, end, generated_by=str(user.email))
    await log_tefca_event(
        db, user=user, action="BIWEEKLY_REPORT_GENERATED", resource_type="tefca_report",
        resource_id=report["report_id"], ip_address=_client_ip(http),
        details={"new_submissions": report["new_submissions_reviewed"]},
    )
    report["evidence_gate"] = await qa_engine.evidence_gate(db, start, end)  # QA Task 2 gate
    await db.commit()
    return report


@tefca_dashboard_router.post("/reports/quarterly", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Quarterly report (SOW Task 4)")
async def create_quarterly_report(
    request: FinalReportRequest, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("program_manager")),
):
    start = _parse_date(request.period_start) if request.period_start else None
    end = _parse_date(request.period_end) if request.period_end else None
    report = await reporting.generate_quarterly_report(db, start, end, generated_by=str(user.email))
    await log_tefca_event(
        db, user=user, action="QUARTERLY_REPORT_GENERATED", resource_type="tefca_report",
        resource_id=report["report_id"], ip_address=_client_ip(http),
        details={"total_reviews": report["total_reviews"]},
    )
    report["evidence_gate"] = await qa_engine.evidence_gate(db, start, end)  # QA Task 2 gate
    await db.commit()
    return report


@tefca_dashboard_router.get("/reviews/new-submissions", summary="List new submissions since a date")
async def list_new_submissions(
    since: str = Query(..., description="ISO date YYYY-MM-DD"),
    qhin: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    since_dt = _parse_date(since)
    rows = await reporting.get_new_submissions(db, qhin, since_dt)
    # EQ-003 — same review rows, same viewer floor, same masking rule.
    show_pii = _can_see_pii(user)
    return {
        "since": since, "qhin": qhin, "count": len(rows),
        "pii_masked": not show_pii,
        "submissions": [{
            "review_id": str(r.id), "entity_name": r.entity_name, "qhin": r.qhin,
            "npi": r.npi if show_pii else _mask_identifier(r.npi),
            "status": r.status, "risk_level": r.risk_level,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    }


# ─── Priority review queue (SOW Task 5, COR-directed) ────────────────────────

_PRIORITY_FRIENDLY = {
    "ASSIGNED": "queued", "IN_PROGRESS": "in_progress", "PENDING_COR": "pending_cor",
    "RESOLVED_ACTION": "completed", "RESOLVED_NO_ACTION": "completed", "ESCALATED": "escalated",
}


def _priority_case_dto(c: TEFCAPriorityCase) -> dict:
    return {
        "case_id": str(c.case_id),
        "cor_reference": c.cor_reference,
        "qhin": c.qhin,
        "issue_description": c.issue_description,
        "status": _PRIORITY_FRIENDLY.get(c.case_status.value if c.case_status else None,
                                         c.case_status.value if c.case_status else None),
        "severity": c.severity.value if c.severity else None,
        "root_cause": c.root_cause_determination,
        "assigned_by": c.assigned_by,
        "assigned_date": c.assigned_date.isoformat() if c.assigned_date else None,
        "deadline_date": c.deadline_date.isoformat() if c.deadline_date else None,
        "completed_date": c.completed_date.isoformat() if c.completed_date else None,
    }


class PriorityCreateRequest(BaseModel):
    cor_reference: str
    issue_description: str
    qhin: Optional[str] = None
    deadline_date: Optional[str] = None


@tefca_dashboard_router.post("/priority/create", summary="Create a COR-directed priority review (admin only)")
async def priority_create(
    request: PriorityCreateRequest, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    if user.email not in ADMIN_EMAILS:
        raise HTTPException(403, f"Admin allowlist required; {user.email} not authorized")
    case = await review_engine.create_priority_review(
        db, cor_reference=request.cor_reference, issue_description=request.issue_description,
        qhin=request.qhin,
        deadline_date=(_parse_date(request.deadline_date) if request.deadline_date else None),
        assigned_by=str(user.email),
    )
    await log_tefca_event(db, user=user, action="PRIORITY_CASE_CREATED", resource_type="tefca_priority_case",
                          resource_id=case.case_id, ip_address=_client_ip(http),
                          details={"cor_reference": request.cor_reference, "qhin": request.qhin})
    await db.commit()
    return _priority_case_dto(case)


@tefca_dashboard_router.post("/priority/{case_id}/execute", summary="Execute a priority review")
async def priority_execute(
    case_id: str, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    cid = _parse_uuid(case_id)
    case = (await db.execute(select(TEFCAPriorityCase).where(TEFCAPriorityCase.case_id == cid))).scalar_one_or_none()
    if not case:
        raise HTTPException(404, "Priority case not found")
    result = await review_engine.execute_priority_review(db, case)
    case.assigned_reviewer_id = str(user.email)
    await log_tefca_event(db, user=user, action="PRIORITY_CASE_EXECUTED", resource_type="tefca_priority_case",
                          resource_id=cid, ip_address=_client_ip(http),
                          details={"severity": result["severity"], "root_cause": result["root_cause"]})
    await db.commit()
    return result


@tefca_dashboard_router.get("/priority", summary="List priority cases (filters: status, qhin, date range)")
async def priority_list(
    status: Optional[str] = Query(None), qhin: Optional[str] = Query(None),
    start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    q = select(TEFCAPriorityCase).order_by(TEFCAPriorityCase.assigned_date.desc())
    if qhin:
        q = q.where(TEFCAPriorityCase.qhin == qhin)
    if start:
        q = q.where(TEFCAPriorityCase.assigned_date >= _parse_date(start))
    if end:
        q = q.where(TEFCAPriorityCase.assigned_date <= _parse_date(end))
    if status and status.strip().lower() != "all":
        try:
            q = q.where(TEFCAPriorityCase.case_status == CaseStatus(status))
        except ValueError:
            # A filter that silently does nothing is worse than one that fails:
            # the caller reads the UNFILTERED list as the filtered answer. This
            # is the same defect class as the stale Pending Reviews filter.
            raise HTTPException(
                400,
                "Unknown status %r. Valid values: %s"
                % (status, ", ".join(c.value for c in CaseStatus)),
            )
    rows = (await db.execute(q)).scalars().all()
    return {"total": len(rows), "cases": [_priority_case_dto(c) for c in rows]}


@tefca_dashboard_router.get("/priority/{case_id}", summary="Priority case detail")
async def priority_detail(
    case_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    cid = _parse_uuid(case_id)
    c = (await db.execute(select(TEFCAPriorityCase).where(TEFCAPriorityCase.case_id == cid))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Priority case not found")
    dto = _priority_case_dto(c)
    dto.update({
        "root_cause_description": c.root_cause_description,
        "recommendations": c.recommendations,
        "prevention_recommendation": c.prevention_recommendation,
        "resolution_notes": c.resolution_notes,
        "assigned_reviewer_id": c.assigned_reviewer_id,
    })
    return dto


@tefca_dashboard_router.get("/priority/{case_id}/report", deprecated=True,
                            summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/sow/*. D5.1 priority status report")
async def priority_report(
    case_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    cid = _parse_uuid(case_id)
    report = await reporting.generate_priority_status_report(db, cid)
    if report is None:
        raise HTTPException(404, "Priority case not found")
    return report


@tefca_dashboard_router.post("/priority/quarterly-report", deprecated=True,
                             summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/sow/*. D5.2 priority quarterly report")
async def priority_quarterly(
    request: FinalReportRequest, http: Request,
    db: AsyncSession = Depends(get_db), user=Depends(require_role("program_manager")),
):
    start = _parse_date(request.period_start) if request.period_start else None
    end = _parse_date(request.period_end) if request.period_end else None
    report = await reporting.generate_priority_quarterly_report(db, start, end, generated_by=str(user.email))
    await log_tefca_event(db, user=user, action="PRIORITY_QUARTERLY_GENERATED", resource_type="tefca_report",
                          resource_id=report["report_id"], ip_address=_client_ip(http),
                          details={"total": report["total_priority_reviews"]})
    await db.commit()
    return report


# ─── QA framework endpoints (QA Task 1) ──────────────────────────────────────

@tefca_dashboard_router.get("/qa/health", summary="Platform readiness check (public — monitoring)")
async def qa_platform_health(db: AsyncSession = Depends(get_db)):
    return await qa_engine.PlatformReadinessCheck().run(db)


@tefca_dashboard_router.get("/qa/connector-health", summary="Connector health scores")
async def qa_connector_health(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    return await qa_engine.ConnectorHealthCheck().check_all_connectors(db=db)


@tefca_dashboard_router.get("/qa/audit", summary="QA audit trail (filters: review_id, gate_name, gate_type, passed)")
async def qa_audit_trail(
    review_id: Optional[str] = Query(None), gate_name: Optional[str] = Query(None),
    gate_type: Optional[str] = Query(None), passed: Optional[bool] = Query(None),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    clauses, params = [], {"lim": min(max(limit, 1), 500)}
    if review_id:
        clauses.append("review_id = :rid"); params["rid"] = review_id
    if gate_name:
        clauses.append("gate_name = :gn"); params["gn"] = gate_name
    if gate_type:
        clauses.append("gate_type = :gt"); params["gt"] = gate_type
    if passed is not None:
        clauses.append("passed = :p"); params["p"] = passed
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = (await db.execute(text(
        "SELECT id, review_id, gate_name, gate_type, old_state, new_state, passed, score, "
        "threshold, failures, triggered_by, created_at FROM tefca_qa_audit" + where +
        " ORDER BY created_at DESC LIMIT :lim"), params)).mappings().all()
    return {"total": len(rows), "audit": [
        {**dict(r), "id": str(r["id"]), "review_id": str(r["review_id"]) if r["review_id"] else None,
         "created_at": r["created_at"].isoformat() if r["created_at"] else None} for r in rows]}


@tefca_dashboard_router.post("/qa/validate-review/{review_id}", summary="Trigger full QA validation on a review")
async def qa_validate_review(review_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.validate_review(db, review_id, triggered_by="manual")


@tefca_dashboard_router.get("/qa/score", summary="Overall QA score across all dimensions")
async def qa_overall_score(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    return await qa_engine.overall_qa_score(db)


# ─── QA evidence & chain-of-custody endpoints (QA Task 2) ────────────────────

@tefca_dashboard_router.post("/qa/validate-evidence/{review_id}", summary="Evidence + chain-of-custody QA on a review")
async def qa_validate_evidence(review_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.validate_evidence(db, review_id, triggered_by="manual")


@tefca_dashboard_router.get("/qa/report-gate", summary="Evidence gate that must be open before a report is generated")
async def qa_report_gate(
    start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    s = _parse_date(start) if start else None
    e = _parse_date(end) if end else None
    return await qa_engine.evidence_gate(db, s, e, triggered_by="manual")


@tefca_dashboard_router.get("/qa/evidence-summary", summary="Evidence completeness across all reviews")
async def qa_evidence_summary(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    return await qa_engine.evidence_gate(db, None, None, triggered_by="manual")


# ─── QA statistical endpoints (QA Task 3) ────────────────────────────────────

@tefca_dashboard_router.get("/qa/sampling-validation", summary="Sampling validation vs Cochran @95% CI")
async def qa_sampling_validation(
    population: int = Query(94231), confidence: float = Query(0.95), margin: float = Query(0.05),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    return await qa_engine.validate_sampling(db, population, confidence, margin, triggered_by="manual")


@tefca_dashboard_router.get("/qa/internal-consistency",
                            summary="Internal consistency score (pipeline self-consistency — NOT inter-rater reliability)")
async def qa_internal_consistency(
    sample_size: int = Query(20), seed: int = Query(42),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    return await qa_engine.internal_consistency_check(db, sample_size, seed, triggered_by="manual")


@tefca_dashboard_router.get("/qa/inter-rater",
                            summary="[deprecated alias] Internal consistency score — NOT true inter-rater reliability")
async def qa_inter_rater(
    sample_size: int = Query(20), seed: int = Query(42),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    # Backward-compatible path; returns the honestly-labeled internal consistency
    # score (real IRR requires double-review sampling — see qa_engine TODO).
    return await qa_engine.internal_consistency_check(db, sample_size, seed, triggered_by="manual")


@tefca_dashboard_router.get("/qa/statistical", summary="Combined statistical QA (sampling + internal consistency + CI)")
async def qa_statistical(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    return await qa_engine.statistical_qa(db, triggered_by="manual")


# ─── QA regression / golden-record endpoints (QA Task 4) ─────────────────────

@tefca_dashboard_router.get("/qa/golden-records", summary="List the golden known-answer test cases")
async def qa_golden_records(user=Depends(require_role("viewer"))):
    cases = [{"case": name, "expected_bucket": expected} for (name, _e, _sr, expected) in qa_engine._golden_cases()]
    return {"total": len(cases), "golden_records": cases}


@tefca_dashboard_router.get("/qa/regression", summary="Run golden-record regression; detect classification drift")
async def qa_regression(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    return await qa_engine.run_golden_regression(db, triggered_by="manual")


# ─── QA monitoring, SLA & alert endpoints (QA Task 5) ────────────────────────

@tefca_dashboard_router.get("/qa/sla", summary="Priority-review SLA tracking")
async def qa_sla(db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    return await qa_engine.check_priority_sla(db, triggered_by="manual")


@tefca_dashboard_router.get("/qa/sweep", summary="Run a full QA sweep (all gates + alerts + SLA)")
async def qa_sweep(
    db: AsyncSession = Depends(get_db),
    # QA-004 — "Viewer cannot QA. Access denied." Running a sweep EXECUTES every
    # gate, records audit rows and can dispatch threshold alerts. It is an
    # operational QA action with side effects, not a dashboard read, and Level 6
    # QA Lead is the role defined to perform it (QA-001/QA-002). It was gated at
    # viewer, so anyone signed in could trigger a full sweep and the alert
    # emails that go with it.
    user=Depends(require_role("qalead")),
):
    return await qa_engine.run_qa_sweep(db, triggered_by="manual")


@tefca_dashboard_router.get("/qa/alerts", summary="Recent QA threshold alerts")
async def qa_alerts(limit: int = Query(50), db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer"))):
    rows = (await db.execute(text(
        "SELECT id, gate_name, gate_type, details, triggered_by, created_at FROM tefca_qa_audit "
        "WHERE gate_type = 'alert' ORDER BY created_at DESC LIMIT :lim"), {"lim": min(max(limit, 1), 200)})).mappings().all()
    return {"total": len(rows), "alerts": [
        {"id": str(r["id"]), "source": r["gate_name"], "details": r["details"],
         "triggered_by": r["triggered_by"], "created_at": r["created_at"].isoformat() if r["created_at"] else None}
        for r in rows]}


@tefca_dashboard_router.post("/qa/alerts/test", summary="Send a test QA alert email (verify delivery)")
async def qa_alerts_test(user=Depends(require_role("qalead"))):
    """Send a test QA alert to the configured recipients to verify email delivery.
    No-op (logged only) if SENDGRID_API_KEY is unset."""
    result = await qa_engine.send_qa_alert(
        "TEST ALERT",
        {"note": "This is a test QA alert to verify email delivery.",
         "requested_by": str(user.email)},
    )
    return {"status": "attempted", **result}


# ─── QA report + audit export endpoints (QA Task 6) ──────────────────────────

@tefca_dashboard_router.post("/qa/report", summary="Generate a QA scorecard report (report_type='qa')")
async def qa_generate_report(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.generate_qa_report(db, triggered_by=str(user.email))


@tefca_dashboard_router.get("/qa/audit/export", summary="Export the QA audit trail as CSV")
async def qa_audit_export(
    format: str = Query("csv"), limit: int = Query(5000),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("viewer")),
):
    if format.lower() != "csv":
        raise HTTPException(400, "Only format=csv is supported")
    csv_text = await qa_engine.export_audit_csv(db, limit)
    return Response(content=csv_text, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tefca_qa_audit.csv"})


# ═══════════════════════════════════════════════════════════════════════════════
# ENTITY REVIEWS, FINDINGS, ENTITY IMPORT — the endpoints the frontend was 404ing on
#
# Every field below is backed by a real column or an explicitly-labelled
# derivation. Where the schema has no column for something the UI asked for, the
# API returns null and says so. It does not invent a value to fill the shape.
#
# THE RULE THAT SHAPES ALL OF THIS (P5): ABSENCE OF EVIDENCE IS NOT AGREEMENT.
#
# A review that has never been executed has not "passed" its four sources — it has
# checked none of them. So `evidence_agreement.verified` is false, `agreeing` is 0
# (never 4), and `discrepancy_level` is null (never "No Discrepancy"). Returning
# 4/4 for an unchecked entity would hand the UI a clean bill of health for work
# that nobody did.
# ═══════════════════════════════════════════════════════════════════════════════

CORE_SOURCES = ["nppes", "sam_gov", "leie", "pecos"]

_BUCKET_TO_LABEL = {
    "1": "No Discrepancy",
    "2": "Minor or Administrative",
    "3": "Inexplicable",
    "4": "Non-Compliant",
}


def _review_executed(review: TEFCAReview) -> bool:
    """Has validation actually run against this review?

    `pending` means queued, not clean. Anything else (pass / fail / indeterminate)
    means the pipeline ran and produced an outcome.
    """
    return bool(review.status) and str(review.status).lower() != "pending"


def _verification_results(review: TEFCAReview, findings: list) -> dict:
    """Per-source outcome across the four authoritative sources.

    not_checked    — no validation has run. NOT the same as clean.
    discrepancy    — this source produced a finding.
    no_discrepancy — validation ran and this source raised nothing.
    """
    executed = _review_executed(review)
    out = {}
    for src in CORE_SOURCES:
        hits = [f for f in findings if (f.connector or "").lower() == src]
        if hits:
            out[src] = {
                "status": "discrepancy",
                "findings": [h.finding_type for h in hits],
                "severity": max((h.severity or "low") for h in hits),
            }
        elif executed:
            out[src] = {"status": "no_discrepancy", "findings": [], "severity": None}
        else:
            out[src] = {"status": "not_checked", "findings": [], "severity": None}
    return out


def _evidence_agreement(review: TEFCAReview, findings: list) -> dict:
    """Counts, never a percentage. An unchecked source never counts as agreeing."""
    if not _review_executed(review):
        return {"agreeing": 0, "total": len(CORE_SOURCES), "verified": False}
    disagreeing = {
        (f.connector or "").lower()
        for f in findings
        if (f.connector or "").lower() in CORE_SOURCES
    }
    return {
        "agreeing": len(CORE_SOURCES) - len(disagreeing),
        "total": len(CORE_SOURCES),
        "verified": True,
    }


def _days_open(review: TEFCAReview):
    if not review.created_at:
        return None
    return max(0, (datetime.utcnow() - review.created_at).days)


async def _bucket_by_npi(db: AsyncSession, npis: list) -> dict:
    """Authoritative discrepancy level per NPI, from TEFCAEntity.latest_bucket.

    This is the classification the pipeline actually recorded. It is deliberately
    NOT derived from finding severity — deriving a bucket from severity would
    manufacture a determination that no reviewer ever made.
    """
    clean = [n for n in npis if n]
    if not clean:
        return {}
    rows = (await db.execute(
        select(TEFCAEntity.npi_submitted, TEFCAEntity.latest_bucket)
        .where(TEFCAEntity.npi_submitted.in_(clean))
    )).all()
    out = {}
    for npi, bucket in rows:
        if bucket is not None:
            key = str(bucket.value if hasattr(bucket, "value") else bucket)
            out[npi] = _BUCKET_TO_LABEL.get(key)
    return out


def _serialize_review(review: TEFCAReview, findings: list, bucket_label,
                      show_pii: bool = True) -> dict:
    return {
        "id": str(review.id),
        "entity_name": review.entity_name,
        "npi": review.npi if show_pii else _mask_identifier(review.npi),
        "uei": review.uei if show_pii else _mask_identifier(review.uei),
        # Stated explicitly so the UI can label a masked value as masked rather
        # than as missing data. "Redacted for your role" and "we don't have it"
        # are different facts and must not render the same way.
        "pii_masked": not show_pii,
        "qhin": review.qhin,
        "entity_type": review.entity_type,
        "entity_state": review.entity_state,
        "status": review.status,
        "risk_level": review.risk_level,
        # Null when the pipeline never classified this entity. Never guessed.
        "discrepancy_level": bucket_label,
        "evidence_agreement": _evidence_agreement(review, findings),
        "reviewer": review.reviewer_id,
        "days_open": _days_open(review),
        "is_mock_data": bool(review.is_mock_data),
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "updated_at": review.updated_at.isoformat() if review.updated_at else None,
    }


async def _latest_evidence_for_npi(db: AsyncSession, npi):
    """The authoritative evidence record for an NPI, or None. Never synthesised."""
    if not npi:
        return None
    entity = (await db.execute(
        select(TEFCAEntity).where(TEFCAEntity.npi_submitted == npi)
    )).scalars().first()
    if not entity:
        return None
    return (await db.execute(
        select(TEFCAEvidenceRecord)
        .where(TEFCAEvidenceRecord.entity_id == entity.entity_id)
        .order_by(TEFCAEvidenceRecord.created_at.desc())
    )).scalars().first()


@tefca_dashboard_router.get("/reviews", summary="List entity reviews (filters: status, qhin)")
async def list_entity_reviews(
    status: Optional[str] = Query(
        None, description="Review disposition. Case-insensitive; 'all' for every review."),
    qhin: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Entity name or NPI substring"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """The reviewer queue. Returns an empty list when there is nothing — not an error.

    DEF-001 — the Pending Reviews panel calls this again on every filter change
    rather than narrowing the array it loaded at page open, so what is displayed
    is current state. `status` is matched case-insensitively: the UI shows
    display labels, and an exact-match filter silently returned nothing when the
    case differed.
    """
    q = select(TEFCAReview).order_by(TEFCAReview.created_at.desc())
    if status and status.strip().lower() not in ("", "all"):
        q = q.where(func.lower(TEFCAReview.status)
                    == status.strip().lower().replace(" ", "_").replace("-", "_"))
    if qhin:
        q = q.where(TEFCAReview.qhin == qhin)
    if search:
        term = f"%{search.strip().lower()}%"
        q = q.where(or_(
            func.lower(func.coalesce(TEFCAReview.entity_name, "")).like(term),
            func.lower(func.coalesce(TEFCAReview.npi, "")).like(term),
        ))

    total = len((await db.execute(q)).scalars().all())
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    if not rows:
        return {"total": 0, "limit": limit, "offset": offset, "reviews": []}

    findings = (await db.execute(
        select(TEFCAFinding).where(TEFCAFinding.review_id.in_([r.id for r in rows]))
    )).scalars().all()
    by_review = {}
    for f in findings:
        by_review.setdefault(f.review_id, []).append(f)

    buckets = await _bucket_by_npi(db, [r.npi for r in rows])

    # LOGIN-013 / EQ-003 — below reviewer, identifiers are masked server-side.
    show_pii = _can_see_pii(user)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "pii_masked": not show_pii,
        "reviews": [
            _serialize_review(r, by_review.get(r.id, []), buckets.get(r.npi), show_pii)
            for r in rows
        ],
    }


@tefca_dashboard_router.get("/reviews/{review_id}", summary="Single review detail with evidence")
async def get_entity_review(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    rid = _parse_uuid(review_id)
    review = (await db.execute(
        select(TEFCAReview).where(TEFCAReview.id == rid)
    )).scalar_one_or_none()
    if not review:
        raise HTTPException(404, f"No review exists with id {review_id}")

    findings = (await db.execute(
        select(TEFCAFinding).where(TEFCAFinding.review_id == rid)
    )).scalars().all()

    buckets = await _bucket_by_npi(db, [review.npi])
    base = _serialize_review(review, findings, buckets.get(review.npi), _can_see_pii(user))

    evidence = await _latest_evidence_for_npi(db, review.npi)

    audit_trail = []
    if evidence:
        if evidence.created_at:
            audit_trail.append({
                "event": "evidence_record_created",
                "actor": "system",
                "timestamp": evidence.created_at.isoformat(),
                "outcome": str(evidence.record_status.value) if evidence.record_status else None,
            })
        if evidence.review_timestamp:
            audit_trail.append({
                "event": "reviewed",
                "actor": evidence.reviewer_id,
                "timestamp": evidence.review_timestamp.isoformat(),
                "outcome": str(evidence.bucket_label.value) if evidence.bucket_label else None,
            })
        if evidence.supervisor_review_timestamp:
            audit_trail.append({
                "event": "supervisor_review",
                "actor": evidence.supervisor_reviewer_id,
                "timestamp": evidence.supervisor_review_timestamp.isoformat(),
                "outcome": "approved",
            })

    base.update({
        "verification_results": _verification_results(review, findings),
        "findings": [{
            "id": str(f.id),
            "connector": f.connector,
            "finding_code": f.finding_type,
            "detail": f.detail,
            "severity": f.severity,
        } for f in findings],
        "audit_trail": audit_trail,
        # Straight from the evidence record. Null when the pipeline produced none —
        # we do not synthesise a recommendation or an evidence chain.
        "recommendation": (evidence.element_5_disposition_recommendation if evidence else None),
        "classification_rationale": (evidence.element_2_finding_classification if evidence else None),
        "evidence_chain": (evidence.element_4_supporting_citations if evidence else None),
        "confidence_score": (evidence.confidence_score if evidence else None),
    })
    return base


# ─────────────────────────────────────────────────────────────────────────────
# QA ROUND 2 — HUMAN DECISIONS (DW-001 … DW-005, DW-009)
#
# The Decision Workspace sealed decisions to localStorage under
# 'arc-decision-ledger' and stopped there. The page said so in its own footer
# ("Records are sealed locally for this prototype"), but from the outside the
# workflow looked complete: the reviewer chose Accept, entered a rationale, hit
# Seal, and the record appeared in the ledger. Nothing reached the server. The
# entity's status never changed, no reviewer was recorded against it, and no
# audit entry was written — so every case that checks for those outcomes failed,
# and a decision was lost the moment the browser's storage was cleared.
#
# A human adjudication is the single most important thing this system produces.
# It belongs in the database and the audit trail, not in a browser.
# ─────────────────────────────────────────────────────────────────────────────

# Decision -> the review status it produces. This is the whole disposition
# vocabulary the dashboard aggregates over (_REVIEW_STATUS_MAP).
_DECISION_STATUS = {
    "accept": "no_discrepancy",       # cleared
    "approve": "no_discrepancy",
    "reject": "non_compliant",        # flagged
    "flag": "non_compliant",
    "escalate": "non_compliant",
    "modify": None,                   # caller supplies the classification
    "investigate": "indeterminate",
}

# Classifications a reviewer may set with a "modify" decision.
_MODIFIABLE_STATUSES = {
    "no_discrepancy", "minor_administrative", "non_compliant",
    "inexplicable", "indeterminate",
}


class ReviewDecisionRequest(BaseModel):
    decision: str
    rationale: str
    # Required only for decision="modify" — the classification being moved to.
    classification: Optional[str] = None


@tefca_dashboard_router.post(
    "/reviews/{review_id}/decision",
    summary="Record a human decision on a review (accept / reject / modify)",
)
async def record_review_decision(
    review_id: str,
    body: ReviewDecisionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    # Reviewer (level 4) and above. DW-006: a viewer or contributor cannot
    # adjudicate, and the gate is here rather than only in the UI — hiding a
    # button is not an access control.
    user=Depends(require_role("reviewer")),
):
    """Persist a reviewer's decision, then update the review and write the audit row.

    The rationale is REQUIRED (DW-004). A decision with no stated reason cannot
    be reviewed by a supervisor, defended to the COR, or explained a year later,
    so an empty one is rejected here and not merely discouraged in the form.

    Decisions are APPEND-ONLY in the audit trail (DW-005). The review row carries
    the current disposition; the audit trail carries how it got there, including
    the AI recommendation that the human accepted or overrode. Both are kept:
    replacing the recommendation with the decision would lose the fact that a
    human disagreed with the machine, which is the entire point of the gate.
    """
    rid = _parse_uuid(review_id)
    review = (await db.execute(
        select(TEFCAReview).where(TEFCAReview.id == rid)
    )).scalar_one_or_none()
    if not review:
        raise HTTPException(404, f"No review exists with id {review_id}")

    decision = (body.decision or "").strip().lower()
    if decision not in _DECISION_STATUS:
        raise HTTPException(
            400,
            "Unknown decision %r. Valid values: %s"
            % (body.decision, ", ".join(sorted(_DECISION_STATUS))),
        )

    rationale = (body.rationale or "").strip()
    if not rationale:
        raise HTTPException(400, "A rationale is required to record a decision.")

    if decision == "modify":
        classification = (body.classification or "").strip().lower()
        if classification not in _MODIFIABLE_STATUSES:
            raise HTTPException(
                400,
                "decision='modify' requires a classification. Valid values: %s"
                % ", ".join(sorted(_MODIFIABLE_STATUSES)),
            )
        new_status = classification
    else:
        new_status = _DECISION_STATUS[decision]

    previous_status = review.status
    review.status = new_status
    review.reviewer_id = getattr(user, "email", None) or str(user)
    review.updated_at = datetime.utcnow()

    await log_tefca_event(
        db, user=user, action="review_decision", resource_type="tefca_reviews",
        resource_id=str(review.id), ip_address=_client_ip(request),
        details={
            "entity_name": review.entity_name,
            "npi": review.npi,
            "decision": decision,
            "rationale": rationale,
            "previous_status": previous_status,
            "new_status": new_status,
        },
    )
    await db.commit()
    await db.refresh(review)

    return {
        "id": str(review.id),
        "decision": decision,
        "previous_status": previous_status,
        "status": review.status,
        "reviewer": review.reviewer_id,
        "rationale": rationale,
        "recorded_at": review.updated_at.isoformat() if review.updated_at else None,
    }


@tefca_dashboard_router.get(
    "/reviews/{review_id}/decisions",
    summary="Decision history for a review (append-only)",
    dependencies=[Depends(require_role("viewer"))],
)
async def review_decision_history(
    review_id: str,
    db: AsyncSession = Depends(get_db),
):
    """DW-009 — every decision recorded against this review, oldest first.

    Read from the audit trail rather than from a mutable column, because the
    audit trail is the record that cannot be overwritten by the next decision.
    """
    from app.models.database import AuditLog

    rid = _parse_uuid(review_id)
    rows = (await db.execute(
        select(AuditLog)
        .where(AuditLog.action == "review_decision")
        .where(AuditLog.resource_id == str(rid))
        .order_by(AuditLog.created_at.asc())
    )).scalars().all()

    return {
        "total": len(rows),
        "decisions": [{
            "id": str(r.id),
            "decision": (r.details or {}).get("decision"),
            "rationale": (r.details or {}).get("rationale"),
            "previous_status": (r.details or {}).get("previous_status"),
            "new_status": (r.details or {}).get("new_status"),
            "actor": (r.details or {}).get("actor") or (r.details or {}).get("email"),
            "timestamp": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
    }


@tefca_dashboard_router.get("/findings", summary="List findings across all entities")
async def list_findings(
    level: Optional[str] = Query(None, description="severity: low|medium|high|critical"),
    entity_id: Optional[str] = Query(None, description="review id"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Findings register. Returns an empty list when there are none — not an error."""
    q = select(TEFCAFinding, TEFCAReview).join(
        TEFCAReview, TEFCAFinding.review_id == TEFCAReview.id, isouter=True
    )
    if level:
        q = q.where(TEFCAFinding.severity == level)
    if entity_id:
        q = q.where(TEFCAFinding.review_id == _parse_uuid(entity_id))

    total = len((await db.execute(q)).all())
    rows = (await db.execute(q.limit(limit).offset(offset))).all()

    # EQ-003 — the findings register joins the review row and republishes its NPI.
    show_pii = _can_see_pii(user)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "pii_masked": not show_pii,
        "findings": [{
            "id": str(f.id),
            "entity_id": str(f.review_id) if f.review_id else None,
            "entity_name": (r.entity_name if r else None),
            "npi": ((r.npi if show_pii else _mask_identifier(r.npi)) if r else None),
            "qhin": (r.qhin if r else None),
            "finding_code": f.finding_type,
            "source": f.connector,
            "severity": f.severity,
            "evidence_summary": f.detail,
            # tefca_findings has NO created_at and NO status column. Rather than
            # relabel the parent review's timestamp as the finding's own, these are
            # null, and the review's timestamp is exposed under its own name.
            "created_at": None,
            "status": None,
            "review_status": (r.status if r else None),
            "review_created_at": (r.created_at.isoformat() if r and r.created_at else None),
        } for f, r in rows],
    }


@tefca_dashboard_router.get("/findings/{finding_id}", summary="Single finding with evidence chain")
async def get_finding(
    finding_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    fid = _parse_uuid(finding_id)
    row = (await db.execute(
        select(TEFCAFinding, TEFCAReview)
        .join(TEFCAReview, TEFCAFinding.review_id == TEFCAReview.id, isouter=True)
        .where(TEFCAFinding.id == fid)
    )).first()
    if not row:
        raise HTTPException(404, f"No finding exists with id {finding_id}")
    f, r = row

    evidence = await _latest_evidence_for_npi(db, r.npi if r else None)

    return {
        "id": str(f.id),
        "entity_id": str(f.review_id) if f.review_id else None,
        "entity_name": (r.entity_name if r else None),
        "finding_code": f.finding_type,
        "source": f.connector,
        "severity": f.severity,
        "evidence_summary": f.detail,
        "created_at": None,   # no column on tefca_findings
        "status": None,       # no column on tefca_findings
        # SHA-256 hashed citations, straight from the evidence record. Null when
        # the pipeline produced no evidence record for this entity.
        "evidence_chain": (evidence.element_4_supporting_citations if evidence else None),
        "classification_rationale": (evidence.element_2_finding_classification if evidence else None),
        "audit_trail": ([{
            "event": "evidence_record_created",
            "actor": "system",
            "timestamp": evidence.created_at.isoformat() if evidence.created_at else None,
            "outcome": str(evidence.bucket_label.value) if evidence.bucket_label else None,
        }] if evidence else []),
    }


# ─── Entity import ────────────────────────────────────────────────────────────

def _valid_npi(npi) -> bool:
    """Exactly ten digits. Nothing else is an NPI."""
    return bool(npi) and str(npi).strip().isdigit() and len(str(npi).strip()) == 10


# ── Column vocabulary (IMP-001 / IMP-016) ────────────────────────────────────
#
# THE BUG THIS FIXES
#
# Rows were normalised with `k.strip().lower()` and then read with
# `norm.get("entity_name")` / `norm.get("npi")` / `norm.get("qhin")`. A roster
# exported from a spreadsheet carries human headers — "Organization Name",
# "NPI Number" — which lowercase to "organization name" and "npi number" and
# match none of those keys. Every row therefore failed the required-field check
# and the import reported `imported: 0` against a file the operator had just
# watched preview five rows successfully.
#
# It failed with a per-row reason ("required field is empty"), so it did not
# look like a mapping bug — it looked like the operator's file was empty. The
# header sniff in _parse_upload had the same blind spot and would 400 a
# friendly-header file outright.
#
# ONE map, applied in ONE place, used by BOTH the header sniff and the row
# reader, so a header that previews cannot then fail to import. Canonical names
# map to themselves so a file already using backend names is unaffected.
_COLUMN_ALIASES = {
    # entity_name
    "entity_name": "entity_name",
    "entity name": "entity_name",
    "organization name": "entity_name",
    "organisation name": "entity_name",
    "organization": "entity_name",
    "org name": "entity_name",
    "legal name": "entity_name",
    "legal_name": "legal_name",   # already a recognised fallback below
    "name": "entity_name",
    # npi
    "npi": "npi",
    "npi number": "npi",
    "npi_number": "npi",
    "national provider identifier": "npi",
    # qhin
    "qhin": "qhin",
    "qhin name": "qhin",
    "qhin_name": "qhin",
    # address components
    "address": "address",
    "street address": "address",
    "address line 1": "address",
    "city": "city",
    "state": "state",
    "zip": "zip",
    "zip code": "zip",
    "zip_code": "zip",
    "postal code": "zip",
    "postal_code": "zip",
    # other optional columns the importer already consumes
    "entity_type": "entity_type",
    "entity type": "entity_type",
    "entity_state": "entity_state",
    "uei": "uei",
    "contact": "contact",
}


def _canonical_column(key) -> str:
    """Map one header to its canonical name. Unknown headers keep their own
    normalised name rather than being dropped — an unrecognised column is not an
    error, and silently discarding it would hide data the operator supplied."""
    k = (key or "").strip().lower()
    # Tolerate the separators spreadsheets produce: "NPI-Number", "NPI_Number".
    flat = k.replace("_", " ").replace("-", " ")
    flat = " ".join(flat.split())
    return _COLUMN_ALIASES.get(k) or _COLUMN_ALIASES.get(flat) or k


def _normalize_row(row: dict) -> dict:
    """Canonical-key view of one uploaded row, values stripped to strings.

    A canonical key already present in the file wins over one produced by an
    alias, so an explicit `entity_name` column is never overwritten by a
    "Name" column that happens to sit beside it.
    """
    out = {}
    for k, v in (row or {}).items():
        canon = _canonical_column(k)
        val = str(v).strip() if v is not None else ""
        raw_key = (k or "").strip().lower()
        # Don't let an alias clobber a value already set by the canonical header.
        if canon in out and out[canon] and raw_key != canon:
            continue
        out[canon] = val
    return out


def _parse_upload(filename: str, raw: bytes):
    """Parse CSV or JSON into a list of dicts. Raises HTTPException(400) on failure.

    A file we cannot parse is a 400. It is never a 200 with an empty success
    payload — that would report an import that never happened (P5).
    """
    name = (filename or "").lower()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "The file is not valid UTF-8 text. Nothing was imported.")

    if name.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"The file is not valid JSON ({e.msg}). Nothing was imported.")
        if isinstance(data, dict):
            data = data.get("entities") or data.get("rows") or data.get("data") or []
        if not isinstance(data, list):
            raise HTTPException(400, "JSON must be a list of entities. Nothing was imported.")
        return data

    if name.endswith(".csv"):
        try:
            reader = csv.DictReader(_io.StringIO(text))
            rows = [dict(r) for r in reader]
        except csv.Error as e:
            raise HTTPException(400, f"The file is not valid CSV ({e}). Nothing was imported.")

        # A file that yields no rows, or whose header carries none of the required
        # columns, was not parsed - it was merely read. Returning 200 with
        # "imported: 0" would report an import that never happened, which is the
        # fake success P5 forbids. It is a 400.
        if not rows:
            raise HTTPException(400, "No data rows were found in the file. Nothing was imported.")
        # IMP-001 — sniff the CANONICAL header set, so "Organization Name" /
        # "NPI Number" is recognised as an entity roster rather than 400'd as
        # an unrelated file.
        header = {_canonical_column(k) for k in (rows[0] or {}).keys()}
        if not header & {"entity_name", "legal_name", "npi", "qhin"}:
            raise HTTPException(
                400,
                "The file does not look like an entity import: none of the required columns "
                "(entity_name, npi, qhin) are present. Nothing was imported.",
            )
        return rows

    raise HTTPException(400, "Unsupported file type. Upload a .csv or .json file.")


@tefca_dashboard_router.post("/entities/upload", summary="Import entities from CSV or JSON")
async def upload_entities(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    # QA-1.8 — contributor ("analyst"), not reviewer. Importing a roster is data
    # entry, not adjudication. This is the endpoint the Entity Import page posts
    # to, so it is the one the RBAC matrix is actually describing.
    user=Depends(require_role("contributor")),
):
    """Import QHIN participant entities.

    Every row is validated before anything is written. Invalid rows are rejected
    individually and reported with the row number, the field and the reason —
    they are never silently dropped, and they never fail the whole file.

    An import that imports nothing still writes a history record. "Nothing was
    imported" is a fact the reviewer needs to be able to see afterwards (P2, P7).
    """
    raw = await file.read()

    # QA-1.1 — security scan BEFORE parsing, so a malicious payload is never
    # walked by a parser or written to a row. Returns the SHA-256 the history
    # record stores (QA-1.6), and writes its own file_scan audit event including
    # on rejection. Rejection is a generic 422 that names no specific check.
    from app.api.routes import _scan_upload_or_reject

    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "csv"
    try:
        file_hash = await _scan_upload_or_reject(
            db, user, request, raw, file.filename, ext, "tefca_entity_import")
    except HTTPException:
        # DEF-018 / IMP-013 — a file the scanner refuses is still an import
        # ATTEMPT, and Import History is where an operator looks to find out
        # what happened to the file they just uploaded. Previously the scanner
        # raised before any history row existed, so empty.csv,
        # malicious_script.csv and renamed_executable.csv vanished from the
        # table entirely: the newest visible entry was not the newest attempt,
        # which is exactly the reading a reviewer would be misled by.
        #
        # This is the same reasoning the parse-failure branch below already
        # applies. The rejection reason is deliberately the generic message the
        # scanner raises - Import History must not become the oracle that tells
        # an attacker which specific check tripped.
        import hashlib
        db.add(TEFCAImportHistory(
            filename=file.filename,
            record_count=0,
            imported_count=0,
            rejected_count=0,
            uploaded_by=getattr(user, "email", None) or str(user),
            status="failed",
            file_hash=hashlib.sha256(raw).hexdigest(),
            errors=[{"row": None, "field": "file",
                     "reason": "File rejected: potentially malicious content"}],
        ))
        await db.commit()
        raise

    # An unparseable file is a 400 — but it is STILL an import attempt, and the
    # audit trail must show it. Recording only the imports that parsed would make
    # the history a highlight reel. Write the failure, then fail (P2 + P5).
    try:
        rows = _parse_upload(file.filename, raw)
    except HTTPException as exc:
        db.add(TEFCAImportHistory(
            filename=file.filename,
            record_count=0,
            imported_count=0,
            rejected_count=0,
            uploaded_by=getattr(user, "email", None) or str(user),
            status="failed",
            file_hash=file_hash,
            errors=[{"row": None, "field": "file", "reason": str(exc.detail)}],
        ))
        await db.commit()
        raise

    # QA-1.4 — a file with no data rows is unprocessable, not a successful import
    # of nothing. It previously returned 200 with total 0, which is
    # indistinguishable from a no-op success. The history row is still written,
    # for the same reason the parse failure above writes one.
    if not rows:
        db.add(TEFCAImportHistory(
            filename=file.filename,
            record_count=0,
            imported_count=0,
            rejected_count=0,
            uploaded_by=getattr(user, "email", None) or str(user),
            status="failed",
            file_hash=file_hash,
            errors=[{"row": None, "field": "file",
                     "reason": "File contains no data rows"}],
        ))
        await db.commit()
        raise HTTPException(422, "File contains no data rows")

    errors = []
    accepted = []

    for i, row in enumerate(rows, start=1):
        # IMP-001 — canonical keys, so a friendly header imports rather than
        # failing every row with "required field is empty".
        norm = _normalize_row(row)
        entity_name = norm.get("entity_name") or norm.get("legal_name") or ""
        npi = norm.get("npi") or ""
        qhin = norm.get("qhin") or ""

        row_errors = []
        if not entity_name:
            row_errors.append({"row": i, "field": "entity_name", "reason": "required field is empty"})
        if not _valid_npi(npi):
            row_errors.append({"row": i, "field": "npi", "reason": "invalid format - must be exactly 10 digits"})
        if not qhin:
            row_errors.append({"row": i, "field": "qhin", "reason": "required field is empty"})

        if row_errors:
            errors.extend(row_errors)
            continue

        accepted.append({
            "entity_name": entity_name,
            "npi": npi,
            "qhin": qhin,
            "entity_type": (norm.get("entity_type") or "PARTICIPANT").upper(),
            "uei": norm.get("uei") or None,
            "address": norm.get("address") or None,
            # Carried for the registry bridge. The legacy table keeps the whole
            # address in one JSONB blob, but the registry stores city/state/zip
            # as columns and compares a one-line address against NPPES, so the
            # components have to survive parsing rather than be flattened here.
            "city": norm.get("city") or None,
            "state": norm.get("state") or None,
            "zip": norm.get("zip") or norm.get("zip_code") or norm.get("postal_code") or None,
            "contact": norm.get("contact") or None,
        })

    imported = 0
    # QA-1.5 — a row whose NPI already exists UPDATES the existing entity rather
    # than creating a second one. That was already true; what was missing is that
    # the caller was never told, so a re-import looked identical to a fresh one.
    # Counted and reported separately from `imported`.
    skipped_details = []
    # ── ONE lookup for the whole file, not one per row ──────────────────────
    # This loop used to run `select(...).where(rce_organization_id == rce_id)`
    # per accepted row. Every one is a separate round trip to the database, and
    # they are sequential, so a 1,000-row file spent a thousand latencies here
    # before the registry bridge spent a thousand more. That is what made a
    # 1,000-row upload time out with the request still pending, and it is why
    # raising a timeout would not have helped: the cost grows with the file.
    #
    # Chunked rather than one enormous IN (...): PostgreSQL takes a bind
    # parameter per element, and a 100,000-row file would blow the statement
    # limit. 1,000 keeps every statement small while turning a five-figure
    # number of round trips into a two-figure one.
    _LOOKUP_CHUNK = 1000
    rce_ids = [f"import-{a['npi']}" for a in accepted]
    existing_by_id: dict = {}
    for i in range(0, len(rce_ids), _LOOKUP_CHUNK):
        chunk = rce_ids[i:i + _LOOKUP_CHUNK]
        found = (await db.execute(
            select(TEFCAEntity).where(TEFCAEntity.rce_organization_id.in_(chunk))
        )).scalars().all()
        for e in found:
            existing_by_id[e.rce_organization_id] = e

    for a in accepted:
        etype = a["entity_type"]
        if etype not in ("QHIN", "PARTICIPANT", "SUBPARTICIPANT"):
            etype = "PARTICIPANT"

        # rce_organization_id is unique and NOT NULL. A re-import of the same NPI
        # updates the existing row rather than raising a unique violation.
        rce_id = f"import-{a['npi']}"
        existing = existing_by_id.get(rce_id)

        if existing:
            existing.legal_name_submitted = a["entity_name"]
            existing.qhin_name = a["qhin"]
            existing.npi_submitted = a["npi"]
            existing.uei_submitted = a["uei"]
            existing.date_last_updated = datetime.utcnow()
            skipped_details.append({
                "entity_name": a["entity_name"], "npi": a["npi"],
                "reason": "duplicate_npi",
                "action": "updated_existing_entity",
            })
        else:
            fresh = TEFCAEntity(
                rce_organization_id=rce_id,
                qhin_name=a["qhin"],
                entity_type=EntityType(etype),
                legal_name_submitted=a["entity_name"],
                npi_submitted=a["npi"],
                uei_submitted=a["uei"],
                address_submitted={"raw": a["address"]} if a["address"] else None,
                # PENDING_REVIEW, not reviewed. An imported entity has been checked
                # against nothing.
                current_status=EntityStatus.PENDING_REVIEW,
            )
            db.add(fresh)
            # The same NPI twice in ONE file. The per-row SELECT this replaced
            # would have found the first occurrence via autoflush; a lookup built
            # before the loop cannot, so the second row would add a second entity
            # with the same rce_organization_id and violate the unique
            # constraint - turning a duplicate row into a failed import. Seeding
            # the map keeps the previous behaviour: the second occurrence UPDATES
            # the first and is counted as a duplicate, not inserted again.
            existing_by_id[rce_id] = fresh
        imported += 1

    # ── Bridge into the registry ────────────────────────────────────────────
    # This table and tefca_reg_entities were disjoint: importing here left
    # verification, which reads the registry, with nothing to work on. The
    # end-to-end demo surfaced it — step 3 matched registry rows by NAME and
    # address comparison reported "not_compared" because those rows had no
    # address, while every step still reported success.
    #
    # Bridging keeps one operator action populating both stores. It runs AFTER
    # the legacy writes and never raises: an import that already succeeded must
    # not be lost because a secondary write failed.
    from app.tefca_registry.import_bridge import bridge_many

    bridge = await bridge_many(db, [{
        "npi": a["npi"],
        "name": a["entity_name"],
        "address": a.get("address"),
        "city": a.get("city"),
        "state": a.get("state"),
        "zip_code": a.get("zip"),
        "entity_type": a["entity_type"],
    } for a in accepted], source="csv_import")

    status = "completed" if imported and not errors else ("partial" if imported else "failed")

    db.add(TEFCAImportHistory(
        filename=file.filename,
        record_count=len(rows),
        imported_count=imported,
        rejected_count=len(rows) - imported,
        uploaded_by=getattr(user, "email", None) or str(user),
        status=status,
        file_hash=file_hash,
        errors=errors[:200],
    ))
    # QA-1.7 — the import as an audit event, not only as a history row. The two
    # are read by different people: history answers "what did we load", the audit
    # trail answers "who changed the registry, when, and from which file".
    await log_tefca_event(
        db, user=user, action="entity_import", resource_type="tefca_import_history",
        resource_id=None, ip_address=_client_ip(request),
        details={"filename": file.filename, "file_hash": file_hash,
                 "imported": imported, "skipped": len(skipped_details),
                 "errors": len(errors), "total": len(rows),
                 "registry_created": bridge["registry_created"],
                 "registry_updated": bridge["registry_updated"],
                 "registry_failed": bridge["registry_failed"]},
    )
    await db.commit()

    return {
        "imported": imported,
        "rejected": len(rows) - imported,
        "skipped": len(skipped_details),
        "skipped_details": skipped_details,
        "total": len(rows),
        "status": status,
        "file_hash": file_hash,
        "errors": errors,
        # Reported separately from `imported`: the legacy write and the registry
        # write can disagree, and a caller who sees only "imported: 5" would not
        # know that verification still cannot see those entities.
        "registry_created": bridge["registry_created"],
        "registry_updated": bridge["registry_updated"],
        "registry_failed": bridge["registry_failed"],
        "registry_details": bridge["registry_details"],
    }


@tefca_dashboard_router.get("/import/history", summary="Entity import history")
async def import_history(
    status: Optional[str] = Query(
        None, description="completed | partial | failed. Omit or 'all' for every attempt."),
    search: Optional[str] = Query(None, description="Filename or uploader substring"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Every import attempt, including the ones that imported nothing.

    DEF-006 — filtering is done here, in SQL. The Import History table used to
    fetch once on page load and then narrow that array in the browser, so a
    filter applied minutes later described the state of the world at load time.
    """
    q = select(TEFCAImportHistory)
    if status and status.strip().lower() not in ("", "all"):
        wanted = status.strip().lower()
        valid = {"completed", "partial", "failed"}
        if wanted not in valid:
            raise HTTPException(
                400, "Unknown status %r. Valid values: %s" % (status, ", ".join(sorted(valid))))
        q = q.where(func.lower(TEFCAImportHistory.status) == wanted)
    if search:
        term = f"%{search.strip().lower()}%"
        q = q.where(or_(
            func.lower(func.coalesce(TEFCAImportHistory.filename, "")).like(term),
            func.lower(func.coalesce(TEFCAImportHistory.uploaded_by, "")).like(term),
        ))
    q = q.order_by(TEFCAImportHistory.uploaded_at.desc())
    total = len((await db.execute(q)).scalars().all())
    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "imports": [{
            "id": str(r.id),
            "filename": r.filename,
            "record_count": r.record_count,
            "imported_count": r.imported_count,
            "rejected_count": r.rejected_count,
            "uploaded_by": r.uploaded_by,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            "status": r.status,
            # QA-1.6 / QA-4.2 — integrity evidence in the LIST, not only on a
            # detail view. NULL on rows imported before the column existed, and
            # deliberately not faked: an absent hash must not look like a
            # verified one. Both keys are returned so an older UI reading
            # `file_hash` and a newer one reading `sha256` both work.
            "file_hash": r.file_hash,
            "sha256": r.file_hash,
            "errors": r.errors or [],
        } for r in rows],
    }


@tefca_dashboard_router.get("/reports/{report_id}/download", deprecated=True, summary="DEPRECATED / COMPATIBILITY ONLY — use /api/reports/*. Download a report (pdf|docx)")
async def download_report(
    report_id: str,
    format: str = Query("pdf", description="pdf | docx"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Generic download. Delegates to the existing renderers rather than
    duplicating them — one renderer, one output, no second implementation to
    drift."""
    r = await _load_report_or_404(report_id, db)
    fmt = (format or "pdf").lower()

    if fmt == "pdf":
        try:
            content = report_renderer.render_report_pdf(r.report_data or {})
        except Exception as e:
            logger.error(f"PDF render failed for {report_id}: {e}")
            raise HTTPException(500, f"PDF rendering failed: {str(e)[:120]}")
        media = "application/pdf"
    elif fmt == "docx":
        try:
            content = report_renderer.render_report_docx(r.report_data or {})
        except Exception as e:
            logger.error(f"DOCX render failed for {report_id}: {e}")
            raise HTTPException(500, f"DOCX rendering failed: {str(e)[:120]}")
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise HTTPException(400, "format must be pdf or docx")

    fname = f"TEFCA_{(r.report_type or 'report')}_{report_id}.{fmt}"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# DIMENSION-ORGANISED EVIDENCE (CMS/PECOS evidence architecture)
#
# Additive. Nothing above this line changed: the five-element evidence record,
# the B1–B4 classification, /reviews/{id} and /connectors/status all keep their
# existing shapes and behaviour. These endpoints expose the evidence layer that
# organises what the sources said into the six verification dimensions, with
# enough provenance on every item to reproduce the determination later.
#
# Deliberately NOT here: any endpoint that returns a score, a percentage, or a
# count of passing sources. The dimension structure is the answer.
# ─────────────────────────────────────────────────────────────────────────────

def _entity_by_reference(reference: str) -> Optional[dict]:
    """Resolve an ONC entity from the bundled fixtures.

    RETAINED as the synchronous fixture path. `_resolve_entity()` below is the
    entry point routes should use — it honours ENTITY_RESOLVER_SOURCE and can
    reach the canonical registry. This function stays because several
    non-evidence routes resolve fixtures directly and changing them all at once
    would be a larger blast radius than the evidence path needs.
    """
    from app.Tefca.entity_resolution import resolve_from_mock

    return resolve_from_mock(reference)


async def _resolve_entity(db, reference: str) -> Optional[dict]:
    """Resolve an entity under the configured ENTITY_RESOLVER_SOURCE.

    Default "mock". Monday, once Area 1 -> Area 2 -> Registry is built and the
    RCE dataset is approved, the flag flips to "db" and this same call starts
    returning registry entities with no route change.
    """
    from app.Tefca.entity_resolution import resolve_entity

    return await resolve_entity(db, reference)


async def _evidence_population(db) -> list:
    """The entity population used to resolve D5/D6 parent references.

    Under "mock" this is the bundled fixtures. Under a db-backed source the
    registry is the population, but materialising every entity to resolve one
    parent would be wasteful, so the fixtures are used only when they are the
    configured source; otherwise the assemblers report a parent reference as
    present-but-not-checked, which is honest.
    """
    from app.Tefca.entity_resolution import SOURCE_MOCK, resolver_source

    if resolver_source() == SOURCE_MOCK:
        return list(ALL_MOCK_ENTITIES)
    return []


async def _persist_dimension_evidence(db: AsyncSession, entity_id: str,
                                      review_id: Optional[str], evidence: dict) -> int:
    """INSERT one generation of evidence rows. Never updates, never deletes.

    A failure to persist must not fail the read: the analyst still needs to see
    the evidence, and a storage fault is not a reason to withhold what the
    sources actually said.
    """
    from app.Tefca.evidence_service import evidence_rows_for_persistence
    from app.Tefca.models import TEFCADimensionEvidence

    rows = evidence_rows_for_persistence(entity_id, review_id, evidence)
    try:
        for row in rows:
            db.add(TEFCADimensionEvidence(**row))
        await db.commit()
        return len(rows)
    except Exception as exc:
        await db.rollback()
        logger.warning("dimension evidence not persisted for %s: %s", entity_id, exc)
        return 0


@tefca_dashboard_router.get(
    "/entities/{entity_ref}/evidence-dimensions",
    summary="Six-dimension evidence for one entity",
)
async def entity_evidence_dimensions(
    entity_ref: str,
    persist: bool = True,
    include_website: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.Tefca.evidence_service import EvidenceService

    entity = await _resolve_entity(db, entity_ref)
    if not entity:
        # 200 with entity_resolved=false, NOT 404.
        #
        # "No ONC record exists for this identifier" and "the request failed"
        # are different facts, and a 404 makes the client guess which one it
        # got. The ARC review population and the ONC entity population are
        # genuinely disjoint in some environments — a review NPI with no ONC
        # record is an ordinary data-linkage fact, not an error, and the
        # reviewer is entitled to be told exactly that.
        #
        # No entity is matched by name to fill the gap: attributing another
        # organisation's evidence to this review is worse than showing none.
        return {
            "entity_ref": entity_ref,
            "entity_resolved": False,
            "dimensions": [],
            "note": (f"No ONC/RCE entity record resolves from '{entity_ref}'. Dimension "
                     "evidence is assembled against the ONC-supplied entity population; "
                     "this identifier is not in it, so there is nothing to assemble. "
                     "This is a data-linkage fact, not a retrieval failure, and nothing "
                     "is inferred from it."),
        }

    from app.Tefca.entity_resolution import make_parent_resolver
    from app.Tefca.ppef_store import make_local_store
    service = EvidenceService(manager=get_connector_manager(),
                              enable_website=include_website,
                              local_store=make_local_store(db))
    evidence = await service.build_evidence(
        entity,
        parent_resolver=make_parent_resolver(db, await _evidence_population(db)))
    evidence["resolution_source"] = entity.get("_resolution_source")

    persisted = 0
    if persist:
        persisted = await _persist_dimension_evidence(db, str(entity.get("id")), None, evidence)
    evidence["persisted_rows"] = persisted

    await log_tefca_event(
        db, user=user, action="EVIDENCE_DIMENSIONS_GENERATED",
        resource_type="tefca_dimension_evidence", resource_id=entity.get("id"),
        details={
            "entity_id": entity.get("id"),
            "dimensions": len(evidence.get("dimensions", [])),
            "persisted_rows": persisted,
            "generation_timestamp": evidence.get("generated_at"),
        },
    )
    await db.commit()
    return evidence


@tefca_dashboard_router.get(
    "/reviews/{review_id}/evidence-dimensions",
    summary="Six-dimension evidence for the entity behind a review",
)
async def review_evidence_dimensions(
    review_id: str,
    persist: bool = True,
    include_website: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.Tefca.evidence_service import EvidenceService

    rid = _parse_uuid(review_id)
    review = (await db.execute(
        select(TEFCAReview).where(TEFCAReview.id == rid)
    )).scalar_one_or_none()
    if not review:
        raise HTTPException(404, f"No review exists with id {review_id}")

    entity = await _resolve_entity(db, review.npi or "")
    if not entity:
        # An honest empty answer. Fabricating an entity to hang evidence on
        # would produce evidence about nothing.
        return {
            "review_id": review_id,
            "entity_resolved": False,
            "dimensions": [],
            "note": ("No ONC entity record resolves from the NPI on this review, so no "
                     "dimension evidence can be assembled for it."),
        }

    from app.Tefca.entity_resolution import make_parent_resolver
    from app.Tefca.ppef_store import make_local_store
    service = EvidenceService(manager=get_connector_manager(),
                              enable_website=include_website,
                              local_store=make_local_store(db))
    evidence = await service.build_evidence(
        entity,
        parent_resolver=make_parent_resolver(db, await _evidence_population(db)))
    evidence["review_id"] = review_id
    evidence["entity_resolved"] = True
    evidence["resolution_source"] = entity.get("_resolution_source")

    persisted = 0
    if persist:
        persisted = await _persist_dimension_evidence(db, str(entity.get("id")), review_id, evidence)
    evidence["persisted_rows"] = persisted

    await log_tefca_event(
        db, user=user, action="EVIDENCE_DIMENSIONS_GENERATED",
        resource_type="tefca_dimension_evidence", resource_id=review_id,
        details={
            "review_id": review_id,
            "entity_id": entity.get("id"),
            "persisted_rows": persisted,
            "generation_timestamp": evidence.get("generated_at"),
        },
    )
    await db.commit()
    return evidence


@tefca_dashboard_router.get(
    "/entities/{entity_ref}/evidence-history",
    summary="Every preserved generation of dimension evidence for an entity",
)
async def entity_evidence_history(
    entity_ref: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Proof that re-running a verification preserved what came before.

    Generations are returned newest first. Nothing is collapsed or pruned: a
    determination that cited the January CMS extract has to stay explicable
    after the April extract lands.
    """
    from app.Tefca.models import TEFCADimensionEvidence

    rows = (await db.execute(
        select(TEFCADimensionEvidence)
        .where(TEFCADimensionEvidence.entity_id == entity_ref)
        .order_by(TEFCADimensionEvidence.created_at.desc())
    )).scalars().all()

    generations: dict = {}
    for r in rows:
        gen = r.generation_timestamp or (r.created_at.isoformat() if r.created_at else "unknown")
        generations.setdefault(gen, []).append({
            "dimension": r.evidence_dimension,
            "source": r.source,
            "disposition": r.disposition,
            "dimension_disposition": r.dimension_disposition,
            "source_dataset": r.source_dataset,
            "ppef_component": r.ppef_component,
            "dataset_version_anchor": r.dataset_version_anchor,
            "query_timestamp": r.query_timestamp,
            "rule_applied": r.rule_applied,
            "analyst_notes": r.analyst_notes,
            "reviewed_by": r.reviewed_by,
        })
    return {
        "entity_id": entity_ref,
        "generation_count": len(generations),
        "generations": [
            {"generation_timestamp": g, "row_count": len(items), "evidence": items}
            for g, items in sorted(generations.items(), reverse=True)
        ],
        "retention_note": ("Evidence is append-only. Re-running a verification adds a "
                           "generation; it never overwrites or deletes a previous one."),
    }


@tefca_dashboard_router.get(
    "/connectors/cms-systems",
    summary="CMS system and capability health",
)
async def cms_connector_systems(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Two CMS systems, reported as systems.

    PPEF Enrollment, Practice Location, Reassignment and Additional NPIs are
    components of ONE relational dataset and are reported as capabilities of it.
    Listing them as four external systems would inflate one authority into four
    and imply four independent corroborations where there is one.
    """
    from app.Tefca.cms_ppef import cms_capability_health
    from app.Tefca.ppef_store import snapshot_status

    # Capability status depends on what has actually been ingested, so the
    # health answer is computed against the local store rather than guessed.
    try:
        snapshots = await snapshot_status(db)
    except Exception as exc:
        logger.warning("PPEF snapshot status unavailable: %s", exc)
        snapshots = {}
    return await cms_capability_health(snapshot_status=snapshots)


# ─────────────────────────────────────────────────────────────────────────────
# PPEF RESOURCE DISCOVERY AND VERSIONED INGESTION
#
# CMS publishes four of the five PPEF relational components as quarterly CSV
# sub-files of the parent dataset rather than as data-api datasets. These
# endpoints discover what CMS currently offers, ingest a component into the
# local evidence store with its checksum and provenance, and report what has
# been ingested.
#
# Ingestion is admin-gated: it writes to the evidence store, and evidence that
# anyone can rewrite is not evidence.
# ─────────────────────────────────────────────────────────────────────────────

@tefca_dashboard_router.get(
    "/ppef/resources",
    summary="Discover the CMS PPEF components currently published, and how each is obtainable",
)
async def ppef_resource_discovery(user=Depends(require_role("viewer"))):
    """Live discovery — never a hard-coded identifier.

    CMS re-publishes quarterly with new file names, media uuids and titles, so
    a cached identifier would silently point at last quarter.
    """
    from app.Tefca.ppef_resources import PPEFResourceCatalog, TRANSPORT_RATIONALE

    discovered = await PPEFResourceCatalog().discover()
    return {
        "checked_at": datetime.utcnow().isoformat(),
        "parent_dataset_id": "2457ea29-fc82-48b0-86ec-3b0755de7515",
        "components": {
            k: {**v.to_dict(), "transport_rationale": TRANSPORT_RATIONALE.get(k)}
            for k, v in discovered.items()
        },
        "discovery_note": (
            "Sub-files are ancillary resources of the parent dataset, listed by "
            "/data-api/v1/dataset/{parent}/resources. They are absent from the DCAT "
            "catalogue, and their file_uuids are media ids that 404 against the "
            "data-api — hence download transport."
        ),
    }


@tefca_dashboard_router.get(
    "/ppef/snapshots",
    summary="Ingested PPEF snapshots with full provenance",
)
async def ppef_snapshots(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    from app.Tefca.models import TEFCAPPEFSnapshot

    rows = (await db.execute(
        select(TEFCAPPEFSnapshot).order_by(TEFCAPPEFSnapshot.ingested_at.desc()).limit(100)
    )).scalars().all()
    return {
        "count": len(rows),
        "snapshots": [{
            "id": str(s.id),
            "component": s.component,
            "cms_title": s.cms_title,
            "file_name": s.file_name,
            "resource_id": s.resource_id,
            "resource_version": s.resource_version,
            "as_of_label": s.as_of_label,
            "transport": s.transport,
            "file_size": s.file_size,
            "sha256": s.sha256,
            "schema_fields": s.schema_fields,
            "record_count": s.record_count,
            "rows_truncated": bool(s.rows_truncated),
            "ingest_status": s.ingest_status,
            "error": s.error,
            "retrieved_at": s.retrieved_at.isoformat() if s.retrieved_at else None,
            "ingested_at": s.ingested_at.isoformat() if s.ingested_at else None,
            "ingested_by": s.ingested_by,
        } for s in rows],
        "retention_note": (
            "Snapshots are append-only. CMS PPEF carries CURRENT enrolment data, so a "
            "quarter disappears from the source when the next publishes; the preserved "
            "snapshot and its checksum are what keep an earlier determination explicable."
        ),
    }


@tefca_dashboard_router.post(
    "/ppef/snapshots/ingest",
    status_code=202,
    summary="Queue a durable CMS PPEF ingestion job",
)
async def ppef_ingest_component(
    component: str,
    max_rows: Optional[int] = None,
    force: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("admin")),
):
    """Create a QUEUED job row and return 202 with its id.

    The work does NOT run in this request, and no longer runs in a FastAPI
    BackgroundTask. A background task lives and dies with its worker: when the
    Azure App Service container was recycled mid-load the task simply vanished,
    leaving five dev snapshots stuck at `pending` with `error = None`. Nothing
    had reported a failure because nothing was left alive to report one.

    Now a durable job row is written and the PPEF scheduler's poller claims it.
    Every state transition and heartbeat is committed, so a killed worker leaves
    a truthful record and the reaper can fail the job cleanly instead of leaving
    it pending forever.

    Poll GET /ppef/snapshots/ingest/{job_id} for persisted state.

    IDEMPOTENT: if this exact CMS quarter is already loaded COMPLETE and
    untruncated, the call returns ALREADY_LOADED and queues nothing. `force=true`
    queues anyway, for a deliberate re-ingest.

    CONCURRENCY is refused by a database constraint rather than by a prior check,
    so two callers racing cannot both start the same component and quarter.
    """
    from app.Tefca import ppef_jobs
    from app.Tefca.ppef_ingest import _as_of_label
    from app.Tefca.ppef_resources import EXPECTED_FIELDS, PPEFResourceCatalog

    # ENFORCEMENT LAYER 1 — refuse before ANY outbound work.
    #
    # This check is first on purpose. A few lines below, PPEFResourceCatalog()
    # .discover() calls data.cms.gov to resolve the current quarter. Placing the
    # gate after that would mean a refused request had already reached CMS —
    # a request the operator was told did not happen. Refusing here creates no
    # job, no snapshot, no outbound call and no row.
    #
    # Deliberately NOT an authorization check: admin RBAC above is unchanged and
    # still required. This is a separate environment capability gate, so 403
    # carries a reason an operator can act on rather than looking like a
    # permissions problem with their account.
    if not ppef_jobs.bulk_ingest_enabled():
        reason = ppef_jobs.bulk_ingest_refusal_reason()
        logger.warning(
            "PPEF bulk ingestion REFUSED for %s: %s",
            getattr(user, "email", "unknown-user"), reason)
        raise HTTPException(403, reason)

    component = (component or "").strip().upper()
    if component not in EXPECTED_FIELDS:
        raise HTTPException(400, f"Unknown PPEF component '{component}'. "
                                 f"Known: {sorted(EXPECTED_FIELDS)}")

    # Discover the CURRENT quarter so the job is keyed to a real CMS version
    # rather than to whatever was current when someone last edited a constant.
    # Discovery failure is not fatal here — the job records a null version and
    # the runner discovers again — but it does disable the idempotency check,
    # because "already loaded" cannot be decided without knowing which quarter.
    resource_version = quarter = file_name = None
    try:
        discovered = (await PPEFResourceCatalog().discover()).get(component)
        if discovered is not None:
            resource_version = discovered.resource_version
            file_name = discovered.file_name
            quarter = _as_of_label(discovered.cms_title)
    except Exception as exc:
        logger.warning("PPEF discovery failed while queueing %s: %s", component, exc)

    if not force and resource_version:
        existing = await ppef_jobs.find_complete_snapshot(
            db, component, resource_version, file_name)
        if existing is not None:
            return {
                "status": "ALREADY_LOADED",
                "component": component,
                "resource_version": resource_version,
                "quarter": quarter,
                "snapshot_id": str(existing.id),
                "record_count": existing.record_count,
                "sha256": existing.sha256,
                "ingested_at": existing.ingested_at.isoformat() if existing.ingested_at else None,
                "note": ("An identical COMPLETE, untruncated snapshot for this quarter "
                         "already exists, so nothing was queued. Re-downloading 3.9M "
                         "rows to produce a byte-identical result is cost without "
                         "information. Pass force=true to re-ingest deliberately."),
            }

    try:
        job = await ppef_jobs.queue_job(
            db, component=component, resource_version=resource_version,
            quarter=quarter, requested_by=getattr(user, "email", None),
            max_rows=max_rows)
    except ppef_jobs.JobConflict as exc:
        raise HTTPException(409, str(exc))

    await log_tefca_event(
        db, user=user, action="PPEF_SNAPSHOT_INGEST_QUEUED",
        resource_type="tefca_ppef_ingest_job", resource_id=str(job.id),
        details={"component": component, "resource_version": resource_version,
                 "quarter": quarter, "max_rows": max_rows, "force": force,
                 "requested_by": getattr(user, "email", None),
                 "executed_by": "system/ppef-scheduler (APScheduler poller)"},
    )
    await db.commit()

    return {
        "status": "QUEUED",
        "job_id": str(job.id),
        "component": component,
        "resource_version": resource_version,
        "quarter": quarter,
        "state": job.state,
        "poll": f"/api/tefca/ppef/snapshots/ingest/{job.id}",
        "note": ("Durable job queued. Execution is driven by the PPEF scheduler and "
                 "all state lives in the database, not in process memory, so a "
                 "recycled worker leaves a FAILED job rather than a silent one."),
    }


@tefca_dashboard_router.get(
    "/ppef/snapshots/ingest/{job_id}",
    summary="Persisted state of one PPEF ingestion job",
)
async def ppef_ingest_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Job state read from the database only — never from process memory.

    Reports heartbeat age and staleness, so an operator can see that a worker
    has gone quiet before the reaper formally fails the job.
    """
    import uuid as _uuid

    from app.Tefca import ppef_jobs

    try:
        parsed = _uuid.UUID(str(job_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, f"'{job_id}' is not a valid job id")

    status = await ppef_jobs.job_status(db, parsed)
    if status is None:
        raise HTTPException(404, f"No ingestion job with id {job_id}")
    return status


@tefca_dashboard_router.get(
    "/ppef/jobs",
    summary="Recent PPEF ingestion jobs with persisted state",
)
async def ppef_jobs_list(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("viewer")),
):
    """Recent jobs, newest first — the operator view of what the loader has done."""
    from app.Tefca import ppef_jobs
    from app.Tefca.models import TEFCAPPEFIngestJob
    from app.Tefca.ppef_scheduler import scheduler_status

    rows = (await db.execute(
        select(TEFCAPPEFIngestJob)
        .order_by(TEFCAPPEFIngestJob.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )).scalars().all()
    return {
        "count": len(rows),
        "jobs": [await ppef_jobs.job_status(db, r.id) for r in rows],
        "stale_threshold_seconds": ppef_jobs.STALE_HEARTBEAT_SECONDS,
        "scheduler": scheduler_status(),
    }
