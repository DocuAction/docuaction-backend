"""Evidence-writing paths outside coverage, and proof that an identity refusal cannot loop forever (2026-11-25).

Owner instruction: "Address the remaining bulk/review-cycle and viewer-GET evidence-writing paths, or demonstrate
that November execution cannot invoke them. ... Test that identity refusals do not create an endless retry loop."

  A. the two viewer GET evidence-dimensions routes no longer write unless asked to by a role that may write evidence;
  B. the bulk writer (`arc_pipeline.verify_and_classify`: bulk verify route and review cycle) no longer appends rows
     to the ENTITY evidence store for a reference the named delivery does not anchor to one delivered record - and
     the review it creates (bucket, rule, tier, status, linkage) is byte-for-byte what it was;
  C. termination: the manual coverage route followed by a client, the scheduler tick across many ticks and across a
     process restart, and the controlled-recheck batch;
  D. the review cycle: a sample member is linked only to a review of its own entity, and a member that cannot be
     reviewed for identity reasons is reported and walked past - it blocks nobody, the sources are not asked about
     it again, it gets no stored status, and the sample is never reported complete because of it.

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


# ═══ D. the review cycle: a member is linked only to a review of itself, and a refused member blocks nobody ═════════
#
# Both defects were reproduced on 2026-11-25 (commit 6480196 pinned them as they stood). They are fixed WITHOUT a
# stored status and without changing what `remaining` or `complete` mean: a member that cannot be reviewed for
# identity reasons is reported and walked past; it stays unreviewed, and the sample stays incomplete.

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


class _Cycle:
    """One synthetic delivery with a drawn sample, and the two edges a review cycle has: how a delivered reference
    resolves (`resolves_to`, `registry_npi`) and the source gatherer (counted in `asked`). Nothing here reaches a
    source: the gatherer is replaced and the resolver is replaced."""

    def __init__(self, db, monkeypatch):
        self.db, self.monkeypatch = db, monkeypatch
        self.asked: list = []
        self.resolves_to: dict = {}
        self.registry_npi: dict = {}

    async def build(self, arc, n, *, delivered_npi=NPI):
        self.rows = make_rows(n, arc=arc)
        for r in self.rows:
            r["NPI"] = delivered_npi
        self.intake_id, _ = await seed_intake(self.db, self.rows, with_job=False)
        await _run(self.db, self.intake_id)
        self.recs = await _records(self.db, self.intake_id)
        self.entities = sorted([await _entity(self.db, f"m{i}") for i in range(n)])   # selection order
        self.refs = [r["id"] for r in self.rows]
        for i, ent in enumerate(self.entities):
            await _curate(self.db, self.intake_id, self.recs[i + 1], ent)
            self.resolves_to[self.refs[i]] = ent
            self.registry_npi[self.refs[i]] = delivered_npi
        self.sample_id = await _sample(self.db, *self.entities)
        _stub_cycle_collaborators(self.monkeypatch, self.sample_id, n)

        def _shape(ref):
            ent = self.resolves_to.get(ref)
            if ent is None:
                return None
            npi = self.registry_npi.get(ref)
            return {"id": str(ent), "name": f"entity {ref}", "_registry_entity_id": ent, "_rce": {},
                    "identifier": ([{"system": NPI_SYSTEM, "value": npi}] if npi else []),
                    "_resolution_source": "test"}

        async def _resolve(_db, ref):
            return _shape(ref)

        async def _gather_all(refs, timing_log=None):
            self.asked.extend(refs)                                   # every reference the SOURCES were asked about
            return [{"ref": ref, "entity": _shape(ref), "evidence": (_evidence() if _shape(ref) else None)}
                    for ref in refs]

        self.monkeypatch.setattr("app.Tefca.entity_resolution.resolve_entity", _resolve)
        self.monkeypatch.setattr(ap, "_gather_all_evidence", _gather_all)
        return self

    async def call(self, **kw):
        return await rc._create_review_cycle(self.db, self.intake_id, user=_CYCLE_USER, **kw)

    async def linked(self):
        members = await _members(self.db, self.sample_id)
        return [members[e].review_id for e in self.entities]


async def test_a_review_about_another_entity_is_never_linked_to_the_sample_member(rolled_back_db, monkeypatch):
    """BEFORE (reproduced, 6480196): the member's delivered reference resolves, globally, to another entity; the
    bulk writer reviewed THAT entity and the cycle linked the review to the member by reference, so the sample member
    was recorded as reviewed - and the cycle reported `complete: true` - on a ReviewRecord about a different entity.
    AFTER: the member is not linked and not marked; it is reported with the reason, the sources are not asked about
    it, and the sample is NOT complete."""
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.40", 1)
    member = cycle.entities[0]
    elsewhere = await _entity(db, "elsewhere")
    cycle.resolves_to[cycle.refs[0]] = elsewhere

    result = await cycle.call()

    assert await cycle.linked() == [None]                                   # never linked
    assert (await _members(db, cycle.sample_id))[member].review_status == "pending"     # and given no status
    assert await _reviews_of(db, member, elsewhere) == []
    assert result["linked_this_call"] == 0 and result["remaining"] == 1 and result["complete"] is False
    assert result["not_reviewable_for_identity"] == [{
        "entity_id": str(member), "entity_ref": cycle.refs[0], "reason": rc.NOT_REVIEWABLE_OTHER_ENTITY}]
    assert result["no_further_reviews_possible"] is True
    assert cycle.asked == []                                                # decided before any source was asked
    assert await _evidence_count(db, member, elsewhere) == 0


async def test_a_review_the_writer_made_about_another_entity_is_still_not_linked(rolled_back_db, monkeypatch):
    """Second line of defence: the check before gathering saw the member's own entity, but the writer resolved the
    reference to another one (the registry changed in between). The review it wrote exists; it is not this member's
    and is never linked to it."""
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.42", 1)
    member = cycle.entities[0]
    elsewhere = await _entity(db, "elsewhere2")
    real_gather = ap._gather_all_evidence

    async def _changed_underneath(refs, timing_log=None):
        cycle.resolves_to[cycle.refs[0]] = elsewhere                        # ... after the pre-check ran
        return await real_gather(refs, timing_log=timing_log)
    monkeypatch.setattr(ap, "_gather_all_evidence", _changed_underneath)

    result = await cycle.call()

    assert await cycle.linked() == [None] and result["linked_this_call"] == 0 and result["complete"] is False
    listed = result["not_reviewable_for_identity"]
    assert len(listed) == 1 and listed[0]["reason"] == rc.NOT_REVIEWABLE_OTHER_ENTITY
    review = (await _reviews_of(db, elsewhere))[0]
    assert listed[0]["review_not_linked"] == review.review_id and review.sample_id is None


async def test_identity_refused_members_in_front_do_not_block_the_members_behind_them(rolled_back_db, monkeypatch):
    """BEFORE (reproduced, 6480196): a member whose anchored record the NPI gate refuses was never linked, was
    selected again on every call, had the sources asked about it every time, and with `batch` such members in front
    no later member was ever reviewed. AFTER: three refused members in front, batch=1 - the first call already
    reviews a member behind them, the next call reviews the other, and the sources are never asked about a refused
    member, however often the cycle is called."""
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.41", 5)
    for ref in cycle.refs[:3]:
        cycle.registry_npi[ref] = OTHER_NPI                                 # the probes would use an NPI not delivered

    first = await cycle.call(batch=1)
    assert first["linked_this_call"] == 1 and first["remaining"] == 4 and first["complete"] is False
    assert [n["entity_ref"] for n in first["not_reviewable_for_identity"]] == cycle.refs[:3]
    assert {n["reason"] for n in first["not_reviewable_for_identity"]} == {ap.REFUSAL_SUBMITTED_NPI_DIFFERS}
    assert first["members_not_examined"] == 1 and first["no_further_reviews_possible"] is False

    second = await cycle.call(batch=1)
    assert second["linked_this_call"] == 1 and second["remaining"] == 3
    assert second["walk_exhausted"] is True and second["no_further_reviews_possible"] is False

    later = [await cycle.call(batch=1) for _ in range(4)]                   # a caller that keeps calling
    for result in later:
        assert result["linked_this_call"] == 0 and result["remaining"] == 3
        assert result["complete"] is False                                  # NEVER complete: three are unreviewed
        assert result["not_reviewable_for_identity_count"] == 3
        assert result["reviewable_remaining_at_most"] == 0 and result["no_further_reviews_possible"] is True

    linked = await cycle.linked()
    assert linked[:3] == [None, None, None] and all(linked[3:])
    assert cycle.asked == cycle.refs[3:]                 # six calls: each valid member asked once, a refused one never
    members = await _members(db, cycle.sample_id)
    assert {members[e].review_status for e in cycle.entities[:3]} == {"pending"}        # no stored status
    assert await _reviews_of(db, *cycle.entities[:3]) == []


async def test_every_identity_reason_is_reported_and_none_blocks(rolled_back_db, monkeypatch):
    """One member per reason, in front of a valid one: no delivered reference, a reference that does not resolve,
    one that resolves elsewhere, an NPI the record did not deliver. All four are reported with their own reason and
    the valid member behind them is reviewed in the same call."""
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.43", 5)
    elsewhere = await _entity(db, "elsewhere3")
    curated = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == cycle.intake_id,
        m.RceCuratedRecord.canonical_entity_id == cycle.entities[0]))).scalars().one()
    curated.rce_org_oid = None                                              # member 0: no delivered reference
    await db.flush()
    cycle.resolves_to[cycle.refs[1]] = None                                 # member 1: does not resolve
    cycle.resolves_to[cycle.refs[2]] = elsewhere                            # member 2: resolves elsewhere
    cycle.registry_npi[cycle.refs[3]] = OTHER_NPI                           # member 3: NPI not delivered

    result = await cycle.call(batch=1)

    assert result["linked_this_call"] == 1 and (await cycle.linked())[4]
    reasons = {n["entity_id"]: n["reason"] for n in result["not_reviewable_for_identity"]}
    assert reasons == {str(cycle.entities[0]): rc.NOT_REVIEWABLE_NO_REFERENCE,
                       str(cycle.entities[1]): rc.NOT_REVIEWABLE_UNRESOLVED,
                       str(cycle.entities[2]): rc.NOT_REVIEWABLE_OTHER_ENTITY,
                       str(cycle.entities[3]): ap.REFUSAL_SUBMITTED_NPI_DIFFERS}
    assert result["remaining"] == 4 and result["complete"] is False
    # Only the unresolvable reference reached the writer (it cannot be told apart beforehand), and resolving it is
    # where the writer stops: no source is asked about a reference with no entity.
    assert cycle.asked == [cycle.refs[1], cycle.refs[4]]


async def test_the_walk_is_bounded_and_resumable(rolled_back_db, monkeypatch):
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.44", 4)
    for ref in cycle.refs[:3]:
        cycle.registry_npi[ref] = OTHER_NPI
    monkeypatch.setattr(rc, "MAX_NOT_REVIEWABLE_PER_CALL", 2)

    capped = await cycle.call(batch=1)
    assert capped["identity_check_limit_reached"] is True and capped["not_reviewable_for_identity_count"] == 2
    assert capped["linked_this_call"] == 0 and capped["no_further_reviews_possible"] is False
    assert capped["members_not_examined"] == 2 and capped["next_after_member_id"]

    resumed = await cycle.call(batch=1, after_member_id=capped["next_after_member_id"])
    assert resumed["linked_this_call"] == 1 and resumed["not_reviewable_for_identity_count"] == 1
    assert resumed["walk_exhausted"] is True and resumed["remaining"] == 3 and resumed["complete"] is False
    assert cycle.asked == [cycle.refs[3]]


async def test_a_correctly_identified_members_review_is_what_it_was_before(rolled_back_db, monkeypatch):
    """An anchored, correctly identified member: the review the cycle links is field-for-field the review the bulk
    writer produces for the same reference on its own (the writer's classification path is untouched; the NPI gates
    were only given a name), and the cycle's bookkeeping on it is exactly the old one."""
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.45", 2)
    ref, member = cycle.refs[0], cycle.entities[0]

    direct = (await ap.verify_and_classify(db, [ref], intake_id=cycle.intake_id))["outcomes"][0]
    result = await cycle.call(batch=2)

    assert result["linked_this_call"] == 2 and result["remaining"] == 0 and result["complete"] is True
    assert result["not_reviewable_for_identity"] == [] and result["no_further_reviews_possible"] is False
    assert result["verification"]["requested"] == 2 and result["verification"]["verified"] == 2
    assert result["verification"]["unresolved"] == [] and result["verification"]["execution_status"] == "COMPLETED"
    members = await _members(db, cycle.sample_id)
    assert members[member].review_status == "in_review"
    linked = (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == members[member].review_id))).scalars().one()
    baseline = (await db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == direct["review_id"]))).scalars().one()
    for field in ("entity_id", "source_record_id", "classification_bucket", "classification_rule",
                  "classification_rule_version", "classification_rationale"):
        assert getattr(linked, field) == getattr(baseline, field), field
    assert linked.entity_id == member and linked.source_record_id == cycle.recs[1].id      # anchored to its record
    ours = {k: v for k, v in linked.verification_results.items()
            if k not in ("source_intake_id", "queue_source", "sample_id")}
    assert ours == baseline.verification_results                            # same snapshot, same classifier input
    assert linked.sample_id == cycle.sample_id and baseline.sample_id is None
    assert linked.verification_results["queue_source"] == rc.QUEUE_SOURCE
    assert cycle.asked == [ref, ref, cycle.refs[1]]                         # the direct run, then one ask per member


