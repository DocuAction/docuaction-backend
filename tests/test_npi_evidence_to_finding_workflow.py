"""Regression coverage for the NPI evidence-contract defect fixed 2026-09-18.

`verification_findings.npi_outcome_from_evidence` read `dimension["items"]`;
the real evidence producer (`DimensionResult.to_dict()`,
app/Tefca/evidence_dimensions.py) only ever writes `dimension["evidence"]`.
Every test here goes through the REAL, now-fixed production entry point -
`record_from_evidence` calling `npi_outcome_from_evidence` - with evidence
bundles shaped exactly as `_dimension_identity` (app/Tefca/evidence_assembly.py)
actually builds them, not a hand-rolled shape chosen to make the test pass.

NPI_NOT_FOUND is the reachable "invalid NPI" scenario at the VERIFICATION
layer: a malformed NPI *format* is a curation-time rule (NPI-001..004,
quality_rules.py) that never reaches this module at all - a delivered NPI
that passes format/checksum but does not exist in NPPES is what
verification actually sees, and is what NPI-005 exists for.
NPI_DEACTIVATED is the reachable "conflicting" scenario: NPPES's current
record (deactivated) conflicts with the submitted/assumed-active identifier,
and (quality_rules.NON_QUALITY_ISSUE_TYPES) is routed QA_REQUIRED - an
independent-QA work item, not merely logged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as rce_m
from app.tefca_registry.rce import verification_findings as vf
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_VALID_OTHER, SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

pytestmark = pytest.mark.asyncio


# ── evidence bundles shaped exactly as `_dimension_identity` builds them ────

def _bundle(nppes_item: dict) -> dict:
    return {"dimensions": [{"dimension": "D1_IDENTITY", "disposition": "PASS",
                            "applicability": "APPLICABLE", "evidence": [nppes_item]}]}


def _exact_match_evidence(npi: str) -> dict:
    return _bundle({"source": "NPPES", "disposition": "PASS",
                    "original_values": {"npi": npi, "status": "ACTIVE"},
                    "rule_applied": "NPPES_PRIMARY_IDENTITY_AUTHORITY"})


def _not_found_evidence() -> dict:
    return _bundle({"source": "NPPES", "disposition": "NOT_FOUND",
                    "note": "NPI not present in NPPES."})


def _deactivated_evidence(npi: str) -> dict:
    return _bundle({"source": "NPPES", "disposition": "REVIEW",
                    "rule_applied": "NPPES_NPI_DEACTIVATED",
                    "note": "NPI deactivated in NPPES on 2024-01-31.",
                    "original_values": {"npi": npi, "status": "DEACTIVATED",
                                        "deactivation_date": "2024-01-31"}})


def _malformed_evidence_missing_key() -> dict:
    return {"dimensions": [{"dimension": "D1_IDENTITY", "disposition": "PASS",
                            "applicability": "APPLICABLE"}]}  # no "evidence" key at all


@pytest.fixture
async def promoted_entity(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.52")
    rows[0]["NPI"] = NPI_VALID_OTHER
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    from rce_traceability_support import curated_by_oid
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    assert curated.canonical_entity_id is not None
    return db, intake_id, curated


async def test_exact_npi_match_creates_no_finding_and_no_review_task(promoted_entity):
    db, intake_id, curated = promoted_entity
    entity_id = curated.canonical_entity_id

    issue = await vf.record_from_evidence(
        db, entity_id=entity_id, npi=NPI_VALID_OTHER,
        evidence=_exact_match_evidence(NPI_VALID_OTHER))

    assert issue is None
    rows = (await db.execute(select(rce_m.RceIssue).where(
        rce_m.RceIssue.rule_id.in_(["NPI-005", "NPI-006", "NPI-009"]),
        rce_m.RceIssue.source_record_id == curated.source_record_id))).scalars().all()
    assert rows == [], "an exact NPPES match must create no NPI-verification finding"

    entity = await db.get(reg.TefcaRegEntity, entity_id)
    assert entity.verification_status != "in_review"


async def test_not_found_npi_evidence_creates_the_correct_finding_with_full_provenance(
        promoted_entity):
    db, intake_id, curated = promoted_entity
    entity_id = curated.canonical_entity_id
    run_before = None

    issue = await vf.record_from_evidence(
        db, entity_id=entity_id, npi=NPI_VALID_OTHER, evidence=_not_found_evidence())

    assert issue is not None
    assert issue.rule_id == "NPI-005"
    assert issue.issue_type == "NPI_NOT_FOUND"
    assert issue.severity == "MEDIUM"
    assert issue.correction_authority == "HUMAN_REQUIRED"
    # delivery / entity / source / rule / reason / evidence references:
    assert str(issue.source_intake_id) == str(intake_id)
    assert issue.source_record_id == curated.source_record_id
    assert issue.original_value == NPI_VALID_OTHER
    assert issue.issue_code.startswith("VR-")
    assert "NPPES" in issue.description or "npi" in issue.description.lower()


async def test_conflicting_deactivated_npi_evidence_creates_the_correct_finding_and_routes_a_work_item(
        promoted_entity):
    db, intake_id, curated = promoted_entity
    entity_id = curated.canonical_entity_id

    issue = await vf.record_from_evidence(
        db, entity_id=entity_id, npi=NPI_VALID_OTHER,
        evidence=_deactivated_evidence(NPI_VALID_OTHER))

    assert issue is not None
    assert issue.rule_id == "NPI-006"
    assert issue.issue_type == "NPI_DEACTIVATED"
    assert issue.severity == "HIGH"
    # QA_REQUIRED is this outcome's work-item routing signal (Decision 2,
    # 2026-09-18 pre-merge review, quality_rules.NON_QUALITY_ISSUE_TYPES):
    # curation.transition_issue's independent-QA gate picks it up.
    assert issue.correction_authority == "QA_REQUIRED"
    assert "2024-01-31" in issue.description

    from rce_traceability_support import active_npi_rows
    rows = await active_npi_rows(db, entity_id)
    assert [(r.identifier_value, r.identifier_status) for r in rows] == [
        (NPI_VALID_OTHER, vf.INACTIVE_PENDING_REVIEW)]


async def test_rerunning_verification_creates_no_duplicate_finding_or_work_item(
        promoted_entity):
    db, intake_id, curated = promoted_entity
    entity_id = curated.canonical_entity_id

    first = await vf.record_from_evidence(
        db, entity_id=entity_id, npi=NPI_VALID_OTHER, evidence=_not_found_evidence())
    second = await vf.record_from_evidence(
        db, entity_id=entity_id, npi=NPI_VALID_OTHER, evidence=_not_found_evidence())

    assert first is not None and second is not None
    assert first.id == second.id, "a repeat verification must not duplicate an open finding"

    rows = (await db.execute(select(rce_m.RceIssue).where(
        rce_m.RceIssue.rule_id == "NPI-005",
        rce_m.RceIssue.source_record_id == curated.source_record_id))).scalars().all()
    assert len(rows) == 1


async def test_cross_delivery_isolation_of_npi_findings(rolled_back_db):
    db = rolled_back_db
    from rce_traceability_support import curated_by_oid

    deliveries = {}
    for label, arc in (("A", "9.99.777.82"), ("B", "9.99.777.83")):
        rows = make_rows(1, arc=arc)
        rows[0]["NPI"] = NPI_VALID_OTHER
        intake_id, job = await seed_intake(db, rows)
        await run_quality_and_curation(db, intake_id)
        await promote_delivery(db, intake_id, actor=SYN)
        curated = await curated_by_oid(db, intake_id, rows[0]["id"])
        deliveries[label] = {"intake_id": intake_id, "curated": curated}

    def _count(intake_id):
        return len([i for i in issues if str(i.source_intake_id) == str(intake_id)])

    issues = (await db.execute(select(rce_m.RceIssue))).scalars().all()
    a_before = _count(deliveries["A"]["intake_id"])
    b_before = _count(deliveries["B"]["intake_id"])

    issue = await vf.record_from_evidence(
        db, entity_id=deliveries["B"]["curated"].canonical_entity_id,
        npi=NPI_VALID_OTHER, evidence=_not_found_evidence())
    assert issue is not None
    assert str(issue.source_intake_id) == str(deliveries["B"]["intake_id"])

    issues = (await db.execute(select(rce_m.RceIssue))).scalars().all()
    a_after = _count(deliveries["A"]["intake_id"])
    b_after = _count(deliveries["B"]["intake_id"])

    assert b_after == b_before + 1
    assert a_after == a_before, (
        "delivery A's finding count changed after recording delivery B's "
        "NPI_NOT_FOUND finding - cross-delivery leakage")

    entity_a = await db.get(reg.TefcaRegEntity, deliveries["A"]["curated"].canonical_entity_id)
    assert entity_a.verification_status != "in_review"


async def test_malformed_evidence_creates_no_finding_does_not_crash_and_does_not_silently_verify(
        promoted_entity):
    db, intake_id, curated = promoted_entity
    entity_id = curated.canonical_entity_id

    # None of these may raise, and none may be read as VERIFIED (silence
    # about the evidence is not a pass).
    for bad_evidence in (
        _malformed_evidence_missing_key(),
        {"dimensions": [{"dimension": "D1_IDENTITY", "evidence": "not-a-list"}]},
        {"dimensions": [{"dimension": "D1_IDENTITY", "evidence": [None, 42, "x"]}]},
        {"dimensions": []},
        {},
    ):
        issue = await vf.record_from_evidence(
            db, entity_id=entity_id, npi=NPI_VALID_OTHER, evidence=bad_evidence)
        assert issue is None

    rows = (await db.execute(select(rce_m.RceIssue).where(
        rce_m.RceIssue.rule_id.in_(["NPI-005", "NPI-006", "NPI-009"]),
        rce_m.RceIssue.source_record_id == curated.source_record_id))).scalars().all()
    assert rows == [], "malformed evidence must never produce an NPI-verification finding"

    entity = await db.get(reg.TefcaRegEntity, entity_id)
    assert entity.verification_status != "in_review"
