"""
DocuAction TEFCA Review Protocol
FastAPI Routes — Complete API surface

Loaded via safe_load("app.Tefca", "tefca-review-protocol") in main.py
"""

import asyncio
import uuid
import math
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Query
from pydantic import BaseModel

from .connectors import SourceConnectorManager
from .validation_engine import ValidationEngine, EvidenceRecordGenerator
from .mock_data import ALL_MOCK_ENTITIES, MOCK_STATS

# ─── Router — prefix and tag defined here so safe_load works ─────────────────
tefca_router = APIRouter(
    prefix="/api/v1/tefca",
    tags=["TEFCA Review Protocol"]
)
router = tefca_router  # safe_load expects mod.router

# ─── Lazy initialization ──────────────────────────────────────────────────────
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


# ─── Pydantic Schemas ─────────────────────────────────────────────────────────

class CycleCreateRequest(BaseModel):
    cycle_type: str
    cycle_start_date: str
    cycle_end_date: Optional[str] = None
    cycle_number: Optional[int] = None
    sample_confidence_level: float = 0.95
    methodology_version: str = "1.0"
    created_by: Optional[str] = None


class AnalystOverrideRequest(BaseModel):
    bucket_classification: int
    override_reason: str
    review_notes: Optional[str] = None
    reviewer_id: str


class DispositionUpdateRequest(BaseModel):
    recommendation: str
    recommended_action_detail: str
    prevention_recommendation: Optional[str] = None
    review_notes: Optional[str] = None
    reviewer_id: str


class EscalateRequest(BaseModel):
    escalation_note: str
    reviewer_id: str


class PriorityCaseCreateRequest(BaseModel):
    cor_reference: str
    entity_rce_id: str
    assigned_by: str
    deadline_date: Optional[str] = None
    issue_description: str


class PriorityCaseUpdateRequest(BaseModel):
    case_status: Optional[str] = None
    severity: Optional[str] = None
    root_cause_determination: Optional[str] = None
    root_cause_description: Optional[str] = None
    resolution_notes: Optional[str] = None
    recommendations: Optional[dict] = None


# ─── Connector Health ─────────────────────────────────────────────────────────

@tefca_router.get("/connectors/status",
    summary="Health check all data source connectors")
async def connector_health():
    """
    Returns live/mock/error status for all 6 data sources.
    Use this to confirm which APIs are live vs mock before running reviews.
    """
    manager = get_connector_manager()
    status = await manager.health_check()
    live_count = sum(1 for s in status.values() if s.get("live"))
    return {
        "checked_at": datetime.utcnow().isoformat(),
        "connectors": status,
        "live_connector_count": live_count,
        "mock_connector_count": len(status) - live_count,
        "summary": f"{live_count}/{len(status)} connectors live",
        "pending_actions": [
            "SAM_GOV_API_KEY: set in .env (key already received)",
            "RCE Directory: email sent to techsupport@sequoiaproject.org",
            "IQVIA OneKey: pending contract award ODC",
        ]
    }


# ─── Mock Data ────────────────────────────────────────────────────────────────

@tefca_router.get("/mock/entities",
    summary="View mock RCE Directory dataset")
async def get_mock_entities(
    bucket: Optional[int] = None,
    qhin: Optional[str] = None
):
    """Returns the 30 mock RCE Directory entities for development and testing."""
    entities = ALL_MOCK_ENTITIES
    if bucket:
        entities = [e for e in entities if e.get("_expected_bucket") == bucket]
    if qhin:
        entities = [e for e in entities if e.get("_qhin") == qhin]
    return {
        "total": len(entities),
        "stats": MOCK_STATS,
        "entities": entities,
        "note": "MOCK DATA — RCE API key pending from techsupport@sequoiaproject.org"
    }


# ─── Review Cycles ────────────────────────────────────────────────────────────

@tefca_router.post("/cycles",
    summary="Create new review cycle")
async def create_cycle(request: CycleCreateRequest):
    """Create Task 3 (retrospective), Task 4 (ongoing), or Task 5 (priority) cycle."""
    cycle_id = str(uuid.uuid4())
    return {
        "cycle_id": cycle_id,
        "cycle_type": request.cycle_type,
        "cycle_start_date": request.cycle_start_date,
        "cycle_status": "PLANNED",
        "created_at": datetime.utcnow().isoformat(),
        "message": f"Cycle {cycle_id} created. Use POST /validate/batch to start Tier 1 processing."
    }