def test_the_writers_npi_gates_are_the_same_rule_under_a_name():
    """`anchored_npi_refusal` is the two gates `verify_and_classify` always applied, with the same refusal texts."""
    record = SimpleNamespace(parsed={"NPI": NPI}, npi=NPI)
    fhir = lambda npi: {"identifier": [{"system": NPI_SYSTEM, "value": npi}], "_rce": {}}   # noqa: E731
    assert ap.anchored_npi_refusal(fhir(NPI), None) is None                 # no anchor: never refused here
    assert ap.anchored_npi_refusal(fhir(NPI), record) is None
    assert ap.anchored_npi_refusal(fhir(OTHER_NPI), record) == ap.REFUSAL_SUBMITTED_NPI_DIFFERS
    assert ap.anchored_npi_refusal({"identifier": [], "_rce": {}}, record) == ap.REFUSAL_SUBMITTED_NPI_DIFFERS
    assert ap.anchored_npi_refusal({**fhir(NPI), "_rce": {"NPI": OTHER_NPI}}, record) == ap.REFUSAL_ENROLLMENT_PROBE_NPI
    blank = SimpleNamespace(parsed={"NPI": ""}, npi=None)
    assert ap.anchored_npi_refusal({"identifier": [], "_rce": {}}, blank) is None
    assert "submitted NPI differs from the identifier used by source probes" in ap.REFUSAL_SUBMITTED_NPI_DIFFERS


