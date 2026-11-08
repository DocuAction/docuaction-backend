"""EX-3 configuration consistency: the four-state matrix, the runtime guard,
and the release-transition processing barrier (release prerequisite,
2026-11-08).

THE FOUR-STATE MATRIX IS ASSERTED AGAINST THE REAL CODE — the real translator
(`arc_pipeline.evidence_item_state`, flag included), the real composition
(`rule_configuration.compose`) and the real classifier
(`BucketClassifier.classify`) — so the table in
`processing_controls`' docstring can never drift from behavior. The guard
tests prove both mismatched combinations REFUSE (never reroute a bucket) on
the actual bulk and manual classification paths BEFORE any write commits, and
that the ordinary inactive baseline (both off) is untouched. The barrier
tests prove enforcement at the entry points and that "drained" is a verified
advisory-lock acquisition, not an absence of visible jobs.

Entirely synthetic data; no Government rows, no network (every refusal fires
before any connector would be reached).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.tefca_registry import models as reg
from app.tefca_registry.bucket_classifier import BucketClassifier
from app.tefca_registry.processing_controls import (
    BARRIER_LOCK_KEY,
    ExclusionConfigurationMismatch,
    ProcessingBarrierActive,
    assert_exclusion_configuration_consistent,
    barrier_status,
    exclusion_states_consumer_active,
    set_barrier,
)
from app.tefca_registry.rce.arc_pipeline import (
    EXCLUSION_DIMENSION,
    PROPOSED_EXCLUSION_STATES_ENV,
    evidence_item_state,
)
from app.tefca_registry.rule_configuration import APPROVAL_EVENT_TYPE, compose
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401

pytestmark = pytest.mark.regression


# ── helpers ──────────────────────────────────────────────────────────────────

def _clean():
    """Classifies B1 under the baseline (and under the composed sets)."""
    return {"sources": {"nppes": "verified", "oig_leie": "clear",
                        "pecos": "verified", "sam_gov": "verified"},
            "fields": {}}


def _with(source, status):
    """The clean input with ONE source's state replaced."""
    results = _clean()
    results["sources"][source] = status
    return results


def _classify(rules, results):
    """Classify one synthetic input under an explicit rule list."""
    return BucketClassifier(rules=[{**r, "version": 99} for r in rules]).classify(results)


def _candidate_state(monkeypatch, *, flag_on: bool) -> str:
    """What the REAL translator emits for an exclusion-dimension REVIEW
    (a pending candidate) under the given flag state."""
    if flag_on:
        monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")
    else:
        monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    return evidence_item_state(EXCLUSION_DIMENSION, "REVIEW")


BASELINE = compose(set())
COMPONENT = compose({"exclusion_candidate_b3"})


class _User:
    def __init__(self, role="admin"):
        """A minimal user object with the given role and a synthetic email."""
        self.role = role
        self.email = f"ex3-guard-test-{role}@synthetic-test.docuaction.invalid"
        self.id = None


# ── 1. the four-state matrix, against the real code ──────────────────────────

def test_state_1_both_off_is_the_deployed_baseline(monkeypatch):
    """flag OFF + component OFF: a candidate collapses to not_found and
    RULE-005 v3 disqualifies it (B4); clean input is B1. Today's behavior."""
    state = _candidate_state(monkeypatch, flag_on=False)
    assert state == "not_found"
    out = _classify(BASELINE, _with("sam_gov", state))
    assert (out.bucket, out.rule_code) == ("B4", "RULE-005")
    out = _classify(BASELINE, _with("oig_leie", state))
    assert (out.bucket, out.rule_code) == ("B4", "RULE-005")
    assert _classify(BASELINE, _clean()).bucket == "B1"


def test_state_2_flag_on_component_off_sam_candidate_would_pass_b1(monkeypatch):
    """flag ON + component OFF (MISMATCH): the hazard the guard exists for —
    a SAM candidate with otherwise-clean sources classifies B1 via RULE-001;
    a LEIE candidate falls to B3-default. (The guard refuses this combination
    on the application paths; this asserts WHAT it is refusing.)"""
    state = _candidate_state(monkeypatch, flag_on=True)
    assert state == "possible_match"
    out = _classify(BASELINE, _with("sam_gov", state))
    assert (out.bucket, out.rule_code) == ("B1", "RULE-001"), \
        "the silent clean pass this guard was built to prevent"
    out = _classify(BASELINE, _with("oig_leie", state))
    assert (out.bucket, out.rule_code) == ("B3", None)


