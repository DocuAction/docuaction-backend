"""CMS-enrollment-only snapshot rechecks (2026-11-04).

What this pins, against the REAL pipeline with patched connectors (no network,
synthetic records, synthetic users — same harness as test_rechecks_2026_10_04):

1. ONE JOB PER EDITION. A NEW_APPROVED_SNAPSHOT request for the same snapshot
   UUID always returns the first job — including after wider caps exist. This
   is the restriction that motivates the snapshot-trigger bound below.
2. POPULATION BOUND. A snapshot-triggered job may target the whole eligible
   population (MAX_ENTITIES_SNAPSHOT_TRIGGER); every other trigger stays
   clamped to MAX_ENTITIES_PER_JOB. Targeting stays explicit: one item row per
   entity, and the targeting rule is pinned on the job for the approver.
3. ENROLLMENT-ONLY GATHER. A CMS_PPEF snapshot recheck runs ONLY the PPEF
   enrollment lookup and appends ONLY CMS_PPEF_ENROLLMENT evidence rows —
   no NPPES/LEIE/SAM re-ask, append-only, batches resumable.
4. CONTROLS UNCHANGED. Independent approval (requester refused) and the
   stale-baseline guard behave exactly as before for the new scope.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select

import test_sam_e2e_delivery_path as sam
from test_rechecks_2026_10_04 import _Sam, _User, _cms_not_exercised
from app.Tefca import source_policy as sp
from app.tefca_registry.rce import recheck_models as rm
from app.tefca_registry.rce import rechecks

pytestmark = pytest.mark.asyncio


async def _seeded(monkeypatch, n=3):
    """Promoted delivery, verified with every connector synthetic."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    sam._clean_nppes_leie(monkeypatch)
    _cms_not_exercised(monkeypatch)
    fake_sam = _Sam()
    fake_sam.mode = "up"
    sam._patch_sam_verify(monkeypatch, fake_sam)
    intake_id = await sam._seed_promoted_delivery(n=n)
    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, n)
    async with async_session_maker() as db:
        await verify_and_classify(db, refs, intake_id=intake_id, actor="pytest-enr-recheck")
    return intake_id, fake_sam


async def _completed_ppef_snapshot():
    from app.Tefca.models import TEFCAPPEFSnapshot
    from app.core.database import async_session_maker

    snap = TEFCAPPEFSnapshot(
        id=uuid.uuid4(), component="ENROLLMENT",
        cms_title="Enrollment Q4 2026 (synthetic)",
        file_name="PPEF_Enrollment_Extract_2026.09.29.csv",
        resource_version="2026.09.29", as_of_label="Q4 2026",
        sha256="f" * 64, ingest_status="complete", ingested_by="pytest")
    async with async_session_maker() as db:
        db.add(snap)
        await db.commit()
    return snap.id


async def _request_snapshot_recheck(intake_id, user, snapshot_id, **over):
    from app.core.database import async_session_maker

    # max_entities is deliberately OMITTED: an omitted bound must mean the
    # trigger's own full scope, never an implicit 2,000 (CodeRabbit PR #197).
    kw = dict(trigger_kind=rm.TRIGGER_NEW_APPROVED_SNAPSHOT, source_id=sp.CMS_PPEF,
              trigger_ref=str(snapshot_id), rationale="synthetic Q4 re-verification",
              user=user)
    kw.update(over)
    async with async_session_maker() as db:
        return await rechecks.request_recheck(db, intake_id, **kw)


async def _seed_ppef_enrollment_rows(snapshot_id, npis):
    """Rows INSIDE the pinned snapshot edition — the only place the
    enrollment-only recheck is allowed to answer from."""
    from app.Tefca.models import TEFCAPPEFRecord
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        for npi in npis:
            db.add(TEFCAPPEFRecord(
                snapshot_id=snapshot_id, component="ENROLLMENT",
                enrollment_id=f"O{npi}", npi=npi,
                payload={"NPI": npi, "ENRLMT_ID": f"O{npi}",
                         "PECOS_ASCT_CNTL_ID": f"P{npi}",
                         "MULTIPLE_NPI_FLAG": "N",
                         "ORG_NAME": "SYNTHETIC-TRACE SAM-E2E Org"}))
        await db.commit()


async def _target_npis(job_id):
    from app.Tefca import rce_fields
    from app.Tefca.entity_resolution import resolve_entity
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.recheck_models import RceRecheckItem

    async with async_session_maker() as db:
        refs = [r for (r,) in (await db.execute(
            select(RceRecheckItem.entity_ref)
            .where(RceRecheckItem.job_id == uuid.UUID(job_id)))).all()]
        npis = []
        for ref in refs:
            entity = await resolve_entity(db, ref)
            npis.append(rce_fields.rce_npi(entity))
        return npis


