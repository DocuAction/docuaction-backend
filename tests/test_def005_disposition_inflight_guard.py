"""DEF-005 §7-8 — one disposition chain per finding at a time, terminal
refusal preserved before any side effect.

Incident ffb44190-dae2-453c-a7b9-9b4b34dd5561 (2026-09-28): a hold-releasing
disposition on the 24,589-record September delivery ran 75.8 s server-side
(full re-promotion + reconciliation, committed in stages) while the client
aborted at 30 s. The terminal-finding 409 protects only after the resolution
transition commits; the advisory-lock guard added here closes the in-flight
window so a duplicate can never start a SECOND promotion/reconciliation chain.

Proven here:
  1. the second guard acquisition for the SAME finding is refused 409 with the
     fixed operator message, and the lock is released on exit;
  2. two findings do not block each other;
  3. two CONCURRENT duplicates race: exactly one proceeds;
  4. the route refuses 409 in-flight while the lock is held elsewhere, and
     gets PAST the guard once it is released;
  5. a terminal (settled) finding is refused with TERMINAL_FINDING_MESSAGE
     before any side effect — no disposition event, resolution unchanged;
  6. the route body actually holds the guard (source pin, so a refactor
     cannot silently drop it);
  7. the refusal message carries no SQL, credentials or record values.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import _normalize_url
from app.tefca_registry.rce import curation
from app.tefca_registry.rce import delivery_routes as dr

pytestmark = pytest.mark.asyncio


# ── 1-3. the guard itself ────────────────────────────────────────────────────

async def test_second_acquisition_for_the_same_finding_is_refused(db_required):
    issue_id = uuid.uuid4()
    async with dr._issue_disposition_guard(issue_id):
        with pytest.raises(HTTPException) as refused:
            async with dr._issue_disposition_guard(issue_id):
                pass  # pragma: no cover - must not be reached
        assert refused.value.status_code == 409
        assert refused.value.detail == dr.DISPOSITION_IN_FLIGHT_MESSAGE
    # released on exit: acquirable again
    async with dr._issue_disposition_guard(issue_id):
        pass


async def test_different_findings_do_not_block_each_other(db_required):
    async with dr._issue_disposition_guard(uuid.uuid4()):
        async with dr._issue_disposition_guard(uuid.uuid4()):
            pass


async def test_concurrent_duplicates_exactly_one_proceeds(db_required):
    issue_id = uuid.uuid4()
    outcomes = []

    async def attempt():
        try:
            async with dr._issue_disposition_guard(issue_id):
                await asyncio.sleep(0.3)   # long enough for the loser to arrive
                outcomes.append("proceeded")
        except HTTPException as exc:
            outcomes.append(exc.status_code)

    await asyncio.gather(attempt(), attempt())
    assert sorted(outcomes, key=str) == [409, "proceeded"]


# ── 4. the route behind the guard ────────────────────────────────────────────

@pytest.fixture
async def seeded_issue(db_required):
    """A minimal OPEN issue on a minimal intake. No source record on purpose:
    once a request gets PAST the guard, apply_disposition answers with its own
    'names no source record' refusal — a different 409 than the in-flight one,
    which is exactly how the test tells the two apart."""
    engine = create_async_engine(_normalize_url(os.environ["DATABASE_URL"]),
                                 poolclass=NullPool)
    intake_id, issue_id = uuid.uuid4(), uuid.uuid4()
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text("""
            INSERT INTO rce_source_intakes (id, original_filename, storage_path, sha256,
                file_size_bytes, headers, schema_fingerprint)
            VALUES (:i, 'syn.csv', '/dev/null', :h, 1, '["h"]'::jsonb, 'fp')"""),
            {"i": intake_id, "h": uuid.uuid4().hex.ljust(64, "0")})
        await db.execute(text("""
            INSERT INTO rce_issues (id, issue_code, source_intake_id, rule_id, issue_type,
                severity, correction_authority, description, resolution)
            VALUES (:id, :code, :i, 'RULE-SYN', 'QUALITY', 'HIGH', 'NO_CORRECTION',
                    'synthetic DEF-005 guard finding', 'OPEN')"""),
            {"id": issue_id, "code": f"SYN-DEF005-{uuid.uuid4().hex[:8]}", "i": intake_id})
        await db.commit()
    try:
        yield engine, intake_id, issue_id
    finally:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.execute(text("DELETE FROM tefca_reg_audit_log "
                                  "WHERE metadata->>'issue_id' = :id"), {"id": str(issue_id)})
            await db.execute(text("DELETE FROM rce_issues WHERE id = :id"), {"id": issue_id})
            await db.execute(text("DELETE FROM rce_source_intakes WHERE id = :i"), {"i": intake_id})
            await db.commit()
        await engine.dispose()


async def test_route_refuses_409_while_a_disposition_is_in_flight(seeded_issue, client):
    from support_delivery_api import headers_for

    engine, _, issue_id = seeded_issue
    body = {"decision": "ACCEPT", "reason": "SYNTHETIC DEF-005 duplicate attempt"}

    def post():
        return client.post(f"/api/tefca/rce/issues/{issue_id}/dispositions",
                           json=body, headers=headers_for("reviewer"))

    # Hold the same advisory lock the guard takes, from a separate connection —
    # exactly the state while a first disposition is running.
    async with engine.connect() as holder:
        got = (await holder.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"),
            {"k": f"issue-disposition:{issue_id}"})).scalar()
        assert got is True
        blocked = await asyncio.to_thread(post)
        await holder.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"),
                             {"k": f"issue-disposition:{issue_id}"})
    def message_of(response):
        body = response.json()
        # the app's global handler shapes errors {"error", "code", "request_id"}
        return body.get("error") or body.get("detail") or ""

    assert blocked.status_code == 409, blocked.text
    assert message_of(blocked) == dr.DISPOSITION_IN_FLIGHT_MESSAGE
    # MQA-2026-011: the machine code names THIS refusal, not the generic 409
    # word — a client distinguishes "wait" from "settled" without parsing prose.
    assert blocked.json()["code"] == dr.DISPOSITION_IN_FLIGHT_CODE == "DISPOSITION_IN_FLIGHT"
    assert blocked.json()["request_id"]

    # Released: the same POST now gets PAST the guard, to apply_disposition's
    # own refusal for this record-less finding — a different message AND a
    # different (generic) code.
    after = await asyncio.to_thread(post)
    assert after.status_code == 409, after.text
    assert message_of(after) != dr.DISPOSITION_IN_FLIGHT_MESSAGE
    assert after.json()["code"] != dr.DISPOSITION_IN_FLIGHT_CODE
    assert "names no source record" in message_of(after)


# ── 5. terminal refusal before side effects (§8) ─────────────────────────────

async def test_a_settled_finding_is_refused_before_any_side_effect(seeded_issue):
    engine, intake_id, issue_id = seeded_issue
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text("UPDATE rce_issues SET resolution = 'RESOLVED' WHERE id = :id"),
                         {"id": issue_id})
        await db.commit()
        events_before = (await db.execute(text(
            "SELECT count(*) FROM rce_disposition_events WHERE intake_id = :i"),
            {"i": intake_id})).scalar()
        with pytest.raises(curation.CorrectionRefused) as refused:
            await curation.apply_disposition(
                db, issue_id, decision="accept",
                reason="SYNTHETIC DEF-005 terminal attempt", actor="SYN")
        assert str(refused.value) == curation.TERMINAL_FINDING_MESSAGE
        await db.rollback()
        events_after = (await db.execute(text(
            "SELECT count(*) FROM rce_disposition_events WHERE intake_id = :i"),
            {"i": intake_id})).scalar()
        resolution = (await db.execute(text(
            "SELECT resolution FROM rce_issues WHERE id = :id"), {"id": issue_id})).scalar()
    assert events_after == events_before == 0
    assert resolution == "RESOLVED"


# ── 6-7. wiring and hygiene pins ─────────────────────────────────────────────

def test_the_route_actually_holds_the_guard():
    source = inspect.getsource(dr.post_issue_disposition)
    assert "async with _issue_disposition_guard(issue.id):" in source, (
        "the disposition route no longer holds the DEF-005 in-flight guard")


def test_the_refusal_message_is_fixed_and_carries_nothing_sensitive():
    import re

    m = dr.DISPOSITION_IN_FLIGHT_MESSAGE
    assert not re.search(r"SELECT |INSERT |UPDATE |DELETE |pg_|password|token|secret", m, re.I)
    assert "already being processed" in m
