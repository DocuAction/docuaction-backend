"""Controlled rechecks -- idempotent, bounded, maker/checker, crash-safe.

Real pipeline (ingest -> quality -> curate -> promote -> verify_and_classify)
with patched connectors: no network, synthetic records, synthetic users.
Every approval below is a SIMULATED identity; no real approval is recorded
and no policy is marked approved.

The scenario: SAM.gov is down when a delivery is verified, so every entity's
SAM evidence is UNAVAILABLE. SAM then recovers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

import test_sam_e2e_delivery_path as sam
from app.Tefca import source_policy as sp
from app.tefca_registry.rce import recheck_models as rm
from app.tefca_registry.rce import rechecks

pytestmark = pytest.mark.asyncio


class _User:
    def __init__(self, role):
        self.id = uuid.uuid4()
        self.role = role
        self.email = f"recheck-{role}-{uuid.uuid4().hex[:6]}@synthetic-test.docuaction.invalid"


class _Sam:
    """A switchable fake SAM.gov with a call counter."""

    def __init__(self):
        self.mode, self.calls = "down", 0

    def __call__(self, *, uei, legal_name):
        from app.Tefca.connectors import SourceResult

        self.calls += 1
        if self.mode == "down":
            return SourceResult.unavailable("SAM_GOV", "synthetic outage (HTTP 503)",
                                            {"legal_name": legal_name}, "v3+v4")
        excluded = self.mode == "up" and str(legal_name).strip().endswith("0000")
        return SourceResult.ok("SAM_GOV", {
            "found": True, "matched_by": "uei", "excluded": excluded,
            "excluded_known": True, "identity_ambiguous": False,
            "registration_current": True}, {"uei": uei})


def _cms_not_exercised(monkeypatch):
    """The two CMS connectors were unpatched here, so every recheck test
    queried the live CMS data API (2026-10-04: 50 tests took 380 s offline,
    and their result depended on the Internet). Deterministic and identical to
    what an unreachable API yields: unavailable, never a finding."""
    from app.Tefca import cms_ppef
    from app.Tefca.connectors import SourceResult

    async def unavailable(self, npi):
        return SourceResult.unavailable(
            self.SOURCE_NAME, "synthetic: CMS data API not exercised by this test", {"npi": npi})

    monkeypatch.setattr(cms_ppef.PPEFEnrollmentConnector, "lookup_by_npi", unavailable)
    monkeypatch.setattr(cms_ppef.CMSRevocationConnector, "lookup_by_npi", unavailable)


async def _delivery_verified_during_an_outage(monkeypatch, n=3):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    monkeypatch.setenv("ENTITY_RESOLVER_SOURCE", "db")
    sam._clean_nppes_leie(monkeypatch)
    _cms_not_exercised(monkeypatch)
    fake = _Sam()
    sam._patch_sam_verify(monkeypatch, fake)
    intake_id = await sam._seed_promoted_delivery(n=n)
    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, n)
    async with async_session_maker() as db:
        result = await verify_and_classify(db, refs, intake_id=intake_id, actor="pytest-recheck")
    entity_ids = [uuid.UUID(o["entity_id"]) for o in result["outcomes"]]
    fake.calls = 0
    return intake_id, entity_ids, fake


async def _request(intake_id, analyst, **over):
    from app.core.database import async_session_maker

    kw = dict(trigger_kind=rm.TRIGGER_SOURCE_RECOVERY, source_id=sp.SAM_GOV,
              trigger_ref="INC-SYNTHETIC-001 SAM recovered", rationale="synthetic recovery",
              user=analyst)
    kw.update(over)
    async with async_session_maker() as db:
        return await rechecks.request_recheck(db, intake_id, **kw)


async def _approve(job_id, qa):
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        return await rechecks.approve_recheck(db, uuid.UUID(job_id), user=qa)


async def _claim_and_run(job_id, **kw):
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        await rechecks.claim(db, uuid.UUID(job_id))
    async with async_session_maker() as db:
        return await rechecks.run_batch(db, uuid.UUID(job_id), **kw)


async def _run(job_id, **kw):
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        return await rechecks.run_batch(db, uuid.UUID(job_id), **kw)


async def _snapshot(entity_ids):
    """Counts that must behave: evidence rows, review records, ledger issues,
    entity statuses, and the ids+dispositions of the SAM evidence rows."""
    from app.Tefca.models import TEFCADimensionEvidence as DE
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m

    ids = [str(e) for e in entity_ids]
    async with async_session_maker() as db:
        sam_rows = (await db.execute(select(DE.id, DE.disposition).where(
            DE.entity_id.in_(ids), DE.source == "SAM_GOV").order_by(DE.id))).all()
        return {
            "evidence": int((await db.execute(select(func.count()).select_from(DE).where(
                DE.entity_id.in_(ids)))).scalar()),
            "reviews": int((await db.execute(select(func.count()).select_from(
                reg.ReviewRecord).where(reg.ReviewRecord.entity_id.in_(entity_ids)))).scalar()),
            "issues": int((await db.execute(select(func.count()).select_from(m.RceIssue))).scalar()),
            "status": {str(i): s for i, s in (await db.execute(select(
                reg.TefcaRegEntity.id, reg.TefcaRegEntity.verification_status).where(
                reg.TefcaRegEntity.id.in_(entity_ids)))).all()},
            "sam_rows": {str(i): d for i, d in sam_rows},
        }


# ── request: idempotent, bounded, nothing looked up ──────────────────────────

async def test_a_repeated_trigger_returns_the_same_job_and_looks_nothing_up(db_required, monkeypatch):
    intake_id, entity_ids, fake = await _delivery_verified_during_an_outage(monkeypatch)
    analyst = _User("reviewer")

    first = await _request(intake_id, analyst)
    again = await _request(intake_id, analyst)
    other_requester = await _request(intake_id, _User("reviewer"))

    assert first["already_exists"] is False and first["state"] == rm.STATE_PENDING_APPROVAL
    assert first["target_count"] == 3 and first["untargeted_remaining"] == 0
    assert again["already_exists"] is True and again["job_id"] == first["job_id"]
    assert other_requester["job_id"] == first["job_id"]          # the trigger is the identity
    assert fake.calls == 0                                       # a request spends no quota
    assert first["is_compliance_approval"] is False
    pinned = first["pinned"]
    assert pinned["official_policy_status"] == sp.POLICY_UNAPPROVED
    assert pinned["rule_set_version"] and pinned["field_map_version"]
    assert pinned["evidence_sources"] == ["SAM_GOV"]


async def test_the_entity_bound_is_enforced_and_the_remainder_is_counted(db_required, monkeypatch):
    intake_id, _, _ = await _delivery_verified_during_an_outage(monkeypatch)
    job = await _request(intake_id, _User("reviewer"), max_entities=2)
    assert job["target_count"] == 2 and job["untargeted_remaining"] == 1


@pytest.mark.parametrize("over,match", [
    (dict(trigger_kind=rm.TRIGGER_APPROVED_MAPPING_CHANGE, trigger_ref="V2"),
     "no APPROVED mapping/policy"),
    (dict(trigger_kind=rm.TRIGGER_NEW_APPROVED_SNAPSHOT, trigger_ref=str(uuid.uuid4())),
     "not answered from an ingested reference snapshot"),
    (dict(source_id=sp.IQVIA), "is not re-evaluated on this path"),
    (dict(trigger_ref="  "), "trigger_ref is required"),
    (dict(rationale=""), "rationale is required"),
    (dict(trigger_kind="WHENEVER"), "trigger_kind must be one of"),
])
async def test_unauthorised_or_unsupported_triggers_are_refused(db_required, monkeypatch, over, match):
    intake_id, _, fake = await _delivery_verified_during_an_outage(monkeypatch, n=1)
    with pytest.raises(rechecks.RecheckRefused, match=match):
        await _request(intake_id, _User("reviewer"), **over)
    assert fake.calls == 0


async def test_no_job_is_created_when_nothing_needs_a_recheck(db_required, monkeypatch):
    intake_id, _, _ = await _delivery_verified_during_an_outage(monkeypatch, n=1)
    with pytest.raises(rechecks.RecheckRefused, match="no entity of this delivery needs"):
        await _request(intake_id, _User("reviewer"), source_id=sp.OIG_LEIE)   # LEIE answered


# ── maker/checker ────────────────────────────────────────────────────────────

async def test_the_requester_cannot_approve_and_an_unapproved_job_cannot_run(db_required, monkeypatch):
    from app.core.database import async_session_maker

    intake_id, _, fake = await _delivery_verified_during_an_outage(monkeypatch, n=1)
    analyst, qa = _User("reviewer"), _User("qalead")
    job = await _request(intake_id, analyst)

    with pytest.raises(rechecks.RecheckRefused, match="segregation of duties"):
        await _approve(job["job_id"], analyst)
    async with async_session_maker() as db:
        assert await rechecks.claim(db, uuid.UUID(job["job_id"])) is None      # not QUEUED
    with pytest.raises(rechecks.RecheckRefused, match="only RUNNING jobs run"):
        await _run(job["job_id"])
    assert fake.calls == 0

    approved = await _approve(job["job_id"], qa)
    assert approved["state"] == rm.STATE_QUEUED and approved["approved_by"] == qa.email
    assert approved["requested_by"] == analyst.email != approved["approved_by"]


# ── the run: re-evaluation, not approval; history preserved ──────────────────

async def test_recovery_recheck_appends_evidence_and_approves_nothing(db_required, monkeypatch):
    from app.core.database import async_session_maker

    intake_id, entity_ids, fake = await _delivery_verified_during_an_outage(monkeypatch)
    before = await _snapshot(entity_ids)
    assert set(before["sam_rows"].values()) == {"UNAVAILABLE"}

    job = await _request(intake_id, _User("reviewer"))
    await _approve(job["job_id"], _User("qalead"))
    fake.mode = "up"                                             # SAM recovers

    first = await _claim_and_run(job["job_id"], batch_size=2)
    assert first["processed_this_call"] == 2 and first["state"] == rm.STATE_RUNNING
    second = await _run(job["job_id"], batch_size=2)
    assert second["processed_this_call"] == 1 and second["state"] == rm.STATE_SUCCEEDED
    assert second["summary"]["by_outcome"] == {rm.OUTCOME_ANSWERED_NO_SIGNAL: 2,
                                               rm.OUTCOME_RISK_SIGNAL: 1}

    after = await _snapshot(entity_ids)
    # History preserved: every prior SAM row is still there, still UNAVAILABLE.
    for row_id, disposition in before["sam_rows"].items():
        assert after["sam_rows"][row_id] == disposition == "UNAVAILABLE"
    assert len(after["sam_rows"]) == len(before["sam_rows"]) + 3     # one new row per entity
    assert after["evidence"] > before["evidence"]
    # Re-evaluation, not approval: no review record written, and no entity
    # moved to "verified" -- a status either stays as it was or moves toward
    # scrutiny. (Compared per entity: the initial bucket depends on sources
    # this fixture does not patch.)
    assert after["reviews"] == before["reviews"]
    for eid, status in after["status"].items():
        assert status == before["status"][eid] or status == "in_review", (eid, status)
    # The seeded exclusion surfaced on the recovered source and is in review.
    async with async_session_maker() as db:
        items = (await rechecks.list_items(db, uuid.UUID(job["job_id"])))["items"]
    risk = [i for i in items if i["outcome"] == rm.OUTCOME_RISK_SIGNAL]
    assert len(risk) == 1 and risk[0]["new_disposition"] == "REVIEW"
    assert risk[0]["prior_disposition"] == "UNAVAILABLE"
    assert after["status"][risk[0]["entity_id"]] == "in_review"
    assert all(i["detail"]["entity_status_changed_to_verified"] is False for i in items)
    assert fake.calls == 3                                      # exactly one lookup per entity


async def test_a_finished_job_cannot_run_again_and_repeating_the_trigger_adds_nothing(
        db_required, monkeypatch):
    intake_id, entity_ids, fake = await _delivery_verified_during_an_outage(monkeypatch)
    analyst = _User("reviewer")
    job = await _request(intake_id, analyst)
    await _approve(job["job_id"], _User("qalead"))
    fake.mode = "up"
    done = await _claim_and_run(job["job_id"])
    assert done["state"] == rm.STATE_SUCCEEDED
    settled = await _snapshot(entity_ids)
    calls = fake.calls

    with pytest.raises(rechecks.RecheckRefused, match="only RUNNING jobs run"):
        await _run(job["job_id"])
    repeat = await _request(intake_id, analyst)
    assert repeat["already_exists"] is True and repeat["state"] == rm.STATE_SUCCEEDED

    again = await _snapshot(entity_ids)
    assert again["evidence"] == settled["evidence"]              # no duplicate evidence
    assert again["issues"] == settled["issues"]                  # no duplicate findings
    assert fake.calls == calls                                   # no further quota spent


# ── crash recovery ───────────────────────────────────────────────────────────

async def test_a_dead_worker_is_reaped_and_each_entity_is_still_looked_up_exactly_once(
        db_required, monkeypatch):
    from app.core.database import async_session_maker

    intake_id, entity_ids, fake = await _delivery_verified_during_an_outage(monkeypatch)
    job = await _request(intake_id, _User("reviewer"))
    jid = uuid.UUID(job["job_id"])
    await _approve(job["job_id"], _User("qalead"))
    fake.mode = "up"

    partial = await _claim_and_run(job["job_id"], batch_size=1)
    assert partial["processed_count"] == 1 and partial["state"] == rm.STATE_RUNNING

    # The worker dies: no further heartbeat.
    async with async_session_maker() as db:
        row = await db.get(rechecks.RceRecheckJob, jid)
        row.heartbeat_at = datetime.utcnow() - timedelta(
            seconds=rechecks.STALE_HEARTBEAT_SECONDS + 5)
        await db.commit()
    async with async_session_maker() as db:
        reaped = await rechecks.reap_stale_jobs(db)
    assert [r["job_id"] for r in reaped] == [job["job_id"]]
    assert reaped[0]["state"] == rm.STATE_QUEUED

    finished = await _claim_and_run(job["job_id"])
    assert finished["state"] == rm.STATE_SUCCEEDED and finished["attempt_count"] == 2
    assert finished["processed_count"] == 3
    assert fake.calls == 3                                      # not 4: the done item was skipped
    async with async_session_maker() as db:
        items = (await rechecks.list_items(db, jid))["items"]
    assert [i["state"] for i in items] == [rm.ITEM_DONE] * 3


# ── rate-limit protection: the source has not actually recovered ─────────────

async def test_a_still_unavailable_source_stops_the_job_and_retries_are_bounded(
        db_required, monkeypatch):
    intake_id, entity_ids, fake = await _delivery_verified_during_an_outage(monkeypatch)
    before = await _snapshot(entity_ids)
    qa = _User("qalead")
    job = await _request(intake_id, _User("reviewer"))
    await _approve(job["job_id"], qa)
    # fake.mode stays "down": the recovery was declared but did not happen.

    stopped = await _claim_and_run(job["job_id"], batch_size=1)
    assert stopped["state"] == rm.STATE_STOPPED_SOURCE_UNAVAILABLE
    assert stopped["summary"]["pending"] == 2                    # the rest was NOT spent
    assert fake.calls == 1
    assert (await _snapshot(entity_ids))["status"] == before["status"]   # nothing changed

    from app.core.database import async_session_maker
    for expected_attempt in (2, 3):
        async with async_session_maker() as db:
            await rechecks.resume_stopped(db, uuid.UUID(job["job_id"]), user=qa)
        again = await _claim_and_run(job["job_id"], batch_size=1)
        assert again["attempt_count"] == expected_attempt
        assert again["state"] in (rm.STATE_STOPPED_SOURCE_UNAVAILABLE, rm.STATE_SUCCEEDED)
    if again["state"] == rm.STATE_STOPPED_SOURCE_UNAVAILABLE:
        async with async_session_maker() as db:
            with pytest.raises(rechecks.RecheckRefused, match="attempts exhausted"):
                await rechecks.resume_stopped(db, uuid.UUID(job["job_id"]), user=qa)
    assert fake.calls <= 3                                       # bounded by MAX_ATTEMPTS


# ── stale-baseline protection ────────────────────────────────────────────────

async def test_evidence_that_changed_after_approval_refuses_the_run(db_required, monkeypatch):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    intake_id, entity_ids, fake = await _delivery_verified_during_an_outage(monkeypatch, n=2)
    job = await _request(intake_id, _User("reviewer"))
    await _approve(job["job_id"], _User("qalead"))

    # Something else re-verifies the delivery after approval: the baseline moves.
    fake.mode = "up"
    async with async_session_maker() as db:
        refs = await sam._promoted_refs(db, intake_id, 2)
    async with async_session_maker() as db:
        await verify_and_classify(db, refs, intake_id=intake_id, actor="pytest-recheck-other")
    fake.calls = 0

    result = await _claim_and_run(job["job_id"])
    assert result["state"] == rm.STATE_REFUSED_STALE and result["processed_this_call"] == 0
    assert fake.calls == 0                                       # nothing was looked up
    assert "stale baseline" in result["error_reason"]


# ── surface: flag, drill-down CSV ────────────────────────────────────────────

async def test_routes_refuse_while_the_flag_is_off_and_the_csv_is_neutralised(
        db_required, monkeypatch):
    from fastapi import HTTPException

    from app.core.config import settings
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import preflight_shadow_routes as routes

    assert settings.ENABLE_CONTROLLED_RECHECKS is False
    with pytest.raises(HTTPException) as exc:
        routes._rechecks_enabled_or_409()
    assert exc.value.status_code == 409

    intake_id, _, _ = await _delivery_verified_during_an_outage(monkeypatch, n=1)
    job = await _request(intake_id, _User("reviewer"))
    async with async_session_maker() as db:
        body = await rechecks.items_csv(db, uuid.UUID(job["job_id"]))
    lines = body.strip().split("\r\n")
    assert lines[0].startswith("job_id,entity_id,entity_ref,state,prior_disposition")
    assert len(lines) == 2 and ",PENDING,UNAVAILABLE," in lines[1]


async def test_the_delivery_recheck_list_states_whether_the_feature_is_on_and_who_may_act(
        db_required, monkeypatch):
    """What the screen reads to decide whether to offer a control at all."""
    from app.core.config import settings
    from app.core.database import async_session_maker

    intake_id, _, _fake = await _delivery_verified_during_an_outage(monkeypatch, n=1)
    async with async_session_maker() as db:
        empty = await rechecks.list_jobs(db, intake_id)
    assert empty["items"] == [] and empty["enabled"] is bool(settings.ENABLE_CONTROLLED_RECHECKS)
    assert {s["source_id"] for s in empty["supported_sources"]} == set(rechecks.SUPPORTED_SOURCES)
    assert all(s["label"] and s["label"] != s["source_id"] for s in empty["supported_sources"])
    assert empty["roles"]["request"] == "reviewer"
    assert empty["roles"]["approve_run_resume"] == "qalead"

    job = await _request(intake_id, _User("reviewer"))
    async with async_session_maker() as db:
        listed = await rechecks.list_jobs(db, intake_id)
    assert [j["job_id"] for j in listed["items"]] == [job["job_id"]]
    assert listed["items"][0]["state"] == rm.STATE_PENDING_APPROVAL
    assert listed["items"][0]["is_compliance_approval"] is False