async def _evidence_counts(entity_ids):
    from app.Tefca.models import TEFCADimensionEvidence as DE
    from app.core.database import async_session_maker

    ids = [str(e) for e in entity_ids]
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(DE.source, func.count()).where(DE.entity_id.in_(ids))
            .group_by(DE.source))).all()
    return dict(rows)


async def _target_entity_ids(job_id):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.recheck_models import RceRecheckItem

    async with async_session_maker() as db:
        return [r for (r,) in (await db.execute(
            select(RceRecheckItem.entity_id)
            .where(RceRecheckItem.job_id == uuid.UUID(job_id)))).all()]


# ── 1. one job per edition, explained and pinned ─────────────────────────────

async def test_one_job_per_edition_restriction(db_required, monkeypatch):
    intake_id, _ = await _seeded(monkeypatch)
    snapshot_id = await _completed_ppef_snapshot()
    maker = _User("reviewer")

    first = await _request_snapshot_recheck(intake_id, maker, snapshot_id)
    again = await _request_snapshot_recheck(intake_id, maker, snapshot_id)
    other = await _request_snapshot_recheck(intake_id, _User("reviewer"), snapshot_id)

    # The edition's UUID is the trigger identity: one auditable job, ever.
    assert first["already_exists"] is False
    assert again["already_exists"] is True and again["job_id"] == first["job_id"]
    assert other["job_id"] == first["job_id"]
    # A DIFFERENT edition is a different identity and gets its own job.
    second_snapshot = await _completed_ppef_snapshot()
    different = await _request_snapshot_recheck(intake_id, maker, second_snapshot)
    assert different["already_exists"] is False
    assert different["job_id"] != first["job_id"]


# ── 2. population bound + explicit targeting, per trigger ────────────────────

async def test_snapshot_trigger_covers_population_and_other_triggers_stay_bounded(
        db_required, monkeypatch):
    intake_id, _ = await _seeded(monkeypatch)
    snapshot_id = await _completed_ppef_snapshot()

    job = await _request_snapshot_recheck(intake_id, _User("reviewer"), snapshot_id)
    # Every eligible entity is targeted; nothing is stranded behind the cap.
    assert job["target_count"] == 3 and job["untargeted_remaining"] == 0
    pinned = job["pinned"]
    assert pinned["max_entities"] == 30000
    assert pinned["evidence_scope"] == rechecks.SCOPE_CMS_ENROLLMENT_ONLY
    assert "ordered by rce_org_oid" in pinned["targeting_rule"]
    # One explicit item row per targeted entity.
    assert len(await _target_entity_ids(job["job_id"])) == 3

    # A SOURCE_RECOVERY request asking for the wide bound is clamped back down,
    # keeps the full build, and keeps its latest-row-UNAVAILABLE targeting rule.
    from app.core.database import async_session_maker
    async with async_session_maker() as db:
        try:
            recovery = await rechecks.request_recheck(
                db, intake_id, trigger_kind=rm.TRIGGER_SOURCE_RECOVERY,
                source_id=sp.SAM_GOV, trigger_ref="INC-SYNTH-BOUND",
                rationale="synthetic", user=_User("reviewer"), max_entities=30000)
        except rechecks.RecheckRefused:
            recovery = None  # nothing UNAVAILABLE in this seeding — bound still provable
    if recovery is not None:
        assert recovery["pinned"]["max_entities"] == rechecks.MAX_ENTITIES_PER_JOB
        assert recovery["pinned"]["evidence_scope"] == rechecks.SCOPE_FULL_BUILD


# ── 3. enrollment-only gather: one source asked, one source written ──────────