def test_state_3_flag_off_component_on_rule008_unreachable(monkeypatch):
    """flag OFF + component ON (MISMATCH): candidates collapse to not_found,
    RULE-008 never fires — B3-default, so no silent pass, but the approved
    configuration is not in effect. (The guard refuses this combination.)"""
    state = _candidate_state(monkeypatch, flag_on=False)
    assert state == "not_found"
    for source in ("sam_gov", "oig_leie"):
        out = _classify(COMPONENT, _with(source, state))
        assert out.bucket == "B3" and out.rule_code != "RULE-008", (source, out.rule_code)


def test_state_4_both_on_is_the_approved_configuration(monkeypatch):
    """flag ON + component ON: candidate -> B3 via RULE-008; confirmed
    exclusion -> B4 via RULE-005; clean -> B1."""
    state = _candidate_state(monkeypatch, flag_on=True)
    for source in ("sam_gov", "oig_leie"):
        out = _classify(COMPONENT, _with(source, state))
        assert (out.bucket, out.rule_code) == ("B3", "RULE-008"), source
    out = _classify(COMPONENT, _with("oig_leie", "excluded"))
    assert (out.bucket, out.rule_code) == ("B4", "RULE-005")
    assert _classify(COMPONENT, _clean()).bucket == "B1"


def test_release_sequence_non_persisting_checks_under_the_approved_set(monkeypatch):
    """The four checks release-sequence step 6 prescribes, through real logic
    under the approved composed configuration: otherwise-clean SAM candidate
    -> B3; confirmed applicable exclusion -> B4; clean no-candidate input ->
    unchanged (B1); candidate independently meeting the approved Treatment
    rule -> B4 (the Treatment rule, priority 6, wins over RULE-008's B3)."""
    approved = compose({"exclusion_candidate_b3", "treatment_b4_owner_adopted",
                        "structural_npi_handling", "mandatory_source_guards"})
    state = _candidate_state(monkeypatch, flag_on=True)

    out = _classify(approved, _with("sam_gov", state))
    assert (out.bucket, out.rule_code) == ("B3", "RULE-008")

    out = _classify(approved, _with("oig_leie", "excluded"))
    assert (out.bucket, out.rule_code) == ("B4", "RULE-005")

    assert _classify(approved, _clean()).bucket == "B1"

    treating_candidate = _with("sam_gov", state)
    treating_candidate["fields"]["treatment_owner_adopted_missing_npi"] = {"status": True}
    out = _classify(approved, treating_candidate)
    assert (out.bucket, out.rule_code) == ("B4", "RULE-006")


# ── 2. the guard decision, all four combinations ─────────────────────────────

def test_guard_passes_both_off_and_both_on(monkeypatch):
    """The two CONSISTENT combinations pass silently (baseline preserved;
    approved configuration allowed)."""
    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    assert assert_exclusion_configuration_consistent(BASELINE) is None
    monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")
    assert assert_exclusion_configuration_consistent(COMPONENT) is None


def test_guard_refuses_both_mismatches_and_never_reroutes(monkeypatch):
    """Both MISMATCHED combinations raise; the message states refusal, never
    a silent bucket change."""
    monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")
    with pytest.raises(ExclusionConfigurationMismatch) as exc:
        assert_exclusion_configuration_consistent(BASELINE)
    assert "refused" in str(exc.value) and "rerouted" in str(exc.value)

    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    with pytest.raises(ExclusionConfigurationMismatch) as exc:
        assert_exclusion_configuration_consistent(COMPONENT)
    assert "RULE-008 is unreachable" in str(exc.value)


def test_consumer_detection_is_behavioral_not_historical():
    """Only a POSITIVE possible_match clause in the EFFECTIVE list counts.
    A none_of guard alone (exclusion routing unreachable) does not, and the
    decision takes the effective rule list as input — a retired/historical
    RULE-008 row that the loader filtered out can never satisfy it."""
    assert exclusion_states_consumer_active(COMPONENT) is True
    assert exclusion_states_consumer_active(BASELINE) is False
    guards_only = [{"rule_code": "RULE-001", "bucket": "B1", "priority": 10,
                    "name": "g", "conditions": {
                        "all_of": [{"source": "nppes", "status": "verified"}],
                        "none_of": [{"source": "sam_gov", "status": "possible_match"}]}}]
    assert exclusion_states_consumer_active(guards_only) is False
    assert exclusion_states_consumer_active([]) is False
    assert exclusion_states_consumer_active(None) is False


