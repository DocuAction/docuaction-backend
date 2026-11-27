"""OPTION A (PROPOSED - OWNER DECISION REQUIRED): classification may continue while default-routed determinations
await adjudication, and nothing else is relaxed. (2026-11-26)

The one thing that changes: when the ONLY failed reconciliation check is
`reconciliation.ADJUDICATION_CHECK`, the review-cycle gate lets the next classification batch run and reports the
requirement as OPEN - on the response, in the audit trail and in the status wording.

Everything below is exercised with reconciliation UNSTUBBED through a real multi-batch cycle (real intake ->
quality -> curation -> promotion, real `reconcile_delivery` and `persist_snapshot`, real cycle, writer, gatherer,
classifier and shipped rules; sources are recorders; outbound network refused):

* all batches complete with default-routed outcomes, and every response states the live verdict, the open count
  and what the open requirement still blocks;
* `reconcile_delivery`'s output is untouched: same check names, same order, `passed` false;
* ANY other failed check - alone or together with the adjudication one - refuses exactly as before
  (parametrised over every other check); a malformed result and an evaluation error refuse too;
* a snapshot written after the cycle stores passed=false;
* snapshot approval (final acceptance) is refused - by the stored snapshot and by the live evaluation - until
  the determinations are adjudicated, and is allowed afterwards;
* an earlier delivery's unadjudicated reviews on the same entities: the later delivery's cycle proceeds, the
  count includes them, and the response says so;
* the status wording never says "populations do not close", never says passed, reconciled or approvable.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import delivery_dashboard as dash
from app.tefca_registry.rce import delivery_jobs as jobs
from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce import snapshot_effects as se
from app.tefca_registry.rce import status_model
from app.tefca_registry.rce.promotion import promote_delivery
from rce_traceability_support import (  # noqa: F401 - `rolled_back_db` is a fixture
    SYN, make_rows, rolled_back_db, run_quality_and_curation, seed_intake,
)
from test_blank_npi_classified_on_submitted_values_2026_11_25 import _PM
from test_review_cycle_batch_gate_characterization_2026_11_26 import (  # noqa: F401 - `delivery` is a fixture
    BATCH, MEMBERS, _default_routed, delivery, text_entities,
)

pytestmark = pytest.mark.usefixtures("no_outbound_network")

#: THE PIN. The gate's one exception is this exact check name; a renamed check must fail here, not open the gate.
THE_ONE_CHECK = "Every default-routed determination is adjudicated"

#: Every OTHER check `reconcile_delivery` can emit. Any of them failing - alone or beside THE_ONE_CHECK - refuses.
OTHER_CHECKS = [
    "A: every delivered line stored",
    "D == A: every source record curated",
    "C = A − B: eligible population is exact",
    "E == C: every eligible record promoted",
    "E: Area 1 promotion markers agree with Area 2",
    "E: one registry entity per promoted record",
    "F ⊆ E: every verification traces to a promoted entity",
    "Zero orphan Area 2 records",
    "Zero orphan promotions",
    "Zero orphan issues",
    "Zero orphan corrections",
    "Every correction is explained",
    "Every rule evaluated every record",
    "No rule execution failed",
    "Every determination traces to evidence",
    "Every determination cites a rule",
    "Every source record has a current disposition",
    "Disposition equation: Received = Created + Updated + Matched/Unchanged + Held + Rejected + Missing Key "
    "+ Excluded",
    "Zero orphan report links",
    "Unresolved identifier conflicts are all HELD",
    "Snapshot effects completed (PENDING or APPROVED snapshot)",
    "Area 1 raw lines still hash to their intake values",
    "Original delivery file unmodified",
]
QALEAD = SimpleNamespace(id=None, email="qalead@synthetic-test.docuaction.invalid", role="qalead")


async def _run_all_batches(d, intake=None):
    out = []
    for _ in range(MEMBERS):                     # more than enough calls; stops when complete
        out.append(await rc._create_review_cycle(d.db, intake or d.intake, user=_PM, full_population=True,
                                                 batch=BATCH))
        if out[-1]["complete"]:
            break
    return out


async def _audit(db, action):
    return (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == action))).scalars().all()


# ── the pin ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_exception_is_one_named_constant_pinned_to_the_exact_check_string():
    assert reconciliation.ADJUDICATION_CHECK == THE_ONE_CHECK
    assert THE_ONE_CHECK not in OTHER_CHECKS and len(set(OTHER_CHECKS)) == len(OTHER_CHECKS)


def test_gate_view_opens_for_exactly_one_shape_of_failure():
    def view(*failed, extra=()):
        checks = [{"check": n, "passed": n not in failed} for n in [THE_ONE_CHECK, *OTHER_CHECKS]] + list(extra)
        return reconciliation.gate_view(checks)

    assert view(THE_ONE_CHECK)["only_adjudication_open"] is True
    assert view()["only_adjudication_open"] is False                       # nothing failed: not "open", passed
    for other in OTHER_CHECKS:
        assert view(other)["only_adjudication_open"] is False
        assert view(THE_ONE_CHECK, other)["only_adjudication_open"] is False
        assert view(THE_ONE_CHECK, other)["blocking_for_classification"] == [other]
    # malformed entries are failures, never the exception
    assert view(THE_ONE_CHECK, extra=[{"passed": False}])["only_adjudication_open"] is False
    assert view(THE_ONE_CHECK, extra=["not a check"])["only_adjudication_open"] is False
    assert view(THE_ONE_CHECK, extra=[{"check": THE_ONE_CHECK, "passed": False}])["only_adjudication_open"] is False
    assert reconciliation.gate_view(None)["only_adjudication_open"] is False
    assert reconciliation.gate_view([])["only_adjudication_open"] is False


# ── all batches complete, and every call says what is open ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_batch_completes_and_each_response_states_the_open_requirement(delivery):
    d = delivery
    calls = await _run_all_batches(d)
    assert [c["linked_this_call"] for c in calls] == [BATCH] * (MEMBERS // BATCH)
    assert calls[-1]["complete"] is True and calls[-1]["remaining"] == 0
    waiting = len(await _default_routed(d.db, d.entities))
    assert waiting >= 1

    first, last = calls[0], calls[-1]
    assert first["reconciliation_passed_at_gate"] is True              # no review existed at the first gate
    assert first["reconciliation_passed"] is False                     # ... but one awaits adjudication after it
    for call in calls[1:]:
        assert call["reconciliation_passed_at_gate"] is False and call["reconciliation_passed"] is False
    assert last["adjudication_open"] is True and last["default_routed_unadjudicated"] == waiting
    (requirement,) = last["open_requirements"]
    assert requirement["requirement"] == THE_ONE_CHECK and requirement["count"] == waiting
    assert requirement["open"] is True and requirement["approvable"] is False
    assert requirement["classification_may_continue"] is True
    assert requirement["label"] == f"Classification may continue - adjudication open ({waiting})"
    assert requirement["detail"] == (f"{waiting} default-routed (DEFAULT-UNMATCHED) review(s) await an analyst "
                                     f"resolution")
    assert any("approval of the delivery snapshot" in b for b in requirement["still_blocks"])
    assert any("passed=false" in b for b in requirement["still_blocks"])

    # the audit trail: nothing refused; each call that passed the gate with the requirement open says so
    assert await _audit(d.db, "review_cycle_refused") == []
    continued = await _audit(d.db, "review_cycle_continued_adjudication_open")
    assert len(continued) == len(calls) - 1
    for row in continued:
        meta = row.metadata_
        assert meta["reconciliation_passed"] is False and meta["failed_checks"] == [THE_ONE_CHECK]
        assert meta["default_routed_unadjudicated"] >= 1 and meta["open_requirement"]["approvable"] is False
    (created,) = await _audit(d.db, "review_cycle_created")
    assert created.metadata_["reconciliation_passed"] is True          # the live verdict at THAT gate, not a constant


@pytest.mark.asyncio
async def test_reconciliation_itself_is_untouched_and_still_does_not_pass(delivery):
    d = delivery
    await _run_all_batches(d)
    waiting = len(await _default_routed(d.db, d.entities))
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert live["passed"] is False
    assert [c["check"] for c in live["checks"]] == d.check_names       # same checks, same order, nothing added
    assert [c["check"] for c in live["failed_checks"]] == [THE_ONE_CHECK]
    assert all(set(c) == {"check", "passed", "detail"} for c in live["checks"])
    assert live["dimensions"]["verification"]["default_unadjudicated"] == waiting
    assert set(d.check_names) - {THE_ONE_CHECK} <= set(OTHER_CHECKS)   # the list above covers every real check
    assert set(OTHER_CHECKS) - set(d.check_names) <= {"Original delivery file unmodified"}


@pytest.mark.asyncio
async def test_a_snapshot_written_after_the_cycle_stores_passed_false(delivery):
    d = delivery
    await _run_all_batches(d)
    waiting = len(await _default_routed(d.db, d.entities))
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    snapshot = await reconciliation.persist_snapshot(d.db, d.intake, live, job_id=d.job_id, actor=SYN,
                                                     trigger="MANUAL")
    assert snapshot.passed is False and THE_ONE_CHECK in snapshot.failure_reason
    assert snapshot.hash == reconciliation.snapshot_hash(live["equation"], live["dimensions"], live["checks"])
    stored = next(c for c in snapshot.checks if c["check"] == THE_ONE_CHECK)
    assert stored["passed"] is False and stored["detail"].startswith(f"{waiting} default-routed")
    earlier = await d.db.get(type(snapshot), __import__("uuid").UUID(d.snapshot_id))
    assert earlier.passed is True and earlier.sequence == d.snapshot_sequence      # the earlier snapshot is as it was


# ── everything else still refuses ────────────────────────────────────────────────────────────────────────────

def _failing(result, *names):
    out = copy.deepcopy(result)
    seen = {c["check"] for c in out["checks"]}
    for name in names:
        if name not in seen:                                           # the stored-file check is conditional
            out["checks"].append({"check": name, "passed": True, "detail": "synthetic"})
    for c in out["checks"]:
        if c["check"] in names:
            c["passed"] = False
    out["passed"] = False
    out["failed_checks"] = [c for c in out["checks"] if not c["passed"]]
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("with_adjudication", [False, True], ids=["alone", "with_the_adjudication_check"])
@pytest.mark.parametrize("other", OTHER_CHECKS)
async def test_any_other_failed_check_still_refuses_exactly_as_before(delivery, monkeypatch, other,
                                                                      with_adjudication):
    d = delivery
    real = await reconciliation.reconcile_delivery(d.db, d.intake)
    names = (THE_ONE_CHECK, other) if with_adjudication else (other,)
    forced = _failing(real, *names)
    order = [c["check"] for c in forced["checks"] if not c["passed"]]

    async def _reconcile(_db, _intake):
        return forced
    monkeypatch.setattr(reconciliation, "reconcile_delivery", _reconcile)

    with pytest.raises(rc.CycleRefused) as refused:
        await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert str(refused.value) == (
        f"Delivery {d.intake} has not passed reconciliation; a review cycle cannot be drawn from a population "
        f"that does not close. Failed: {', '.join(order)}.")
    assert d.sources.npi_calls == [] and d.sources.name_calls == []            # no source asked
    assert not (await d.db.execute(select(reg.ReviewSample).where(
        reg.ReviewSample.strata_config["source_intake_id"].astext == str(d.intake)))).scalars().all()
    (row,) = await _audit(d.db, "review_cycle_refused")
    assert row.metadata_["failed_checks"] == order
    assert await _audit(d.db, "review_cycle_continued_adjudication_open") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["no_failed_check_named", "checks_missing", "duplicate_adjudication_entry",
                                   "unnamed_failed_entry"])
async def test_a_result_that_does_not_pass_and_is_not_exactly_the_one_exception_refuses(delivery, monkeypatch,
                                                                                         shape):
    d = delivery
    real = await reconciliation.reconcile_delivery(d.db, d.intake)
    forced = copy.deepcopy(real)
    forced["passed"] = False
    if shape == "checks_missing":
        forced.pop("checks")
    elif shape == "duplicate_adjudication_entry":
        forced = _failing(real, THE_ONE_CHECK)
        forced["checks"].append({"check": THE_ONE_CHECK, "passed": False, "detail": "synthetic duplicate"})
    elif shape == "unnamed_failed_entry":
        forced = _failing(real, THE_ONE_CHECK)
        forced["checks"].append({"check": "A check this code has never heard of", "passed": False, "detail": "x"})

    async def _reconcile(_db, _intake):
        return forced
    monkeypatch.setattr(reconciliation, "reconcile_delivery", _reconcile)
    with pytest.raises(rc.CycleRefused, match="has not passed reconciliation"):
        await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert d.sources.npi_calls == [] and d.sources.name_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [{"passed": False, "detail": "no name"}, "not a check"])
async def test_a_failed_entry_with_no_name_stops_the_cycle(delivery, monkeypatch, entry):
    """Not a refusal message - the gate's own (unchanged) line cannot read such an entry and raises. What
    matters here: the cycle does not proceed."""
    d = delivery
    forced = _failing(await reconciliation.reconcile_delivery(d.db, d.intake), THE_ONE_CHECK)
    forced["checks"].append(entry)

    async def _reconcile(_db, _intake):
        return forced
    monkeypatch.setattr(reconciliation, "reconcile_delivery", _reconcile)
    with pytest.raises((KeyError, TypeError)):
        await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert d.sources.npi_calls == [] and d.sources.name_calls == []
    assert await _audit(d.db, "review_cycle_continued_adjudication_open") == []


@pytest.mark.asyncio
async def test_the_check_named_as_failed_with_nothing_counted_open_refuses(delivery, monkeypatch):
    d = delivery
    forced = _failing(await reconciliation.reconcile_delivery(d.db, d.intake), THE_ONE_CHECK)
    assert forced["dimensions"]["verification"]["default_unadjudicated"] == 0

    async def _reconcile(_db, _intake):
        return forced
    monkeypatch.setattr(reconciliation, "reconcile_delivery", _reconcile)
    with pytest.raises(rc.CycleRefused, match=THE_ONE_CHECK):
        await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)


def test_the_delivery_report_prints_the_open_requirement_not_records_reconciled():
    """The Delivery Processing Report's "Did processing finish?" row: an adjudication-open outcome prints its own
    detail; any other Completed - With Exceptions outcome prints what it always did."""
    from pathlib import Path

    from jinja2 import Environment

    template = (Path(__file__).resolve().parents[1] / "app" / "reports" / "templates"
                / "delivery_processing.html").read_text(encoding="utf-8")
    cell = next(line for line in template.splitlines() if "Records reconciled;" in line).strip()
    env = Environment(autoescape=True)
    env.filters["number"] = lambda v: f"{v:,}"
    render = env.from_string(cell).render
    open_outcome, _state = _status(_snapshot(THE_ONE_CHECK, count=3))
    text = render(outcome=open_outcome, findings={"open_high": 0})
    assert "Classification may continue - adjudication open (3)" in text and "Reconciliation has NOT passed" in text
    assert "Records reconciled" not in text
    plain = {"code": "COMPLETED_WITH_EXCEPTIONS", "value": "x", "detail": "d"}
    assert "Records reconciled; 2 HIGH/CRITICAL" in render(outcome=plain, findings={"open_high": 2})


@pytest.mark.asyncio
async def test_an_evaluation_error_still_stops_the_cycle(delivery, monkeypatch):
    d = delivery

    async def _broken(_db, _intake):
        raise RuntimeError("synthetic: reconciliation could not be evaluated")
    monkeypatch.setattr(reconciliation, "reconcile_delivery", _broken)
    with pytest.raises(RuntimeError, match="could not be evaluated"):
        await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    assert d.sources.npi_calls == [] and d.sources.name_calls == []
    assert not (await d.db.execute(select(reg.ReviewSample).where(
        reg.ReviewSample.strata_config["source_intake_id"].astext == str(d.intake)))).scalars().all()


# ── final acceptance stays blocked ───────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_approval_is_refused_until_the_determinations_are_adjudicated(delivery):
    d = delivery
    await se.apply_snapshot_effects(d.db, d.intake, actor=SYN)
    clean = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert clean["passed"] is True
    await reconciliation.persist_snapshot(d.db, d.intake, clean, job_id=d.job_id, actor=SYN, trigger="MANUAL")
    assert (await se.approval_gates(d.db, d.intake))["all"] is True    # approvable BEFORE any review exists

    await _run_all_batches(d)

    # (1) the latest STORED snapshot still says passed (it predates the reviews): the LIVE evaluation refuses
    assert (await se.approval_gates(d.db, d.intake))["gates"]["reconciliation_persisted_passed"] is True
    with pytest.raises(se.ApprovalRefused, match="reconciliation does not pass now") as live_refusal:
        await se.approve_delivery_snapshot(d.db, d.intake, user=QALEAD, approval_ref="REF-OPEN-1")
    assert THE_ONE_CHECK in str(live_refusal.value)

    # (2) a snapshot taken now stores passed=false: the STORED gate refuses as well
    after = await reconciliation.reconcile_delivery(d.db, d.intake)
    snapshot = await reconciliation.persist_snapshot(d.db, d.intake, after, job_id=d.job_id, actor=SYN,
                                                     trigger="MANUAL")
    assert snapshot.passed is False
    assert (await se.approval_gates(d.db, d.intake))["gates"]["reconciliation_persisted_passed"] is False
    with pytest.raises(se.ApprovalRefused, match="has not PASSED"):
        await se.approve_delivery_snapshot(d.db, d.intake, user=QALEAD, approval_ref="REF-OPEN-2")

    # (3) only an analyst resolution opens it: adjudicate every one, snapshot, approve
    for review in await _default_routed(d.db, d.entities):
        review.reviewer_resolution = "confirmed"
    await d.db.flush()
    resolved = await reconciliation.reconcile_delivery(d.db, d.intake)
    assert resolved["passed"] is True
    await reconciliation.persist_snapshot(d.db, d.intake, resolved, job_id=d.job_id, actor=SYN, trigger="MANUAL")
    approved = await se.approve_delivery_snapshot(d.db, d.intake, user=QALEAD, approval_ref="REF-OPEN-3",
                                                  commit=False)
    assert approved["status"] == "APPROVED"


# ── an earlier delivery's backlog on the same entities ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_later_delivery_proceeds_and_its_count_includes_the_earlier_deliverys_reviews(delivery):
    """STATED EXPECTED RESULT: the later delivery's cycle is not refused; the count it reports is the check's own
    (entity-scoped, UNCHANGED) and therefore includes the earlier delivery's unadjudicated reviews; the response
    says so; the later delivery's job ends READY_FOR_REVIEW with reconciliation_passed stored FALSE."""
    d = delivery
    # ONE batch on the earlier delivery. (Owner, 2026-11-27: a FULL-POPULATION frame no longer excludes an entity
    # because an earlier delivery's review set its status to `in_review` - qhin_sampling
    # FULL_POPULATION_ELIGIBILITY_BASIS - so the later delivery's frame is ALL the entities, including those the
    # earlier cycle reached. Statistical draws keep that exclusion.)
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    earlier = len(await _default_routed(d.db, d.entities))
    assert earlier >= 1
    later, later_job = await seed_intake(d.db, make_rows(MEMBERS, arc="9.99.781.1",
                                                         address_line="2 Synthetic Way"))
    later_job_id = later_job.id
    await run_quality_and_curation(d.db, later)
    await promote_delivery(d.db, later, actor=SYN)
    assert set((await d.db.execute(text_entities(later))).scalars().all()) == set(d.entities)

    # the job's own end-of-processing reconciliation: NOT passed, adjudication only
    at_job_end = await reconciliation.reconcile_delivery(d.db, later)
    assert at_job_end["passed"] is False
    assert reconciliation.gate_view(at_job_end["checks"])["only_adjudication_open"] is True
    await reconciliation.persist_snapshot(d.db, later, at_job_end, job_id=later_job_id, actor=SYN,
                                          trigger="PIPELINE")
    await jobs.finish_succeeded(d.db, later_job_id, reconciliation_passed=False,
                                classification_may_continue=True)
    job = await jobs.job_for_intake(d.db, later)
    assert job.stage == "READY_FOR_REVIEW" and job.reconciliation_passed is False

    calls = await _run_all_batches(d, intake=later)
    assert calls[-1]["complete"] is True
    assert calls[0]["default_routed_unadjudicated_at_gate"] == earlier
    assert calls[0]["reconciliation_passed_at_gate"] is False
    total = len(await _default_routed(d.db, d.entities))
    assert calls[-1]["default_routed_unadjudicated"] == total > earlier
    assert sum(c["linked_this_call"] for c in calls) == MEMBERS
    assert "earlier delivery of the same organisation" in calls[-1]["open_requirements"][0]["scope"]


