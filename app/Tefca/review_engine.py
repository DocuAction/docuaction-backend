"""
DocuAction TEFCA — Review Engine, Sampling Methodology & Discrepancy Taxonomy
ONC TEFCA Review Protocol — Contract No. 7571MN26F80064 (HHS/ONC)

New module (TEFCA ARC Task 2). Builds on the existing connectors + validation
engine — it does not duplicate the 4-bucket classification logic, it reuses
ValidationEngine and exposes a sampling methodology + control framework on top.
"""
import math
import random
from typing import List, Dict, Any

from . import connectors
from .validation_engine import ValidationEngine, FindingCode

# The 11 QHINs in the TEFCA network (used for stratification + reporting).
QHINS = [
    "eHealth Exchange", "Epic Nexus", "Health Gorilla", "KONZA", "MedAllies",
    "CommonWell", "Kno2", "eClinicalWorks", "Netsmart", "Surescripts", "Oracle Health",
]

# ─── Discrepancy taxonomy ────────────────────────────────────────────────────
# Maps the 4 review buckets to a dashboard status + risk level + the finding
# codes that land an entity there. Finding codes are reused from the existing
# ValidationEngine (validation_engine.FindingCode) — single source of truth.
DISCREPANCY_TAXONOMY: Dict[str, Dict[str, Any]] = {
    "B1": {
        "bucket": 1, "label": "No Discrepancy", "status": "no_discrepancy",
        "risk_level": "low",
        "description": "All authoritative-source checks passed within tolerance.",
        "finding_codes": [FindingCode.NO_DISCREPANCY],
    },
    "B2": {
        "bucket": 2, "label": "Minor / Administrative", "status": "minor_administrative",
        "risk_level": "medium",
        "description": "Cosmetic/administrative differences (abbreviation, suffix, ZIP format, DBA name, resolved historical exclusion).",
        "finding_codes": [
            FindingCode.NAME_ABBREVIATION_DIFF, FindingCode.NAME_PUNCTUATION_DIFF,
            FindingCode.NAME_DBA_VS_LEGAL, FindingCode.ADDRESS_UNIT_DIFF,
            FindingCode.ADDRESS_FORMAT_DIFF, FindingCode.PHONE_DISCREPANCY,
            FindingCode.ZIP_FORMAT_DIFF, FindingCode.LEIE_HISTORICAL_RESOLVED,
            FindingCode.MINOR_CORP_SUFFIX_DIFF,
        ],
    },
    "B3": {
        "bucket": 3, "label": "Inexplicable", "status": "inexplicable",
        "risk_level": "high",
        "description": "Material discrepancy requiring QHIN investigation (name mismatch, state conflict, entity-type mismatch, lapsed SAM registration, missing NPI).",
        "finding_codes": [
            FindingCode.NAME_COMPLETELY_DIFFERENT, FindingCode.ADDRESS_STATE_CONFLICT,
            FindingCode.ENTITY_TYPE_MISMATCH, FindingCode.NPI_MISSING,
            FindingCode.SAM_REGISTRATION_LAPSED, FindingCode.SOURCE_CONFLICT,
            FindingCode.HIERARCHY_MISMATCH,
        ],
    },
    "B4": {
        "bucket": 4, "label": "Non-Compliant", "status": "non_compliant",
        "risk_level": "critical",
        "description": "Disqualifying finding (active OIG LEIE exclusion, SAM debarment, PECOS payment suspension, invalid/deactivated NPI).",
        "finding_codes": [
            FindingCode.NPI_NOT_FOUND, FindingCode.NPI_INACTIVE, FindingCode.NPI_DEACTIVATED,
            FindingCode.LEIE_ACTIVE_EXCLUSION, FindingCode.SAM_ACTIVE_DEBARMENT,
            FindingCode.PECOS_PAYMENT_SUSPENSION, FindingCode.NAME_UNRESOLVABLE,
        ],
    },
}

_BUCKET_TO_TAX = {1: "B1", 2: "B2", 3: "B3", 4: "B4"}


# ─── Sampling methodology ────────────────────────────────────────────────────

