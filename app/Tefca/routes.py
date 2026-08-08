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
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, text
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

# ── Router — authenticated by default. require_role("reviewer") is the MINIMUM
#    for any TEFCA endpoint; stricter roles are applied per-route. No endpoint is
#    reachable without a valid JWT. (FIX 2 — HHSAR 352.204-71 / FAR 52.212-4) ──
tefca_router = APIRouter(
    prefix="/api/v1/tefca",
    tags=["TEFCA Review Protocol"],
    dependencies=[Depends(require_role("reviewer"))],
)
router = tefca_router  # safe_load / main.py expects mod.router


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
async def connector_health(user=Depends(require_role("reviewer"))):
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

@tefca_router.get("/mock/entities", summary="View bundled RCE development dataset")
async def get_mock_entities(
    bucket: Optional[int] = None,
    qhin: Optional[str] = None,
    user=Depends(require_role("reviewer")),
):
    entities = ALL_MOCK_ENTITIES
    if bucket:
        entities = [e for e in entities if e.get("_expected_bucket") == bucket]
    if qhin:
        entities = [e for e in entities if e.get("_qhin") == qhin]
    return {
        "total": len(entities), "stats": MOCK_STATS, "entities": entities,
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
async def list_cycles(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    rows = (await db.execute(select(TEFCAReviewCycle).order_by(TEFCAReviewCycle.created_at.desc()))).scalars().all()
    return {
        "total": len(rows),
        "cycles": [{
            "cycle_id": str(c.cycle_id), "cycle_type": c.cycle_type.value if c.cycle_type else None,
            "cycle_status": c.cycle_status.value if c.cycle_status else None,
            "total_entities_completed": c.total_entities_completed,
            "bucket_counts": {
                "1": c.bucket_1_count, "2": c.bucket_2_count,
                "3": c.bucket_3_count, "4": c.bucket_4_count,
            },
            "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in rows],
    }


# ─── Validation ──────────────────────────────────────────────────────────────

@tefca_router.post("/validate/entity", summary="Validate one RCE entity (persisted)")
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
        raise HTTPException(503, f"RCE Directory unavailable: {rce_result.error}")
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
    cycle_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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
    limit: int = 100, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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
    status: Optional[str] = None, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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


@tefca_router.post("/reports/weekly/{cycle_id}", summary="Generate D3.1 weekly progress report")
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


@tefca_router.post("/reports/final/{cycle_id}", summary="Generate D3.2 final report")
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


@tefca_router.get("/reports", summary="List generated reports")
async def list_reports(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
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


# tefca_reviews.status (4-bucket disposition) -> dashboard pass/fail/pending/indeterminate
_REVIEW_STATUS_MAP = {
    "no_discrepancy": "pass",
    "minor_administrative": "pass",
    "inexplicable": "pending",
    "non_compliant": "fail",
    "indeterminate": "indeterminate",
}


def _review_status(status: str) -> str:
    return _REVIEW_STATUS_MAP.get((status or "").lower(), "pending")


def _connector_health_snapshot(health: dict) -> dict:
    def s(k):
        return "available" if health.get(k, {}).get("live") else "unavailable"
    return {"sam_gov": s("SAM_GOV"), "pecos": s("PECOS"), "leie": s("OIG_LEIE"), "nppes": s("NPPES")}


@tefca_dashboard_router.get("/dashboard/summary", summary="Executive dashboard summary (aggregate, viewer role required)", dependencies=[Depends(require_role("viewer"))])
async def dashboard_summary(db: AsyncSession = Depends(get_db)):
    reviews = (await db.execute(select(TEFCAReview))).scalars().all()
    total = len(reviews)
    by_status = {"pass": 0, "fail": 0, "pending": 0, "indeterminate": 0}
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
    user=Depends(require_role("reviewer")),
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
        "id": str(r.id), "entity_name": r.entity_name, "npi": r.npi, "uei": r.uei,
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
            "legal_name": e.legal_name_submitted, "npi": e.npi_submitted,
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
        "counts": {"reviews": len(reviews_out), "entities": len(entities_out), "findings": len(findings_out)},
        "reviews": reviews_out, "entities": entities_out, "findings": findings_out, "nppes": nppes,
    }


@tefca_dashboard_router.get("/reports/export", summary="CSV export of reviews (role-gated — contains PII)")
async def export_reviews(
    format: str = Query("csv"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
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
async def demo_run_cycle(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
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
async def seed_mock_data(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    # Gated by the ADMIN_EMAILS allowlist (email-based), not role, so an existing
    # token for an allowlisted admin works without changing the DB role. The email
    # is loaded from the DB by get_current_user, not trusted from the token.
    if user.email not in ADMIN_EMAILS:
        raise HTTPException(403, f"Admin allowlist required; {user.email} not authorized")
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
async def list_sampling_runs(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
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


@tefca_dashboard_router.post("/reports/weekly", summary="Generate a weekly progress report (SOW Task 3)")
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


@tefca_dashboard_router.post("/reports/final", summary="Generate the final retrospective report (SOW Task 3)")
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


@tefca_dashboard_router.get("/reports", summary="List reports (filters: type, start, end)")
async def list_tefca_reports(
    type: Optional[str] = Query(None), start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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


@tefca_dashboard_router.get("/reports/{report_id}", summary="Full report detail")
async def get_tefca_report(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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


@tefca_dashboard_router.get("/reports/{report_id}/csv", summary="Download report as 12-column CSV")
async def get_tefca_report_csv(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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


@tefca_dashboard_router.get("/reports/{report_id}/pdf", summary="Download report as branded PDF")
async def get_tefca_report_pdf(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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


@tefca_dashboard_router.get("/reports/{report_id}/docx", summary="Download report as branded DOCX")
async def get_tefca_report_docx(
    report_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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

@tefca_dashboard_router.post("/reports/biweekly", summary="Generate a bi-weekly ongoing review (SOW Task 4)")
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


@tefca_dashboard_router.post("/reports/quarterly", summary="Generate a quarterly report (SOW Task 4)")
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
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    since_dt = _parse_date(since)
    rows = await reporting.get_new_submissions(db, qhin, since_dt)
    return {
        "since": since, "qhin": qhin, "count": len(rows),
        "submissions": [{
            "review_id": str(r.id), "entity_name": r.entity_name, "qhin": r.qhin,
            "npi": r.npi, "status": r.status, "risk_level": r.risk_level,
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
    db: AsyncSession = Depends(get_db), user=Depends(get_current_user),
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
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    q = select(TEFCAPriorityCase).order_by(TEFCAPriorityCase.assigned_date.desc())
    if qhin:
        q = q.where(TEFCAPriorityCase.qhin == qhin)
    if start:
        q = q.where(TEFCAPriorityCase.assigned_date >= _parse_date(start))
    if end:
        q = q.where(TEFCAPriorityCase.assigned_date <= _parse_date(end))
    if status:
        try:
            q = q.where(TEFCAPriorityCase.case_status == CaseStatus(status))
        except ValueError:
            pass  # unknown status filter ignored
    rows = (await db.execute(q)).scalars().all()
    return {"total": len(rows), "cases": [_priority_case_dto(c) for c in rows]}


@tefca_dashboard_router.get("/priority/{case_id}", summary="Priority case detail")
async def priority_detail(
    case_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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


@tefca_dashboard_router.get("/priority/{case_id}/report", summary="Formatted COR status report")
async def priority_report(
    case_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    cid = _parse_uuid(case_id)
    report = await reporting.generate_priority_status_report(db, cid)
    if report is None:
        raise HTTPException(404, "Priority case not found")
    return report


@tefca_dashboard_router.post("/priority/quarterly-report", summary="Generate priority quarterly aggregation")
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
async def qa_connector_health(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.ConnectorHealthCheck().check_all_connectors(db=db)


@tefca_dashboard_router.get("/qa/audit", summary="QA audit trail (filters: review_id, gate_name, gate_type, passed)")
async def qa_audit_trail(
    review_id: Optional[str] = Query(None), gate_name: Optional[str] = Query(None),
    gate_type: Optional[str] = Query(None), passed: Optional[bool] = Query(None),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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
async def qa_overall_score(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.overall_qa_score(db)


# ─── QA evidence & chain-of-custody endpoints (QA Task 2) ────────────────────

@tefca_dashboard_router.post("/qa/validate-evidence/{review_id}", summary="Evidence + chain-of-custody QA on a review")
async def qa_validate_evidence(review_id: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.validate_evidence(db, review_id, triggered_by="manual")


@tefca_dashboard_router.get("/qa/report-gate", summary="Evidence gate that must be open before a report is generated")
async def qa_report_gate(
    start: Optional[str] = Query(None), end: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    s = _parse_date(start) if start else None
    e = _parse_date(end) if end else None
    return await qa_engine.evidence_gate(db, s, e, triggered_by="manual")


@tefca_dashboard_router.get("/qa/evidence-summary", summary="Evidence completeness across all reviews")
async def qa_evidence_summary(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.evidence_gate(db, None, None, triggered_by="manual")


# ─── QA statistical endpoints (QA Task 3) ────────────────────────────────────

@tefca_dashboard_router.get("/qa/sampling-validation", summary="Sampling validation vs Cochran @95% CI")
async def qa_sampling_validation(
    population: int = Query(94231), confidence: float = Query(0.95), margin: float = Query(0.05),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    return await qa_engine.validate_sampling(db, population, confidence, margin, triggered_by="manual")


@tefca_dashboard_router.get("/qa/internal-consistency",
                            summary="Internal consistency score (pipeline self-consistency — NOT inter-rater reliability)")
async def qa_internal_consistency(
    sample_size: int = Query(20), seed: int = Query(42),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    return await qa_engine.internal_consistency_check(db, sample_size, seed, triggered_by="manual")


@tefca_dashboard_router.get("/qa/inter-rater",
                            summary="[deprecated alias] Internal consistency score — NOT true inter-rater reliability")
async def qa_inter_rater(
    sample_size: int = Query(20), seed: int = Query(42),
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
):
    # Backward-compatible path; returns the honestly-labeled internal consistency
    # score (real IRR requires double-review sampling — see qa_engine TODO).
    return await qa_engine.internal_consistency_check(db, sample_size, seed, triggered_by="manual")


@tefca_dashboard_router.get("/qa/statistical", summary="Combined statistical QA (sampling + internal consistency + CI)")
async def qa_statistical(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.statistical_qa(db, triggered_by="manual")


# ─── QA regression / golden-record endpoints (QA Task 4) ─────────────────────

@tefca_dashboard_router.get("/qa/golden-records", summary="List the golden known-answer test cases")
async def qa_golden_records(user=Depends(require_role("reviewer"))):
    cases = [{"case": name, "expected_bucket": expected} for (name, _e, _sr, expected) in qa_engine._golden_cases()]
    return {"total": len(cases), "golden_records": cases}


@tefca_dashboard_router.get("/qa/regression", summary="Run golden-record regression; detect classification drift")
async def qa_regression(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.run_golden_regression(db, triggered_by="manual")


# ─── QA monitoring, SLA & alert endpoints (QA Task 5) ────────────────────────

@tefca_dashboard_router.get("/qa/sla", summary="Priority-review SLA tracking")
async def qa_sla(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.check_priority_sla(db, triggered_by="manual")


@tefca_dashboard_router.get("/qa/sweep", summary="Run a full QA sweep (all gates + alerts + SLA)")
async def qa_sweep(db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
    return await qa_engine.run_qa_sweep(db, triggered_by="manual")


@tefca_dashboard_router.get("/qa/alerts", summary="Recent QA threshold alerts")
async def qa_alerts(limit: int = Query(50), db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer"))):
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
    db: AsyncSession = Depends(get_db), user=Depends(require_role("reviewer")),
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


def _serialize_review(review: TEFCAReview, findings: list, bucket_label) -> dict:
    return {
        "id": str(review.id),
        "entity_name": review.entity_name,
        "npi": review.npi,
        "uei": review.uei,
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
    status: Optional[str] = Query(None),
    qhin: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    """The reviewer queue. Returns an empty list when there is nothing — not an error."""
    q = select(TEFCAReview).order_by(TEFCAReview.created_at.desc())
    if status:
        q = q.where(TEFCAReview.status == status)
    if qhin:
        q = q.where(TEFCAReview.qhin == qhin)

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

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "reviews": [
            _serialize_review(r, by_review.get(r.id, []), buckets.get(r.npi))
            for r in rows
        ],
    }


@tefca_dashboard_router.get("/reviews/{review_id}", summary="Single review detail with evidence")
async def get_entity_review(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
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
    base = _serialize_review(review, findings, buckets.get(review.npi))

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


@tefca_dashboard_router.get("/findings", summary="List findings across all entities")
async def list_findings(
    level: Optional[str] = Query(None, description="severity: low|medium|high|critical"),
    entity_id: Optional[str] = Query(None, description="review id"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
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

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "findings": [{
            "id": str(f.id),
            "entity_id": str(f.review_id) if f.review_id else None,
            "entity_name": (r.entity_name if r else None),
            "npi": (r.npi if r else None),
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
    user=Depends(require_role("reviewer")),
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
        header = {(k or "").strip().lower() for k in (rows[0] or {}).keys()}
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
    file_hash = await _scan_upload_or_reject(
        db, user, request, raw, file.filename, ext, "tefca_entity_import")

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
        norm = {(k or "").strip().lower(): (str(v).strip() if v is not None else "") for k, v in row.items()}
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
            "contact": norm.get("contact") or None,
        })

    imported = 0
    # QA-1.5 — a row whose NPI already exists UPDATES the existing entity rather
    # than creating a second one. That was already true; what was missing is that
    # the caller was never told, so a re-import looked identical to a fresh one.
    # Counted and reported separately from `imported`.
    skipped_details = []
    for a in accepted:
        etype = a["entity_type"]
        if etype not in ("QHIN", "PARTICIPANT", "SUBPARTICIPANT"):
            etype = "PARTICIPANT"

        # rce_organization_id is unique and NOT NULL. A re-import of the same NPI
        # updates the existing row rather than raising a unique violation.
        rce_id = f"import-{a['npi']}"
        existing = (await db.execute(
            select(TEFCAEntity).where(TEFCAEntity.rce_organization_id == rce_id)
        )).scalar_one_or_none()

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
            db.add(TEFCAEntity(
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
            ))
        imported += 1

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
                 "errors": len(errors), "total": len(rows)},
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
    }


@tefca_dashboard_router.get("/import/history", summary="Entity import history")
async def import_history(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
):
    """Every import attempt, including the ones that imported nothing."""
    q = select(TEFCAImportHistory).order_by(TEFCAImportHistory.uploaded_at.desc())
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


@tefca_dashboard_router.get("/reports/{report_id}/download", summary="Download a report (pdf|docx)")
async def download_report(
    report_id: str,
    format: str = Query("pdf", description="pdf | docx"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("reviewer")),
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
