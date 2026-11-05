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


class _Enrollment:
    """A switchable fake CMS PPEF enrollment connector with a call log."""

    def __init__(self):
        self.calls = []

    async def __call__(self, connector_self, npi):
        from app.Tefca.connectors import SourceResult

        self.calls.append(npi)
        if not npi:
            return SourceResult.unavailable(
                "CMS_PPEF_ENROLLMENT",
                "no_npi_submitted: entity has no NPI identifier to look up", {"npi": npi})
        return SourceResult.ok("CMS_PPEF_ENROLLMENT", {
            "found": True,
            "records": [{"NPI": npi, "ENRLMT_ID": f"O{npi}", "ORG_NAME": "SYNTHETIC"}],
            "enrollment_ids": [f"O{npi}"],
            "pac_ids": [f"P{npi}"],
            "record_count": 1,
            "provenance": {"as_of_label": "Q4 2026 (synthetic)",
                           "dataset_version_anchor": "synthetic-q4-anchor",
                           "query_timestamp": datetime.utcnow().isoformat()},
        }, {"npi": npi})


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

    kw = dict(trigger_kind=rm.TRIGGER_NEW_APPROVED_SNAPSHOT, source_id=sp.CMS_PPEF,
              trigger_ref=str(snapshot_id), rationale="synthetic Q4 re-verification",
              user=user, max_entities=30000)
    kw.update(over)
    async with async_session_maker() as db:
        return await rechecks.request_recheck(db, intake_id, **kw)


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

    # Switch the enrollment connector to a healthy synthetic AFTER seeding.
    from app.Tefca import cms_ppef
    fake_enrollment = _Enrollment()

    async def lookup(self, npi):
        return await fake_enrollment(self, npi)

    monkeypatch.setattr(cms_ppef.PPEFEnrollmentConnector, "lookup_by_npi", lookup)

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
    assert len(fake_enrollment.calls) == 3     # one enrollment lookup per entity

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
