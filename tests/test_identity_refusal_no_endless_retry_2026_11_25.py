"""Evidence-writing paths outside coverage, and proof that an identity refusal cannot loop forever (2026-11-25).

Owner instruction: "Address the remaining bulk/review-cycle and viewer-GET evidence-writing paths, or demonstrate
that November execution cannot invoke them. ... Test that identity refusals do not create an endless retry loop."

  A. the two viewer GET evidence-dimensions routes no longer write unless asked to by a role that may write evidence;
  B. the bulk writer (`arc_pipeline.verify_and_classify`: bulk verify route and review cycle) no longer appends rows
     to the ENTITY evidence store for a reference the named delivery does not anchor to one delivered record - and
     the review it creates (bucket, rule, tier, status, linkage) is byte-for-byte what it was;
  C. termination: the manual coverage route followed by a client, the scheduler tick across many ticks and across a
     process restart, and the controlled-recheck batch;
  D. two review-cycle behaviours that are REPRODUCED here and deliberately NOT changed, because changing them
     decides what a sample member is and when a sample is complete (a methodology decision). The tests pin today's
     behaviour so it cannot be forgotten or changed by accident; each says what an owner decision would invert.

Synthetic data only; every source call is mocked.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.Tefca import routes as legacy_routes
from app.Tefca.models import TEFCADimensionEvidence
from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import arc_pipeline as ap
from app.tefca_registry.rce import automated_verification as av
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import recheck_models as rm
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_entity, seed_intake  # noqa: F401
from test_automated_verification_backoff import FakeClock, harness  # noqa: F401 - fixture
from test_npi_outcome_record_anchoring_2026_11_16 import NPI, T0
from test_recheck_identity_validation_2026_11_16 import (
    OTHER_NPI, _one_delivery, _real_job, _resolved, _run_job, _state, _untouched,
)
from test_review_lineage_writers_db_2026_11_12 import _curate, _entity, _records, _run

pytestmark = pytest.mark.asyncio

NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"


@pytest.fixture(autouse=True)
def _registry_resolver(monkeypatch):
    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")


def _evidence() -> dict:
    return {"generated_at": "2026-11-25T12:00:00Z", "applicability": {}, "sufficiency": {}, "data_quality_flags": [],
            "dimensions": [{"dimension": "IDENTITY", "disposition": "PASS", "applicability": "APPLICABLE",
                            "evidence": [{"source": "NPPES", "disposition": "PASS"}]}]}


async def _evidence_count(db, *entities) -> int:
    return len((await db.execute(select(TEFCADimensionEvidence.id).where(
        TEFCADimensionEvidence.entity_id.in_([str(e) for e in entities])))).scalars().all())


# ═══ A. the viewer GET routes: a read does not write ═════════════════════════════════════════════════════════════════

def _user(role):
    return SimpleNamespace(id=uuid.uuid4(), email=f"{role}@synthetic-test.docuaction.invalid", role=role)


async def _get_entity_evidence(db, ent, user, **kw):
    build = AsyncMock(return_value=_evidence())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(return_value=_resolved(ent))), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build), \
            patch.object(legacy_routes, "log_tefca_event", new=AsyncMock()):
        out = await legacy_routes.entity_evidence_dimensions(
            NPI, include_website=False, db=db, user=user, **kw)
    return out, build


def test_the_evidence_get_routes_do_not_store_by_default():
    for route in (legacy_routes.entity_evidence_dimensions, legacy_routes.review_evidence_dimensions):
        assert inspect.signature(route).parameters["persist"].default is False
    assert legacy_routes.EVIDENCE_PERSIST_ROLE == "program_manager"


@pytest.mark.parametrize("role", ["viewer", "reviewer", "program_manager"])
async def test_a_page_view_reads_the_evidence_and_stores_nothing(rolled_back_db, role):
    """What the Entity Reviews workspace does (frontend EvidenceDimensions.js: no `persist` parameter): the evidence
    is returned to the reader and NOTHING is appended under the entity - whatever the reader's role."""
    db = rolled_back_db
    ent, _intake, recs = await _one_delivery(db, "9.99.778.01", entity_npi=NPI, delivered_npi=NPI)
    out, build = await _get_entity_evidence(db, ent, _user(role))
    assert build.await_count == 1 and len(out["dimensions"]) == 1          # the reader still gets the evidence
    assert out["persisted_rows"] == 0
    _untouched(await _state(db, ent, *recs))
    # ... so a page view can no longer make automated coverage believe the entity was already covered
    assert (await av.coverage_progress(db, _intake))["remaining"] == 1


