"""Deferred delivery processing after an analyst decision (2026-11-18).

Owner authorization 2026-11-18 (night), item 6. Everything goes through the
real routes on the isolated database with SYNTHETIC rows; the worker is driven
by calling its own tick body (`disposition_followups.process_next`), which is
exactly what the scheduler calls.

Pinned here:
  * normal completion: 202 + QUEUED, nothing promoted in the request, then
    COMPLETED with a snapshot; the UI-facing states never say "completed" early;
  * duplicate submission (Idempotency-Key) applies nothing twice;
  * two reviewers on one delivery: one coalesced run, each record attributed to
    its own reviewer; a second run cannot overlap the first;
  * lost response / refresh / reopen: the accepted work is found again;
  * worker restart and partial failure: retried, never re-applied;
  * an interrupted request is not replayed, and an approved-but-unapplied
    finding is never released by recovery;
  * unauthorized users are refused and nothing is recorded;
  * a record with another blocking finding stays HELD and queues nothing;
  * totals, dispositions and the snapshot equal the synchronous workflow's.
"""

from __future__ import annotations

import uuid

import pytest
import support_delivery_api as support
from support_delivery_api import cleanup, headers_for, run, user_for

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"
FLAG = "ENABLE_DEFERRED_DISPOSITION_PROCESSING"


def valid_npi(seed: int) -> str:
    """A synthetic NPI that passes the Luhn check (prefix 80840)."""
    body = f"1{seed % 10**8:08d}"
    digits = [int(c) for c in "80840" + body]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return body + str((10 - total % 10) % 10)


_NPI_SEQ = [int(uuid.uuid4().int % 10**7)]


def next_npi() -> str:
    _NPI_SEQ[0] += 1
    return valid_npi(_NPI_SEQ[0])


def fresh_delivery(issues: int = 3):
    """A delivery whose organisation ids are unique to it, so its promotion
    outcome does not depend on what an earlier test promoted."""
    support.TAG = uuid.uuid4().hex[:8]
    return support.seed_delivery(state="SUCCEEDED", issues=issues)


@pytest.fixture(scope="module", autouse=True)
def _module_cleanup():
    yield

    async def _drop_followups():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(
                text("DELETE FROM rce_disposition_followups WHERE intake_id::text = ANY(:i)"),
                {"i": list(support._SEEDED["intakes"]) or [None]},
            )
            await db.commit()

    run(_drop_followups())
    cleanup()


@pytest.fixture
def deferred(monkeypatch):
    """The feature enabled, with an EMPTY queue before and after the test.

    The worker serves the oldest queued delivery first and the isolated
    database is shared by every test module, so a row another test left queued
    would be served ahead of this test's own. Draining first makes "one tick"
    mean this test's delivery; draining afterwards leaves nothing behind."""
    monkeypatch.setenv(FLAG, "true")
    from app.tefca_registry.rce import disposition_followups as followups

    monkeypatch.setattr(followups, "_stage_hook", None)
    run(followups.drain_all())
    yield followups
    followups._stage_hook = None
    run(followups.drain_all())


def correct(client, issue_id, *, role="reviewer", key=None, value=None, reason="synthetic check"):
    headers = headers_for(role, {"Idempotency-Key": key} if key else None)
    return client.post(
        f"{BASE}/issues/{issue_id}/dispositions",
        json={"decision": "CORRECT", "reason": reason, "corrected_value": value or next_npi()},
        headers=headers,
    )


async def _facts(delivery, issue_id=None):
    """Everything the tests compare, read fresh."""
    from sqlalchemy import func, select, text

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import dispositions as disp
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import reconciliation
    from app.tefca_registry.rce import traceability_models as tm

    intake = uuid.UUID(delivery["intake_id"])
    job = uuid.UUID(delivery["job_id"])
    async with async_session_maker() as db:
        out = {}
        out["statuses"] = dict(
            (
                await db.execute(
                    select(m.RceCuratedRecord.record_status, func.count())
                    .where(m.RceCuratedRecord.source_intake_id == intake)
                    .group_by(m.RceCuratedRecord.record_status)
                )
            ).all()
        )
        out["promoted"] = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(m.RceCuratedRecord)
                    .where(
                        m.RceCuratedRecord.source_intake_id == intake,
                        m.RceCuratedRecord.canonical_entity_id.isnot(None),
                    )
                )
            ).scalar()
            or 0
        )
        out["dispositions"] = await disp.counts_for_intake(db, intake)
        out["snapshots"] = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(tm.RceReconciliationSnapshot)
                    .where(tm.RceReconciliationSnapshot.job_id == job)
                )
            ).scalar()
            or 0
        )
        snap = await reconciliation.latest_snapshot(db, job)
        out["snapshot"] = (
            None
            if snap is None
            else {
                "id": str(snap.id),
                "passed": bool(snap.passed),
                "trigger": snap.trigger,
                "actor": snap.actor,
                "equation": {
                    k: getattr(snap, k)
                    for k in (
                        "received",
                        "created",
                        "updated",
                        "matched_unchanged",
                        "held",
                        "rejected",
                        "missing_key",
                        "excluded",
                    )
                },
                "checks": sorted(
                    (c.get("check"), bool(c.get("passed"))) for c in (snap.checks or [])
                ),
                "followups": (snap.source_evidence or {}).get("disposition_followups"),
            }
        )
        out["corrections"] = int(
            (
                await db.execute(
                    text(
                        "SELECT count(*) FROM rce_correction_details WHERE curated_record_id IN "
                        "(SELECT id FROM rce_curated_records WHERE source_intake_id = CAST(:i AS uuid))"
                    ),
                    {"i": str(intake)},
                )
            ).scalar()
            or 0
        )
        if issue_id:
            issue = await db.get(m.RceIssue, uuid.UUID(issue_id))
            out["resolution"] = issue.resolution
            history = await disp.history_for_record(db, issue.source_record_id)
            out["events"] = [
                (e["disposition"], e["reason_code"], e["actor_type"], e["actor"]) for e in history
            ]
            curated = (
                await db.execute(
                    select(m.RceCuratedRecord).where(
                        m.RceCuratedRecord.source_record_id == issue.source_record_id
                    )
                )
            ).scalar_one()
            out["record_status"] = curated.record_status
            out["entity"] = curated.canonical_entity_id
            out["submitted_npi"] = (
                await db.execute(
                    select(m.RceSourceRecord.npi).where(
                        m.RceSourceRecord.id == issue.source_record_id
                    )
                )
            ).scalar()
            out["curated_npi"] = curated.npi
            out["entity_npi"] = (
                (
                    await db.execute(
                        text(
                            "SELECT identifier_value FROM tefca_entity_identifiers "
                            "WHERE entity_id = CAST(:e AS uuid) AND identifier_type = 'npi' "
                            "AND identifier_status = 'active'"
                        ),
                        {"e": str(curated.canonical_entity_id)},
                    )
                ).scalar()
                if curated.canonical_entity_id
                else None
            )
            out["entity_audit_actors"] = sorted(
                {
                    a
                    for (a,) in (
                        await db.execute(
                            text(
                                "SELECT actor_email FROM tefca_reg_audit_log WHERE entity_id = CAST(:e AS uuid) "
                                "AND action IN ('entity_created','entity_updated')"
                            ),
                            {"e": str(curated.canonical_entity_id)},
                        )
                    ).all()
                }
                if curated.canonical_entity_id
                else []
            )
        return out


def facts(delivery, issue_id=None):
    return run(_facts(delivery, issue_id))


def drain(followups):
    return run(followups.drain_all())


def state_of(client, pid):
    return client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()["processing"]["state"]


def tick_until_settled(client, followups, pid, ticks=12):
    """Run the scheduler's OWN poll tick until this row is no longer QUEUED.

    One tick serves one delivery, oldest queued first, and the isolated
    database is shared across test modules: a tick may legitimately serve a
    row another test left queued before it reaches this one."""
    for _ in range(ticks):
        run(followups._poll_tick())
        if state_of(client, pid) != "QUEUED":
            return


# ── normal completion ────────────────────────────────────────────────────────