@pytest.mark.asyncio
async def test_a_job_that_did_not_reconcile_for_another_reason_still_stays_at_reconciliation(delivery):
    d = delivery
    later, later_job = await seed_intake(d.db, make_rows(2, arc="9.99.781.7"))
    later_job_id = later_job.id
    await jobs.finish_succeeded(d.db, later_job_id, reconciliation_passed=False)
    job = await jobs.job_for_intake(d.db, later)
    assert job.stage == "RECONCILIATION" and job.reconciliation_passed is False


# ── the wording: never "populations do not close", never passed, reconciled or approvable ────────────────────

def _snapshot(*failed, count=3):
    checks = [{"check": n, "passed": n not in failed, "detail": "d"} for n in [THE_ONE_CHECK, *OTHER_CHECKS]]
    return {"id": "snap-1", "passed": not failed, "failure_reason": "; ".join(failed) or None,
            "equation": {"received": 6, "accounted": 6, "held": 0, "rejected": 0, "missing_key": 0},
            "dimensions": {"verification": {"default_unadjudicated": count if THE_ONE_CHECK in failed else 0}},
            "checks": checks}


def _status(snapshot, **review):
    outcome = status_model.processing_outcome(
        job_state="SUCCEEDED", job_stage="READY_FOR_REVIEW", snapshot=snapshot,
        stages_completed=list(status_model.REQUIRED_STAGES))
    state = status_model.review_state(
        outcome_code=outcome["code"], snapshot_passed=bool(snapshot.get("passed")),
        adjudication_open=outcome.get("adjudication_open"), **review)
    return outcome, state