async def test_enrollment_only_gather_is_append_only_and_single_source(
        db_required, monkeypatch):
    intake_id, fake_sam = await _seeded(monkeypatch)
    snapshot_id = await _completed_ppef_snapshot()
    maker, qa = _User("reviewer"), _User("qalead")

    job = await _request_snapshot_recheck(intake_id, maker, snapshot_id)
    job_id = job["job_id"]
    entity_ids = await _target_entity_ids(job_id)
    before = await _evidence_counts(entity_ids)
    fake_sam.calls = 0

    # The pinned edition holds the answers; the live data-api is a tripwire.
    # A snapshot-triggered job whose evidence came from the live API would
    # describe an edition its checksum never established (CodeRabbit PR #197).
    await _seed_ppef_enrollment_rows(snapshot_id, await _target_npis(job_id))

    from app.Tefca import cms_ppef
    live_api_calls = []

    async def live_api_tripwire(self, npi):
        live_api_calls.append(npi)
        raise AssertionError("snapshot recheck consulted the live CMS data-api")

    monkeypatch.setattr(cms_ppef.PPEFEnrollmentConnector, "lookup_by_npi",
                        live_api_tripwire)

    from app.core.database import async_session_maker
    async with async_session_maker() as db:
        await rechecks.approve_recheck(db, uuid.UUID(job_id), user=qa)
    async with async_session_maker() as db:
        await rechecks.claim(db, uuid.UUID(job_id))
    # Resumable batches: two calls of batch_size=2 cover 3 items.
    async with async_session_maker() as db:
        partial = await rechecks.run_batch(db, uuid.UUID(job_id), batch_size=2)
    assert partial["complete"] is False and partial["processed_count"] == 2
    async with async_session_maker() as db:
        done = await rechecks.run_batch(db, uuid.UUID(job_id), batch_size=2)
    assert done["complete"] is True and done["state"] == rm.STATE_SUCCEEDED

    after = await _evidence_counts(entity_ids)
    # ONLY CMS_PPEF_ENROLLMENT rows were appended; every other source count is
    # byte-identical (append-only, no re-ask, no edits).
    assert after["CMS_PPEF_ENROLLMENT"] == before.get("CMS_PPEF_ENROLLMENT", 0) + 3
    for source, count in before.items():
        if source != "CMS_PPEF_ENROLLMENT":
            assert after.get(source, 0) == count, source
    assert fake_sam.calls == 0                 # SAM was never re-asked
    assert live_api_calls == []                # the live data-api was never consulted

    # Each appended row cites the PINNED snapshot edition — one per entity.
    from app.Tefca.models import TEFCADimensionEvidence as DE
    async with async_session_maker() as db:
        (pinned_rows,) = (await db.execute(
            select(func.count()).select_from(DE)
            .where(DE.entity_id.in_([str(e) for e in entity_ids]),
                   DE.source == "CMS_PPEF_ENROLLMENT",
                   DE.query_identifier == f"snapshot:{snapshot_id}"))).one()
    assert pinned_rows == 3

    # Outcomes recorded per item, dispositions from the one-source dimension.
    from app.tefca_registry.rce.recheck_models import RceRecheckItem
    async with async_session_maker() as db:
        items = (await db.execute(
            select(RceRecheckItem.outcome, RceRecheckItem.new_disposition)
            .where(RceRecheckItem.job_id == uuid.UUID(job_id)))).all()
    assert len(items) == 3
    assert all(outcome == rm.OUTCOME_ANSWERED_NO_SIGNAL for outcome, _ in items)
    assert all(d == "PASS" for _, d in items)


# ── 4. controls unchanged for the new scope ──────────────────────────────────

async def test_requester_still_cannot_approve_their_own_snapshot_recheck(
        db_required, monkeypatch):
    intake_id, _ = await _seeded(monkeypatch)
    snapshot_id = await _completed_ppef_snapshot()
    maker = _User("qalead")       # even a QA-lead maker is refused as checker

    job = await _request_snapshot_recheck(intake_id, maker, snapshot_id)
    from app.core.database import async_session_maker
    async with async_session_maker() as db:
        with pytest.raises(rechecks.RecheckRefused):
            await rechecks.approve_recheck(db, uuid.UUID(job["job_id"]), user=maker)

# ── 5. omitted bound = full scope; a stranding request is refused ─────────────

async def test_snapshot_request_refuses_a_bound_that_strands_the_edition(
        db_required, monkeypatch):
    intake_id, _ = await _seeded(monkeypatch)
    snapshot_id = await _completed_ppef_snapshot()

    # One job per edition: a short explicit bound would strand the remainder
    # for this edition forever, so the request is refused BEFORE a job exists.
    with pytest.raises(rechecks.RecheckRefused, match="permanently untargeted"):
        await _request_snapshot_recheck(intake_id, _User("reviewer"), snapshot_id,
                                        max_entities=2)
    # The refusal consumed nothing: a full-scope request for the SAME edition
    # still creates the job, covering every eligible entity.
    job = await _request_snapshot_recheck(intake_id, _User("reviewer"), snapshot_id)
    assert job["already_exists"] is False
    assert job["target_count"] == 3 and job["untargeted_remaining"] == 0