def test_a_releasing_decision_is_acknowledged_queued_then_completed(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    before = facts(d, issue_id)
    assert before["record_status"] == "HELD" and before["snapshots"] == 0

    r = correct(client, issue_id)
    assert r.status_code == 202, r.text
    body = r.json()
    processing = body["processing"]
    assert processing["state"] == "QUEUED" and processing["pending"] is True
    assert "queued" in processing["message"].lower()
    assert body["result"]["consequence_deferred"] is True
    assert body["result"]["re_promoted"] is False and body["result"]["snapshot_id"] is None

    # The decision is recorded; the delivery work has NOT run inside the request.
    mid = facts(d, issue_id)
    assert mid["resolution"] == "RESOLVED" and mid["record_status"] == "CORRECTED"
    assert mid["corrections"] == 1 and mid["entity"] is None and mid["snapshots"] == 0
    assert mid["submitted_npi"] == before["submitted_npi"]  # Area 1 untouched

    # Every reader is told the reconciliation is not current.
    ledger = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/exceptions", headers=headers_for("reviewer")
    ).json()
    assert ledger["disposition_processing"]["pending"] == 1
    assert ledger["disposition_processing"]["reconciliation_current"] is False
    row = next(x for x in ledger["items"] if x["issue_id"] == issue_id)
    assert row["processing"]["state"] == "QUEUED"
    listing = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/disposition-processing",
        headers=headers_for("reviewer"),
    ).json()
    assert listing["summary"]["queued"] == 1 and listing["items"][0]["id"] == processing["id"]

    results = drain(deferred)
    assert [x["outcome"] for x in results] == ["COMPLETED"]

    status = client.get(processing["status_url"], headers=headers_for("reviewer")).json()
    assert status["processing"]["state"] == "COMPLETED"
    assert status["processing"]["snapshot_id"] and status["processing"]["pending"] is False
    assert status["delivery"]["reconciliation_current"] is True

    after = facts(d, issue_id)
    assert after["entity"] is not None and after["snapshots"] == 1
    assert after["snapshot"]["trigger"] == "DISPOSITION"
    assert after["snapshot"]["followups"]["followup_ids"] == [processing["id"]]
    last = after["events"][-1]
    assert last[1] == "ANALYST_DISPOSITION" and last[2] == "HUMAN"
    assert last[3] == user_for("reviewer")["email"]
    assert after["entity_audit_actors"] == [user_for("reviewer")["email"]]
    assert after["corrections"] == 1