def test_status_adjudication_only_is_not_partially_processed_and_never_reads_as_reconciled():
    outcome, state = _status(_snapshot(THE_ONE_CHECK, count=3), open_work_items=4)
    assert outcome["code"] == "COMPLETED_WITH_EXCEPTIONS" and outcome["adjudication_open"] == 3
    assert outcome["approvable"] is False and outcome["classification_may_continue"] is True
    assert "Classification may continue - adjudication open (3)" in outcome["detail"]
    assert "Reconciliation has NOT passed" in outcome["detail"] and "cannot be approved" in outcome["detail"]
    assert "do not close" not in outcome["detail"] and "and reconciled" not in outcome["detail"]
    basis = {b["criterion"]: b["held"] for b in outcome["basis"]}
    assert basis["reconciliation_passed"] is False                     # never rewritten
    assert basis["populations_close"] is True and basis["default_routed_determinations_adjudicated"] is False
    assert state["code"] != "NOT_READY" and state["approvable"] is False and state["adjudication_open"] == 3
    assert state["detail"].startswith("Classification may continue - adjudication open (3).")
    assert "Reconciliation has NOT passed" in state["detail"]


def test_status_can_never_read_clean_or_qa_approved_while_adjudication_is_open():
    outcome, state = _status(_snapshot(THE_ONE_CHECK, count=1), qa_approved=5)
    assert outcome["code"] == "COMPLETED_WITH_EXCEPTIONS"
    assert state["code"] == "UNDER_REVIEW" and "still await adjudication" in state["detail"]