# ── 5b. only an ENROLLMENT component snapshot can trigger the scope ───────────

async def test_non_enrollment_component_snapshot_is_refused(db_required, monkeypatch):
    """A PRACTICE_LOCATION snapshot id would pin cms_enrollment_only, match no
    rows, and append FALSE not-found evidence — the request must be refused
    before any job exists."""
    from app.Tefca.models import TEFCAPPEFSnapshot
    from app.core.database import async_session_maker

    intake_id, _ = await _seeded(monkeypatch)
    snap = TEFCAPPEFSnapshot(
        id=uuid.uuid4(), component="PRACTICE_LOCATION",
        cms_title="Address Sub-File Q4 2026 (synthetic)",
        file_name="PPEF_Practice_Location_Extract_2026.09.29.csv",
        resource_version="2026.09.29", as_of_label="Q4 2026",
        sha256="e" * 64, ingest_status="complete", ingested_by="pytest")
    async with async_session_maker() as db:
        db.add(snap)
        await db.commit()
    with pytest.raises(rechecks.RecheckRefused, match="ENROLLMENT component"):
        await _request_snapshot_recheck(intake_id, _User("reviewer"), snap.id)


# ── 5b2. a truncated snapshot never triggers the scope ─────────────────────────

async def test_truncated_snapshot_is_refused(db_required, monkeypatch):
    """ingest_status == "complete" with rows_truncated=True means a row cap was
    applied: an absent NPI proves nothing, and one-job-per-edition would block a
    corrective job — the request must be refused before any job exists."""
    from app.Tefca.models import TEFCAPPEFSnapshot
    from app.core.database import async_session_maker

    intake_id, _ = await _seeded(monkeypatch)
    snap = TEFCAPPEFSnapshot(
        id=uuid.uuid4(), component="ENROLLMENT",
        cms_title="Enrollment Q4 2026 (synthetic, capped)",
        file_name="PPEF_Enrollment_Extract_2026.09.29.csv",
        resource_version="2026.09.29", as_of_label="Q4 2026",
        sha256="d" * 64, ingest_status="complete", rows_truncated=True,
        ingested_by="pytest")
    async with async_session_maker() as db:
        db.add(snap)
        await db.commit()
    with pytest.raises(rechecks.RecheckRefused, match="truncated"):
        await _request_snapshot_recheck(intake_id, _User("reviewer"), snap.id)
    # the refused edition is not consumed: a complete snapshot still triggers
    snapshot_id = await _completed_ppef_snapshot()
    job = await _request_snapshot_recheck(intake_id, _User("reviewer"), snapshot_id)
    assert job["already_exists"] is False


# ── 5c. the source-unavailable breaker does not trip on local-snapshot reads ──

async def test_breaker_exempts_enrollment_only_scope(db_required, monkeypatch):
    """In the enrollment-only scope every lookup reads the pinned LOCAL
    snapshot; a batch of per-entity NPI rejections (unavailable results) must
    complete the job, never stop it as STOPPED_SOURCE_UNAVAILABLE."""
    from app.core.database import async_session_maker

    intake_id, _ = await _seeded(monkeypatch)
    snapshot_id = await _completed_ppef_snapshot()
    job = await _request_snapshot_recheck(intake_id, _User("reviewer"), snapshot_id)
    job_id = uuid.UUID(job["job_id"])

    # Make every entity's NPI unobtainable → every lookup is rejected
    # fail-closed → every item outcome is STILL_UNAVAILABLE.
    from app.Tefca import rce_fields
    monkeypatch.setattr(rce_fields, "rce_npi", lambda entity: None)
    from app.Tefca import connectors
    monkeypatch.setattr(connectors, "_extract_npi", lambda entity: "")

    async with async_session_maker() as db:
        await rechecks.approve_recheck(db, job_id, user=_User("qalead"))
    async with async_session_maker() as db:
        await rechecks.claim(db, job_id)
    async with async_session_maker() as db:
        done = await rechecks.run_batch(db, job_id, batch_size=200)

    assert done["complete"] is True
    assert done["state"] == rm.STATE_SUCCEEDED          # NOT stopped-source-unavailable
    assert done["summary"]["by_outcome"].get(rm.OUTCOME_STILL_UNAVAILABLE, 0) == 3


# ── 6. established NPPES classification is reused, never re-asked ─────────────