# ── 3. refusal on the actual paths, BEFORE any write commits ─────────────────

async def _review_rows(db) -> int:
    """Total ReviewRecord rows visible to this session."""
    return int((await db.execute(
        select(func.count()).select_from(reg.ReviewRecord))).scalar() or 0)


async def _deactivate_possible_match_consumers(db) -> None:
    """Deterministic precondition: whatever the shared DB holds, no ACTIVE
    rule consumes possible_match (rolled back with the fixture)."""
    rows = (await db.execute(
        select(reg.ReviewRule).where(reg.ReviewRule.is_active.is_(True)))).scalars().all()
    for row in rows:
        if exclusion_states_consumer_active([{"conditions": row.conditions or {}}]):
            row.is_active = False
    await db.flush()


async def _activate_exclusion_component(db) -> None:
    """The REAL governed activation: record the approval, then activate."""
    from app.tefca_registry.rule_configuration import activate_rule_configuration

    approval = reg.TefcaRegAuditLog(
        action=APPROVAL_EVENT_TYPE,
        actor_email="ex3-guard-test-owner@synthetic-test.docuaction.invalid",
        metadata_={"components": ["exclusion_candidate_b3"],
                   "decision": "EX-3/D-16 (synthetic test approval)"})
    db.add(approval)
    await db.flush()
    await activate_rule_configuration(
        db, {"exclusion_candidate_b3": str(approval.id)},
        actor="ex3-guard-test@synthetic-test.docuaction.invalid")


@pytest.mark.asyncio
async def test_bulk_path_refuses_flag_on_component_off_before_any_write(
        rolled_back_db, monkeypatch):
    """Bulk path, mismatch direction 1 (flag on, component off): refuses
    before any ReviewRecord exists."""
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    db = rolled_back_db
    await _deactivate_possible_match_consumers(db)
    monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")
    before = await _review_rows(db)
    with pytest.raises(ExclusionConfigurationMismatch):
        await verify_and_classify(db, ["urn:oid:9.99.777.0.1"])
    assert await _review_rows(db) == before, "refusal must precede any ReviewRecord"


@pytest.mark.asyncio
async def test_bulk_path_refuses_component_on_flag_off_before_any_write(
        rolled_back_db, monkeypatch):
    """Bulk path, mismatch direction 2 (component ACTIVATED through the real
    governed mechanism, flag off): refuses before any ReviewRecord exists."""
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    db = rolled_back_db
    await _activate_exclusion_component(db)
    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    before = await _review_rows(db)
    with pytest.raises(ExclusionConfigurationMismatch):
        await verify_and_classify(db, ["urn:oid:9.99.777.0.1"])
    assert await _review_rows(db) == before


@pytest.mark.asyncio
async def test_bulk_path_baseline_both_off_still_runs(rolled_back_db, monkeypatch):
    """Ordinary inactive baseline is preserved: with both sides off the bulk
    path proceeds (empty input: classifies nothing, refuses nothing)."""
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify

    db = rolled_back_db
    await _deactivate_possible_match_consumers(db)
    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    result = await verify_and_classify(db, [])
    assert result.get("requested") == 0