# ═══ E. review findings on #245 (R1, R3, R4, R5) ═════════════════════════════════════════════════════════════════════

async def test_the_writer_refuses_on_identity_before_it_asks_any_source(rolled_back_db, monkeypatch):
    """R1. The NPI gates run BEFORE evidence is gathered: a refused record costs no source call. What is refused and
    how it is reported is exactly what it was (same `unresolved`, same `resolution_failures` text, same lineage
    count, no review, nothing stored); the accepted record beside it is asked about once and reviewed."""
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.50", 3)
    cycle.registry_npi[cycle.refs[0]] = OTHER_NPI                           # gate 1: registered NPI != delivered NPI
    real_resolve = cycle.monkeypatch                                        # (kept for readability; see below)
    shapes = {}

    async def _resolve(_db, ref):
        ent = cycle.resolves_to[ref]
        shape = {"id": str(ent), "name": f"entity {ref}", "_registry_entity_id": ent, "_resolution_source": "test",
                 "identifier": [{"system": NPI_SYSTEM, "value": cycle.registry_npi[ref]}], "_rce": {}}
        if ref == cycle.refs[1]:
            shape["_rce"] = {"NPI": OTHER_NPI}                              # gate 2: the CMS probes' NPI differs
        shapes[ref] = shape
        return shape
    monkeypatch.setattr("app.Tefca.entity_resolution.resolve_entity", _resolve)
    del real_resolve

    res = await ap.verify_and_classify(db, list(cycle.refs), intake_id=cycle.intake_id)

    assert cycle.asked == [cycle.refs[2]]                                   # the sources heard about ONE record
    assert res["refused_before_gather"] == cycle.refs[:2]
    assert res["unresolved"] == cycle.refs[:2] and res["verified"] == 1
    assert res["resolution_failures"] == [
        {"entity_ref": cycle.refs[0], "reason": ap.REFUSAL_SUBMITTED_NPI_DIFFERS},
        {"entity_ref": cycle.refs[1], "reason": ap.REFUSAL_ENROLLMENT_PROBE_NPI}]
    assert res["lineage"][ap.LINEAGE_ANCHORED] == 3                         # counted as before
    assert res["execution_status"] == "PARTIAL"
    assert await _reviews_of(db, *cycle.entities[:2]) == []
    assert await _evidence_count(db, *cycle.entities[:2]) == 0
    assert [o["entity_ref"] for o in res["outcomes"]] == [cycle.refs[2]]