async def test_asking_a_read_to_store_is_refused_below_the_evidence_writing_role_before_any_source_is_asked(
        rolled_back_db):
    db = rolled_back_db
    ent, _intake, recs = await _one_delivery(db, "9.99.778.02", entity_npi=NPI, delivered_npi=NPI)
    for role in ("viewer", "reviewer", "qalead"):
        with pytest.raises(HTTPException) as refused:
            await _get_entity_evidence(db, ent, _user(role), persist=True)
        assert refused.value.status_code == 403
    _untouched(await _state(db, ent, *recs))
    with pytest.raises(HTTPException) as refused:                           # the review-keyed route: same rule,
        await legacy_routes.review_evidence_dimensions(                     # refused before the review is even read
            str(uuid.uuid4()), persist=True, include_website=False, db=db, user=_user("viewer"))
    assert refused.value.status_code == 403


async def test_an_explicit_store_by_the_evidence_writing_role_still_stores(rolled_back_db):
    db = rolled_back_db
    ent, _intake, _recs = await _one_delivery(db, "9.99.778.03", entity_npi=NPI, delivered_npi=NPI)
    out, _build = await _get_entity_evidence(db, ent, _user("program_manager"), persist=True)
    assert out["persisted_rows"] == 1 and await _evidence_count(db, ent) == 1


# ═══ B. the bulk writer: the entity evidence store is written only for an anchored record ════════════════════════════

def _gather(monkeypatch, entity_by_ref, npi_by_ref=None):
    """Replace only the network edge of the bulk writer (resolution + evidence gathering)."""
    async def fake(refs, timing_log=None):
        out = []
        for ref in refs:
            ent = entity_by_ref.get(ref)
            if ent is None:
                out.append({"ref": ref, "entity": None, "evidence": None})
                continue
            npi = (npi_by_ref or {}).get(ref)
            out.append({"ref": ref, "evidence": _evidence(),
                        "entity": {"id": str(ent), "name": f"entity {ref}", "_registry_entity_id": ent,
                                   "identifier": ([{"system": NPI_SYSTEM, "value": npi}] if npi else []),
                                   "_rce": {}, "_resolution_source": "test"}})
        return out
    monkeypatch.setattr(ap, "_gather_all_evidence", fake)


async def _reviews_of(db, *entities):
    return (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.entity_id.in_(list(entities))))).scalars().all()


async def _bulk_case(db, case):
    """(intake, refs to verify, entity_by_ref, expected lineage, entities that must get no stored evidence)."""
    arc = {"ambiguous": "9.99.778.10", "entity_mismatch": "9.99.778.11", "no_delivery_record": "9.99.778.12"}[case]
    rows = make_rows(2, arc=arc)
    if case == "ambiguous":
        rows[1]["id"] = rows[0]["id"]                          # one delivered id on two records
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    linked = await _entity(db, "linked")
    await _curate(db, intake_id, recs[1], linked)
    if case == "ambiguous":
        await _curate(db, intake_id, recs[2], linked)
        return intake_id, [rows[0]["id"]], {rows[0]["id"]: linked}, ap.LINEAGE_AMBIGUOUS, [linked]
    if case == "entity_mismatch":
        resolved = await _entity(db, "resolved")               # the reference resolves to ANOTHER entity
        return intake_id, [rows[0]["id"]], {rows[0]["id"]: resolved}, ap.LINEAGE_ENTITY_MISMATCH, [linked, resolved]
    other_ref = "9.99.778.999.1"                               # a reference this delivery does not carry
    return intake_id, [other_ref], {other_ref: linked}, ap.LINEAGE_NO_DELIVERY_RECORD, [linked]


