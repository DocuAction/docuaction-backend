"""IDENTITY FIRST on the coverage evidence paths (2026-11-25).

Evidence rows are stored per registry ENTITY and `resolve_entity` resolves a delivered reference globally, so every
path that writes source evidence or a screening finding for a delivery must first establish that the delivered record
is the one the sources are being asked about (`verification_findings.validated_delivery_context`). Until this change
two coverage paths and one backfill path did not:

  1. full coverage      `automated_verification.record_coverage_attempt`      evidence rows + entity status were
                                                                               written even when validation refused;
  2. SAM-extract scope  `automated_verification.record_sam_extract_attempt`   no validation at all: the extract row,
                                                                               the EXC-001 candidate finding and the
                                                                               entity status were written;
  3. the batch          `automated_verification.run_coverage_batch`           (the manual route and the scheduler)
                                                                               had no way to report a refusal or to
                                                                               get past one;
  4. candidate backfill `sam_extract_enumeration.backfill_candidate_issues`   wrote a finding on this delivery's
                                                                               ledger from an entity-level row;
  5. the scheduler tick `automated_verification._coverage_tick`               would pick a delivery with only
                                                                               refusals left on every tick.

One test per path fails on the previous code and passes now; the last section proves a correctly identified record
gets exactly what it got before. Synthetic data only; no network (every source call is mocked).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from app.Tefca.connectors import SourceResult
from app.Tefca.models import TEFCADimensionEvidence
from app.tefca_registry import models as reg
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import sam_extract_enumeration as se
from app.tefca_registry.rce import verification_findings as vf
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_entity, seed_intake  # noqa: F401
from test_automated_verification_backoff import FakeClock, harness  # noqa: F401 - fixture
from test_npi_outcome_record_anchoring_2026_11_16 import NPI, T0
from test_recheck_identity_validation_2026_11_16 import (
    OTHER_NPI, _one_delivery, _resolved, _state, _untouched,
)

pytestmark = pytest.mark.asyncio

EXTRACT = av.EXTRACT_SOURCE
EDITION = {"publication_date": "2026-11-24", "extract_sha256": "a" * 64, "match_methods": ["npi"]}


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """The DEV configuration this change is about: extract screening ON, the registry resolver."""
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")


def _everything_evidence() -> dict:
    """One bundle that reaches EVERY write the full path can make: evidence rows, the NPI ledger row with its
    identifier-status change (deactivated NPI), the EXC-001 candidate finding, and the entity status change."""
    return {"dimensions": [
        {"dimension": "IDENTITY", "disposition": "REVIEW", "applicability": "APPLICABLE",
         "evidence": [{"source": "NPPES", "disposition": "REVIEW", "rule_applied": "NPPES_NPI_DEACTIVATED",
                       "original_values": {"status": "DEACTIVATED", "npi": NPI},
                       "note": "NPI deactivated in NPPES."}]},
        {"dimension": "EXCLUSION_REVOCATION", "disposition": "REVIEW", "applicability": "APPLICABLE",
         "evidence": [{"source": EXTRACT, "disposition": "REVIEW", "query_identifier": f"npi={NPI}",
                       "rule_applied": "SAM_PUBLIC_EXTRACT_SUPPLEMENTARY_SCREEN",
                       "original_values": {"candidate_match": True, "candidate_count": 1, **EDITION}}]},
    ]}


def _candidate_screen() -> SourceResult:
    return SourceResult(source_name="SAM_EXTRACT", success=True,
                        data={"candidate_match": True, "candidate_count": 1, "candidates": [], **EDITION},
                        query_params={"npi": NPI, "org": None}, api_version="GSA-Public-Extract-V2")


async def _all_issues(db, *intakes):
    return (await db.execute(select(m.RceIssue).where(
        m.RceIssue.source_intake_id.in_(list(intakes)),
        m.RceIssue.issue_type.in_((vf.NPI_DEACTIVATED, vf.NPI_NOT_FOUND, vf.NPI_VERIFICATION_UNAVAILABLE,
                                   vf.SAM_EXTRACT_CANDIDATE))))).scalars().all()


async def _delivery_of(db, arc, ent, *, delivered_npi, promoted_at=T0):
    """One synthetic delivery whose ONE record links `ent` and delivered `delivered_npi`."""
    rows = make_rows(1, arc=arc)
    rows[0]["NPI"] = delivered_npi
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    rec = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().one()
    db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
                              record_status="CLEAN", rce_org_oid=rec.source_rce_id, transformation_version="test",
                              canonical_entity_id=ent, promoted_at=promoted_at, npi=delivered_npi or None))
    await db.flush()
    return intake_id, rec


async def _refusal_case(db, case):
    """(entity, intake, records, ref, resolved) for one way identity validation must refuse."""
    arc = "9.99.777.90"
    if case == "duplicate_reference":
        ent, intake, recs = await _one_delivery(db, arc, entity_npi=NPI, delivered_npi=NPI, duplicate=True)
        return ent, intake, recs, recs[0].source_rce_id, _resolved(ent)
    ent, intake, recs = await _one_delivery(db, arc, entity_npi=NPI, delivered_npi=NPI)
    ref, resolved = recs[0].source_rce_id, _resolved(ent)
    if case == "npi_not_delivered":
        resolved = _resolved(ent, OTHER_NPI)                  # the probes would ask about an NPI this record did not submit
    elif case == "resolves_to_another_entity":
        other = await seed_entity(db, oid=f"{SYN}-GATE-{uuid.uuid4().hex[:8]}", name=f"{SYN} gate other")
        resolved = _resolved(other, NPI)
    elif case == "reference_not_in_this_delivery":
        ref = "9.99.777.999.1"
    elif case == "conflicting_rce_and_fhir_npi":
        resolved["_rce"] = {"NPI": OTHER_NPI}                 # the CMS probes read this one, NPPES / LEIE / SAM the other
    else:
        raise AssertionError(case)
    return ent, intake, recs, ref, resolved


REFUSAL_CASES = ["npi_not_delivered", "resolves_to_another_entity", "duplicate_reference",
                 "reference_not_in_this_delivery", "conflicting_rce_and_fhir_npi"]


# ── path 1: the full-coverage attempt ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case", REFUSAL_CASES)
async def test_full_coverage_attempt_refused_at_identity_asks_no_source_and_persists_nothing(rolled_back_db, case):
    db = rolled_back_db
    ent, intake, recs, ref, resolved = await _refusal_case(db, case)
    build = AsyncMock(return_value=_everything_evidence())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
        out = await av.record_coverage_attempt(db, ref=ref, canonical_entity_id=ent, intake_id=intake)
    await db.flush()
    build.assert_not_awaited()                                # no source was asked
    _untouched(await _state(db, ent, *recs))                  # no evidence, ledger row, identifier or status change
    assert await _all_issues(db, intake) == []                # ... and no EXC-001 candidate finding either
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED and out["reason"] and out["persisted"] is False
    assert out["entity_id"] == str(ent) and out["dimensions_recorded"] == 0
    resolved_id = resolved.get("_registry_entity_id")
    if resolved_id and str(resolved_id) != str(ent):          # nor anything under the entity the reference resolves to
        other = await _state(db, resolved_id)
        assert other["evidence"] == 0 and other["entity_status"] == "not_verified"


# ── path 2: the SAM-extract-only attempt (ENABLE_SAM_EXTRACT_SCREENING on) ───────────────────────────────────────────

@pytest.mark.parametrize("case", REFUSAL_CASES)
async def test_sam_extract_attempt_refused_at_identity_screens_nothing_and_writes_no_candidate(rolled_back_db, case):
    db = rolled_back_db
    ent, intake, recs, ref, resolved = await _refusal_case(db, case)
    screen = AsyncMock(return_value=_candidate_screen())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=resolved)), \
            patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=screen):
        out = await av.record_sam_extract_attempt(db, ref=ref, canonical_entity_id=ent, intake_id=intake)
    await db.flush()
    screen.assert_not_awaited()                               # the extract was not consulted for it
    _untouched(await _state(db, ent, *recs))                  # no SAM_EXTRACT_SUPPLEMENTARY row, no status change
    assert await _all_issues(db, intake) == []                # no candidate finding on the delivery's ledger
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED and out["reason"] and out["persisted"] is False


async def test_an_extract_attempt_that_names_no_delivery_is_refused(rolled_back_db):
    """No delivery, no delivered record to validate against: fail closed (the batch always names its delivery)."""
    db = rolled_back_db
    ent, _intake, recs = await _one_delivery(db, "9.99.777.91", entity_npi=NPI, delivered_npi=NPI)
    screen = AsyncMock(return_value=_candidate_screen())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))), \
            patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=screen):
        out = await av.record_sam_extract_attempt(db, ref=recs[0].source_rce_id, canonical_entity_id=ent,
                                                  intake_id=None)
    screen.assert_not_awaited()
    _untouched(await _state(db, ent, *recs))
    assert out["outcome"] == av.OUTCOME_IDENTITY_REFUSED


# ── path 3: the batch (POST .../verification-coverage/run, and the scheduler's batch) ────────────────────────────────

async def _four_entities_two_refused_first(db, arc):
    """Four one-record entities in one delivery. The two with the LOWEST entity ids - the ones every batch reaches
    first - resolve with a registry NPI the delivery did not submit."""
    rows = make_rows(4, arc=arc)
    for r in rows:
        r["NPI"] = NPI
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    entities = sorted([await seed_entity(db, oid=f"{SYN}-GATE-{uuid.uuid4().hex[:8]}", name=f"{SYN} gate entity")
                       for _ in recs])
    by_ref = {}
    for index, (rec, ent) in enumerate(zip(recs, entities)):
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
                                  record_status="CLEAN", rce_org_oid=rec.source_rce_id, transformation_version="test",
                                  canonical_entity_id=ent, promoted_at=T0))
        by_ref[rec.source_rce_id] = _resolved(ent, OTHER_NPI if index < 2 else NPI)
    await db.flush()
    return intake_id, entities, by_ref


async def _evidence_entities(db, entities, source=None):
    stmt = select(TEFCADimensionEvidence.entity_id).where(
        TEFCADimensionEvidence.entity_id.in_([str(e) for e in entities]))
    if source:
        stmt = stmt.where(TEFCADimensionEvidence.source == source)
    return {row for row in (await db.execute(stmt)).scalars().all()}


@pytest.mark.parametrize("scope", [av.SCOPE_FULL, av.SCOPE_SAM_EXTRACT])
async def test_batch_reports_refusals_persists_nothing_for_them_and_still_covers_the_records_behind(
        rolled_back_db, scope):
    db = rolled_back_db
    intake_id, entities, by_ref = await _four_entities_two_refused_first(
        db, "9.99.777.92" if scope == av.SCOPE_FULL else "9.99.777.93")
    refused_ids, valid_ids = {str(e) for e in entities[:2]}, {str(e) for e in entities[2:]}

    async def _resolve(_db, ref):
        return by_ref[ref]
    source_call = AsyncMock(return_value=(
        {"dimensions": [{"dimension": "IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
                         "evidence": [{"source": "NPPES", "disposition": "PASS"}]}]}
        if scope == av.SCOPE_FULL else
        SourceResult(source_name="SAM_EXTRACT", success=True, data={"candidate_match": False, **EDITION},
                     api_version="GSA-Public-Extract-V2")))
    target = ("app.Tefca.evidence_service.EvidenceService.build_evidence" if scope == av.SCOPE_FULL
              else "app.Tefca.sam_extract.SAMExtractConnector.screen")
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=_resolve)), \
            patch(target, new=source_call):
        first = await av.run_coverage_batch(db, intake_id, batch_size=1, scope=scope)
        second = await av.run_coverage_batch(db, intake_id, batch_size=1, scope=scope)
        third = await av.run_coverage_batch(db, intake_id, batch_size=1, scope=scope)

    # Nothing was stored for a refused record, and no source was asked about one.
    assert await _evidence_entities(db, entities) == valid_ids
    assert source_call.await_count == 2                       # one source call per VALID record, none for a refusal
    # Two refusals sit in front of every call; batch_size=1 still reaches a record it may look up.
    for call in (first, second):
        assert call["processed_this_call"] == 1 and call["identity_refused"] == 2
        assert call["by_outcome"] == {av.OUTCOME_IDENTITY_REFUSED: 2, av.OUTCOME_VERIFIED: 1}
        assert call["no_further_lookups_possible"] is False   # it looked something up: not a stop
    # The first call stopped at its lookup budget with a record still ahead; the second consumed the list to its
    # end, so it already knows that only the refusals are left.
    assert first["candidates_exhausted"] is False and first["remaining_are_all_identity_refused"] is False
    assert second["candidates_exhausted"] is True and second["remaining_are_all_identity_refused"] is True
    assert (first["remaining"], second["remaining"]) == (3, 2)
    # Nothing left but the refusals: said so, never reported as complete or covered.
    assert third["processed_this_call"] == 0 and third["identity_refused"] == 2
    assert third["remaining"] == 2 and third["covered"] == 2 and third["complete"] is False
    assert third["candidates_exhausted"] is True and third["remaining_are_all_identity_refused"] is True
    assert third["no_further_lookups_possible"] is True       # the stop signal for a "call again" loop
    assert {o["entity_id"] for o in third["outcomes"]} == refused_ids
    assert all(o["outcome"] == av.OUTCOME_IDENTITY_REFUSED and o["reason"] for o in third["outcomes"])
    for ent in entities[:2]:
        row = await db.get(reg.TefcaRegEntity, ent)
        assert row.verification_status == "not_verified"
    assert await _all_issues(db, intake_id) == []


async def test_batch_stops_stepping_over_refusals_at_the_cap_and_returns_the_cursor(rolled_back_db, monkeypatch):
    db = rolled_back_db
    intake_id, entities, by_ref = await _four_entities_two_refused_first(db, "9.99.777.94")
    monkeypatch.setattr(av, "MAX_IDENTITY_REFUSALS_PER_CALL", 2)

    async def _resolve(_db, ref):
        return by_ref[ref]
    build = AsyncMock(return_value={"dimensions": [
        {"dimension": "IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
         "evidence": [{"source": "NPPES", "disposition": "PASS"}]}]})
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=_resolve)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
        capped = await av.run_coverage_batch(db, intake_id, batch_size=1)
        assert capped["identity_refusal_scan_limit_reached"] is True and capped["processed_this_call"] == 0
        assert capped["next_after_entity_id"] == str(entities[1])
        assert capped["remaining_are_all_identity_refused"] is False      # not known: the walk was cut short
        assert capped["no_further_lookups_possible"] is False             # ... so this is not a stop either
        assert capped["identity_refused"] == 2                            # the cap is never exceeded
        resumed = await av.run_coverage_batch(db, intake_id, batch_size=5,
                                              after_entity_id=capped["next_after_entity_id"])
    assert resumed["processed_this_call"] == 2 and resumed["identity_refused"] == 0
    assert resumed["remaining"] == 2 and resumed["candidates_exhausted"] is True
    assert await _evidence_entities(db, entities) == {str(e) for e in entities[2:]}


# ── path 4: the candidate backfill (POST .../sam-extract/candidates/backfill-issues) ─────────────────────────────────

async def _candidate_row(db, ent, *, screened_npi=NPI):
    db.add(TEFCADimensionEvidence(
        id=uuid.uuid4(), entity_id=str(ent), evidence_dimension="EXCLUSION_REVOCATION", source=EXTRACT,
        disposition="REVIEW", query_identifier=(f"npi={screened_npi}" if screened_npi else None),
        rule_applied="SAM_PUBLIC_EXTRACT_SUPPLEMENTARY_SCREEN", original_values=dict(EDITION)))
    await db.flush()


async def test_backfill_writes_no_finding_on_a_delivery_whose_record_fails_identity(rolled_back_db):
    """One entity, two deliveries. Delivery A delivered the entity's NPI and its screen found a candidate (the
    extract row is stored per entity). Delivery B's record delivered a DIFFERENT NPI: the candidate is not B's, and
    B's ledger must not receive it - on a dry run or a real one - while A's still does."""
    db = rolled_back_db
    ent = await seed_entity(db, oid=f"{SYN}-GATE-{uuid.uuid4().hex[:8]}", name=f"{SYN} gate shared", npi=NPI)
    intake_a, rec_a = await _delivery_of(db, "9.99.777.95", ent, delivered_npi=NPI)
    intake_b, rec_b = await _delivery_of(db, "9.99.777.96", ent, delivered_npi=OTHER_NPI,
                                         promoted_at=T0 + timedelta(days=1))
    await _candidate_row(db, ent)

    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))):
        plan_b = await se.backfill_candidate_issues(db, intake_b, dry_run=True)
        done_b = await se.backfill_candidate_issues(db, intake_b, dry_run=False, actor=SYN)
        assert await _all_issues(db, intake_b) == []          # nothing on B's ledger
        assert plan_b["candidates"] == 1 and plan_b["would_write"] == 0 and plan_b["identity_refused"] == 1
        assert done_b["written"] == 0 and done_b["identity_refused"] == 1
        assert "did not submit" in done_b["items"][0]["identity_refused"]

        done_a = await se.backfill_candidate_issues(db, intake_a, dry_run=False, actor=SYN)
    assert done_a["identity_refused"] == 0 and done_a["written"] == 1
    issues_a = await _all_issues(db, intake_a)
    assert [(i.issue_type, i.source_record_id) for i in issues_a] == [(vf.SAM_EXTRACT_CANDIDATE, rec_a.id)]
    assert await _all_issues(db, intake_b) == []


async def test_backfill_refuses_a_row_that_was_screened_on_an_npi_the_record_did_not_deliver(rolled_back_db):
    db = rolled_back_db
    ent, intake, recs = await _one_delivery(db, "9.99.777.97", entity_npi=NPI, delivered_npi=NPI)
    await _candidate_row(db, ent, screened_npi=OTHER_NPI)     # an older generation, screened on another NPI
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))):
        done = await se.backfill_candidate_issues(db, intake, dry_run=False, actor=SYN)
    assert await _all_issues(db, intake) == []
    assert done["written"] == 0 and done["identity_refused"] == 1


# ── path 5: the scheduler tick ───────────────────────────────────────────────────────────────────────────────────────

def _batch_result(**over):
    return {"covered": 0, "eligible": 2, "remaining": 2, "processed_this_call": 0, "identity_refused": 0,
            "candidates_exhausted": True, "identity_refusal_scan_limit_reached": False,
            "next_after_entity_id": None, **over}


async def test_refusals_cost_one_candidate_query_per_page_not_one_per_refused_record(rolled_back_db):
    """Three refused records in front of one valid one, batch_size=1: the walk fetches pages of a fixed minimum
    size, so it reaches the valid record with ONE candidate query - not one query per refusal."""
    from sqlalchemy import event
    db = rolled_back_db
    rows = make_rows(4, arc="9.99.777.88")
    for r in rows:
        r["NPI"] = NPI
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    entities = sorted([await seed_entity(db, oid=f"{SYN}-GATE-{uuid.uuid4().hex[:8]}", name=f"{SYN} gate entity")
                       for _ in recs])
    by_ref = {}
    for index, (rec, ent) in enumerate(zip(recs, entities)):
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
                                  record_status="CLEAN", rce_org_oid=rec.source_rce_id, transformation_version="test",
                                  canonical_entity_id=ent, promoted_at=T0))
        by_ref[rec.source_rce_id] = _resolved(ent, OTHER_NPI if index < 3 else NPI)
    await db.flush()

    async def _resolve(_db, ref):
        return by_ref[ref]
    seen = []
    sync_conn = (await db.connection()).sync_connection

    def _capture(_conn, _cursor, statement, *_rest):
        if "DISTINCT ON (r.canonical_entity_id)" in statement:
            seen.append(statement)
    event.listen(sync_conn, "before_cursor_execute", _capture)
    try:
        with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=_resolve)), \
                patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=AsyncMock(return_value={
                    "dimensions": [{"dimension": "IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
                                    "evidence": [{"source": "NPPES", "disposition": "PASS"}]}]})):
            result = await av.run_coverage_batch(db, intake_id, batch_size=1)
    finally:
        event.remove(sync_conn, "before_cursor_execute", _capture)
    assert result["processed_this_call"] == 1 and result["identity_refused"] == 3
    assert len(seen) == 1, f"{len(seen)} candidate queries for 3 refusals + 1 lookup"


async def test_tick_continues_behind_the_refusal_cap_instead_of_holding_the_delivery(harness):
    """A walk cut short at the refusal cap is NOT a delivery with nothing left: the tick remembers where it stopped
    and the next tick continues from there, so valid records behind many refusals are still reached."""
    engine, clock = harness
    intake, stopped_at = uuid.UUID(int=7), str(uuid.UUID(int=70))
    capped = _batch_result(identity_refused=av.MAX_IDENTITY_REFUSALS_PER_CALL, candidates_exhausted=False,
                           identity_refusal_scan_limit_reached=True, next_after_entity_id=stopped_at)
    with patch.object(av, "_next_delivery_needing_coverage", new=AsyncMock(return_value=intake)), \
            patch.object(av, "run_coverage_batch", new=AsyncMock(return_value=capped)) as batch:
        await av._coverage_tick(now=clock)
    assert batch.await_args.kwargs == {}                                    # the ordinary call is unchanged
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 0

    clock.advance(av.TICK_INTERVAL_SECONDS)
    behind = _batch_result(processed_this_call=2, covered=2, candidates_exhausted=True)
    lookup = AsyncMock(return_value=intake)
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup), \
            patch.object(av, "run_coverage_batch", new=AsyncMock(return_value=behind)) as batch:
        await av._coverage_tick(now=clock)
    assert "exclude_intake_ids" not in lookup.await_args.kwargs             # it was never held
    assert batch.await_args.kwargs == {"after_entity_id": stopped_at}       # continued behind the refusals

    clock.advance(av.TICK_INTERVAL_SECONDS)                                  # the walk ended: start over next time
    with patch.object(av, "_next_delivery_needing_coverage", new=AsyncMock(return_value=intake)), \
            patch.object(av, "run_coverage_batch", new=AsyncMock(return_value=behind)) as batch:
        await av._coverage_tick(now=clock)
    assert batch.await_args.kwargs == {}


async def test_tick_holds_a_delivery_with_only_refusals_left_instead_of_picking_it_forever(harness):
    engine, clock = harness
    stuck, older = uuid.UUID(int=1), uuid.UUID(int=2)
    lookup = AsyncMock(return_value=stuck)
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup), \
            patch.object(av, "run_coverage_batch",
                         new=AsyncMock(return_value=_batch_result(identity_refused=2))):
        await av._coverage_tick(now=clock)
    assert lookup.await_args.kwargs == {"raise_on_timeout": True}            # the ordinary call is unchanged
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 1

    # The next tick leaves the held delivery out of the lookup, so an older delivery is reached.
    clock.advance(av.TICK_INTERVAL_SECONDS)
    lookup = AsyncMock(return_value=older)
    batch = AsyncMock(return_value=_batch_result(processed_this_call=2, covered=2, remaining=0))
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup), \
            patch.object(av, "run_coverage_batch", new=batch):
        await av._coverage_tick(now=clock)
    assert lookup.await_args.kwargs["exclude_intake_ids"] == [str(stuck)]
    assert batch.await_args.args[1] == older
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 1   # a normal batch holds nothing new

    # The hold expires, so a record whose identity was corrected is picked up again without a restart.
    clock.advance(av.backoff_max_seconds() + 1)
    lookup = AsyncMock(return_value=None)
    with patch.object(av, "_next_delivery_needing_coverage", new=lookup):
        await av._coverage_tick(now=clock)
    assert "exclude_intake_ids" not in lookup.await_args.kwargs
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 0


async def test_tick_does_not_hold_a_delivery_that_still_had_records_to_look_up(harness):
    engine, clock = harness
    with patch.object(av, "_next_delivery_needing_coverage", new=AsyncMock(return_value=uuid.UUID(int=3))), \
            patch.object(av, "run_coverage_batch",
                         new=AsyncMock(return_value=_batch_result(identity_refused=2, processed_this_call=1))):
        await av._coverage_tick(now=clock)
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 0


async def test_the_candidate_lookup_leaves_out_a_held_delivery(rolled_back_db):
    """Real SQL: with the newest succeeded delivery excluded, the lookup does not name it."""
    db = rolled_back_db
    ent, intake_id, _recs = await _one_delivery(db, "9.99.777.98", entity_npi=NPI, delivered_npi=NPI)
    sha = uuid.uuid4().hex * 2
    db.add(RceDeliveryJob(
        id=uuid.uuid4(), identity=uuid.uuid4().hex * 2, delivery_label=f"{SYN}-GATE", original_filename="synthetic.csv",
        storage_path="(synthetic)", sha256=sha, file_size_bytes=1, state=RceDeliveryJob.STATE_SUCCEEDED,
        stage=RceDeliveryJob.STAGE_QUALITY, active_marker=None, registered_by=SYN, created_at=datetime.utcnow(),
        started_at=datetime.utcnow(), heartbeat_at=datetime.utcnow(), completed_at=datetime(2999, 1, 1),
        attempt_count=1, stage_detail={}, source_intake_id=intake_id, records_received=1))
    await db.commit()
    assert await av._next_delivery_needing_coverage(db) == intake_id
    assert await av._next_delivery_needing_coverage(db, exclude_intake_ids=[str(intake_id)]) != intake_id
    assert await av._next_delivery_needing_coverage(db, exclude_intake_ids=[]) == intake_id


# ── a correctly identified record is unaffected ──────────────────────────────────────────────────────────────────────

async def test_a_correctly_identified_record_gets_every_write_it_got_before(rolled_back_db):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.777.99", entity_npi=NPI, delivered_npi=NPI)
    evidence = _everything_evidence()
    build = AsyncMock(return_value=evidence)
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
        out = await av.record_coverage_attempt(db, ref=rec.source_rce_id, canonical_entity_id=ent, intake_id=intake)
    await db.flush()
    assert out["outcome"] == av.OUTCOME_REVIEW_REQUIRED and out["dimensions_recorded"] == 2
    assert "reason" not in out and "persisted" not in out
    assert build.await_count == 1 and build.await_args.args[0] == _resolved(ent)   # the sources saw the same entity
    rows = (await db.execute(select(TEFCADimensionEvidence).where(
        TEFCADimensionEvidence.entity_id == str(ent)))).scalars().all()
    assert sorted((r.source, r.disposition) for r in rows) == [("NPPES", "REVIEW"), (EXTRACT, "REVIEW")]
    st = await _state(db, ent, rec)
    assert st["identifier"] == [vf.INACTIVE_PENDING_REVIEW] and st["entity_status"] == "in_review"
    issues = await _all_issues(db, intake)
    assert sorted((i.issue_type, i.source_record_id) for i in issues) == [
        (vf.NPI_DEACTIVATED, rec.id), (vf.SAM_EXTRACT_CANDIDATE, rec.id)]


async def test_a_correctly_identified_record_is_screened_and_its_candidate_recorded_in_the_extract_scope(rolled_back_db):
    db = rolled_back_db
    ent, intake, (rec,) = await _one_delivery(db, "9.99.777.89", entity_npi=NPI, delivered_npi=NPI)
    screen = AsyncMock(return_value=_candidate_screen())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))), \
            patch("app.Tefca.sam_extract.SAMExtractConnector.screen", new=screen):
        out = await av.record_sam_extract_attempt(db, ref=rec.source_rce_id, canonical_entity_id=ent,
                                                  intake_id=intake)
    await db.flush()
    assert out["outcome"] == av.OUTCOME_REVIEW_REQUIRED and out["candidate_match"] is True
    assert screen.await_args.args[0] == NPI                   # screened on the delivered NPI
    rows = (await db.execute(select(TEFCADimensionEvidence).where(
        TEFCADimensionEvidence.entity_id == str(ent)))).scalars().all()
    assert [(r.source, r.disposition) for r in rows] == [(EXTRACT, "REVIEW")]
    assert (await db.get(reg.TefcaRegEntity, ent)).verification_status == "in_review"
    issues = await _all_issues(db, intake)
    assert [(i.issue_type, i.source_record_id) for i in issues] == [(vf.SAM_EXTRACT_CANDIDATE, rec.id)]


async def test_the_probe_npi_rule_is_the_recheck_rule_not_a_new_one():
    """`resolved_probe_npi` is the rule `rechecks._recheck_one` already applies: the RCE NPI and the FHIR NPI
    identifiers must be the same stripped string. Agreement, or only one representation present, is not a refusal;
    any disagreement is - a formatting-only one included (accepting it would be a new identity rule)."""
    fhir = [{"system": "http://hl7.org/fhir/sid/us-npi", "value": NPI}]
    assert vf.resolved_probe_npi({"identifier": fhir, "_rce": {"NPI": f" {NPI} "}}) == (NPI, None)
    assert vf.resolved_probe_npi({"identifier": [], "_rce": {}}) == ("", None)
    assert vf.resolved_probe_npi({"identifier": fhir}) == (NPI, None)
    assert vf.resolved_probe_npi({"identifier": [], "_rce": {"NPI": NPI}}) == (NPI, None)
    for rce in (OTHER_NPI, f"{NPI}.0"):
        probe, conflict = vf.resolved_probe_npi({"identifier": fhir, "_rce": {"NPI": rce}})
        assert probe == "" and "conflicting NPI" in conflict
    two = fhir + [{"system": "http://hl7.org/fhir/sid/us-npi", "value": OTHER_NPI}]
    assert vf.resolved_probe_npi({"identifier": two})[1]


async def test_a_coverage_call_that_refused_records_leaves_an_audit_row_naming_them():
    """The refusals must outlive the HTTP response: the manual route audits them (entity ids and reasons only)."""
    import inspect
    from app.tefca_registry.rce import delivery_routes as dr
    result = {"scope": "full", "identity_refused": 2, "processed_this_call": 1, "remaining": 2, "outcomes": [
        {"entity_id": "e1", "outcome": av.OUTCOME_IDENTITY_REFUSED, "reason": "r1", "ref": "9.99.777.1"},
        {"entity_id": "e2", "outcome": av.OUTCOME_VERIFIED},
        {"entity_id": "e3", "outcome": av.OUTCOME_IDENTITY_REFUSED, "reason": "r3", "ref": "9.99.777.3"}]}
    meta = dr.coverage_refusal_audit_metadata("intake-x", result)
    assert meta["identity_refused"] == 2 and meta["looked_up"] == 1 and meta["persisted_for_refused"] is False
    assert meta["refused_entities"] == [{"entity_id": "e1", "reason": "r1"}, {"entity_id": "e3", "reason": "r3"}]
    assert meta["refused_entities_truncated"] is False and "9.99.777" not in str(meta)   # no delivered reference
    source = inspect.getsource(dr.run_verification_coverage)
    assert 'if result.get("identity_refused")' in source
    assert '"verification_coverage_identity_refused"' in source


async def test_the_coverage_route_keeps_its_role_floor_and_takes_the_cursor():
    """The manual batch route is still Program-Manager-only and accepts the continuation cursor as a UUID."""
    import inspect
    from app.tefca_registry.rce import delivery_routes as dr
    signature = inspect.signature(dr.run_verification_coverage)
    assert "after_entity_id" in signature.parameters
    source = inspect.getsource(dr.run_verification_coverage)
    assert 'require_role("program_manager")' in source and "after_entity_id=after_entity_id" in source
