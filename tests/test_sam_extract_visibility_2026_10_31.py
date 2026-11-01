"""SAM public-extract visibility as a DISTINCT source + candidate worklist routing.

The gap under test (found 2026-10-31, after the October catch-up): every
eligible entity carried a `SAM_EXTRACT_SUPPLEMENTARY` row, and NO deployed
surface could enumerate them — `verification_coverage._SOURCE_CASE` mapped
only nppes/pecos/leie/sam (the extract spelling fell to the NULL key),
`findings.csv` is issue-ledger-based while a candidate hit wrote no ledger
row, and `in_review` status does not discriminate. The design under test:

* `sam_extract` is a STATE-layer source of its own (`source_states`,
  `list_not_attempted`): extract rows never count under `sam`, never claim
  exclusion clearance, and never touch the primary API's availability. The
  HEADLINE outcome vocabulary (`SOURCES`/drilldown) deliberately does NOT
  gain the extract — its REVIEW→not_found mapping would label a candidate
  "not found".
* `sam_extract_enumeration` is the authoritative per-entity reading: one
  CURRENT outcome per entity (latest row), historical/duplicate generations
  counted separately, totals reconciled to the eligible population, edition
  provenance carried from the row's own `original_values`.
* A candidate match writes ONE idempotent EXC-001 / SAM_EXTRACT_CANDIDATE
  ledger finding (HIGH, HUMAN_REQUIRED) — on the analyst worklist and in
  findings.csv — and the backfill places pre-EXC-001 candidates there under
  an explicit dry-run-first act. A candidate is never a confirmed exclusion;
  absence of a candidate is never exclusion clearance; nothing adjudicates.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.Tefca.connectors import SourceResult
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce import sam_extract_enumeration as se
from app.tefca_registry.rce import verification_coverage as vc
from app.tefca_registry.rce import verification_findings as vf
from app.tefca_registry.rce.promotion import promote_delivery
from app.tefca_registry.rce.quality_rules import (
    NON_QUALITY_ISSUE_TYPES, RULE_BY_ID, RULE_SET_VERSION,
)
from rce_traceability_support import (  # noqa: F401
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)

@pytest.fixture(autouse=True)
def _resolve_entities_from_the_registry(monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")


EDITION = {
    "publication_date": "2026-10-29",
    "extract_sha256": "faa40b80" + "0" * 56,
    "source_dataset": "SAM_Exclusions_Public_Extract_V2",
    "match_methods": ["normalized_business_name"],
}


def _screen(candidate: bool) -> SourceResult:
    return SourceResult(
        source_name="SAM_EXTRACT", success=True,
        data={"candidate_match": candidate, "candidate_count": int(candidate),
              "candidates": [], **EDITION},
        api_version="GSA-Public-Extract-V2")


def _unavailable() -> SourceResult:
    return SourceResult(source_name="SAM_EXTRACT", success=False,
                        error="extract unavailable or refused")


# ── pure: vocabulary, rule registry, state mapping ───────────────────────────

def test_exc_001_registered_with_truthful_authority():
    rule = RULE_BY_ID["EXC-001"]
    assert rule.default_severity == "HIGH"
    assert rule.stage == "VERIFICATION"
    rule_id, severity, authority = NON_QUALITY_ISSUE_TYPES["SAM_EXTRACT_CANDIDATE"]
    assert (rule_id, severity) == ("EXC-001", "HIGH")
    # HUMAN_REQUIRED, never auto-applied and never QA-auto: the determination
    # (confirm / rule out) belongs to an analyst.
    assert authority == "HUMAN_REQUIRED"
    assert RULE_SET_VERSION == "1.7.0"


def test_state_layer_gains_sam_extract_and_headline_does_not():
    # State layer: the distinct source exists and is truthfully labelled.
    assert "sam_extract" in vc.STATE_SOURCE_KEYS
    assert "not exclusion clearance" in vc.SOURCE_DISPLAY_NAMES["sam_extract"]
    assert "sam_extract_supplementary" in vc._STATE_SOURCE_CASE
    # Headline outcome vocabulary: deliberately unchanged. Its documented
    # REVIEW→not_found mapping would render a CANDIDATE as "not found".
    assert "sam_extract" not in vc.SOURCES
    assert "sam_extract_supplementary" not in vc._SOURCE_CASE
    # And the extract spelling never counts as the primary 'sam' source.
    assert "sam_extract_supplementary" not in vc.SOURCES["sam"]


def test_candidate_values_read_only_review_items():
    bundle = {"dimensions": [{"dimension": "EXCLUSION_REVOCATION", "evidence": [
        {"source": "SAM_EXTRACT_SUPPLEMENTARY", "disposition": "REVIEW",
         "original_values": dict(EDITION)}]}]}
    assert vf.sam_extract_candidate_values(bundle) == dict(EDITION)
    for disp in ("PASS", "UNAVAILABLE"):
        bundle["dimensions"][0]["evidence"][0]["disposition"] = disp
        assert vf.sam_extract_candidate_values(bundle) is None
    assert vf.sam_extract_candidate_values({"dimensions": []}) is None


# ── database ─────────────────────────────────────────────────────────────────

CLEAN_EVIDENCE = {
    "dimensions": [
        {"dimension": "D1_IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]},
    ],
}


@pytest.fixture
async def covered_three(rolled_back_db, monkeypatch):
    """Three promoted entities with (mocked) full coverage and no extract row."""
    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.777.71")
    intake_id, _job = await seed_intake(db, rows)
    await run_quality_and_curation(db, intake_id)
    await promote_delivery(db, intake_id, actor=SYN)
    with patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
               new=AsyncMock(return_value=CLEAN_EVIDENCE)):
        full = await av.run_coverage_batch(db, intake_id, batch_size=100)
    assert full["remaining"] == 0
    return db, intake_id


async def _issues(db, intake_id):
    return (await db.execute(text(
        "SELECT issue_code, rule_id, issue_type, severity, resolution, description "
        "FROM rce_issues WHERE source_intake_id = CAST(:i AS uuid) "
        "AND issue_type = 'SAM_EXTRACT_CANDIDATE' ORDER BY created_at"),
        {"i": str(intake_id)})).all()


async def _curated_oids(db, intake_id):
    return (await db.execute(text(
        "SELECT rce_org_oid, canonical_entity_id FROM rce_curated_records "
        "WHERE source_intake_id = CAST(:i AS uuid) "
        "AND canonical_entity_id IS NOT NULL ORDER BY canonical_entity_id"),
        {"i": str(intake_id)})).all()


async def test_candidate_writes_one_idempotent_ledger_finding(covered_three):
    db, intake_id = covered_three
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(side_effect=[_screen(True), _screen(False), _unavailable()])):
        result = await av.run_coverage_batch(db, intake_id, batch_size=100,
                                             scope="sam_extract")
    assert result["remaining"] == 0

    found = await _issues(db, intake_id)
    assert len(found) == 1
    issue = found[0]
    assert (issue.rule_id, issue.severity, issue.resolution) == ("EXC-001", "HIGH", "OPEN")
    # The finding states candidacy, never a confirmed exclusion.
    assert "never a confirmed exclusion" in issue.description
    assert "2026-10-29" in issue.description  # edition provenance travels

    # Re-screening the same entity (a new generation) does not duplicate the
    # undecided finding.
    oids = await _curated_oids(db, intake_id)
    first = oids[0]
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(return_value=_screen(True))):
        again = await av.record_sam_extract_attempt(
            db, ref=first.rce_org_oid, canonical_entity_id=first.canonical_entity_id)
    assert again["candidate_match"] is True
    assert len(await _issues(db, intake_id)) == 1


async def test_enumeration_is_entity_grained_and_reconciles(covered_three):
    db, intake_id = covered_three
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(side_effect=[_screen(True), _screen(False)])):
        partial = await av.run_coverage_batch(db, intake_id, batch_size=2,
                                              scope="sam_extract")
    assert partial["remaining"] == 1  # third entity deliberately missing

    summary = await se.extract_summary(db, intake_id)
    assert summary["eligible"] == 3
    assert summary["entities_with_extract_row"] == 2
    assert summary["current_outcomes"] == {
        "candidate": 1, "no_candidate": 1, "unavailable": 0, "other": 0, "missing": 1}
    assert summary["reconciles"] is True
    assert summary["evidence_rows"]["duplicate_rows_beyond_latest"] == 0
    assert summary["editions"][0]["publication_date"] == "2026-10-29"

    # A second generation for the candidate entity: still ONE entity, one
    # current outcome; the extra row is counted as history, never a new entity.
    oids = await _curated_oids(db, intake_id)
    first = oids[0]
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(return_value=_screen(True))):
        await av.record_sam_extract_attempt(
            db, ref=first.rce_org_oid, canonical_entity_id=first.canonical_entity_id)
    summary = await se.extract_summary(db, intake_id)
    assert summary["current_outcomes"]["candidate"] == 1
    assert summary["entities_with_extract_row"] == 2
    assert summary["evidence_rows"]["duplicate_rows_beyond_latest"] == 1
    assert summary["evidence_rows"]["entities_with_more_than_one_row"] == 1

    candidates = await se.list_candidates(db, intake_id)
    assert candidates["total"] == 1
    item = candidates["items"][0]
    assert item["entity_id"] == str(first.canonical_entity_id)
    assert item["extract_row_count"] == 2
    assert item["provenance"]["publication_date"] == "2026-10-29"
    assert item["provenance"]["extract_sha256"].startswith("faa40b80")
    assert item["ledger_issue_code"]  # the live path already listed it


async def test_state_layer_counts_extract_apart_from_sam_primary(covered_three):
    db, intake_id = covered_three
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(side_effect=[_screen(True), _screen(False), _screen(False)])):
        await av.run_coverage_batch(db, intake_id, batch_size=100, scope="sam_extract")

    states = await vc.source_states(db, intake_id, eligible=3)
    extract = states["sources"]["sam_extract"]
    assert extract["attempted"] == 3
    assert extract["potential_match"] == 1          # the candidate
    assert extract["confirmed_result"] == 2         # the two no-candidate PASSes
    assert extract["not_attempted"] == 0
    assert extract["is_exclusion_clearance"] is False
    assert extract["is_sam_primary_api"] is False
    # The primary 'sam' source saw no extract row: nothing attempted there.
    assert states["sources"]["sam"]["attempted"] == 0

    # Headline coverage (SOURCES) is untouched by extract rows.
    counts = await vc.coverage_counts(db, intake_id)
    assert counts["sam"]["attempted"] == 0
    assert "sam_extract" not in counts

    # Missing-coverage identities ride the existing not_attempted listing.
    not_attempted = await vc.list_not_attempted(db, intake_id, "sam_extract")
    assert not_attempted["count"] == 0


async def test_backfill_is_dry_run_first_and_idempotent(covered_three):
    db, intake_id = covered_three
    with patch("app.Tefca.sam_extract.SAMExtractConnector.screen",
               new=AsyncMock(side_effect=[_screen(True), _screen(False), _screen(True)])):
        await av.run_coverage_batch(db, intake_id, batch_size=100, scope="sam_extract")
    # Simulate the pre-EXC-001 world: candidate evidence rows exist, no
    # ledger finding does (the October delivery's actual state).
    await db.execute(text(
        "DELETE FROM rce_issues WHERE source_intake_id = CAST(:i AS uuid) "
        "AND issue_type = 'SAM_EXTRACT_CANDIDATE'"), {"i": str(intake_id)})
    await db.flush()

    plan = await se.backfill_candidate_issues(db, intake_id, dry_run=True)
    assert plan["dry_run"] is True
    assert plan["candidates"] == 2
    assert plan["would_write"] == 2
    assert plan["written"] == 0
    assert len(await _issues(db, intake_id)) == 0  # a plan writes nothing
    assert "rce_issues" in plan["write_scope"]

    done = await se.backfill_candidate_issues(db, intake_id, dry_run=False, actor=SYN)
    assert done["written"] == 2
    issues = await _issues(db, intake_id)
    assert len(issues) == 2
    assert all(i.resolution == "OPEN" for i in issues)

    # Running it again changes nothing it already did.
    repeat = await se.backfill_candidate_issues(db, intake_id, dry_run=False, actor=SYN)
    assert repeat["written"] == 0
    assert repeat["already_on_worklist"] == 2
    assert len(await _issues(db, intake_id)) == 2