@pytest.mark.parametrize("failed", [("E == C: every eligible record promoted",),
                                    (THE_ONE_CHECK, "E == C: every eligible record promoted"),
                                    ("Every determination cites a rule",)])
def test_status_any_other_failure_reads_exactly_as_before(failed):
    outcome, state = _status(_snapshot(*failed), open_work_items=4)
    assert outcome["code"] == "PARTIALLY_PROCESSED" and outcome["detail"].startswith("Reconciliation did not pass")
    assert "adjudication_open" not in outcome
    assert state["code"] == "NOT_READY"


def test_status_a_passing_snapshot_reads_exactly_as_before():
    outcome, state = _status(_snapshot(), open_work_items=4)
    assert outcome["code"] == "COMPLETED_CLEAN" and "adjudication_open" not in outcome
    assert state["code"] == "READY_FOR_ANALYST_REVIEW" and "adjudication_open" not in state


def test_status_other_exceptions_beside_open_adjudication_are_named_and_review_stays_where_a_passed_delivery_would():
    """Held records and unresolved identifier conflicts are EXCEPTIONS, not reconciliation failures: on main a
    delivery whose reconciliation passed with them reads Completed - With Exceptions and stays reviewable. With
    adjudication also open the same holds; the exceptions are named in the outcome, and the delivery is still
    not approvable. (Reviewed 2026-11-26: making this branch stricter than a passed reconciliation would mark
    every real delivery with a held record Not Ready and switch off the cycle.)"""
    snapshot = _snapshot(THE_ONE_CHECK, count=2)
    snapshot["equation"]["held"] = 3
    outcome = status_model.processing_outcome(
        job_state="SUCCEEDED", job_stage="READY_FOR_REVIEW", snapshot=snapshot,
        stages_completed=list(status_model.REQUIRED_STAGES), unresolved_conflicts=1)
    assert outcome["classification_may_continue"] is True and outcome["approvable"] is False
    assert "Other exceptions remain: 3 held; 1 unresolved identifier conflict(s)." in outcome["detail"]
    # ... and the review state matches what a PASSED snapshot with the same exceptions gives, plus the notice
    passed = _snapshot()
    passed["equation"]["held"] = 3
    baseline = status_model.processing_outcome(
        job_state="SUCCEEDED", job_stage="READY_FOR_REVIEW", snapshot=passed,
        stages_completed=list(status_model.REQUIRED_STAGES), unresolved_conflicts=1)
    assert baseline["code"] == outcome["code"] == "COMPLETED_WITH_EXCEPTIONS"
    base_state = status_model.review_state(outcome_code=baseline["code"], snapshot_passed=True, open_work_items=4)
    state = status_model.review_state(outcome_code=outcome["code"], snapshot_passed=False, open_work_items=4,
                                      adjudication_open=outcome["adjudication_open"])
    assert state["code"] == base_state["code"] and state["approvable"] is False
    assert state["detail"].endswith(base_state["detail"])