async def test_gating_before_gathering_changes_nothing_for_an_accepted_record(rolled_back_db, monkeypatch):
    """R1. The same anchored, correctly identified record run (a) with the reference resolvable before the gather -
    the new order - and (b) with the pre-resolution finding nothing, which is the old order (gates after the
    gather): bucket, rule, version, tier, lineage, anchor, stored evidence and the review's frozen snapshot are
    identical, and the evidence was built for the very entity object the gates were applied to."""
    db = rolled_back_db
    cycle = await _Cycle(db, monkeypatch).build("9.99.778.51", 1)
    ref, ent = cycle.refs[0], cycle.entities[0]

    new_order = (await ap.verify_and_classify(db, [ref], intake_id=cycle.intake_id))["outcomes"][0]

    async def _nothing_beforehand(_refs):
        return {r: None for r in _refs}
    monkeypatch.setattr(ap, "_resolve_all", _nothing_beforehand)
    old_order = (await ap.verify_and_classify(db, [ref], intake_id=cycle.intake_id))["outcomes"][0]

    for key in ("bucket", "rule_code", "rule_version", "rule_matched", "tier", "assigned_role", "lineage",
                "source_record_id", "evidence_stored", "entity_id", "dimensions", "applicability",
                "entity_marked_verified"):
        assert new_order[key] == old_order[key], key
    reviews = {r.review_id: r for r in await _reviews_of(db, ent)}
    a, b = reviews[new_order["review_id"]], reviews[old_order["review_id"]]
    assert a.verification_results == b.verification_results and a.classification_rationale == b.classification_rationale
    assert await _evidence_count(db, ent) == 2 and cycle.asked == [ref, ref]