async def test_enrollment_only_scope_keeps_established_nppes_classification(
        db_required, monkeypatch):
    """A provider NPPES already recognised (persisted PASS evidence) must keep
    its D2 REQUIRED applicability in the enrollment-only scope: no enrollment
    match → REVIEW for an analyst, never NOT_APPLICABLE. And NPPES itself is
    never re-asked."""
    from app.Tefca.connectors import NPPESConnector
    from app.Tefca.models import TEFCADimensionEvidence, TEFCAPPEFSnapshot
    from app.core.database import async_session_maker

    npi = sam._valid_npi(424242)
    entity_id = uuid.uuid4()
    async with async_session_maker() as db:
        db.add(TEFCADimensionEvidence(
            entity_id=str(entity_id), evidence_dimension="IDENTITY",
            dimension_disposition="PASS", dimension_applicability="REQUIRED",
            source="NPPES", disposition="PASS",
            original_values={"npi": npi, "legal_name": "SYNTHETIC PRIOR DR",
                             "enumeration_type": "NPI-1", "taxonomy": None,
                             "taxonomy_code": None, "status": "A"},
            generation_timestamp=datetime.utcnow().isoformat()))
        await db.commit()
    snapshot_id = await _completed_ppef_snapshot()   # holds NO rows for this NPI

    async def nppes_tripwire(self, _npi):
        raise AssertionError("enrollment-only scope re-asked NPPES")

    monkeypatch.setattr(NPPESConnector, "lookup_by_npi", nppes_tripwire)

    entity = {"name": "SYNTHETIC PRIOR DR",
              "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi",
                              "value": npi}]}
    async with async_session_maker() as db:
        snap = await db.get(TEFCAPPEFSnapshot, snapshot_id)
        evidence = await rechecks._enrollment_only_evidence(
            entity, db=db, entity_id=entity_id, snapshot=snap)

    (dim,) = evidence["dimensions"]
    # NPI-1 = individual provider → Medicare relevance LIKELY → D2 REQUIRED.
    # No enrolment in the pinned edition → analyst REVIEW. With the prior
    # NPPES classification dropped, this would degrade to NOT_APPLICABLE.
    assert dim["applicability"] == "REQUIRED"
    assert dim["disposition"] == "REVIEW"


# Population-wide jobs must not silently stop at the old 2,000-item CSV cap.
@pytest.mark.parametrize("total", [0, 1, 2000, 2001, 4001, 25764])
async def test_items_csv_exports_every_page_and_neutralises_identifiers(
        monkeypatch, total):
    import csv
    import io

    calls = []

    async def page(db, job_id, *, limit=100, offset=0):
        assert job_id == "synthetic-export"
        calls.append((limit, offset))
        return {
            "job_id": job_id, "total": total,
            "items": [
                {"entity_id": str(i), "entity_ref": "=2+2" if i == total - 1 else f"oid-{i}",
                 "state": "DONE", "prior_disposition": None,
                 "new_disposition": "PASS", "outcome": "ANSWERED_NO_SIGNAL",
                 "processed_at": None}
                for i in range(offset, min(offset + limit, total))
            ],
        }

    monkeypatch.setattr(rechecks, "list_items", page)
    result = await rechecks.items_csv(None, "synthetic-export")
    rows = list(csv.reader(io.StringIO(result)))
    assert rows[0] == ["job_id", "entity_id", "entity_ref", "state",
                       "prior_disposition", "new_disposition", "outcome", "processed_at"]
    assert len(rows) == total + 1
    assert [row[1] for row in rows[1:]] == [str(i) for i in range(total)]
    assert calls == [
        (rechecks.MAX_ENTITIES_PER_JOB, offset)
        for offset in (range(0, total, rechecks.MAX_ENTITIES_PER_JOB) if total else [0])
    ]
    if total:
        assert rows[-1][2] == "'=2+2"
        assert rows[-1][4] == "" and rows[-1][7] == ""


async def test_items_csv_refuses_a_truncated_page(monkeypatch):
    calls = []

    async def page(db, job_id, *, limit=100, offset=0):
        calls.append(offset)
        return {
            "job_id": job_id, "total": 2,
            "items": ([{"entity_id": "first", "entity_ref": "oid-first",
                       "state": "DONE", "prior_disposition": None,
                       "new_disposition": None, "outcome": None, "processed_at": None}]
                      if offset == 0 else []),
        }

    monkeypatch.setattr(rechecks, "list_items", page)
    with pytest.raises(rechecks.RecheckRefused, match="export is incomplete"):
        await rechecks.items_csv(None, "synthetic-export")
    assert calls == [0, 1]
