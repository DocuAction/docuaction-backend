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
    monkeypatch.setenv(FLAG, "true")
    from app.tefca_registry.rce import disposition_followups as followups

    monkeypatch.setattr(followups, "_stage_hook", None)
    return followups


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