def test_status_a_failed_snapshot_without_stored_checks_is_not_given_the_benefit_of_the_doubt():
    snapshot = _snapshot(THE_ONE_CHECK)
    snapshot["checks"] = []
    outcome, state = _status(snapshot)
    assert outcome["code"] == "PARTIALLY_PROCESSED" and state["code"] == "NOT_READY"


@pytest.mark.asyncio
async def test_dashboard_names_the_state_and_keeps_the_cycle_available(delivery):
    d = delivery
    await rc._create_review_cycle(d.db, d.intake, user=_PM, full_population=True, batch=BATCH)
    waiting = len(await _default_routed(d.db, d.entities))
    job = await jobs.job_for_intake(d.db, d.intake)
    job.state = "SUCCEEDED"
    live = await reconciliation.reconcile_delivery(d.db, d.intake)
    status = dash._status(job, live)
    assert status["state"] == "ADJUDICATION_OPEN" and status["reconciliation_passed"] is False
    assert status["approvable"] is False and status["adjudication_open"] == waiting
    assert status["detail"].startswith(f"Classification may continue - adjudication open ({waiting}).")
    assert "do not close" not in status["detail"] and "has NOT passed" in status["detail"]
    actions = dash._actions(status, live, {})
    assert actions[dash.ACTION_CREATE_REVIEW_CYCLE]["available"] is True
    assert dash._stages(job, live, {})["RECONCILIATION"]["state"] == "ADJUDICATION_OPEN"

    other = copy.deepcopy(live)
    other["checks"][0]["passed"] = False                                # a population check fails as well
    refused = dash._status(job, other)
    assert refused["state"] == "RECONCILIATION_FAILED"
    assert dash._actions(refused, other, {})[dash.ACTION_CREATE_REVIEW_CYCLE]["available"] is False
    assert dash._stages(job, other, {})["RECONCILIATION"]["state"] == "FAIL"