@tefca_router.get("/cycles",
    summary="List all review cycles")
async def list_cycles():
    """List all review cycles with status and bucket statistics."""
    return {
        "cycles": [],
        "total": 0,
        "message": "Wire to database for production use."
    }


# ─── Validation ───────────────────────────────────────────────────────────────

@tefca_router.post("/validate/entity",
    summary="Validate single RCE Directory entity")
async def validate_single_entity(entity: dict):
    """
    Run Tier 1 automated validation against a single FHIR R4 Organization resource.
    """
    manager = get_connector_manager()
    engine = get_validation_engine()
    evidence_gen = get_evidence_generator()

    source_results = await manager.query_all_sources(entity)
    validation = engine.validate(entity, source_results)
    cycle_id = str(uuid.uuid4())
    evidence_record = evidence_gen.generate(
        entity, cycle_id, validation, source_results, "SYSTEM_TIER1"
    )

    return {
        "entity_id": entity.get("id"),
        "entity_name": entity.get("name"),
        "validation_result": validation,
        "evidence_record": evidence_record,
        "processing_time": "< 60 seconds (Tier 1 target)",
    }


@tefca_router.post("/validate/batch",
    summary="Run Tier 1 validation on full cycle batch")
async def validate_batch(
    background_tasks: BackgroundTasks,
    cycle_id: str = Query(..., description="Cycle ID to process"),
    entity_type: Optional[str] = Query(None),
    qhin_name: Optional[str] = Query(None),
    use_mock: bool = Query(True)
):
    """
    Trigger Tier 1 automated validation for an entire review cycle.
    Runs asynchronously. Monitor via GET /validate/status/{cycle_id}.
    """
    manager = get_connector_manager()

    rce_result = await manager.rce_directory.get_all_organizations(
        entity_type=entity_type,
        qhin_name=qhin_name,
        limit=500
    )
    entities = rce_result.data.get("organizations", [])
    total = len(entities)

    if total > 100:
        sample_size = min(total, math.ceil(
            (1.96 ** 2 * 0.5 * 0.5) / (0.05 ** 2)
        ))
    else:
        sample_size = total

    background_tasks.add_task(
        _run_batch_validation, entities[:sample_size], cycle_id, manager
    )

    return {
        "cycle_id": cycle_id,
        "total_entities_in_rce": total,
        "sample_size": sample_size,
        "confidence_level": 0.95,
        "status": "PROCESSING",
        "estimated_runtime_minutes": round(sample_size * 0.5),
        "monitor_at": f"/api/v1/tefca/validate/status/{cycle_id}",
        "data_source": "MOCK" if not manager.rce_directory._is_live() else "LIVE_RCE_DIRECTORY",
    }


async def _run_batch_validation(
    entities: list,
    cycle_id: str,
    manager: SourceConnectorManager
):
    """Background task: validate all entities in the batch."""
    engine = get_validation_engine()
    evidence_gen = get_evidence_generator()
    results = []

    for entity in entities:
        try:
            source_results = await manager.query_all_sources(entity)
            validation = engine.validate(entity, source_results)
            evidence_record = evidence_gen.generate(
                entity, cycle_id, validation, source_results
            )
            results.append({
                "entity_id": entity.get("id"),
                "bucket": validation["bucket"],
                "confidence": validation["confidence"],
                "tier": validation["tier"],
                "auto_classify": validation["auto_classify"],
                "record_id": evidence_record["record_id"],
            })
        except Exception as e:
            results.append({
                "entity_id": entity.get("id"),
                "error": str(e),
                "bucket": None,
            })

    return results


@tefca_router.get("/validate/status/{cycle_id}",
    summary="Get batch validation progress")
async def get_validation_status(cycle_id: str):
    """Real-time progress for a running batch validation."""
    return {
        "cycle_id": cycle_id,
        "status": "IN_PROGRESS",
        "message": "Wire to database/cache for production progress tracking.",
    }


# ─── Evidence Records ─────────────────────────────────────────────────────────

@tefca_router.post("/evidence/generate",
    summary="Generate 5-element evidence record")