@pytest.mark.parametrize("case", ["ambiguous", "entity_mismatch", "no_delivery_record"])
async def test_bulk_writer_stores_no_entity_evidence_for_a_reference_the_delivery_does_not_anchor(
        rolled_back_db, monkeypatch, case):
    db = rolled_back_db
    intake_id, refs, entity_by_ref, lineage, entities = await _bulk_case(db, case)
    _gather(monkeypatch, entity_by_ref)
    res = await ap.verify_and_classify(db, refs, intake_id=intake_id)

    assert await _evidence_count(db, *entities) == 0                       # nothing appended to the entity store
    assert res["evidence_not_stored_unanchored"] == [{"entity_ref": refs[0], "lineage": lineage}]   # and said so
    # THE REVIEW IS EXACTLY WHAT IT WAS: created, classified, unanchored, carrying its own evidence snapshot.
    assert res["lineage"][lineage] == 1 and res["verified"] == 1 and res["unresolved"] == []
    outcome = res["outcomes"][0]
    assert outcome["source_record_id"] is None and outcome["bucket"] and outcome["evidence_stored"] is False
    reviews = await _reviews_of(db, entity_by_ref[refs[0]])
    assert [r.review_id for r in reviews] == [outcome["review_id"]]
    assert reviews[0].classification_bucket == outcome["bucket"] and reviews[0].source_record_id is None
    assert reviews[0].verification_results["dimensions"] == _evidence()["dimensions"]


async def test_bulk_writer_still_stores_the_evidence_of_an_anchored_record_and_of_a_call_with_no_delivery(
        rolled_back_db, monkeypatch):
    """Unchanged: a correctly identified record, and the no-delivery call (which has no delivery to be unanchored in)."""
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.778.13")
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    ent = await _entity(db, "anchored")
    await _curate(db, intake_id, rec, ent)
    _gather(monkeypatch, {rows[0]["id"]: ent})

    res = await ap.verify_and_classify(db, [rows[0]["id"]], intake_id=intake_id)
    assert res["lineage"][ap.LINEAGE_ANCHORED] == 1 and res["evidence_not_stored_unanchored"] == []
    assert res["outcomes"][0]["evidence_stored"] is True and res["outcomes"][0]["source_record_id"] == str(rec.id)
    assert await _evidence_count(db, ent) == 1

    res = await ap.verify_and_classify(db, [rows[0]["id"]])                # no delivery named
    assert res["lineage"][ap.LINEAGE_NO_DELIVERY_RECORD] == 1 and res["evidence_not_stored_unanchored"] == []
    assert await _evidence_count(db, ent) == 2


# ═══ C. termination ══════════════════════════════════════════════════════════════════════════════════════════════════