async def test_the_real_gatherer_builds_evidence_for_the_entity_the_gates_saw_and_resolves_once(monkeypatch):
    """R1. The unpatched gather function uses the entity resolved before the gates (no second resolution)."""
    entity = {"id": "x", "_registry_entity_id": uuid.uuid4(), "identifier": [], "_rce": {}}
    resolve = AsyncMock(return_value={"id": "another object"})
    build = AsyncMock(return_value=_evidence())
    monkeypatch.setattr("app.Tefca.entity_resolution.resolve_entity", resolve)
    token = ap._PRE_RESOLVED.set({"ref-1": entity, "ref-2": None})
    try:
        with patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
            got = await ap._resolve_and_gather_evidence("ref-1")
            none = await ap._resolve_and_gather_evidence("ref-2")
    finally:
        ap._PRE_RESOLVED.reset(token)
    assert got["entity"] is entity and build.await_args.args[0] is entity and build.await_count == 1
    assert none == {"ref": "ref-2", "entity": None, "evidence": None}
    resolve.assert_not_awaited()


async def test_the_cycle_response_and_audit_carry_lineage_and_what_was_not_stored(rolled_back_db, monkeypatch):
    """R3. On the official path the bulk result's `lineage` and `evidence_not_stored_unanchored` were dropped (the
    cycle copied seven keys and audited none of this). A duplicate delivered id in a sample: the member is reviewed
    (unanchored, as the methodology has it), its evidence is not stored - and the cycle now SAYS so, in the response
    and in the audit row."""
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.778.52")
    rows[2]["id"] = rows[0]["id"]                                           # one delivered id on two records
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    dup, ok = sorted([await _entity(db, "dup"), await _entity(db, "ok")])
    await _curate(db, intake_id, recs[1], dup)
    await _curate(db, intake_id, recs[3], dup)
    await _curate(db, intake_id, recs[2], ok)
    sample_id = await _sample(db, dup, ok)
    _stub_cycle_collaborators(monkeypatch, sample_id, 2)
    _gather(monkeypatch, {rows[0]["id"]: dup, rows[1]["id"]: ok})

    result = await rc._create_review_cycle(db, intake_id, user=_CYCLE_USER)

    assert result["linked_this_call"] == 2 and result["complete"] is True   # both reviewed, as before
    verification = result["verification"]
    assert verification["lineage"][ap.LINEAGE_AMBIGUOUS] == 1 and verification["lineage"][ap.LINEAGE_ANCHORED] == 1
    assert verification["evidence_not_stored_unanchored_count"] == 1
    assert verification["evidence_not_stored_unanchored"] == [
        {"entity_ref": rows[0]["id"], "lineage": ap.LINEAGE_AMBIGUOUS}]
    audit = (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_members_verified")
        .order_by(reg.TefcaRegAuditLog.created_at.desc()))).scalars().all()
    meta = next(a.metadata_ for a in audit if (a.metadata_ or {}).get("sample_id") == str(sample_id))
    assert meta["lineage"][ap.LINEAGE_AMBIGUOUS] == 1 and meta["evidence_not_stored_unanchored"] == 1
    assert meta["evidence_not_stored_unanchored_refs"] == verification["evidence_not_stored_unanchored"]
    assert meta["not_reviewable_for_identity"] == 0