def calculate_sample_size(N: int, confidence: float = 0.95, margin: float = 0.05) -> int:
    """Cochran's sample-size formula with finite population correction.
    z=1.96 (95% CI), p=0.5 (maximum variance), margin=±5%.
    For N=94,231 this returns 383 (the contract's stated sample size)."""
    if N <= 0:
        return 0
    z = 1.96 if abs(confidence - 0.95) < 1e-9 else 2.576 if confidence >= 0.99 else 1.645
    p = 0.5
    n_0 = (z ** 2 * p * (1 - p)) / (margin ** 2)
    n = n_0 / (1 + (n_0 - 1) / N)
    return min(N, math.ceil(n))


def _qhin_of(entity: dict) -> str:
    return entity.get("_qhin") or entity.get("qhin") or "Unknown QHIN"


def select_stratified_sample(entities: List[dict], sample_size: int, seed: int = 42) -> List[dict]:
    """Deterministic stratified sample, proportionally allocated per QHIN.
    A fixed seed makes the draw reproducible/auditable (NIST sampling control)."""
    if not entities or sample_size <= 0:
        return []
    if sample_size >= len(entities):
        return list(entities)
    rng = random.Random(seed)
    by_qhin: Dict[str, list] = {}
    for e in entities:
        by_qhin.setdefault(_qhin_of(e), []).append(e)
    total = len(entities)
    sample: List[dict] = []
    for qhin in sorted(by_qhin):
        items = by_qhin[qhin]
        share = max(1, round(sample_size * len(items) / total))
        share = min(share, len(items))
        sample.extend(rng.sample(items, share))
    rng.shuffle(sample)
    return sample[:sample_size]


# ─── Entity review (reuses ValidationEngine + connectors) ────────────────────

_engine = ValidationEngine()


async def run_entity_review(entity: dict, db=None) -> Dict[str, Any]:
    """Query all connectors for an entity, classify via the existing
    ValidationEngine, and map to the discrepancy taxonomy."""
    results = await connectors.check_all_connectors(entity, db=db)
    # Adapt connector keys to the ValidationEngine's expected source_results keys.
    source_results = {
        "nppes": results["nppes"],
        "leie_npi": results["leie"],
        "sam_entity": results["sam"],
        "sam_exclusion": results["sam"],
        "pecos": results["pecos"],
    }
    validation = _engine.validate(entity, source_results)
    bucket = validation["bucket"]
    tax_key = _BUCKET_TO_TAX.get(bucket, "B1")
    tax = DISCREPANCY_TAXONOMY[tax_key]
    return {
        "entity_id": entity.get("id"),
        "entity_name": entity.get("name"),
        "qhin": _qhin_of(entity),
        "bucket": bucket,
        "taxonomy": tax_key,
        "status": tax["status"],
        "risk_level": tax["risk_level"],
        "confidence": validation["confidence"],
        "classification_state": validation.get("classification_state"),
        "indeterminate": validation.get("indeterminate"),
        "finding_codes": validation["finding_codes"],
        "sources_checked": list(results.keys()),
    }


# ─── Control framework / methodology ─────────────────────────────────────────

def generate_control_framework() -> Dict[str, Any]:
    """Returns the review methodology as structured JSON (deliverable artifact)."""
    return {
        "methodology": "TEFCA QHIN Participant & Subparticipant Data Accuracy Review",
        "contract": "7571MN26F80064",
        "contractor": "Alliance Global Tech, Inc. (AGT)",
        "sampling": {
            "method": "Cochran sample size with finite population correction",
            "confidence_level": 0.95,
            "z_score": 1.96,
            "margin_of_error": 0.05,
            "assumed_variance_p": 0.5,
            "stratification": "Proportional allocation across the 11 QHINs",
            "deterministic_seed": 42,
            "note": "For N=94,231 connections the sample size is 383.",
        },
        "qhins": QHINS,
        "discrepancy_taxonomy": DISCREPANCY_TAXONOMY,
        "tier_routing": {
            "tier_1": "Automated validation; Bucket-1 high-confidence auto-completes.",
            "tier_2": "Analyst review for Bucket 2/3 and any INDETERMINATE (source unavailable).",
            "tier_3": "SME escalation for Bucket 4 / confirmed non-compliance.",
        },
        "authoritative_sources": ["NPPES", "OIG LEIE", "SAM.gov", "PECOS"],
        "fail_closed": "A required source being unavailable yields INDETERMINATE (never a clean Bucket-1 auto-complete).",
        "agt_does_not_adjudicate": "AGT produces findings and recommendations; the ONC COR makes all final determinations.",
    }


