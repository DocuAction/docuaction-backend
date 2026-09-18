"""Verification-time NPI outcomes: NOT_FOUND / DEACTIVATED / UNAVAILABLE are
distinct, land in the issue ledger under NPI-005/006/009, and never duplicate."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import run_selection
from app.tefca_registry.rce import verification_findings as vf
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401
    NPI_VALID_OTHER, SYN, active_npi_rows, curated_by_oid, issues_for, make_rows,
    rolled_back_db, run_quality_and_curation, seed_intake,
)


# ── pure: classifying the connector result ───────────────────────────────────

def test_four_outcomes_from_nppes_results():
    assert vf.npi_outcome_from_nppes(None, ok=False, error="timeout")["outcome"] == vf.NPI_VERIFICATION_UNAVAILABLE
    assert vf.npi_outcome_from_nppes({"found": False, "npi": "x"}, ok=True)["outcome"] == vf.NPI_NOT_FOUND
    assert vf.npi_outcome_from_nppes({"found": True, "status": "ACTIVE"}, ok=True)["outcome"] == vf.VERIFIED
    assert vf.npi_outcome_from_nppes({"found": True, "status": "A"}, ok=True)["outcome"] == vf.VERIFIED
    deact = vf.npi_outcome_from_nppes(
        {"found": True, "status": "DEACTIVATED", "deactivation_date": "2024-01-31"}, ok=True)
    assert deact["outcome"] == vf.NPI_DEACTIVATED
    assert deact["deactivation_date"] == "2024-01-31"
    assert "2024-01-31" in deact["detail"]
    by_date = vf.npi_outcome_from_nppes(
        {"found": True, "status": "A", "deactivation_date": "2024-01-31"}, ok=True)
    assert by_date["outcome"] == vf.NPI_DEACTIVATED
    reactivated = vf.npi_outcome_from_nppes(
        {"found": True, "status": "A", "deactivation_date": "2024-01-31",
         "reactivation_date": "2024-06-01"}, ok=True)
    assert reactivated["outcome"] == vf.VERIFIED


def test_connector_shape_normalises_status():
    from app.Tefca.connectors import NPPESConnector

    active = NPPESConnector._shape({"number": "1234567893", "basic": {"status": "A"}}, "")
    assert active["status"] == "ACTIVE" and active["npi_active"] is True
    assert active["status_raw"] == "A"
    gone = NPPESConnector._shape(
        {"number": "1234567893", "basic": {"deactivation_date": "2024-01-31"}}, "")
    assert gone["status"] == "DEACTIVATED" and gone["npi_active"] is False
    assert gone["deactivation_date"] == "2024-01-31"
    back = NPPESConnector._shape(
        {"number": "1234567893", "basic": {"deactivation_date": "2024-01-31",
                                           "reactivation_date": "2024-06-01"}}, "")
    assert back["status"] == "ACTIVE"


def test_outcome_from_evidence_bundle():
    # `"evidence"` is the canonical per-source list key - the one
    # `DimensionResult.to_dict()` (app/Tefca/evidence_dimensions.py) actually
    # serialises, and the one every real producer/consumer in the codebase
    # uses. Before 2026-09-18 this fixture used `"items"`, silently mirroring
    # a bug in `npi_outcome_from_evidence` itself rather than testing the
    # real production evidence shape.
    def bundle(item):
        return {"dimensions": [{"dimension": "D1_IDENTITY", "evidence": [item]}]}

    assert vf.npi_outcome_from_evidence(bundle(
        {"source": "NPPES", "disposition": "UNAVAILABLE", "note": "down"}))["outcome"] == vf.NPI_VERIFICATION_UNAVAILABLE
    assert vf.npi_outcome_from_evidence(bundle(
        {"source": "NPPES", "disposition": "NOT_FOUND"}))["outcome"] == vf.NPI_NOT_FOUND
    assert vf.npi_outcome_from_evidence(bundle(
        {"source": "NPPES", "disposition": "REVIEW", "rule_applied": "NPPES_NPI_DEACTIVATED",
         "original_values": {"status": "DEACTIVATED", "deactivation_date": "2024-01-31"}}))["outcome"] == vf.NPI_DEACTIVATED
    assert vf.npi_outcome_from_evidence(bundle(
        {"source": "NPPES", "disposition": "PASS", "original_values": {"status": "ACTIVE"}}))["outcome"] == vf.VERIFIED
    assert vf.npi_outcome_from_evidence({"dimensions": []}) is None


def test_outcome_from_evidence_handles_a_malformed_or_missing_evidence_key_safely():
    """A dimension with no `evidence` key, a non-list `evidence`, or a
    non-dict item must never raise and must never be read as VERIFIED -
    silence about the evidence is not a pass."""
    assert vf.npi_outcome_from_evidence(
        {"dimensions": [{"dimension": "D1_IDENTITY"}]}) is None
    assert vf.npi_outcome_from_evidence(
        {"dimensions": [{"dimension": "D1_IDENTITY", "evidence": "not-a-list"}]}) is None
    assert vf.npi_outcome_from_evidence(
        {"dimensions": [{"dimension": "D1_IDENTITY", "evidence": [None, "also-not-a-dict"]}]}) is None
    assert vf.npi_outcome_from_evidence({}) is None
    assert vf.npi_outcome_from_evidence(None or {}) is None


def test_evidence_assembly_marks_a_deactivated_npi_for_review():
    from app.Tefca.applicability import build_profile
    from app.Tefca.connectors import SourceResult
    from app.Tefca.evidence_assembly import _dimension_identity

    entity = {"name": "SYNTHETIC ORG",
              "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi",
                              "value": NPI_VALID_OTHER}]}
    nppes_data = {"found": True, "npi": NPI_VALID_OTHER, "legal_name": "SYNTHETIC ORG",
                  "status": "DEACTIVATED", "deactivation_date": "2024-01-31"}
    profile = build_profile(entity, nppes_data=nppes_data)
    nppes = SourceResult.ok("NPPES", nppes_data, {}, "2.1")
    result = _dimension_identity(entity, profile, {"nppes": nppes})
    item = next(i for i in result.items if i.source == "NPPES")
    assert item.disposition == "REVIEW"
    assert item.rule_applied == "NPPES_NPI_DEACTIVATED"
    assert "deactivated" in (item.note or "").lower()
    assert result.disposition == "REVIEW"
    assert "deactivated" in result.rationale.lower()


# ── database: the ledger rows ────────────────────────────────────────────────

@pytest.fixture
async def promoted_entity(rolled_back_db):
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.777.51")
    rows[0]["NPI"] = NPI_VALID_OTHER
    intake_id, job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    curated = await curated_by_oid(db, intake_id, rows[0]["id"])
    assert curated.canonical_entity_id is not None
    return db, intake_id, curated


async def test_not_found_deactivated_and_unavailable_are_distinct_and_not_duplicated(
        promoted_entity):
    db, intake_id, curated = promoted_entity
    entity_id = curated.canonical_entity_id
    run = await run_selection.current_run(db, intake_id)

    nf = await vf.record_npi_outcome(db, entity_id=entity_id, npi=NPI_VALID_OTHER,
                                     outcome=vf.NPI_NOT_FOUND, detail="not in NPPES")
    assert nf.rule_id == "NPI-005" and nf.issue_type == "NPI_NOT_FOUND"
    assert nf.severity == "MEDIUM" and nf.correction_authority == "HUMAN_REQUIRED"
    assert nf.run_id == run.id and nf.source_record_id == curated.source_record_id
    assert nf.issue_code.startswith("VR-")

    again = await vf.record_npi_outcome(db, entity_id=entity_id, npi=NPI_VALID_OTHER,
                                        outcome=vf.NPI_NOT_FOUND, detail="not in NPPES")
    assert again.id == nf.id, "a repeat verification does not duplicate an open finding"

    un = await vf.record_npi_outcome(db, entity_id=entity_id, npi=NPI_VALID_OTHER,
                                     outcome=vf.NPI_VERIFICATION_UNAVAILABLE,
                                     detail="timeout")
    assert un.rule_id == "NPI-009" and un.severity == "INFORMATIONAL"
    assert un.correction_authority == "NO_CORRECTION"

    de = await vf.record_npi_outcome(db, entity_id=entity_id, npi=NPI_VALID_OTHER,
                                     outcome=vf.NPI_DEACTIVATED,
                                     detail={"detail": "deactivated",
                                             "deactivation_date": "2024-01-31"})
    assert de.rule_id == "NPI-006" and de.severity == "HIGH"
    assert "2024-01-31" in de.description
    rows = await active_npi_rows(db, entity_id)
    assert [(r.identifier_value, r.identifier_status) for r in rows] == [
        (NPI_VALID_OTHER, vf.INACTIVE_PENDING_REVIEW)]

    ok = await vf.record_npi_outcome(db, entity_id=entity_id, npi=NPI_VALID_OTHER,
                                     outcome=vf.VERIFIED, detail="active")
    assert ok is None

    ledger = await issues_for(db, curated.source_record_id)
    types = sorted(i.issue_type for i in ledger if i.rule_id.startswith("NPI-00")
                   and i.rule_id in ("NPI-005", "NPI-006", "NPI-009"))
    assert types == ["NPI_DEACTIVATED", "NPI_NOT_FOUND", "NPI_VERIFICATION_UNAVAILABLE"]
    with pytest.raises(ValueError):
        await vf.record_npi_outcome(db, entity_id=entity_id, npi=NPI_VALID_OTHER,
                                    outcome="SOMETHING_ELSE", detail=None)


async def test_record_from_sources_reads_the_probed_nppes_dict(promoted_entity):
    db, intake_id, curated = promoted_entity
    issue = await vf.record_from_sources(
        db, entity_id=curated.canonical_entity_id,
        sources={"nppes": {"status": "not_found", "npi_outcome": "NPI_NOT_FOUND",
                           "npi_outcome_detail": "NPI not present in NPPES.",
                           "lookup_identifier": NPI_VALID_OTHER}})
    assert issue is not None and issue.rule_id == "NPI-005"
    assert issue.original_value == NPI_VALID_OTHER
    assert await vf.record_from_sources(db, entity_id=curated.canonical_entity_id,
                                        sources={"nppes": {"status": "verified"}}) is None


async def test_an_entity_without_a_delivery_writes_no_ledger_row(rolled_back_db):
    db = rolled_back_db
    import uuid
    entity_id = uuid.uuid4()
    db.add(reg.TefcaRegEntity(id=entity_id, name="SYNTHETIC NO-DELIVERY",
                              entity_level="participant", entity_type="provider",
                              current_version=1))
    await db.flush()
    out = await vf.record_npi_outcome(db, entity_id=entity_id, npi=NPI_VALID_OTHER,
                                      outcome=vf.NPI_NOT_FOUND, detail="x")
    assert out is None
    n = (await db.execute(select(m.RceIssue).where(
        m.RceIssue.rule_id == "NPI-005",
        m.RceIssue.original_value == NPI_VALID_OTHER))).scalars().all()
    assert all(i.source_record_id is not None for i in n)