async def _refused_then_valid(db, arc, *, refused, valid):
    """One delivery: `refused` entities with the LOWEST ids resolve with an NPI the delivery did not submit, then
    `valid` entities that pass."""
    rows = make_rows(refused + valid, arc=arc)
    for r in rows:
        r["NPI"] = NPI
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    recs = (await db.execute(select(m.RceSourceRecord).where(
        m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()
    entities = sorted([await seed_entity(db, oid=f"{SYN}-LOOP-{uuid.uuid4().hex[:8]}", name=f"{SYN} loop entity")
                       for _ in recs])
    by_ref = {}
    for index, (rec, ent) in enumerate(zip(recs, entities)):
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=rec.id,
                                  record_status="CLEAN", rce_org_oid=rec.source_rce_id, transformation_version="test",
                                  canonical_entity_id=ent, promoted_at=T0))
        by_ref[rec.source_rce_id] = _resolved(ent, OTHER_NPI if index < refused else NPI)
    await db.flush()
    return intake_id, entities, by_ref


@pytest.mark.parametrize("refused,valid", [(5, 0), (5, 3), (2, 3), (0, 3)])
async def test_a_client_following_the_cursor_and_the_stop_signal_always_stops(rolled_back_db, monkeypatch,
                                                                              refused, valid):
    """(i) The manual coverage route, driven the way its documentation says: call again until `remaining` is 0 or
    `no_further_lookups_possible`; pass `next_after_entity_id` back when the refusal cap cut the walk short. With
    more refusals than the cap (cap forced to 2 here) the client still stops, in a bounded number of calls, having
    looked up every valid record exactly once and nothing for a refused one."""
    db = rolled_back_db
    intake_id, entities, by_ref = await _refused_then_valid(
        db, f"9.99.778.2{refused}{valid}", refused=refused, valid=valid)
    monkeypatch.setattr(av, "MAX_IDENTITY_REFUSALS_PER_CALL", 2)

    async def _resolve(_db, ref):
        return by_ref[ref]
    build = AsyncMock(return_value=_evidence())
    calls, after, stopped, refusal_checks = 0, None, None, 0
    bound = 2 * (refused + valid) + 4                                      # generous; an endless loop blows through it
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=_resolve)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
        while calls < bound:
            result = await av.run_coverage_batch(db, intake_id, batch_size=1, after_entity_id=after)
            calls += 1
            refusal_checks += result["identity_refused"]
            assert result["identity_refused"] <= 2                         # bounded work per call
            if result["remaining"] == 0:
                stopped = "complete"
                break
            if result["no_further_lookups_possible"]:
                stopped = "nothing_left_to_look_up"
                break
            after = result["next_after_entity_id"]                         # None unless the cap cut the walk short
    assert stopped == ("complete" if refused == 0 else "nothing_left_to_look_up"), f"no stop after {calls} calls"
    assert build.await_count == valid                                      # each valid record once, no refusal ever
    final = await av.coverage_progress(db, intake_id)
    assert final == {"eligible": refused + valid, "remaining": refused, "covered": valid}
    assert await _evidence_count(db, *entities[:refused]) == 0


def _tick_result(**over):
    return {"covered": 0, "eligible": 0, "remaining": 0, "processed_this_call": 0, "identity_refused": 0,
            "candidates_exhausted": True, "identity_refusal_scan_limit_reached": False,
            "next_after_entity_id": None, **over}


class _FakeDelivery:
    """A delivery as the scheduler sees it: `refused` un-coverable entities in front of `valid` coverable ones.
    Answers `run_coverage_batch` the way the real batch does (cap, cursor, exhausted) and counts the work done."""

    def __init__(self, intake, refused, valid, cap, batch=2):
        self.intake, self.refused, self.valid, self.cap, self.batch = intake, refused, valid, cap, batch
        self.covered, self.validations, self.calls = 0, 0, 0

    def run(self, after):
        self.calls += 1
        start = int(after) if after else 0                                 # index into the refused run
        seen = min(self.cap, self.refused - start)
        self.validations += seen
        if start + seen < self.refused:                                    # cap reached, more refusals ahead
            return _tick_result(identity_refused=seen, candidates_exhausted=False,
                                identity_refusal_scan_limit_reached=True, next_after_entity_id=str(start + seen),
                                remaining=self.refused + self.valid - self.covered)
        looked = min(self.batch, self.valid - self.covered)
        self.covered += looked
        left = self.valid - self.covered
        return _tick_result(identity_refused=seen, processed_this_call=looked, candidates_exhausted=(left == 0),
                            remaining=self.refused + left)