def test_a_decision_that_changes_no_record_status_queues_nothing(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][2]
    r = client.post(
        f"{BASE}/issues/{issue_id}/dispositions",
        json={"decision": "DEFER", "reason": "waiting for a synthetic answer"},
        headers=headers_for("reviewer"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["processing"]["state"] == "NOT_REQUIRED"
    assert drain(deferred) == []
    assert facts(d, issue_id)["record_status"] == "HELD"


def test_a_record_with_another_blocking_finding_stays_held(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]

    async def _second_finding():
        from app.core.database import async_session_maker
        from app.tefca_registry.rce import models as m

        async with async_session_maker() as db:
            first = await db.get(m.RceIssue, uuid.UUID(issue_id))
            db.add(
                m.RceIssue(
                    issue_code=f"DQ-{uuid.uuid4().hex[:10]}",
                    source_intake_id=first.source_intake_id,
                    source_record_id=first.source_record_id,
                    run_id=first.run_id,
                    rule_id="NAME-001",
                    rule_version="1.2.0",
                    issue_type="NAME_MISSING",
                    severity="HIGH",
                    field_name="name",
                    original_value=None,
                    correction_authority="HUMAN_REQUIRED",
                    description="second synthetic finding",
                )
            )
            await db.commit()

    run(_second_finding())

    r = correct(client, issue_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["processing"]["state"] == "NOT_REQUIRED"
    assert body["result"]["record_status_after"] == "HELD"
    assert body["result"]["hold_released"] is False
    assert drain(deferred) == []
    after = facts(d, issue_id)
    assert after["record_status"] == "HELD" and after["entity"] is None


# ── the same outcome as the synchronous workflow ─────────────────────────────


def test_final_totals_and_snapshot_match_the_synchronous_workflow(client, monkeypatch):
    from app.tefca_registry.rce import disposition_followups as followups

    monkeypatch.delenv(FLAG, raising=False)
    sync = fresh_delivery()
    r = correct(client, sync["issue_ids"][0])
    assert r.status_code == 200, r.text
    assert "processing" not in r.json() and r.json()["result"]["snapshot_id"]
    a = facts(sync, sync["issue_ids"][0])

    monkeypatch.setenv(FLAG, "true")
    later = fresh_delivery()
    r = correct(client, later["issue_ids"][0])
    assert r.status_code == 202, r.text
    run(followups.drain_all())
    b = facts(later, later["issue_ids"][0])

    for key in (
        "statuses",
        "promoted",
        "dispositions",
        "snapshots",
        "corrections",
        "resolution",
        "record_status",
    ):
        assert a[key] == b[key], key
    for key in ("passed", "trigger", "actor", "equation", "checks"):
        assert a["snapshot"][key] == b["snapshot"][key], key
    assert [e[:4] for e in a["events"]] == [e[:4] for e in b["events"]]
    assert a["entity_audit_actors"] == b["entity_audit_actors"]
    assert (a["entity"] is None) == (b["entity"] is None)


# ── duplicate submissions and lost responses ─────────────────────────────────


def test_the_same_submission_twice_applies_once(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    key, value = uuid.uuid4().hex, next_npi()
    first = correct(client, issue_id, key=key, value=value)
    assert first.status_code == 202, first.text
    again = correct(client, issue_id, key=key, value=value)
    assert again.status_code == 202, again.text
    assert again.json()["replayed"] is True
    assert again.json()["processing"]["id"] == first.json()["processing"]["id"]
    # the replay carries states only, never the corrected value
    assert value not in again.text
    assert facts(d, issue_id)["corrections"] == 1

    different = correct(client, issue_id, key=key, value=next_npi())
    assert different.status_code == 422, different.text

    without_key = correct(client, issue_id)
    assert without_key.status_code == 409  # terminal finding, as before

    drain(deferred)
    done = correct(client, issue_id, key=key, value=value)
    assert done.status_code == 200 and done.json()["processing"]["state"] == "COMPLETED"
    after = facts(d, issue_id)
    assert after["corrections"] == 1 and after["snapshots"] == 1
    assert len([e for e in after["events"] if e[1] == "ANALYST_DISPOSITION"]) == 1


def test_a_lost_response_is_recoverable_after_refresh_and_reopen(client, deferred):
    """The browser timed out or was closed: the response never arrived."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    key, value = uuid.uuid4().hex, next_npi()
    correct(client, issue_id, key=key, value=value)  # response discarded

    # reopen the Exceptions tab: the finding says what is happening to it
    ledger = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/exceptions", headers=headers_for("reviewer")
    ).json()
    row = next(x for x in ledger["items"] if x["issue_id"] == issue_id)
    assert row["processing"]["state"] == "QUEUED" and row["terminal"] is True
    # reopen the delivery page: its reconciliation is declared not current
    detail = client.get(
        f"{BASE}/delivery-jobs/{d['job_id']}/detail", headers=headers_for("reviewer")
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["reconciliation"]["current"] is False
    assert detail.json()["reconciliation"]["disposition_processing"]["pending"] == 1

    drain(deferred)
    ledger = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/exceptions", headers=headers_for("reviewer")
    ).json()
    row = next(x for x in ledger["items"] if x["issue_id"] == issue_id)
    assert row["processing"]["state"] == "COMPLETED"
    assert ledger["disposition_processing"]["reconciliation_current"] is True
    assert facts(d, issue_id)["corrections"] == 1


# ── two reviewers, one delivery ──────────────────────────────────────────────


def test_two_reviewers_on_one_delivery_are_one_run_with_their_own_attribution(client, deferred):
    d = fresh_delivery()
    first, second = d["issue_ids"][0], d["issue_ids"][2]
    r1 = correct(client, first, role="reviewer")
    r2 = correct(client, second, role="qalead")
    assert r1.status_code == 202 and r2.status_code == 202, (r1.text, r2.text)

    results = drain(deferred)
    assert len(results) == 1 and results[0]["outcome"] == "COMPLETED"
    assert results[0]["completed"] == 2

    a, b = facts(d, first), facts(d, second)
    assert a["snapshots"] == 1  # one snapshot covers both
    assert a["snapshot"]["actor"] == "SYSTEM"
    assert a["snapshot"]["followups"]["decisions_covered"] == 2
    assert a["events"][-1][3] == user_for("reviewer")["email"]
    assert b["events"][-1][3] == user_for("qalead")["email"]
    assert a["events"][-1][2] == b["events"][-1][2] == "HUMAN"
    assert a["entity_audit_actors"] == [user_for("reviewer")["email"]]
    assert b["entity_audit_actors"] == [user_for("qalead")["email"]]
    s1 = client.get(r1.json()["processing"]["status_url"], headers=headers_for("reviewer")).json()
    s2 = client.get(r2.json()["processing"]["status_url"], headers=headers_for("reviewer")).json()
    assert s1["processing"]["snapshot_id"] == s2["processing"]["snapshot_id"]


def test_a_decision_accepted_while_a_run_is_in_progress_is_processed_from_fresh_state(
    client, deferred, monkeypatch
):
    """Found by the two-reviewer timing burst (2026-11-18): the worker kept ONE
    database session across its rounds, so a record corrected by a request
    while the run was in progress was re-read from the session's stale copy -
    released as CLEAN instead of CORRECTED and promoted with its uncorrected
    value. Every round must read the database, not the previous round."""
    import asyncio

    d = fresh_delivery()
    first, second = d["issue_ids"][0], d["issue_ids"][2]
    first_value, second_value = next_npi(), next_npi()
    headers_for("qalead")  # create the user before any loop is running
    assert correct(client, first, value=first_value).status_code == 202
    seen = {"promotions": 0, "second_status": None}

    async def accept_second_decision_mid_run(stage):
        if stage == "PROMOTION":
            seen["promotions"] += 1
            if seen["promotions"] == 1:
                response = await asyncio.to_thread(
                    correct, client, second, role="qalead", value=second_value
                )
                seen["second_status"] = response.status_code

    monkeypatch.setattr(deferred, "_stage_hook", accept_second_decision_mid_run)
    results = drain(deferred)
    assert seen["second_status"] == 202
    assert [x["outcome"] for x in results] == ["COMPLETED"]
    assert results[0]["completed"] == 2 and results[0]["drain_rounds"] >= 2

    a, b = facts(d, first), facts(d, second)
    for got, value, role in ((a, first_value, "reviewer"), (b, second_value, "qalead")):
        assert got["record_status"] == "CORRECTED"  # what a synchronous save leaves
        assert got["curated_npi"] == value
        assert got["entity"] is not None
        assert got["entity_npi"] == value  # promoted with the corrected value
        assert got["events"][-1][2] == "HUMAN"
        assert got["events"][-1][3] == user_for(role)["email"]
    assert a["corrections"] == 2 and a["snapshots"] == 1


def test_a_second_run_cannot_overlap_a_run_in_progress(client, deferred):
    d = fresh_delivery()
    assert correct(client, d["issue_ids"][0]).status_code == 202

    async def _while_held():
        async with deferred.delivery_lock(d["intake_id"]) as slot:
            assert slot.acquired
            busy = await deferred.process_next()
            return busy

    busy = run(_while_held())
    assert busy["outcome"] == "DELIVERY_BUSY"
    assert facts(d, d["issue_ids"][0])["snapshots"] == 0  # nothing ran
    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]


# ── worker restart and partial failure ───────────────────────────────────────


def test_a_partial_failure_is_retried_without_reapplying_anything(client, deferred, monkeypatch):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    r = correct(client, issue_id)
    pid = r.json()["processing"]["id"]
    calls = {"n": 0}

    async def fail_reconciliation_once(stage):
        if stage == "RECONCILIATION":
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("synthetic failure after promotion committed")

    monkeypatch.setattr(deferred, "_stage_hook", fail_reconciliation_once)

    first = run(deferred.process_next())
    assert first["outcome"] == "RETRY_OR_FAILED" and first["failed_stage"] == "RECONCILIATION"
    mid = facts(d, issue_id)
    assert mid["entity"] is not None and mid["snapshots"] == 0  # promotion had committed
    status = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()["processing"]
    assert status["state"] == "QUEUED" and status["attempt_count"] == 1
    events_mid = mid["events"]

    second = run(deferred.process_next())
    assert second["outcome"] == "COMPLETED"
    after = facts(d, issue_id)
    assert after["events"] == events_mid  # no second disposition event
    assert after["corrections"] == 1 and after["snapshots"] == 1
    assert after["entity"] == mid["entity"]  # no second entity


def test_a_dead_worker_is_reaped_and_the_work_resumes(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = correct(client, issue_id).json()["processing"]["id"]

    async def _crash_after_claim():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(
                text(
                    "UPDATE rce_disposition_followups SET state='PROCESSING', attempt_count=1, "
                    "batch_id=gen_random_uuid(), heartbeat_at=now() - interval '1 hour' "
                    "WHERE id = CAST(:i AS uuid)"
                ),
                {"i": pid},
            )
            await db.commit()

    run(_crash_after_claim())
    assert drain(deferred) == []  # nothing queued: the claim was lost
    reaped = run(deferred.reap_stale_processing())
    assert [x["state"] for x in reaped if x["id"] == pid] == ["QUEUED"]
    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]
    after = facts(d, issue_id)
    assert after["corrections"] == 1 and after["snapshots"] == 1


def test_exhausted_retries_fail_safely_and_an_authorised_retry_completes(
    client, deferred, monkeypatch
):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = correct(client, issue_id).json()["processing"]["id"]

    async def always_fail(stage):
        if stage == "PROMOTION":
            raise RuntimeError("synthetic outage")

    monkeypatch.setattr(deferred, "_stage_hook", always_fail)
    for _ in range(deferred.MAX_ATTEMPTS):
        run(deferred.process_next())
    failed = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()
    assert failed["processing"]["state"] == "FAILED"
    assert failed["processing"]["error_code"] == "RETRIES_EXHAUSTED"
    assert (
        failed["processing"]["safe_explanation"] == deferred.SAFE_EXPLANATIONS["RETRIES_EXHAUSTED"]
    )
    assert "synthetic outage" not in str(failed) and "RuntimeError" not in str(failed)
    assert failed["delivery"]["failed"] == 1
    assert failed["delivery"]["reconciliation_current"] is False
    assert facts(d, issue_id)["corrections"] == 1  # the decision stands

    monkeypatch.setattr(deferred, "_stage_hook", None)
    assert (
        client.post(
            f"{BASE}/disposition-processing/{pid}/retry", headers=headers_for("viewer")
        ).status_code
        == 403
    )
    retried = client.post(
        f"{BASE}/disposition-processing/{pid}/retry", headers=headers_for("reviewer")
    )
    assert retried.status_code == 202 and retried.json()["processing"]["state"] == "QUEUED"
    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]
    after = facts(d, issue_id)
    assert after["corrections"] == 1 and after["snapshots"] == 1 and after["entity"] is not None


# ── an interrupted request is never replayed ─────────────────────────────────


async def _orphan_received(delivery, issue_id, *, resolution):
    """A request that died after its first commit: a RECEIVED row, old, with
    the finding left in `resolution`."""
    from sqlalchemy import text

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import disposition_followup_models as fum
    from app.tefca_registry.rce import disposition_followups as followups
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        issue = await db.get(m.RceIssue, uuid.UUID(issue_id))
        issue.resolution = resolution
        row = followups.new_row(
            kind=fum.KIND_ISSUE_DISPOSITION,
            intake_id=issue.source_intake_id,
            issue_id=issue.id,
            source_record_id=issue.source_record_id,
            decision="CORRECT",
            reason="synthetic",
            actor="lane-a@test.local",
            actor_id=None,
            attribution=fum.ATTRIBUTION_HUMAN,
            idempotency_key=None,
            payload_fingerprint=None,
        )
        db.add(row)
        await db.commit()
        await db.execute(
            text(
                "UPDATE rce_disposition_followups SET received_at = now() - interval '2 hours' "
                "WHERE id = CAST(:i AS uuid)"
            ),
            {"i": str(row.id)},
        )
        await db.commit()
        return str(row.id)


def test_recovery_never_releases_an_approved_but_unapplied_finding(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = run(_orphan_received(d, issue_id, resolution="APPROVED"))

    swept = run(deferred.sweep_received())
    assert [x["state"] for x in swept if x["id"] == pid] == ["FAILED"]
    status = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()["processing"]
    assert status["error_code"] == "DECISION_INTERRUPTED"
    assert drain(deferred) == []  # nothing was queued by recovery
    after = facts(d, issue_id)
    assert after["record_status"] == "HELD" and after["corrections"] == 0
    assert after["entity"] is None

    retry = client.post(
        f"{BASE}/disposition-processing/{pid}/retry", headers=headers_for("reviewer")
    )
    assert retry.status_code == 409
    assert facts(d, issue_id)["record_status"] == "HELD"


def test_recovery_resumes_delivery_work_for_a_decision_that_had_completed(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = run(_orphan_received(d, issue_id, resolution="RESOLVED"))
    swept = run(deferred.sweep_received())
    assert [x["state"] for x in swept if x["id"] == pid] == ["QUEUED"]
    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]
    status = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()["processing"]
    assert status["state"] == "COMPLETED" and status["recovered"] is True
    assert facts(d, issue_id)["corrections"] == 0  # recovery applied no correction


def test_a_refusal_after_a_partial_commit_closes_the_row(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    r = correct(client, issue_id, value="123")  # not a valid NPI
    assert r.status_code == 409, r.text
    listing = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/disposition-processing",
        headers=headers_for("reviewer"),
    ).json()
    assert [x["state"] for x in listing["items"]] == ["REFUSED"]
    assert listing["summary"]["pending"] == 0
    assert drain(deferred) == []
    after = facts(d, issue_id)
    assert after["corrections"] == 0 and after["entity"] is None


# ── permissions ──────────────────────────────────────────────────────────────


def test_an_unauthorised_user_is_refused_and_nothing_is_recorded(client, deferred):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    assert correct(client, issue_id, role="viewer").status_code == 403
    assert client.post(
        f"{BASE}/issues/{issue_id}/dispositions",
        json={"decision": "CORRECT", "reason": "x", "corrected_value": next_npi()},
    ).status_code in (401, 403)
    assert (
        client.get(
            f"{BASE}/deliveries/{d['intake_id']}/disposition-processing",
            headers=headers_for("viewer"),
        ).status_code
        == 403
    )
    listing = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/disposition-processing",
        headers=headers_for("reviewer"),
    ).json()
    assert listing["items"] == [] and listing["summary"]["pending"] == 0
    after = facts(d, issue_id)
    assert after["resolution"] in (None, "OPEN") and after["record_status"] == "HELD"


# ── disclosure and gates ─────────────────────────────────────────────────────


def test_pending_processing_blocks_the_approval_gate_and_is_disclosed(client, deferred):
    d = fresh_delivery()
    assert correct(client, d["issue_ids"][0]).status_code == 202

    async def _read():
        from app.core.database import async_session_maker
        from app.tefca_registry.rce import delivery_dashboard, snapshot_effects

        async with async_session_maker() as db:
            gates = await snapshot_effects.approval_gates(db, uuid.UUID(d["intake_id"]))
            dash = await delivery_dashboard._disposition_processing(db, uuid.UUID(d["intake_id"]))
            return gates, dash

    gates, dash = run(_read())
    assert gates["gates"]["no_disposition_processing_outstanding"] is False
    assert gates["all"] is False
    assert dash["pending"] == 1 and "NOT current" in dash["notice"]

    drain(deferred)
    gates, dash = run(_read())
    assert gates["gates"]["no_disposition_processing_outstanding"] is True
    assert dash["pending"] == 0 and dash["notice"] is None


def test_with_the_flag_off_the_save_is_synchronous_and_records_no_followup(client, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    d = fresh_delivery()
    r = correct(client, d["issue_ids"][0])
    assert r.status_code == 200, r.text
    assert r.json()["result"]["re_promoted"] is True
    listing = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/disposition-processing",
        headers=headers_for("reviewer"),
    ).json()
    assert listing["items"] == [] and listing["deferred_processing_enabled"] is False


# ── the second save route: /identifier-decisions ─────────────────────────────


async def _make_conflict(delivery):
    """A committed identifier conflict: the registry holds one synthetic NPI
    for the organisation, the delivery submits another, and promotion holds
    the record with an NPI-008 finding."""
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce.promotion import promote_delivery

    intake = uuid.UUID(delivery["intake_id"])
    registered, submitted = next_npi(), next_npi()
    async with async_session_maker() as db:
        issue = await db.get(m.RceIssue, uuid.UUID(delivery["issue_ids"][0]))
        curated = (
            await db.execute(
                select(m.RceCuratedRecord).where(
                    m.RceCuratedRecord.source_record_id == issue.source_record_id
                )
            )
        ).scalar_one()
        entity_id = uuid.uuid4()
        db.add(
            reg.TefcaRegEntity(
                id=entity_id,
                name=curated.name,
                display_name=curated.name,
                entity_level="participant",
                entity_type="provider",
                operational_status="active",
                verification_status="not_verified",
                current_version=1,
                is_active=True,
                exchange_purposes={"purposes": []},
                rce_org_oid=curated.rce_org_oid,
                is_test_record=False,
                rce_attributes={},
            )
        )
        await db.flush()
        db.add(
            reg.TefcaEntityIdentifier(
                id=uuid.uuid4(),
                entity_id=entity_id,
                identifier_type="rce_org_oid",
                identifier_value=curated.rce_org_oid,
                system_uri="urn:docuaction:tefca/identifier/rce-org-oid",
                is_primary=True,
                identifier_status="active",
            )
        )
        db.add(
            reg.TefcaEntityIdentifier(
                id=uuid.uuid4(),
                entity_id=entity_id,
                identifier_type="npi",
                identifier_value=registered,
                system_uri="http://hl7.org/fhir/sid/us-npi",
                is_primary=False,
                identifier_status="active",
            )
        )
        issue.resolution = "RESOLVED"  # the seeded quality finding is settled
        curated.record_status = "CLEAN"
        curated.npi = submitted
        await db.commit()
        await promote_delivery(db, intake, actor="SYSTEM")
        await db.refresh(curated)
        assert curated.record_status == "HELD" and curated.canonical_entity_id is None
        conflict = (
            await db.execute(
                select(m.RceIssue).where(
                    m.RceIssue.source_record_id == curated.source_record_id,
                    m.RceIssue.rule_id == "NPI-008",
                )
            )
        ).scalar_one()
        return {
            "entity_id": str(entity_id),
            "issue_id": str(conflict.id),
            "source_record_id": str(curated.source_record_id),
        }


def _decide_conflict(client, conflict, role="reviewer"):
    return client.post(
        f"{BASE}/identifier-decisions",
        headers=headers_for(role),
        json={
            "entity_id": conflict["entity_id"],
            "identifier_type": "npi",
            "decision": "CONFIRM_EXISTING",
            "reason": "registered value confirmed (synthetic)",
            "issue_id": conflict["issue_id"],
            "source_record_id": conflict["source_record_id"],
        },
    )


async def _conflict_facts(delivery, conflict):
    from sqlalchemy import func, select

    from app.core.database import async_session_maker
    from app.tefca_registry.rce import dispositions as disp
    from app.tefca_registry.rce import models as m
    from app.tefca_registry.rce import traceability_models as tm

    async with async_session_maker() as db:
        curated = (
            await db.execute(
                select(m.RceCuratedRecord).where(
                    m.RceCuratedRecord.source_record_id == uuid.UUID(conflict["source_record_id"])
                )
            )
        ).scalar_one()
        history = await disp.history_for_record(db, curated.source_record_id)
        return {
            "record_status": curated.record_status,
            "linked": str(curated.canonical_entity_id) if curated.canonical_entity_id else None,
            "events": [(e["disposition"], e["actor_type"]) for e in history],
            "decisions": int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(tm.TefcaIdentifierDecisionEvent)
                        .where(
                            tm.TefcaIdentifierDecisionEvent.issue_id
                            == uuid.UUID(conflict["issue_id"]),
                            tm.TefcaIdentifierDecisionEvent.decision != "CONFLICT_RAISED",
                        )
                    )
                ).scalar()
                or 0
            ),
            "snapshots": int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(tm.RceReconciliationSnapshot)
                        .where(tm.RceReconciliationSnapshot.job_id == uuid.UUID(delivery["job_id"]))
                    )
                ).scalar()
                or 0
            ),
        }


def test_the_identifier_decision_route_defers_the_same_delivery_work(client, deferred):
    d = fresh_delivery(issues=1)
    conflict = run(_make_conflict(d))
    assert _decide_conflict(client, conflict, role="viewer").status_code == 403

    r = _decide_conflict(client, conflict)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["processing"]["state"] == "QUEUED"
    assert body["processing"]["kind"] == "IDENTIFIER_DECISION"
    assert body["follow_up"]["repromotion"] == {
        "ran": False,
        "deferred": True,
        "processing_id": body["processing"]["id"],
    }
    assert body["follow_up"]["snapshot"]["deferred"] is True

    mid = run(_conflict_facts(d, conflict))
    assert mid["decisions"] == 1  # the decision is recorded, durably
    assert mid["snapshots"] == 0  # no reconciliation ran in the request
    listing = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/disposition-processing",
        headers=headers_for("reviewer"),
    ).json()
    assert listing["summary"]["queued"] == 1
    assert listing["summary"]["reconciliation_current"] is False

    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]
    after = run(_conflict_facts(d, conflict))
    assert after["snapshots"] == 1 and after["decisions"] == 1  # decided once, reconciled once
    # this route's re-promotion has always been a SYSTEM event; deferral keeps that
    assert {actor_type for _d, actor_type in after["events"]} == {"SYSTEM"}
    # What this route does to the record itself (whether the still-open NPI-008
    # finding keeps it held) is its existing behaviour and is pinned by the
    # comparison with the synchronous path in the next test, not asserted here.


def test_the_identifier_decision_route_matches_its_synchronous_outcome(client, monkeypatch):
    from app.tefca_registry.rce import disposition_followups as followups

    monkeypatch.delenv(FLAG, raising=False)
    sync = fresh_delivery(issues=1)
    sync_conflict = run(_make_conflict(sync))
    r = _decide_conflict(client, sync_conflict)
    assert r.status_code == 200, r.text
    assert r.json()["follow_up"]["repromotion"]["ran"] is True
    a = run(_conflict_facts(sync, sync_conflict))

    monkeypatch.setenv(FLAG, "true")
    later = fresh_delivery(issues=1)
    later_conflict = run(_make_conflict(later))
    assert _decide_conflict(client, later_conflict).status_code == 202
    run(followups.drain_all())
    b = run(_conflict_facts(later, later_conflict))

    # Deferral changes WHEN the delivery work runs, never its result.
    assert a["record_status"] == b["record_status"]
    assert a["events"] == b["events"]
    assert a["decisions"] == b["decisions"] == 1
    assert a["snapshots"] == b["snapshots"] == 1
    assert (a["linked"] is None) == (b["linked"] is None)


# ── concurrency on one delivery (found by the two-reviewer timing burst) ─────
#
# Local synthetic burst, 2026-11-18: two reviewers saving on one delivery left
# corrected records marked CLEAN instead of CORRECTED. The delivery-wide hold
# recomputation is a read-modify-write over every record; a decision commits
# its finding as APPROVED before it commits the correction, and a concurrent
# recomputation that read the record in between wrote CLEAN over CORRECTED.
# Under the flag a decision now re-derives ONLY its own record, with a guarded
# update whose values the database reads at write time, and decisions for one
# delivery are recorded one at a time.


async def _set_issue(issue_id, **values):
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        issue = await db.get(m.RceIssue, uuid.UUID(issue_id))
        for key, value in values.items():
            setattr(issue, key, value)
        await db.commit()


def test_a_deferred_decision_never_runs_the_delivery_wide_hold_recomputation(
    client, deferred, monkeypatch
):
    from app.tefca_registry.rce import curation

    calls = []
    real = curation.recompute_hold_status

    async def spy(db, intake_id, **kw):
        calls.append(str(intake_id))
        return await real(db, intake_id, **kw)

    monkeypatch.setattr(curation, "recompute_hold_status", spy)
    d = fresh_delivery()
    r = correct(client, d["issue_ids"][0])
    assert r.status_code == 202, r.text
    assert r.json()["result"]["hold"]["scope"] == "RECORD"
    drain(deferred)
    assert calls == []  # neither the request nor the worker rewrote the whole delivery
    after = facts(d, d["issue_ids"][0])
    assert after["record_status"] == "CORRECTED" and after["entity"] is not None


def test_another_reviewers_decision_does_not_release_an_approved_but_unapplied_finding(
    client, deferred
):
    """Finding X was left APPROVED with its correction never applied (its
    request was interrupted). A different reviewer then decides finding Y on
    the SAME delivery. The record of X must stay held and unpromoted."""
    d = fresh_delivery()
    x, y = d["issue_ids"][0], d["issue_ids"][2]
    run(_set_issue(x, resolution="APPROVED", resolved_by="lane-a@test.local"))

    r = correct(client, y, role="qalead")
    assert r.status_code == 202, r.text
    assert [o["outcome"] for o in drain(deferred)] == ["COMPLETED"]

    held = facts(d, x)
    assert held["resolution"] == "APPROVED"
    assert held["record_status"] == "HELD" and held["entity"] is None
    assert held["corrections"] == 1  # only the correction for Y exists
    # X is accounted for as HELD (promotion writes that for every held record)
    # and nothing else: no CREATED / UPDATED / MATCHED event, no release.
    assert held["events"] and all(e[0] == "HELD" for e in held["events"])
    done = facts(d, y)
    assert done["record_status"] == "CORRECTED" and done["entity"] is not None


def test_a_record_reheld_while_its_decision_was_finishing_is_still_processed(
    client, deferred, monkeypatch
):
    """A running promotion can put a just-corrected record back to HELD from
    its earlier reading of what blocks it. The request then sees the same
    status before and after. The decision is complete, the record is unblocked
    and unpromoted, so delivery work IS needed: queue on that state, not on
    the status difference, or the record is stranded."""
    from sqlalchemy import text

    from app.core.database import async_session_maker

    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    real = deferred.rederive_record_hold

    async def reheld_by_a_running_promotion(db, intake_id, source_record_id):
        out = await real(db, intake_id, source_record_id)
        async with async_session_maker() as other:
            await other.execute(
                text(
                    "UPDATE rce_curated_records SET record_status = 'HELD' "
                    "WHERE source_record_id = CAST(:r AS uuid)"
                ),
                {"r": str(source_record_id)},
            )
            await other.commit()
        return out

    monkeypatch.setattr(deferred, "rederive_record_hold", reheld_by_a_running_promotion)
    r = correct(client, issue_id)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["result"]["record_status_before"] == "HELD"
    assert body["result"]["record_status_after"] == "HELD"
    assert body["processing"]["state"] == "QUEUED"

    monkeypatch.setattr(deferred, "rederive_record_hold", real)
    assert [o["outcome"] for o in drain(deferred)] == ["COMPLETED"]
    after = facts(d, issue_id)
    assert after["record_status"] == "CORRECTED" and after["entity"] is not None
    assert after["entity_npi"] == after["curated_npi"]


def test_a_promotion_working_from_an_earlier_blocking_set_does_not_strand_the_record(
    client, deferred, monkeypatch
):
    """The second decision is accepted AFTER the running promotion has read
    what blocks each record. That promotion re-holds the second record; the
    same batch must release it again and promote it with its corrected value."""
    import asyncio

    from app.tefca_registry.rce import curation

    d = fresh_delivery()
    first, second = d["issue_ids"][0], d["issue_ids"][2]
    second_value = next_npi()
    headers_for("qalead")
    assert correct(client, first).status_code == 202
    real = curation._blocking_by_record
    state = {"in_promotion": False, "fired": False, "status": None}

    async def stale_for_promotion(db, intake_id, **kw):
        result = await real(db, intake_id, **kw)
        if state["in_promotion"] and not state["fired"]:
            state["fired"] = True  # promotion has its reading; now the decision lands
            response = await asyncio.to_thread(
                correct, client, second, role="qalead", value=second_value
            )
            state["status"] = response.status_code
        return result

    async def mark(stage):
        state["in_promotion"] = stage == "PROMOTION"

    monkeypatch.setattr(curation, "_blocking_by_record", stale_for_promotion)
    monkeypatch.setattr(deferred, "_stage_hook", mark)
    results = drain(deferred)
    assert state["status"] == 202
    assert results and all(o["outcome"] == "COMPLETED" for o in results)

    b = facts(d, second)
    assert b["record_status"] == "CORRECTED" and b["entity"] is not None
    assert b["curated_npi"] == second_value and b["entity_npi"] == second_value
    assert b["events"][-1][2] == "HUMAN"
    assert b["events"][-1][3] == user_for("qalead")["email"]
    assert facts(d, first)["record_status"] == "CORRECTED"


def test_decisions_for_one_delivery_are_recorded_one_at_a_time(client, deferred, monkeypatch):
    """While another decision for the delivery is being recorded, a second one
    waits a bounded time and is then refused with its own code - and writes
    nothing."""
    import asyncio

    monkeypatch.setattr(deferred, "DECISION_SLOT_WAIT_SECONDS", 0.5)
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    headers_for("reviewer")

    async def _while_another_decision_holds_the_delivery():
        async with deferred.decision_slot(d["intake_id"]) as acquired:
            assert acquired
            return await asyncio.to_thread(correct, client, issue_id)

    refused = run(_while_another_decision_holds_the_delivery())
    assert refused.status_code == 409, refused.text
    assert deferred.DELIVERY_DECISION_BUSY_CODE in refused.text
    assert deferred.DELIVERY_DECISION_BUSY_MESSAGE in refused.text
    untouched = facts(d, issue_id)
    assert untouched["resolution"] in (None, "OPEN") and untouched["corrections"] == 0
    listing = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/disposition-processing",
        headers=headers_for("reviewer"),
    ).json()
    assert listing["items"] == []

    assert correct(client, issue_id).status_code == 202  # free again: accepted


# ── the rollback window: the flag is OFF and accepted work is outstanding ────
#
# These are ACTUAL failure/recovery tests through the routes and the worker's
# own tick functions. (The UI screenshots of a FAILED state were produced by a
# disclosed state injection; they are not the evidence for recovery. These are.)
#
# Turning the flag off does not make the synchronous path a safe concurrent
# fallback: its own race between overlapping saves is unchanged. What these
# pin is narrower: accepted work is still finished, and a synchronous save
# cannot run on top of it.


def test_work_accepted_before_the_flag_is_turned_off_is_still_completed(
    client, deferred, monkeypatch
):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = correct(client, issue_id).json()["processing"]["id"]

    monkeypatch.delenv(FLAG, raising=False)
    assert deferred.enabled() is False
    # the readers still say work is outstanding, and that deferral is now off
    ledger = client.get(
        f"{BASE}/deliveries/{d['intake_id']}/exceptions", headers=headers_for("reviewer")
    ).json()
    assert ledger["disposition_processing"]["pending"] == 1
    assert ledger["disposition_processing"]["reconciliation_current"] is False
    assert ledger["disposition_processing"]["deferred_enabled"] is False

    tick_until_settled(client, deferred, pid)  # the scheduler's own tick, flag OFF
    status = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()
    assert status["processing"]["state"] == "COMPLETED"
    assert status["delivery"]["reconciliation_current"] is True
    after = facts(d, issue_id)
    assert after["record_status"] == "CORRECTED" and after["entity"] is not None
    assert after["corrections"] == 1 and after["snapshots"] == 1


def test_a_synchronous_save_is_refused_while_earlier_work_is_still_pending(
    client, deferred, monkeypatch
):
    """Flag off with a decision still queued: a synchronous save on the same
    delivery would promote and reconcile at the same time as the worker. It is
    refused with its own code and writes nothing; once drained it is accepted."""
    d = fresh_delivery()
    first, second = d["issue_ids"][0], d["issue_ids"][2]
    assert correct(client, first).status_code == 202

    monkeypatch.delenv(FLAG, raising=False)
    refused = correct(client, second, role="qalead")
    assert refused.status_code == 409, refused.text
    assert deferred.PROCESSING_PENDING_CODE in refused.text
    assert deferred.PROCESSING_PENDING_MESSAGE in refused.text
    untouched = facts(d, second)
    assert untouched["resolution"] in (None, "OPEN") and untouched["record_status"] == "HELD"
    assert untouched["corrections"] == 1 and untouched["snapshots"] == 0  # only the first decision

    assert all(o["outcome"] == "COMPLETED" for o in drain(deferred))
    assert facts(d, first)["snapshots"] == 1  # this delivery's earlier decision is done
    accepted = correct(client, second, role="qalead")
    assert accepted.status_code == 200, accepted.text  # synchronous again
    assert "processing" not in accepted.json()
    assert accepted.json()["result"]["re_promoted"] is True
    done = facts(d, second)
    assert done["record_status"] == "CORRECTED" and done["entity"] is not None
    assert done["corrections"] == 2 and done["snapshots"] == 2


def test_with_the_flag_off_a_dead_worker_is_still_reaped_and_the_work_resumes(
    client, deferred, monkeypatch
):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = correct(client, issue_id).json()["processing"]["id"]

    async def _crash_after_claim():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(
                text(
                    "UPDATE rce_disposition_followups SET state='PROCESSING', attempt_count=1, "
                    "batch_id=gen_random_uuid(), heartbeat_at=now() - interval '1 hour' "
                    "WHERE id = CAST(:i AS uuid)"
                ),
                {"i": pid},
            )
            await db.commit()

    run(_crash_after_claim())
    monkeypatch.delenv(FLAG, raising=False)
    run(deferred._poll_tick())  # this row is not queued: its claim died with its worker
    assert facts(d, issue_id)["snapshots"] == 0
    run(deferred._reap_tick())  # the scheduler's own reaper, flag OFF
    tick_until_settled(client, deferred, pid)
    status = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()["processing"]
    assert status["state"] == "COMPLETED" and status["attempt_count"] == 2
    after = facts(d, issue_id)
    assert after["corrections"] == 1 and after["snapshots"] == 1 and after["entity"] is not None


def test_with_the_flag_off_a_failure_after_promotion_committed_is_retried_not_reapplied(
    client, deferred, monkeypatch
):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = correct(client, issue_id).json()["processing"]["id"]
    monkeypatch.delenv(FLAG, raising=False)
    calls = {"n": 0}

    async def fail_reconciliation_once(stage):
        if stage == "RECONCILIATION":
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("synthetic failure after promotion committed")

    monkeypatch.setattr(deferred, "_stage_hook", fail_reconciliation_once)
    run(deferred._poll_tick())  # the queue held only this delivery (see the fixture)
    mid = facts(d, issue_id)
    assert mid["entity"] is not None and mid["snapshots"] == 0  # promotion committed, no snapshot
    status = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()
    assert status["processing"]["state"] == "QUEUED"
    assert status["delivery"]["reconciliation_current"] is False

    run(deferred._poll_tick())
    after = facts(d, issue_id)
    assert after["events"] == mid["events"]  # no second disposition event
    assert after["entity"] == mid["entity"]  # no second entity
    assert after["corrections"] == 1 and after["snapshots"] == 1
    assert (
        client.get(f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")).json()[
            "processing"
        ]["state"]
        == "COMPLETED"
    )


# ── review round 1 (2026-11-19): recovery, retry and ownership edges ─────────


def _processing_for(client, delivery, issue_id):
    listing = client.get(
        f"{BASE}/deliveries/{delivery['intake_id']}/disposition-processing",
        headers=headers_for("reviewer"),
    ).json()
    return [row for row in listing["items"] if row["issue_id"] == issue_id]


async def _row_state(pid):
    from sqlalchemy import text

    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        return (
            await db.execute(
                text(
                    "SELECT state, batch_id::text, error_code FROM rce_disposition_followups "
                    "WHERE id = CAST(:i AS uuid)"
                ),
                {"i": pid},
            )
        ).one()


async def _add_finding(issue_id, **values):
    """A second HIGH finding on the SAME record as `issue_id`."""
    from app.core.database import async_session_maker
    from app.tefca_registry.rce import models as m

    async with async_session_maker() as db:
        first = await db.get(m.RceIssue, uuid.UUID(issue_id))
        extra = m.RceIssue(
            issue_code=f"DQ-{uuid.uuid4().hex[:10]}",
            source_intake_id=first.source_intake_id,
            source_record_id=first.source_record_id,
            run_id=first.run_id,
            rule_id="NAME-001",
            rule_version="1.2.0",
            issue_type="NAME_MISSING",
            severity="HIGH",
            field_name="name",
            original_value=None,
            correction_authority="HUMAN_REQUIRED",
            description="second synthetic finding",
        )
        for key, value in values.items():
            setattr(extra, key, value)
        db.add(extra)
        await db.commit()
        return str(extra.id)


def test_a_failed_decision_is_taken_into_the_next_run_with_its_own_attribution(
    client, deferred, monkeypatch
):
    """Reviewer A's processing exhausted its retries. Reviewer B then decides
    another finding on the same delivery. B's run promotes the whole delivery,
    A's released record included: A's row must be part of that run (released,
    promoted as A, covered by the snapshot) - neither left FAILED behind a
    promoted record nor promoted as SYSTEM."""
    d = fresh_delivery()
    a, b = d["issue_ids"][0], d["issue_ids"][2]
    pid_a = correct(client, a).json()["processing"]["id"]

    async def always_fail(stage):
        if stage == "PROMOTION":
            raise RuntimeError("synthetic outage")

    monkeypatch.setattr(deferred, "_stage_hook", always_fail)
    for _ in range(deferred.MAX_ATTEMPTS):
        run(deferred.process_next())
    assert state_of(client, pid_a) == "FAILED"
    assert facts(d, a)["entity"] is None

    monkeypatch.setattr(deferred, "_stage_hook", None)
    pid_b = correct(client, b, role="qalead").json()["processing"]["id"]
    results = drain(deferred)
    assert [x["outcome"] for x in results] == ["COMPLETED"] and results[0]["completed"] == 2
    assert state_of(client, pid_a) == "COMPLETED" and state_of(client, pid_b) == "COMPLETED"
    for issue, role in ((a, "reviewer"), (b, "qalead")):
        got = facts(d, issue)
        assert got["record_status"] == "CORRECTED" and got["entity"] is not None
        assert got["events"][-1][2] == "HUMAN"
        assert got["events"][-1][3] == user_for(role)["email"]
    snap = facts(d)["snapshot"]
    assert snap["passed"] is True and snap["followups"]["decisions_covered"] == 2
    assert sorted(snap["followups"]["followup_ids"]) == sorted([pid_a, pid_b])


def test_a_failed_run_does_not_complete_rows_it_never_owned(client, deferred):
    """Completion writes only rows still PROCESSING in the completing batch: a
    row the reaper has taken back stays as the reaper left it."""
    d = fresh_delivery()
    pid = correct(client, d["issue_ids"][0]).json()["processing"]["id"]

    async def _claim_then_reap_then_complete():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        batch = uuid.uuid4()
        async with async_session_maker() as db:
            assert await deferred._claim(db, uuid.UUID(d["intake_id"]), batch) == 1
            stale_rows = (
                (await db.execute(deferred.select(deferred.F).where(deferred.F.batch_id == batch)))
                .scalars()
                .all()
            )
            await db.execute(
                text(
                    "UPDATE rce_disposition_followups SET heartbeat_at = now() - interval '1 hour' "
                    "WHERE id = CAST(:i AS uuid)"
                ),
                {"i": pid},
            )
            await db.commit()
        reaped = await deferred.reap_stale_processing()
        async with async_session_maker() as db:
            # the slow worker, still holding the rows it read, now writes
            written = 0
            for row in stale_rows:
                result = await db.execute(
                    deferred._owned_update(row.id, batch).values(state="COMPLETED")
                )
                written += int(result.rowcount or 0)
            await db.commit()
        return reaped, written

    reaped, written = run(_claim_then_reap_then_complete())
    assert [x["state"] for x in reaped if x["id"] == pid] == ["QUEUED"]
    assert written == 0
    assert run(_row_state(pid))[0] == "QUEUED"
    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]


def test_the_reaper_leaves_a_row_that_was_completed_after_it_was_read(
    client, deferred, monkeypatch
):
    """The reaper reads a silent PROCESSING row; the worker completes it before
    the reaper writes. The reaper's write must find nothing to change."""
    d = fresh_delivery()
    pid = correct(client, d["issue_ids"][0]).json()["processing"]["id"]

    async def _scenario():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        batch = uuid.uuid4()
        async with async_session_maker() as db:
            await deferred._claim(db, uuid.UUID(d["intake_id"]), batch)
            await db.execute(
                text(
                    "UPDATE rce_disposition_followups SET heartbeat_at = now() - interval '1 hour' "
                    "WHERE id = CAST(:i AS uuid)"
                ),
                {"i": pid},
            )
            await db.commit()

        real_now = deferred._now
        calls = {"n": 0}

        def now_then_complete():
            # First call computes the cutoff; the read follows; the second call
            # is made after the read and before the writes. Complete the row
            # there, from another connection, as a finishing worker would.
            calls["n"] += 1
            if calls["n"] == 2:
                import asyncio
                import threading

                def _finish():
                    # Its own driver connection on its own loop: independent of
                    # the application's engine, like a separate worker process.
                    import os

                    import asyncpg

                    async def _go():
                        dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
                        conn = await asyncpg.connect(dsn)
                        try:
                            await conn.execute(
                                "UPDATE rce_disposition_followups SET state = 'COMPLETED', "
                                "completed_at = now() WHERE id = $1::uuid",
                                pid,
                            )
                        finally:
                            await conn.close()

                    asyncio.run(_go())

                worker = threading.Thread(target=_finish)
                worker.start()
                worker.join()
            return real_now()

        monkeypatch.setattr(deferred, "_now", now_then_complete)
        try:
            return await deferred.reap_stale_processing()
        finally:
            monkeypatch.setattr(deferred, "_now", real_now)

    reaped = run(_scenario())
    assert [x for x in reaped if x["id"] == pid] == []
    state, _batch, _code = run(_row_state(pid))
    assert state == "COMPLETED"


def test_a_retry_that_lost_the_race_changes_nothing(client, deferred, monkeypatch):
    """Two retries read the same FAILED row; a worker claims it after the
    first. The second must not put it back to QUEUED or clear the claim."""
    d = fresh_delivery()
    pid = correct(client, d["issue_ids"][0]).json()["processing"]["id"]

    async def always_fail(stage):
        if stage == "PROMOTION":
            raise RuntimeError("synthetic outage")

    monkeypatch.setattr(deferred, "_stage_hook", always_fail)
    for _ in range(deferred.MAX_ATTEMPTS):
        run(deferred.process_next())
    assert state_of(client, pid) == "FAILED"
    monkeypatch.setattr(deferred, "_stage_hook", None)

    async def _second_retry_after_a_claim():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as late:
            seen = await late.get(deferred.F, uuid.UUID(pid))  # reads FAILED
            assert seen.state == "FAILED"
            async with async_session_maker() as other:
                await other.execute(
                    text(
                        "UPDATE rce_disposition_followups SET state = 'PROCESSING', "
                        "batch_id = CAST(:b AS uuid), heartbeat_at = now() "
                        "WHERE id = CAST(:i AS uuid)"
                    ),
                    {"i": pid, "b": str(uuid.uuid4())},
                )
                await other.commit()
            with pytest.raises(deferred.FollowupRefused):
                await deferred.retry(late, uuid.UUID(pid), actor="late@test.local")

    run(_second_retry_after_a_claim())
    state, batch, _code = run(_row_state(pid))
    assert state == "PROCESSING" and batch is not None

    async def _hand_back():
        from sqlalchemy import text

        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(
                text(
                    "UPDATE rce_disposition_followups SET state = 'QUEUED', batch_id = NULL "
                    "WHERE id = CAST(:i AS uuid)"
                ),
                {"i": pid},
            )
            await db.commit()

    run(_hand_back())
    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]


def test_a_decision_that_was_never_recorded_cannot_be_retried_into_completed(client, deferred):
    """A request died after committing PROPOSED only. Its row is FAILED
    `DECISION_INTERRUPTED`. Retrying it would let the worker report COMPLETED
    for a correction that was never applied: refused, and not offered."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = run(_orphan_received(d, issue_id, resolution="PROPOSED"))
    swept = run(deferred.sweep_received())
    assert [x["state"] for x in swept if x["id"] == pid] == ["FAILED"]
    row = client.get(
        f"{BASE}/disposition-processing/{pid}", headers=headers_for("reviewer")
    ).json()["processing"]
    assert row["error_code"] == "DECISION_INTERRUPTED" and row["can_retry"] is False

    retry = client.post(
        f"{BASE}/disposition-processing/{pid}/retry", headers=headers_for("reviewer")
    )
    assert retry.status_code == 409
    assert deferred.RETRY_REFUSED_NOT_RECORDED in retry.text
    assert state_of(client, pid) == "FAILED"
    assert drain(deferred) == []
    after = facts(d, issue_id)
    assert (
        after["record_status"] == "HELD" and after["corrections"] == 0 and after["entity"] is None
    )


def test_an_error_after_the_decision_was_recorded_still_queues_its_delivery_work(
    client, deferred, monkeypatch
):
    """The correction commits and the finding is RESOLVED; then the request
    fails on its last read. The decision stands, so its delivery work is
    queued (recovered) - not left FAILED with nobody to process it - and the
    correction is not applied a second time."""
    from app.tefca_registry.rce import dispositions as disp

    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    real = disp.history_for_record
    calls = {"n": 0}

    async def fail_on_the_final_read(db, record_id, *a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("synthetic failure after the decision committed")
        return await real(db, record_id, *a, **k)

    monkeypatch.setattr(disp, "history_for_record", fail_on_the_final_read)
    r = correct(client, issue_id)
    monkeypatch.setattr(disp, "history_for_record", real)
    assert r.status_code == 500
    assert "synthetic failure" not in r.text
    assert calls["n"] >= 2

    mid = facts(d, issue_id)
    assert mid["resolution"] == "RESOLVED" and mid["corrections"] == 1 and mid["entity"] is None
    rows = _processing_for(client, d, issue_id)
    assert len(rows) == 1
    assert rows[0]["state"] == "QUEUED" and rows[0]["recovered"] is True

    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]
    after = facts(d, issue_id)
    assert after["corrections"] == 1  # applied once
    assert after["record_status"] == "CORRECTED" and after["entity"] is not None
    assert after["events"][-1][2] == "HUMAN"


def test_work_that_arrives_in_the_last_round_is_left_for_the_next_run(
    client, deferred, monkeypatch
):
    """With one drain round allowed, a decision accepted during that round's
    promotion must stay QUEUED: the run never released or promoted it, so it
    must not be marked completed by that run."""
    import asyncio

    d = fresh_delivery()
    first, second = d["issue_ids"][0], d["issue_ids"][2]
    headers_for("qalead")
    pid_first = correct(client, first).json()["processing"]["id"]
    seen = {"n": 0, "pid": None}

    async def accept_second_mid_run(stage):
        if stage == "PROMOTION":
            seen["n"] += 1
            if seen["n"] == 1:
                response = await asyncio.to_thread(correct, client, second, role="qalead")
                seen["pid"] = response.json()["processing"]["id"]

    monkeypatch.setattr(deferred, "MAX_DRAIN_ROUNDS", 1)
    monkeypatch.setattr(deferred, "RECONCILE_SETTLE_ROUNDS", 0)
    monkeypatch.setattr(deferred, "_stage_hook", accept_second_mid_run)
    first_run = run(deferred.process_next())
    monkeypatch.setattr(deferred, "_stage_hook", None)
    assert first_run["drain_rounds"] == 1
    assert seen["pid"] is not None
    assert state_of(client, seen["pid"]) == "QUEUED"  # not swept into the finished run
    assert first_run.get("completed", 0) <= 1
    assert state_of(client, pid_first) in ("COMPLETED", "QUEUED")

    monkeypatch.setattr(deferred, "MAX_DRAIN_ROUNDS", 5)
    monkeypatch.setattr(deferred, "RECONCILE_SETTLE_ROUNDS", 3)
    assert all(x["outcome"] == "COMPLETED" for x in drain(deferred))
    assert state_of(client, seen["pid"]) == "COMPLETED"
    assert state_of(client, pid_first) == "COMPLETED"
    for issue in (first, second):
        got = facts(d, issue)
        assert got["record_status"] == "CORRECTED" and got["entity"] is not None


def test_a_run_whose_lock_connection_died_stops_before_writing(client, deferred, monkeypatch):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    pid = correct(client, issue_id).json()["processing"]["id"]

    async def lost(self):
        return False

    monkeypatch.setattr(deferred.DeliveryLock, "held", lost)
    result = run(deferred.process_next())
    monkeypatch.undo()
    monkeypatch.setenv(FLAG, "true")
    assert result["outcome"] == "RETRY_OR_FAILED"
    after = facts(d, issue_id)
    assert after["entity"] is None and after["snapshots"] == 0  # nothing promoted, no snapshot
    assert run(_row_state(pid))[0] == "QUEUED"
    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]


def test_a_manual_promotion_waits_for_accepted_decisions(client, deferred):
    """The manual promote route would take the released record first and write
    it as SYSTEM. While accepted work is outstanding it is refused; the worker
    then promotes the record under the reviewer who decided it."""
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    assert correct(client, issue_id).status_code == 202
    refused = client.post(
        f"{BASE}/deliveries/{d['intake_id']}/promote", headers=headers_for("reviewer")
    )
    assert refused.status_code == 409, refused.text
    assert deferred.PROCESSING_PENDING_CODE in refused.text
    assert facts(d, issue_id)["entity"] is None

    assert [x["outcome"] for x in drain(deferred)] == ["COMPLETED"]
    after = facts(d, issue_id)
    assert after["entity"] is not None
    assert after["events"][-1][2] == "HUMAN"
    assert after["events"][-1][3] == user_for("reviewer")["email"]


def test_snapshot_approval_is_refused_while_processing_is_outstanding(client, deferred):
    from types import SimpleNamespace

    d = fresh_delivery()
    assert correct(client, d["issue_ids"][0]).status_code == 202

    async def _approve():
        from app.core.database import async_session_maker
        from app.tefca_registry.rce import snapshot_effects

        async with async_session_maker() as db:
            gates = (await snapshot_effects.approval_gates(db, uuid.UUID(d["intake_id"])))["gates"]
            # Make every OLDER gate pass, so the refusal can only be the new one.
            real = snapshot_effects.approval_gates

            async def only_the_new_gate_fails(db_, intake_id):
                g = await real(db_, intake_id)
                g["gates"] = {
                    k: (v if k == "no_disposition_processing_outstanding" else True)
                    for k, v in g["gates"].items()
                }
                return g

            snapshot_effects.approval_gates = only_the_new_gate_fails
            try:
                with pytest.raises(snapshot_effects.ApprovalRefused) as refused:
                    await snapshot_effects.approve_delivery_snapshot(
                        db,
                        uuid.UUID(d["intake_id"]),
                        user=SimpleNamespace(email="qa.lead@test.local", role="qalead"),
                        approval_ref="synthetic",
                        commit=False,
                    )
            finally:
                snapshot_effects.approval_gates = real
            await db.rollback()
            return gates, str(refused.value)

    gates, message = run(_approve())
    assert gates["no_disposition_processing_outstanding"] is False
    assert "delivery processing" in message and "pending or failed" in message
    drain(deferred)


def test_a_release_that_an_unapplied_approval_blocks_is_not_queued(client, deferred):
    """ACCEPT waives finding Y, but the same record carries finding X that is
    APPROVED and unapplied. The record stays HELD, so there is no delivery work
    to queue: reporting COMPLETED for it would be untrue."""
    d = fresh_delivery()
    y = d["issue_ids"][0]
    run(_add_finding(y, resolution="APPROVED", resolved_by="lane-a@test.local"))

    r = client.post(
        f"{BASE}/issues/{y}/dispositions",
        json={"decision": "ACCEPT", "reason": "synthetic waiver"},
        headers=headers_for("reviewer"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["processing"]["state"] == "NOT_REQUIRED"
    assert drain(deferred) == []
    after = facts(d, y)
    assert after["record_status"] == "HELD" and after["entity"] is None


def test_the_same_key_with_a_value_that_differs_only_in_whitespace_is_not_a_replay(
    client, deferred
):
    d = fresh_delivery()
    issue_id = d["issue_ids"][0]
    value = next_npi()
    key = f"idem-{uuid.uuid4().hex}"
    first = correct(client, issue_id, key=key, value=value)
    assert first.status_code == 202, first.text
    again = correct(client, issue_id, key=key, value=value)
    assert again.status_code in (200, 202)  # the same decision: a replay
    different = correct(client, issue_id, key=key, value=value + " ")
    assert different.status_code == 422, different.text
    assert facts(d, issue_id)["corrections"] == 1
    drain(deferred)


def test_an_identifier_decision_naming_only_the_entity_is_scoped_to_its_delivery(
    client, deferred, monkeypatch
):
    """The route allows a decision that names neither a finding nor a line. Its
    delivery is the one of the open conflict it answers, so the flag-off
    pending-work refusal (and the decision slot) apply to it as well."""
    d = fresh_delivery(issues=3)
    conflict = run(_make_conflict(d))
    other = d["issue_ids"][2]  # a still-open finding on another record of this delivery
    assert correct(client, other).status_code == 202  # work pending on this delivery

    monkeypatch.delenv(FLAG, raising=False)
    refused = client.post(
        f"{BASE}/identifier-decisions",
        headers=headers_for("reviewer"),
        json={
            "entity_id": conflict["entity_id"],
            "identifier_type": "npi",
            "decision": "CONFIRM_EXISTING",
            "reason": "registered value confirmed (synthetic)",
        },
    )
    assert refused.status_code == 409, refused.text
    assert deferred.PROCESSING_PENDING_CODE in refused.text
    assert run(_conflict_facts(d, conflict))["decisions"] == 0  # nothing was written
    monkeypatch.setenv(FLAG, "true")
    drain(deferred)