# ═══════════════════════════════════════════════════════════════════════════
# TEFCA Task 5 — COR-directed priority reviews (~20/month). Additive.
# ═══════════════════════════════════════════════════════════════════════════

# CaseStatus enum value -> COR-friendly status name
PRIORITY_STATUS_FRIENDLY = {
    "ASSIGNED": "queued", "IN_PROGRESS": "in_progress", "PENDING_COR": "pending_cor",
    "RESOLVED_ACTION": "completed", "RESOLVED_NO_ACTION": "completed", "ESCALATED": "escalated",
}

_BUCKET_SEVERITY = {1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "CRITICAL"}


def severity_from_issue(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ("exclusion", "debarment", "suspension")):
        return "CRITICAL"
    if any(k in t for k in ("npi mismatch", "unresolvable", "lapsed", "deactivated", "invalid npi")):
        return "HIGH"
    if any(k in t for k in ("discrepancy", "conflict", "duplicate", "mismatch")):
        return "MEDIUM"
    return "LOW"


def root_cause_from_issue(text: str) -> str:
    t = (text or "").lower()
    if "exclusion" in t:
        return "LEIE_ACTIVE_EXCLUSION"
    if "suspension" in t:
        return "PECOS_PAYMENT_SUSPENSION"
    if "enrollment" in t:
        return "PECOS_ENROLLMENT_DISCREPANCY"
    if "npi" in t:
        return "NPI_MISMATCH"
    if "address" in t or "state" in t:
        return "ADDRESS_STATE_CONFLICT"
    if "name" in t:
        return "NAME_MISMATCH"
    return "UNDETERMINED"


async def create_priority_review(db, cor_reference: str, issue_description: str,
                                 qhin: str = None, deadline_date=None,
                                 assigned_by: str = "COR", entity_id=None):
    """Create a COR-directed priority case (Task 5). Returns the ORM row."""
    from .models import TEFCAPriorityCase, CaseStatus
    case = TEFCAPriorityCase(
        cor_reference=cor_reference,
        qhin=qhin,
        entity_id=entity_id,
        assigned_by=assigned_by,
        assigned_date=datetime.utcnow(),
        deadline_date=deadline_date,
        issue_description=issue_description,
        case_status=CaseStatus.ASSIGNED,
    )
    db.add(case)
    await db.flush()
    return case


async def execute_priority_review(db, case) -> dict:
    """Run connectors for the case's linked entity if an NPI is resolvable;
    otherwise assess from the issue text. Determines root cause + severity and
    moves the case to in-progress. Returns a summary dict."""
    from sqlalchemy import select as _select
    from .models import TEFCAEntity, CaseStatus, CaseSeverity
    finding_codes = []
    npi = None
    if case.entity_id:
        ent = (await db.execute(_select(TEFCAEntity).where(TEFCAEntity.entity_id == case.entity_id))).scalar_one_or_none()
        npi = ent.npi_submitted if ent else None

    if npi:
        entity = {"id": str(case.entity_id), "name": "",
                  "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": npi}],
                  "_qhin": case.qhin}
        result = await run_entity_review(entity, db=db)
        finding_codes = result["finding_codes"]
        severity = _BUCKET_SEVERITY.get(result["bucket"], "MEDIUM")
        root_cause = finding_codes[0] if finding_codes else "NO_DISCREPANCY"
        source = "connectors"
    else:
        severity = severity_from_issue(case.issue_description)
        root_cause = root_cause_from_issue(case.issue_description)
        source = "issue_assessment"

    case.case_status = CaseStatus.IN_PROGRESS
    case.severity = CaseSeverity[severity]
    case.root_cause_determination = root_cause
    return {
        "case_id": str(case.case_id), "status": "in_progress",
        "severity": severity, "root_cause": root_cause,
        "assessed_from": source, "finding_codes": finding_codes,
    }