@pytest.mark.asyncio
async def test_manual_path_refuses_mismatch_before_any_write(
        rolled_back_db, monkeypatch):
    """`run_review` refuses BEFORE its first staged write — no ReviewRecord,
    no TefcaVerification, not even the verification_started audit row."""
    from app.tefca_registry import review_service
    from app.tefca_registry.review_service import run_review

    db = rolled_back_db
    await _deactivate_possible_match_consumers(db)
    # The module classifier caches rules per process; force a fresh load so
    # the guard sees this test's rule state, as a fresh worker would.
    monkeypatch.setattr(review_service._classifier, "_rules", None)
    monkeypatch.setattr(review_service._classifier, "_loaded_at", 0.0)
    monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")

    entity = reg.TefcaRegEntity(
        id=uuid.uuid4(), name="SYNTHETIC EX3 GUARD ORG",
        display_name="SYNTHETIC EX3 GUARD ORG", entity_level="participant",
        entity_type="provider", operational_status="active",
        verification_status="not_verified", current_version=1, is_active=True)
    db.add(entity)
    await db.flush()

    with pytest.raises(ExclusionConfigurationMismatch):
        await run_review(db, entity, user=_User("reviewer"))

    assert int((await db.execute(
        select(func.count()).select_from(reg.ReviewRecord)
        .where(reg.ReviewRecord.entity_id == entity.id))).scalar() or 0) == 0
    assert int((await db.execute(
        select(func.count()).select_from(reg.TefcaVerification)
        .where(reg.TefcaVerification.entity_id == entity.id))).scalar() or 0) == 0
    assert int((await db.execute(
        select(func.count()).select_from(reg.TefcaRegAuditLog)
        .where(reg.TefcaRegAuditLog.entity_id == entity.id,
               reg.TefcaRegAuditLog.action == "verification_started"))).scalar() or 0) == 0


# ── 4. the processing barrier ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_barrier_blocks_bulk_and_manual_paths(rolled_back_db, monkeypatch):
    """Barrier ON refuses both classification paths before any write; lifting
    it (newest event wins) restores processing."""
    from app.tefca_registry import review_service
    from app.tefca_registry.rce.arc_pipeline import verify_and_classify
    from app.tefca_registry.review_service import run_review

    db = rolled_back_db
    await _deactivate_possible_match_consumers(db)
    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    monkeypatch.setattr(review_service._classifier, "_rules", None)
    monkeypatch.setattr(review_service._classifier, "_loaded_at", 0.0)
    await set_barrier(db, on=True, actor_email="t@synthetic-test.docuaction.invalid",
                      reason="release transition (synthetic test)")
    await db.flush()

    with pytest.raises(ProcessingBarrierActive):
        await verify_and_classify(db, [])

    entity = reg.TefcaRegEntity(
        id=uuid.uuid4(), name="SYNTHETIC EX3 BARRIER ORG",
        display_name="SYNTHETIC EX3 BARRIER ORG", entity_level="participant",
        entity_type="provider", operational_status="active",
        verification_status="not_verified", current_version=1, is_active=True)
    db.add(entity)
    await db.flush()
    with pytest.raises(ProcessingBarrierActive):
        await run_review(db, entity, user=_User("reviewer"))

    # Lifting it restores processing (newest event wins).
    await set_barrier(db, on=False, actor_email="t@synthetic-test.docuaction.invalid",
                      reason="lifted (synthetic test)")
    await db.flush()
    result = await verify_and_classify(db, [])
    assert result.get("requested") == 0


@pytest.mark.asyncio
async def test_barrier_blocks_review_cycle_before_its_bookkeeping(
        rolled_back_db, monkeypatch):
    """`create_review_cycle` refuses at its own top — before plan/HELD
    bookkeeping writes — while the barrier is on."""
    from app.tefca_registry.review_cycle import create_review_cycle

    db = rolled_back_db
    intake_id, _job = await seed_intake(db, make_rows(2))
    await set_barrier(db, on=True, actor_email="t@synthetic-test.docuaction.invalid",
                      reason="release transition (synthetic test)")
    await db.flush()
    with pytest.raises(ProcessingBarrierActive):
        await create_review_cycle(db, intake_id, user=_User("admin"))


@pytest.mark.asyncio
async def test_barrier_drain_is_verified_by_the_lock_not_by_job_visibility(
        rolled_back_db):
    """`in_flight_drained` is a real exclusive-lock acquisition: a concurrent
    transaction holding the SHARED lock (exactly what every classification
    transaction holds) makes it False even though no 'job' row exists
    anywhere; releasing it makes it True."""
    import os

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url

    db = rolled_back_db
    engine2 = create_async_engine(
        _normalize_url(os.environ["DATABASE_URL"]), poolclass=NullPool)
    conn2 = await engine2.connect()
    try:
        outer2 = await conn2.begin()
        other = AsyncSession(bind=conn2, join_transaction_mode="create_savepoint",
                             expire_on_commit=False)
        await other.execute(
            text("select pg_advisory_xact_lock_shared(hashtext(:k))"),
            {"k": BARRIER_LOCK_KEY})

        status = await barrier_status(db)
        assert status["in_flight_drained"] is False
        assert status["verified"] is False

        await other.close()
        await outer2.rollback()        # releases the shared lock
    finally:
        await conn2.close()
        await engine2.dispose()

    status = await barrier_status(db)
    assert status["in_flight_drained"] is True