async def test_an_unanchored_reference_gets_the_bucket_of_its_anchored_twin_under_an_active_mandatory_policy(
        rolled_back_db, monkeypatch):
    """R4. Not storing the evidence of an unanchored reference must not move its classification. With an ACTIVE
    mandatory sam_gov policy (the configuration in which a missing source answer changes the bucket), an unanchored
    reference and an anchored twin with identical mocked evidence get the same bucket, rule and tier - and the
    policy really was the deciding input (without it the same evidence classifies differently)."""
    from app.tefca_registry.rce import source_policy as sp
    from app.tefca_registry.bucket_classifier import BucketClassifier
    from test_mandatory_source_parity_2026_11_11 import _cls, _policy
    db = rolled_back_db
    # The rules engine answers B1 for this evidence (stubbed: the rules are not under test), so the mandatory-source
    # policy is the ONLY thing that can move the bucket - which is the input the stored evidence could have affected.
    monkeypatch.setattr(BucketClassifier, "classify", lambda self, results, rules=None: _cls("B1"))
    intake_u, refs_u, by_ref_u, _lineage, _ents = await _bulk_case(db, "ambiguous")
    rows = make_rows(1, arc="9.99.778.53")
    intake_a, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_a)
    anchored = await _entity(db, "twin")
    await _curate(db, intake_a, (await _records(db, intake_a))[1], anchored)
    _gather(monkeypatch, {**by_ref_u, rows[0]["id"]: anchored})

    async def _run_pair():
        u = (await ap.verify_and_classify(db, refs_u, intake_id=intake_u))["outcomes"][0]
        a = (await ap.verify_and_classify(db, [rows[0]["id"]], intake_id=intake_a))["outcomes"][0]
        return u, a

    without_u, without_a = await _run_pair()                                # no policy row active (the shipped state)

    async def _active(_db):
        return [_policy()]                                                  # mandatory sam_gov, active
    monkeypatch.setattr(sp, "load_active_policies", _active)
    with_u, with_a = await _run_pair()

    assert (with_u["lineage"], with_a["lineage"]) == (ap.LINEAGE_AMBIGUOUS, ap.LINEAGE_ANCHORED)
    assert (with_u["evidence_stored"], with_a["evidence_stored"]) == (False, True)
    for key in ("bucket", "rule_code", "rule_version", "tier"):
        assert with_u[key] == with_a[key], key                              # the twin's classification, exactly
        assert without_u[key] == without_a[key], key
    assert (with_a["bucket"], with_a["rule_code"]) != (without_a["bucket"], without_a["rule_code"]), (
        "the active policy did not decide the bucket, so this comparison proves nothing")


async def _unresolvable_then_valid(db, arc, *, unresolvable, valid):
    intake_id, entities, by_ref = await _refused_then_valid(db, arc, refused=0, valid=unresolvable + valid)
    refs = {str(v["_registry_entity_id"]): k for k, v in by_ref.items()}
    for ent in entities[:unresolvable]:
        by_ref[refs[str(ent)]] = None                                       # the reference resolves to nothing
    return intake_id, entities, by_ref