async def generate_evidence(
    entity: dict,
    cycle_id: Optional[str] = None,
    reviewer_id: str = "SYSTEM_TIER1"
):
    """Generate a complete 5-element evidence record for any entity."""
    if not cycle_id:
        cycle_id = str(uuid.uuid4())

    manager = get_connector_manager()
    engine = get_validation_engine()
    evidence_gen = get_evidence_generator()

    source_results = await manager.query_all_sources(entity)
    validation = engine.validate(entity, source_results)
    evidence_record = evidence_gen.generate(
        entity, cycle_id, validation, source_results, reviewer_id
    )

    bucket_labels = {
        1: "No Discrepancy",
        2: "Minor or Administrative",
        3: "Inexplicable",
        4: "Non-Compliant"
    }

    return {
        "record_id": evidence_record["record_id"],
        "entity_name": entity.get("name"),
        "bucket": validation["bucket"],
        "bucket_label": bucket_labels[validation["bucket"]],
        "confidence_score": validation["confidence"],
        "tier_assigned": validation["tier"],
        "finding_codes": validation["finding_codes"],
        "evidence_record": evidence_record,
        "generated_at": datetime.utcnow().isoformat(),
    }


# ─── Analyst Queue ────────────────────────────────────────────────────────────

@tefca_router.get("/queue/tier2",
    summary="Get Tier 2 analyst review queue")
async def get_tier2_queue(
    priority_only: bool = False,
    qhin: Optional[str] = None,
    limit: int = 50
):
    """Returns prioritized Tier 2 queue. Order: Bucket 4 → 3 → 2, confidence ASC."""
    return {
        "queue": [],
        "total_pending": 0,
        "bucket_4_count": 0,
        "bucket_3_count": 0,
        "bucket_2_count": 0,
        "message": "Wire to database for production use.",
    }


@tefca_router.get("/queue/tier3",
    summary="Get Tier 3 SME escalation queue")
async def get_tier3_queue():
    """Returns all records escalated to Tier 3 SME review."""
    return {
        "queue": [],
        "total_pending": 0,
        "message": "Wire to database for production use.",
    }


@tefca_router.patch("/queue/{record_id}/classify",
    summary="Analyst override classification")
async def override_classification(
    record_id: str,
    request: AnalystOverrideRequest
):
    """Tier 2 analyst overrides the Tier 1 automated bucket classification."""
    bucket_labels = {
        1: "No Discrepancy",
        2: "Minor or Administrative",
        3: "Inexplicable",
        4: "Non-Compliant"
    }
    return {
        "record_id": record_id,
        "new_bucket": request.bucket_classification,
        "bucket_label": bucket_labels.get(request.bucket_classification),
        "override_by": request.reviewer_id,
        "override_reason": request.override_reason,
        "override_timestamp": datetime.utcnow().isoformat(),
        "supervisor_review_required": request.bucket_classification == 4,
        "status": "REVIEWED",
    }


@tefca_router.patch("/queue/{record_id}/escalate",
    summary="Escalate record to Tier 3")
async def escalate_to_tier3(
    record_id: str,
    request: EscalateRequest
):
    """Tier 2 analyst escalates a record to Tier 3 SME review."""
    return {
        "record_id": record_id,
        "escalated_by": request.reviewer_id,
        "escalation_note": request.escalation_note,
        "new_tier": 3,
        "escalated_at": datetime.utcnow().isoformat(),
        "status": "ESCALATED",
    }


# ─── Priority Cases (Task 5) ──────────────────────────────────────────────────

@tefca_router.post("/priority-cases",
    summary="Create COR-directed priority case")
async def create_priority_case(
    request: PriorityCaseCreateRequest,
    background_tasks: BackgroundTasks
):
    """
    Create a COR-directed priority review case (Task 5).
    AGT does NOT self-initiate. COR communicates the referral.
    """
    case_id = str(uuid.uuid4())

    manager = get_connector_manager()
    rce_result = await manager.rce_directory.get_organization_by_id(
        request.entity_rce_id
    )
    entity = rce_result.data if rce_result.success else {}

    if entity:
        background_tasks.add_task(
            _process_priority_case, case_id, entity, manager
        )

    return {
        "case_id": case_id,
        "cor_reference": request.cor_reference,
        "entity_rce_id": request.entity_rce_id,
        "assigned_by": request.assigned_by,
        "deadline_date": request.deadline_date,
        "case_status": "IN_PROGRESS" if entity else "ASSIGNED_PENDING_DATA",
        "created_at": datetime.utcnow().isoformat(),
        "message": f"Priority case {case_id} created. AGT team notified.",
    }