async def _run_ticks(clock, deliveries, ticks):
    """Drive `_coverage_tick` `ticks` times over fake deliveries (newest first); returns the intake each tick took."""
    taken = []

    async def _lookup(db, **kw):
        held = set(kw.get("exclude_intake_ids") or [])
        for d in deliveries:                                               # newest first, as the real lookup orders
            if str(d.intake) not in held and (d.refused or d.covered < d.valid):
                return d.intake
        return None

    async def _batch(db, intake_id, **kw):
        taken.append(intake_id)
        return next(d for d in deliveries if d.intake == intake_id).run(kw.get("after_entity_id"))

    with patch.object(av, "_next_delivery_needing_coverage", new=AsyncMock(side_effect=_lookup)), \
            patch.object(av, "run_coverage_batch", new=AsyncMock(side_effect=_batch)):
        for _ in range(ticks):
            await av._coverage_tick(now=clock)
            clock.advance(av.TICK_INTERVAL_SECONDS)
    return taken


async def test_the_scheduler_does_not_reselect_a_refused_only_delivery_tick_after_tick(harness):
    """(ii) 200 ticks (50 minutes, inside one hold period). The newest delivery has only refusals: it is walked
    once and then left alone, and the older delivery behind it is covered."""
    engine, clock = harness
    stuck = _FakeDelivery(uuid.UUID(int=1), refused=3, valid=0, cap=1000)
    older = _FakeDelivery(uuid.UUID(int=2), refused=0, valid=6, cap=1000)
    taken = await _run_ticks(clock, [stuck, older], ticks=200)
    assert stuck.calls == 1 and stuck.validations == 3                     # one validation-only batch, not 200
    assert older.covered == 6                                              # the delivery behind it was reached
    assert taken.count(stuck.intake) == 1
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 1


async def test_the_scheduler_walk_past_the_cap_covers_what_it_can_and_then_ends_in_a_hold(harness):
    """(ii) More refusals than the cap, then valid records. The walk needs several ticks (the cursor carries it past
    the refusals); the valid records are covered; once only refusals remain, the next full walk ends in a hold and
    the delivery is not walked again for the rest of the 200 ticks."""
    engine, clock = harness
    delivery = _FakeDelivery(uuid.UUID(int=5), refused=4, valid=3, cap=2, batch=2)
    await _run_ticks(clock, [delivery], ticks=200)
    assert delivery.covered == 3                                           # everything coverable was covered
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 1
    # Bounded: the walks that covered the valid records, then ONE more full walk of the refusals, then silence.
    assert delivery.calls <= 9, f"{delivery.calls} batches in 200 ticks"
    assert delivery.validations <= 4 * 4

    exactly_at_page = _FakeDelivery(uuid.UUID(int=6), refused=4, valid=0, cap=2)
    av.reset_coverage_scheduler_state()
    await _run_ticks(clock, [exactly_at_page], ticks=200)
    assert exactly_at_page.calls == 2 and exactly_at_page.validations == 4
    assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 1


async def test_the_scheduler_after_a_restart_repeats_one_walk_per_held_delivery_and_no_more(harness):
    """(ii) Holds and cursors are process memory. THE WORST CASE, stated: after every restart - and after every hold
    expiry (one hour by default) - each refused-only delivery costs one more validation-only walk (its refusals,
    local reads, no source call), `MAX_IDENTITY_REFUSALS_PER_CALL` per tick at most. It is never a per-tick cost."""
    engine, clock = harness
    stuck = _FakeDelivery(uuid.UUID(int=8), refused=3, valid=0, cap=1000)
    await _run_ticks(clock, [stuck], ticks=50)
    assert stuck.calls == 1
    av.reset_coverage_scheduler_state()                                    # a process restart
    await _run_ticks(clock, [stuck], ticks=50)
    assert stuck.calls == 2 and stuck.validations == 6                     # one more walk, then held again
    clock.advance(av.backoff_max_seconds() + 1)                            # the hold expires
    await _run_ticks(clock, [stuck], ticks=50)
    assert stuck.calls == 3                                                # re-validated once (a correction is seen)