async def test_unresolvable_references_are_stepped_over_and_the_batch_says_when_to_stop(rolled_back_db):
    """R5. A reference that does not resolve was counted as a lookup: it used the batch budget, so
    `no_further_lookups_possible` was never true and the scheduler never held the delivery. No source is asked about
    it, so it is now stepped over like a refusal - the valid record behind it is covered, and a delivery with only
    unresolvable references left signals the stop."""
    db = rolled_back_db
    intake_id, entities, by_ref = await _unresolvable_then_valid(db, "9.99.778.54", unresolvable=2, valid=1)

    async def _resolve(_db, ref):
        return by_ref[ref]
    build = AsyncMock(return_value=_evidence())
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=_resolve)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence", new=build):
        first = await av.run_coverage_batch(db, intake_id, batch_size=1)
        again = [await av.run_coverage_batch(db, intake_id, batch_size=1) for _ in range(3)]

    assert first["processed_this_call"] == 1 and first["not_resolved"] == 2 and first["identity_refused"] == 0
    assert first["by_outcome"] == {av.OUTCOME_NOT_ELIGIBLE: 2, av.OUTCOME_VERIFIED: 1}     # outcome word unchanged
    assert first["remaining"] == 2 and first["no_further_lookups_possible"] is False
    for result in again:
        assert result["processed_this_call"] == 0 and result["not_resolved"] == 2
        assert result["remaining"] == 2 and result["complete"] is False
        assert result["candidates_exhausted"] is True and result["no_further_lookups_possible"] is True
    assert build.await_count == 1
    assert await _evidence_count(db, *entities[:2]) == 0


async def test_a_failed_source_call_is_still_a_lookup_and_is_not_a_stop(rolled_back_db):
    """R5, the other half, stated rather than hidden: a source that was ASKED and failed (RETRY_PENDING) is worth
    another call, so it is a lookup, it is not a stop signal, and the scheduler does not hold the delivery for it.
    That entity is retried on every call until the source answers - by design, and bounded by `batch_size`."""
    db = rolled_back_db
    intake_id, _entities, by_ref = await _refused_then_valid(db, "9.99.778.55", refused=0, valid=1)

    async def _resolve(_db, ref):
        return by_ref[ref]
    with patch("app.Tefca.entity_resolution.resolve_entity", new=AsyncMock(side_effect=_resolve)), \
            patch("app.Tefca.evidence_service.EvidenceService.build_evidence",
                  new=AsyncMock(side_effect=TimeoutError("synthetic outage"))):
        result = await av.run_coverage_batch(db, intake_id, batch_size=5)
    assert result["by_outcome"] == {av.OUTCOME_RETRY_PENDING: 1} and result["processed_this_call"] == 1
    assert result["remaining"] == 1 and result["no_further_lookups_possible"] is False


async def test_the_scheduler_holds_a_delivery_whose_leftovers_cannot_be_asked_about(harness):
    """R5. Leftovers that are unresolvable references, or records with no delivered reference at all (never a
    candidate, so the batch examines nothing), no longer keep the delivery selected on every tick."""
    engine, clock = harness
    for leftovers in ({"not_resolved": 3}, {}):                             # {}: nothing to examine, 3 remain
        av.reset_coverage_scheduler_state()
        intake = uuid.uuid4()
        batch = AsyncMock(return_value=_tick_result(remaining=3, **leftovers))
        lookup = AsyncMock(side_effect=lambda db, **kw: None if kw.get("exclude_intake_ids") else intake)
        with patch.object(av, "_next_delivery_needing_coverage", new=lookup), \
                patch.object(av, "run_coverage_batch", new=batch):
            for _ in range(100):
                await av._coverage_tick(now=clock)
                clock.advance(av.TICK_INTERVAL_SECONDS)
        assert batch.await_count == 1, leftovers                            # one walk in 100 ticks
        assert av.coverage_scheduler_status()["deliveries_on_identity_hold"] == 1


def test_every_caller_of_the_extract_attempt_names_its_delivery():
    """R6. `record_sam_extract_attempt` requires `intake_id`; its one application caller passes it."""
    import pathlib
    import re
    root = pathlib.Path(av.__file__).resolve().parents[2]
    calls = []
    for path in root.rglob("*.py"):
        text_ = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"(?<!def )record_sam_extract_attempt\(", text_):
            calls.append((path.relative_to(root).as_posix(), text_[match.start():match.start() + 260]))
    assert [c[0] for c in calls] == ["tefca_registry/rce/automated_verification.py"], calls
    assert "intake_id=intake_id" in calls[0][1]
    assert inspect.signature(av.record_sam_extract_attempt).parameters["intake_id"].default is inspect.Parameter.empty