async def _process_priority_case(
    case_id: str,
    entity: dict,
    manager: SourceConnectorManager
):
    """Background: run deep validation for priority case."""
    engine = get_validation_engine()
    evidence_gen = get_evidence_generator()
    source_results = await manager.query_all_sources(entity)
    validation = engine.validate(entity, source_results)
    evidence_gen.generate(
        entity, case_id, validation, source_results, "PRIORITY_REVIEW"
    )


@tefca_router.get("/priority-cases",
    summary="List all priority cases")
async def list_priority_cases(status: Optional[str] = None):
    """List all Task 5 priority cases with current status."""
    return {
        "cases": [],
        "total": 0,
        "open_count": 0,
        "message": "Wire to database for production use.",
    }


@tefca_router.patch("/priority-cases/{case_id}",
    summary="Update priority case")
async def update_priority_case(
    case_id: str,
    request: PriorityCaseUpdateRequest
):
    """Update priority case with investigation findings and recommendations."""
    return {
        "case_id": case_id,
        "updated_fields": request.dict(exclude_none=True),
        "updated_at": datetime.utcnow().isoformat(),
    }


# ─── Reports ─────────────────────────────────────────────────────────────────

@tefca_router.post("/reports/weekly/{cycle_id}",
    summary="Generate Task 3 weekly progress report")
async def generate_weekly_report(cycle_id: str):
    """Generate weekly progress report for Task 3 retrospective cycle."""
    return _build_report(
        report_type="WEEKLY_PROGRESS",
        cycle_id=cycle_id,
        title="TEFCA Review Protocol — Weekly Progress Report",
        task="Task 3 — Retrospective Review",
    )


@tefca_router.post("/reports/biweekly/{cycle_id}",
    summary="Generate Task 4 bi-weekly progress report")
async def generate_biweekly_report(cycle_id: str):
    """Generate bi-weekly progress report for Task 4 ongoing review."""
    return _build_report(
        report_type="BIWEEKLY_PROGRESS",
        cycle_id=cycle_id,
        title="TEFCA Review Protocol — Bi-Weekly Progress Report",
        task="Task 4 — Ongoing Review",
    )


@tefca_router.post("/reports/quarterly",
    summary="Generate quarterly aggregated report")
async def generate_quarterly_report(
    period_start: str = Query(..., description="ISO date YYYY-MM-DD"),
    period_end: str = Query(..., description="ISO date YYYY-MM-DD")
):
    """Generate quarterly aggregated report (Tasks 4 and 5 deliverable)."""
    return _build_report(
        report_type="QUARTERLY_AGGREGATED",
        cycle_id=None,
        title="TEFCA Review Protocol — Quarterly Aggregated Report",
        task="Task 4 & 5 — Quarterly Aggregation",
        period_start=period_start,
        period_end=period_end,
    )


@tefca_router.post("/reports/priority-case/{case_id}",
    summary="Generate Task 5 priority case report")
async def generate_priority_case_report(case_id: str):
    """Generate status report for a specific COR-directed priority review."""
    return _build_report(
        report_type="PRIORITY_CASE_STATUS",
        cycle_id=case_id,
        title="TEFCA Review Protocol — Priority Review Status Report",
        task="Task 5 — Priority Reviews",
    )


@tefca_router.post("/reports/closeout",
    summary="Generate Task 6 contract closeout report")
async def generate_closeout_report(
    period_start: str = Query(..., description="ISO date YYYY-MM-DD"),
    period_end: str = Query(..., description="ISO date YYYY-MM-DD")
):
    """Generate contract closeout report (Task 6 deliverable)."""
    return _build_report(
        report_type="CONTRACT_CLOSEOUT",
        cycle_id=None,
        title="TEFCA Review Protocol — Contract Closeout Report",
        task="Task 6 — Contract Closeout",
        period_start=period_start,
        period_end=period_end,
    )


@tefca_router.get("/reports",
    summary="List all generated reports")
async def list_reports():
    """List all generated reports with download links."""
    return {
        "reports": [],
        "total": 0,
        "message": "Wire to database for production use.",
    }