async def test_a_recheck_job_never_revisits_a_refused_item_and_ends(rolled_back_db, monkeypatch):
    """(iii) The controlled-recheck batch: a refused item is DONE (outcome IDENTITY_REFUSED), so it is never pending
    again; a job with only refusals ends FAILED with a reason and cannot be run again."""
    from app.tefca_registry.rce import rechecks
    db = rolled_back_db
    intake_id, entities, by_ref = await _refused_then_valid(db, "9.99.778.30", refused=5, valid=0)
    refs = {str(v["_registry_entity_id"]): k for k, v in by_ref.items()}
    job = await _real_job(db, intake_id, [(e, refs[str(e)]) for e in entities])
    job.batch_size = 2
    await db.flush()
    resolved = AsyncMock(side_effect=lambda _db, ref: by_ref[ref])

    async def _resolve(_db, ref):
        return await resolved(_db, ref)
    batches, out = 0, None
    while batches < 10:
        out = await _run_job(db, monkeypatch, job, _resolve)
        batches += 1
        if out["complete"]:
            break
    assert batches == 3 and out["complete"] is True                        # ceil(5 / 2): each item exactly once
    assert resolved.await_count == 5
    assert out["state"] == rm.STATE_FAILED and out["summary"]["identity_refused"] == 5
    assert out["summary"]["pending"] == 0
    with pytest.raises(rechecks.RecheckRefused):                           # and the job cannot be run again
        await _run_job(db, monkeypatch, job, _resolve)
    assert resolved.await_count == 5
    assert await _evidence_count(db, *entities) == 0


# ═══ D. the review cycle: reproduced, NOT changed (owner / methodology decisions) ════════════════════════════════════

def _stub_cycle_collaborators(monkeypatch, sample_id, members):
    import app.tefca_registry.processing_controls as pc
    import app.tefca_registry.qhin_sampling as qs
    import app.tefca_registry.rce.reconciliation as rec

    async def _enter(_db):
        return None

    async def _reconcile(_db, _intake):
        return {"passed": True, "checks": []}

    async def _finalize(_db, intake_id, **_kw):
        return {"sample_id": str(sample_id), "already_finalized": True, "sample_size": members,
                "population_size": members, "membership_count": members}

    monkeypatch.setattr(pc, "enter_processing", _enter)
    monkeypatch.setattr(rec, "reconcile_delivery", _reconcile)
    monkeypatch.setattr(qs, "finalize_plan", _finalize)


async def _sample(db, *entities):
    sample = reg.ReviewSample(id=uuid.uuid4(), sample_name="synthetic identity sample", review_type="quarterly",
                              population_size=len(entities), sample_size=len(entities), confidence_level=0.95,
                              margin_of_error=0.05, proportion=0.5)
    sample_id = sample.id
    db.add(sample)
    await db.flush()
    for ent in entities:
        db.add(reg.SampleEntity(id=uuid.uuid4(), sample_id=sample_id, entity_id=ent, stratum="s"))
    await db.flush()
    return sample_id


async def _members(db, sample_id):
    return {row.entity_id: row for row in (await db.execute(select(reg.SampleEntity).where(
        reg.SampleEntity.sample_id == sample_id))).scalars().all()}


_CYCLE_USER = SimpleNamespace(id=None, email="cycle@synthetic-test.docuaction.invalid")