# ── 5. the readback and non-persisting preview routes ────────────────────────

@pytest.mark.asyncio
async def test_classification_config_readback_reports_the_verdict(
        rolled_back_db, monkeypatch):
    """The step-5 readback route reports flag, effective-component state and
    the consistency verdict from live DB + env."""
    from app.tefca_registry.review_routes import classification_config

    db = rolled_back_db
    await _deactivate_possible_match_consumers(db)
    monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")
    out = await classification_config(db)
    assert out["exclusion_states_flag"]["enabled"] is True
    assert out["exclusion_candidate_component_effective"] is False
    assert out["configuration_consistent"] is False
    assert "barrier" in out and "rules" in out

    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    out = await classification_config(db)
    assert out["configuration_consistent"] is True


@pytest.mark.asyncio
async def test_classification_preview_runs_real_logic_and_writes_nothing(
        rolled_back_db, monkeypatch):
    """The step-6 preview route classifies through the real translator and
    effective rules and leaves ReviewRecord and audit counts untouched."""
    from app.tefca_registry.review_routes import ClassificationPreview, classification_preview
    from app.tefca_registry.rce.arc_pipeline import _rule_set

    db = rolled_back_db
    await _rule_set(db)                      # ensure baseline rules exist
    await _deactivate_possible_match_consumers(db)
    monkeypatch.delenv(PROPOSED_EXCLUSION_STATES_ENV, raising=False)
    reviews = await _review_rows(db)
    audits = int((await db.execute(
        select(func.count()).select_from(reg.TefcaRegAuditLog))).scalar() or 0)

    out = await classification_preview(
        ClassificationPreview(results=_clean()), db, user=_User("admin"))
    assert out["persisted"] is False
    assert out["classification"]["bucket"] == "B1"

    # Evidence form: the REAL translator runs, flag included.
    monkeypatch.setenv(PROPOSED_EXCLUSION_STATES_ENV, "1")
    out = await classification_preview(
        ClassificationPreview(evidence={"dimensions": [{
            "dimension": EXCLUSION_DIMENSION, "disposition": "REVIEW",
            "applicability": "APPLICABLE",
            "evidence": [{"source": "SAM_GOV", "disposition": "REVIEW"}]}]}),
        db, user=_User("admin"))
    assert out["classifier_input"]["sources"]["sam_gov"]["status"] == "possible_match"
    assert out["configuration"]["configuration_consistent"] is False

    assert await _review_rows(db) == reviews
    assert int((await db.execute(
        select(func.count()).select_from(reg.TefcaRegAuditLog))).scalar() or 0) == audits


@pytest.mark.asyncio
async def test_barrier_route_requires_the_exact_confirm_phrase(rolled_back_db):
    """The admin barrier route refuses a wrong confirm phrase and round-trips
    on -> status -> off."""
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.tefca_registry.review_routes import (SetProcessingBarrier,
                                                  get_processing_barrier,
                                                  set_processing_barrier)

    db = rolled_back_db
    request = Request({"type": "http", "method": "POST", "path": "/",
                       "headers": [], "query_string": b"",
                       "client": ("127.0.0.1", 0), "scheme": "http",
                       "server": ("testserver", 80)})

    with pytest.raises(HTTPException) as exc:
        await set_processing_barrier(
            SetProcessingBarrier(state="on", confirm="wrong", reason="r"),
            request, db, user=_User("admin"))
    assert exc.value.status_code == 400

    status = await set_processing_barrier(
        SetProcessingBarrier(state="on", confirm="PROCESSING-BARRIER-ON",
                             reason="release transition (synthetic test)"),
        request, db, user=_User("admin"))
    assert status["state"] == "on"

    status = await get_processing_barrier(db)
    assert status["state"] == "on"
    assert "lock" in status["note"]

    status = await set_processing_barrier(
        SetProcessingBarrier(state="off", confirm="PROCESSING-BARRIER-OFF",
                             reason="lifted (synthetic test)"),
        request, db, user=_User("admin"))
    assert status["state"] == "off"