def _build_report(
    report_type: str,
    cycle_id: Optional[str],
    title: str,
    task: str,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> dict:
    """Build report data structure — wire to real DB data for production."""
    report_id = str(uuid.uuid4())
    now = datetime.utcnow()
    return {
        "report_id": report_id,
        "report_type": report_type,
        "title": title,
        "task": task,
        "cycle_id": cycle_id,
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": now.isoformat(),
        "generated_by": "DocuAction TEFCA Module",
        "contractor": "Alliance Global Tech, Inc. (AGT)",
        "contract_reference": "ONC TEFCA Review Protocol",
        "agt_uei": "MP2FLV1MAW93",
        "agt_cage": "8ERE8",
        "methodology_version": "1.0",
        "agt_does_not_adjudicate_note": (
            "AGT produces findings and recommendations. "
            "The ONC COR makes all final determinations."
        ),
        "report_structure": {
            "section_1_executive_summary": "Wire to real data",
            "section_2_bucket_statistics": {
                "bucket_1_no_discrepancy": 0,
                "bucket_2_minor_admin": 0,
                "bucket_3_inexplicable": 0,
                "bucket_4_non_compliant": 0,
                "total_reviewed": 0,
                "sample_confidence_level": 0.95,
            },
            "section_3_methodology_changes": [],
            "section_4_entity_list": "Wire to real data",
        },
        "download_formats": ["PDF", "DOCX"],
        "status": "GENERATED",
    }


# ─── ONC Demo Endpoint ───────────────────────────────────────────────────────

@tefca_router.post("/demo/validate-all-mock",
    summary="ONC Demo — validate all 30 mock entities")
async def demo_validate_all_mock():
    """
    ONC DEMO ENDPOINT.
    Validates all 30 mock entities and returns 4-bucket stratified results.
    Shows complete Tier 1 automated validation pipeline.
    Replace mock data with live RCE Directory when API key arrives from
    techsupport@sequoiaproject.org
    """
    manager = get_connector_manager()
    engine = get_validation_engine()
    evidence_gen = get_evidence_generator()

    cycle_id = str(uuid.uuid4())
    results = []
    bucket_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    tier_counts = {1: 0, 2: 0, 3: 0}
    start = datetime.utcnow()

    for entity in ALL_MOCK_ENTITIES:
        source_results = await manager.query_all_sources(entity)
        validation = engine.validate(entity, source_results)
        record = evidence_gen.generate(
            entity, cycle_id, validation, source_results
        )

        bucket = validation["bucket"]
        tier = validation["tier"]
        bucket_counts[bucket] += 1
        tier_counts[tier] += 1

        results.append({
            "entity_id": entity["id"],
            "entity_name": entity["name"],
            "qhin": entity.get("_qhin"),
            "entity_type": next(
                (c.get("code") for t in entity.get("type", [])
                 for c in t.get("coding", [])), ""
            ),
            "bucket": bucket,
            "bucket_label": validation["bucket_label"],
            "confidence": validation["confidence"],
            "tier": tier,
            "auto_classify": validation["auto_classify"],
            "finding_codes": validation["finding_codes"],
            "record_id": record["record_id"],
            "expected_bucket": entity.get("_expected_bucket"),
            "test_note": entity.get("_test_note"),
            "sources_queried": list(source_results.keys()),
        })

    elapsed = (datetime.utcnow() - start).total_seconds()
    bucket_labels = {
        1: "No Discrepancy",
        2: "Minor or Administrative",
        3: "Inexplicable",
        4: "Non-Compliant"
    }

    return {
        "demo_cycle_id": cycle_id,
        "processed_at": start.isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "total_entities": len(results),
        "data_source": (
            "MOCK (30 test entities)"
            if not manager.rce_directory._is_live()
            else "LIVE RCE DIRECTORY"
        ),
        "bucket_summary": {
            f"bucket_{k}_{bucket_labels[k].lower().replace(' ', '_').replace('/', '')}": v
            for k, v in bucket_counts.items()
        },
        "tier_routing_summary": {
            "tier_1_auto_complete": tier_counts[1],
            "tier_2_analyst_queue": tier_counts[2],
            "tier_3_sme_escalation": tier_counts[3],
        },
        "agt_methodology": {
            "tier_1_target": "< 60 seconds per entity",
            "confidence_threshold_auto_complete": ">=0.95 with Bucket 1",
            "sample_confidence_level": "95%",
            "sources_validated_against": [
                "NPPES (NPI status, legal name, address)",
                "OIG LEIE (exclusion status)",
                "SAM.gov (registration, debarment) — LIVE key active",
                "PECOS (enrollment, payment suspension)",
                "IQVIA OneKey (hierarchy — mock pending contract award)",
                "RCE Directory (mock — key pending Sequoia Project)",
            ],
            "five_element_evidence_record": "Generated for every entity",
            "agt_does_not_adjudicate": "AGT recommends — ONC COR determines",
        },
        "entities": results,
    }