async def test_reproduced_a_review_about_another_entity_is_linked_to_the_sample_member(rolled_back_db, monkeypatch):
    """REPRODUCED 2026-11-25, NOT FIXED. The sample member is entity `linked` (what the delivery's curated record
    links). Its delivered reference resolves, globally, to entity `resolved`. The bulk writer reviews `resolved`
    (lineage `entity_mismatch`, unanchored) and `review_cycle` then links that review to the member by REFERENCE
    (`ref_to_entity`, review_cycle.py), so the sample member `linked` is recorded as reviewed by a ReviewRecord whose
    entity is `resolved`.

    Not changed here because every repair decides what the sample member's review IS: refuse the link (the member
    stays unlinked, the cycle never completes - see the next test), or refuse the review (one fewer ReviewRecord).
    Both change which sample members carry a review. When the owner decides, this test is inverted."""
    db = rolled_back_db
    rows = make_rows(1, arc="9.99.778.40")
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    rec = (await _records(db, intake_id))[1]
    linked, resolved = await _entity(db, "member"), await _entity(db, "elsewhere")
    await _curate(db, intake_id, rec, linked)
    sample_id = await _sample(db, linked)
    _stub_cycle_collaborators(monkeypatch, sample_id, 1)
    _gather(monkeypatch, {rows[0]["id"]: resolved})

    result = await rc._create_review_cycle(db, intake_id, user=_CYCLE_USER)

    assert result["linked_this_call"] == 1 and result["remaining"] == 0 and result["complete"] is True
    member = (await _members(db, sample_id))[linked]
    review = (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == member.review_id))).scalars().one()
    assert review.entity_id == resolved and review.entity_id != member.entity_id     # the defect, as it stands
    assert review.source_record_id is None                                           # and it is unanchored
    assert await _reviews_of(db, linked) == []                                       # the member itself has none
    assert await _evidence_count(db, linked, resolved) == 0     # (B) at least no evidence is stored for either now


async def test_reproduced_an_identity_refused_sample_member_is_retried_on_every_call_and_can_stall_the_cycle(
        rolled_back_db, monkeypatch):
    """REPRODUCED 2026-11-25, NOT FIXED: the one identity-refusal path that does NOT converge. The bulk writer
    refuses an anchored record whose delivered NPI is not the NPI the probes used (arc_pipeline, "submitted NPI
    differs ..."): no review is written, so the sample member is never linked, so `_unlinked_members` returns it on
    EVERY call - and the sources are asked about it again each time, because evidence is gathered before the
    refusal. With `batch` such members in front, no later member is ever reached.

    Bounded per call (`batch`), unbounded across calls: `remaining` never reaches 0 and nothing in the response
    tells the caller to stop. Not changed here: making it converge means giving a sample member a terminal
    "not reviewable" status, which changes sample completion - the same decision D-15 (`skipped_held`) put behind
    an owner-approved flag. When the owner decides, this test is inverted."""
    db = rolled_back_db
    rows = make_rows(2, arc="9.99.778.41")
    for r in rows:
        r["NPI"] = NPI
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    first, second = sorted([await _entity(db, "m1"), await _entity(db, "m2")])   # `first` is selected first
    await _curate(db, intake_id, recs[1], first)
    await _curate(db, intake_id, recs[2], second)
    sample_id = await _sample(db, first, second)
    _stub_cycle_collaborators(monkeypatch, sample_id, 2)
    gathered = []
    _gather(monkeypatch, {rows[0]["id"]: first, rows[1]["id"]: second},
            npi_by_ref={rows[0]["id"]: OTHER_NPI, rows[1]["id"]: NPI})    # the first member's probes used another NPI
    real = ap._gather_all_evidence

    async def counting(refs, timing_log=None):
        gathered.extend(refs)
        return await real(refs, timing_log=timing_log)
    monkeypatch.setattr(ap, "_gather_all_evidence", counting)

    results = [await rc._create_review_cycle(db, intake_id, user=_CYCLE_USER, batch=1) for _ in range(3)]

    for result in results:                                                # three calls, the same non-answer
        assert result["linked_this_call"] == 0 and result["remaining"] == 2 and result["complete"] is False
        assert result["verification"]["unresolved"] == [rows[0]["id"]]
    assert gathered == [rows[0]["id"]] * 3                                # the refused member, re-asked every call
    members = await _members(db, sample_id)
    assert members[first].review_id is None and members[second].review_id is None    # the valid member is starved
    assert await _reviews_of(db, first, second) == []
    assert await _evidence_count(db, first, second) == 0                  # the refusal itself persists nothing
