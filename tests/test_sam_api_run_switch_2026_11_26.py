"""The run switch: no live SAM API attempts, extract screening unchanged (2026-11-26).

Owner: "Prepare the explicit switch to disable live SAM registration/exclusions API attempts for the November run
while keeping daily-extract screening enabled. Preserve the configured key. Record 'not attempted - disabled for
this run,' not 'key missing' or 'clear.' Verify zero live SAM API requests, unchanged classification under the
active policy, extract freshness enforcement, and filename/date/hash attribution."

`SAM_API_LIVE_ATTEMPTS_DISABLED` (default off). The same instrumented seven-record batch as the trace file, through
the real bulk writer, evidence assembly and connectors under the DEV-active configuration. Synthetic data; nothing
reaches a network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.Tefca import connectors, sam_extract, sam_screening
from app.reports.data import october_review_loader as ld
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import source_policy as sp
from app.tefca_registry.rce import verification_completeness as vc
from rce_traceability_support import rolled_back_db  # noqa: F401 - fixture
from test_mandatory_sam_policy_cases_2026_11_26 import _policy_text, configuration  # noqa: F401 - fixture
from test_sam_extract_bulk_run_trace_2026_11_26 import (  # noqa: F401 - `batch` is a fixture
    B1, BATCH, EXTRACT_FILE, NAME_ONLY, POLICY_B3, UNMATCHED_B3, WITH_NPI, _Client, _retrieved, _run, _today,
    batch,
)

pytestmark = pytest.mark.asyncio

SWITCH = sam_screening.LIVE_API_DISABLED_ENV
KEY = "synthetic-key-not-a-credential"
WORDS = "not attempted - disabled for this run"
#: What a record must never be told while the switch is on.
NEVER = ("api_key not set", "key not set", "key required", "requires sam_gov_api_key", "no_key", "not_routing",
         "served no route", "404", "not found", "no debarment", "clear")


def _sam_requests(b):
    return [r for r in b.requests if r.host == "api.sam.gov"]


def _decisions(out, labels):
    return [(out[x].bucket, out[x].rule, out[x].rule_version, out[x].rationale, out[x].sam_gov, out[x].sam_extract)
            for x in labels]


async def test_the_setting_is_off_by_default_and_read_at_call_time(monkeypatch):
    assert SWITCH == "SAM_API_LIVE_ATTEMPTS_DISABLED"
    monkeypatch.delenv(SWITCH, raising=False)
    assert sam_screening.live_api_attempts_disabled() is False
    for value, expected in (("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("", False),
                            ("false", False)):
        monkeypatch.setenv(SWITCH, value)                       # no re-import: the next call sees it
        assert sam_screening.live_api_attempts_disabled() is expected, value
    assert sam_screening.NOT_ATTEMPTED_DISABLED in sam_screening.FAILURE_CLASSES
    # The reason classifies as its OWN class even though it mentions the key - never NO_KEY, never NOT_ROUTING.
    for text in (sam_screening.LIVE_API_DISABLED_REASON,
                 f"SAM_GOV unavailable: SAM_GOV unavailable: {sam_screening.LIVE_API_DISABLED_REASON}"):
        assert sam_screening.classify_failure(text) == "NOT_ATTEMPTED_DISABLED"
    assert sam_screening.classify_failure("SAM_GOV_API_KEY not set (register a free key at sam.gov)") == "NO_KEY"


async def test_switch_on__no_request_is_made_and_every_decision_is_what_it_was_with_the_api_not_routing(
        batch, monkeypatch):
    b = batch
    # 1. SWITCH OFF, API not routing: today's behaviour - 2 requests per record with an NPI, 3 without.
    monkeypatch.delenv(SWITCH, raising=False)
    off = await _run(b)
    assert len(_sam_requests(b)) == 2 * len(WITH_NPI) + 3 * len(NAME_ONLY) == 16
    for label in WITH_NPI:
        assert (off[label].api_v3, off[label].api_v4) == (1, 1)
    for label in NAME_ONLY:
        assert (off[label].api_v3, off[label].api_v4) == (2, 1)
    assert {off[x].api_row.normalized_values["failure_class"] for x in b.labels} == {"NOT_ROUTING"}

    # 2. SWITCH ON: the same records again.
    b.requests.clear()
    monkeypatch.setenv(SWITCH, "1")
    on = await _run(b)
    assert _sam_requests(b) == []                                        # ZERO requests to api.sam.gov
    assert {r.host for r in b.requests} == {"npiregistry.cms.hhs.gov"}   # the other sources are still asked
    # The key is still configured, still what it was, and the connector still holds it.
    import os
    assert os.environ["SAM_GOV_API_KEY"] == KEY and connectors.SAMGovConnector().api_key == KEY

    # 3. IDENTICAL DECISIONS, record by record: bucket, rule, rule version, rationale text, and both SAM states.
    assert _decisions(on, b.labels) == _decisions(off, b.labels)
    assert [(on[x].bucket, on[x].rule, on[x].rule_version) for x in b.labels] == [
        B1, B1, B1, POLICY_B3, POLICY_B3, UNMATCHED_B3, UNMATCHED_B3]
    policies = await sp.load_active_policies(b.db)
    for label in b.labels:
        f = on[label]
        # A NON-ANSWER: the same state the policy saw before. It never satisfies the requirement by itself -
        assert f.sam_gov == "unavailable" and f.sources["sam_gov"]["not_attempted"] == WORDS
        assert "not_attempted" not in off[label].sources["sam_gov"]
        alone = {"sam_gov": f.sources["sam_gov"]}
        assert sp.apply_source_policy("B1", "RULE-001", alone, {"has_submitted_npi": True}, policies)[:3] == (
            "B3", None, "SOURCE_NOT_COMPLETED_SAM_GOV")
    for label in ("npi_clean_1", "npi_clean_2", "npi_clean_3"):          # - the clear extract screen does
        assert sp.apply_source_policy("B1", "RULE-001", on[label].sources, {"has_submitted_npi": True},
                                      policies) == ("B1", "RULE-001", None, None)
    for label in ("npi_extract_candidate_by_npi", "npi_extract_candidate_by_name"):
        assert on[label].rationale == _policy_text("EXCLUSION_POSSIBLE_MATCH_PENDING", b.configuration)

    # 4. WHAT IS RECORDED about the API: not attempted - disabled for this run. Never a key, a 404 or a result.
    for label in b.labels:
        row = on[label].api_row
        assert (row.source, row.disposition) == ("SAM_GOV", "UNAVAILABLE")
        assert row.normalized_values["failure_class"] == "NOT_ATTEMPTED_DISABLED"
        assert row.normalized_values["outcome"] == "INCOMPLETE"
        cell = ld._evidence_cell("sam", row)
        assert cell == ("UNAVAILABLE (live SAM.gov API not attempted - disabled for this run; not a missing key, "
                        "not an outage, not a screening result)")          # and no "retrieved <date>"
        assert ld._evidence_cell("sam", off[label].api_row) == (
            f"UNAVAILABLE | retrieved {_retrieved(off[label].api_row)}")
        # a stored state the policy treats as INCOMPLETE - never a new disposition, never a new state
        assert on[label].sources["sam_gov"]["disposition"] == "UNAVAILABLE"
        assert on[label].sam_gov in sp.INCOMPLETE_STATES
        # the D3 roll-up does not say the control "could not be reached"
        candidate = on[label].extract_row.disposition == "REVIEW"
        if candidate:                       # a candidate decides the roll-up, with the switch on or off
            assert on[label].d3_rationale == off[label].d3_rationale
            assert on[label].d3_rationale.startswith("At least one exclusion/debarment/revocation control returned")
        else:
            assert "could not be reached" in off[label].d3_rationale
        if candidate:
            pass
        elif label in WITH_NPI:
            assert on[label].d3_rationale == (
                "The live SAM.gov API was not attempted - disabled for this run; the other controls answered and "
                "are reported separately above. Not a finding and not a clearance."), on[label].d3_rationale
        else:
            assert on[label].d3_rationale.startswith(
                "The live SAM.gov API was not attempted - disabled for this run; CMS_REVOCATION could not be")
        for text in (row.note, row.normalized_values["failure_reason"]):
            assert WORDS in text
            for banned in NEVER:
                if banned in ("clear", "not found", "key not set"):
                    continue                    # the reason itself says "not a missing key ... not a screening result"
                assert banned not in text.lower(), (banned, text)
        assert "missing key" in row.note and "SAM_GOV_API_KEY not set" not in row.note
        # the completeness line shown beside the record
        completeness = vc.completeness({"classifier_input": {"sources": on[label].sources}})
        sam_line = next(i for i in completeness["incomplete"] if i["source"] == "sam_gov")
        assert (sam_line["outcome"], sam_line["outcome_label"]) == (
            "UNAVAILABLE", "Not attempted - disabled for this run")
        before = vc.completeness({"classifier_input": {"sources": off[label].sources}})
        assert next(i for i in before["incomplete"] if i["source"] == "sam_gov")["outcome_label"] == (
            "Source unavailable")

    # 5. THE EXTRACT SCREEN is untouched: same results, with filename, publication date, hash and rows searched.
    for label, npi, _listed in BATCH:
        row, was = on[label].extract_row, off[label].extract_row
        assert (row.disposition, row.query_identifier, row.dataset_version_anchor) == (
            was.disposition, was.query_identifier, was.dataset_version_anchor)
        original = row.original_values
        assert (original["extract_filename"], original["publication_date"], original["extract_sha256"],
                original["list_rows_searched"]) == (EXTRACT_FILE, _today(), "0" * 64, 160_000)
        assert f"edition {_today()} ({EXTRACT_FILE}) | sha256 000000000000" in ld._sam_extract_cell(row)


@pytest.mark.parametrize("extract", ["stale", "unavailable", "screening_flag_off"])
async def test_switch_on__golden_comparison_when_the_extract_cannot_answer(batch, monkeypatch, extract):
    """The switch changes NO decision in the cases where the extract cannot answer either: every bucket, rule,
    version and rationale equals the API-not-routing run's, for NPI and NPI-less records alike."""
    b = batch
    if extract == "stale":
        sam_extract._SAM_CACHE["publication_date"] = datetime.now(timezone.utc).date() - timedelta(days=5)
    elif extract == "unavailable":
        b.world.extract = "unavailable"
    else:
        monkeypatch.delenv("ENABLE_SAM_EXTRACT_SCREENING")
    monkeypatch.delenv(SWITCH, raising=False)
    off = await _run(b)
    assert len(_sam_requests(b)) == 16
    b.requests.clear()
    monkeypatch.setenv(SWITCH, "1")
    on = await _run(b)
    assert _sam_requests(b) == []
    assert _decisions(on, b.labels) == _decisions(off, b.labels)
    expected = POLICY_B3
    assert [(on[x].bucket, on[x].rule, on[x].rule_version) for x in b.labels] == (
        [expected] * len(WITH_NPI) + [UNMATCHED_B3] * len(NAME_ONLY))
    for label in WITH_NPI:
        assert on[label].rationale == _policy_text("SOURCE_NOT_COMPLETED_SAM_GOV", b.configuration)


async def test_switch_on__a_stale_extract_still_fails_closed(batch, monkeypatch):
    """Freshness is enforced exactly as before: an edition older than two days is not screened against, and with
    the API not asked either, nothing meets the mandatory requirement - B3, never B1."""
    b = batch
    monkeypatch.setenv(SWITCH, "1")
    stale = datetime.now(timezone.utc).date() - timedelta(days=5)
    sam_extract._SAM_CACHE["publication_date"] = stale
    out = await _run(b)
    assert _sam_requests(b) == []
    for label in b.labels:
        assert out[label].sam_extract == "unavailable"
        assert out[label].extract_row.note.startswith(
            f"SAM_EXTRACT unavailable: stale_source: extract edition {stale.isoformat()} ")
    for label in WITH_NPI:
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == POLICY_B3
        assert out[label].rationale == _policy_text("SOURCE_NOT_COMPLETED_SAM_GOV", b.configuration)
    for label in NAME_ONLY:
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == UNMATCHED_B3


async def test_switch_on__an_unavailable_extract_fails_closed_too(batch, monkeypatch):
    b = batch
    monkeypatch.setenv(SWITCH, "1")
    b.world.extract = "unavailable"
    out = await _run(b)
    assert _sam_requests(b) == []
    for label in WITH_NPI:
        assert (out[label].bucket, out[label].rule, out[label].rule_version) == POLICY_B3
        assert out[label].rationale == _policy_text("SOURCE_NOT_COMPLETED_SAM_GOV", b.configuration)


async def test_switch_on__every_entry_point_of_the_connector_asks_nothing(monkeypatch):
    """Every path that can reach api.sam.gov goes through `SAMGovConnector`: registration by UEI, registration by
    name, exclusions, the combined check, and the health probe. With the switch on none of them makes a request,
    with or without a key; with it off, each does."""
    client = _Client(200)                                  # would answer - if it were ever asked
    monkeypatch.setattr(connectors, "_shared_http_client", lambda: client)
    monkeypatch.setattr(connectors, "_RATE_LIMITERS", {})
    for key in (KEY, None):
        if key:
            monkeypatch.setenv("SAM_GOV_API_KEY", key)
        else:
            monkeypatch.delenv("SAM_GOV_API_KEY")
        monkeypatch.setenv(SWITCH, "1")
        sam = connectors.SAMGovConnector()
        results = [await sam.lookup_by_uei("SYNTHETICUEI1"), await sam.lookup_by_name("SYNTHETIC ORG"),
                   await sam.check_exclusions(uei="SYNTHETICUEI1"), await sam.check_exclusions(legal_name="SYNTHETIC ORG"),
                   await sam.verify(uei="SYNTHETICUEI1"), await sam.verify(legal_name="SYNTHETIC ORG")]
        assert await sam.probe() is False
        assert client.attempts == 0
        for result in results:
            assert result.success is False and result.data is None
            assert sam_screening.classify_failure(result.error) == "NOT_ATTEMPTED_DISABLED", result.error
            assert WORDS in result.error and "SAM_GOV_API_KEY not set" not in result.error
        assert [r.source_name for r in results[:4]] == ["SAM_GOV", "SAM_GOV", "SAM_GOV_EXCLUSIONS",
                                                         "SAM_GOV_EXCLUSIONS"]
    # the status note names the switch, not a key requirement and not a routing fault
    monkeypatch.setenv("SAM_GOV_API_KEY", KEY)

    async def _down(*_a, **_k):
        return False
    manager = connectors.SourceConnectorManager()
    for other in (manager.nppes, manager.leie, manager.pecos):
        monkeypatch.setattr(type(other), "probe", _down)
    status = (await manager.health_check())["SAM_GOV"]
    assert status["live"] is False and client.attempts == 0
    assert WORDS in status["note"] and SWITCH in status["note"] and "the configured key is unchanged" in status["note"]
    assert "requires SAM_GOV_API_KEY" not in status["note"] and "not routing" not in status["note"]

    # SWITCH OFF: the same calls reach the API again.
    monkeypatch.delenv(SWITCH)
    sam = connectors.SAMGovConnector()
    await sam.verify(legal_name="SYNTHETIC ORG")
    assert client.attempts == 2


async def test_switch_on__the_single_record_path_says_the_same_and_asks_nothing(batch, monkeypatch):
    from app.tefca_registry import review_service
    b = batch
    monkeypatch.setenv(SWITCH, "1")
    row = (await b.db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == b.intake_id,
        m.RceCuratedRecord.rce_org_oid == b.refs[0]))).scalars().one()
    entity = await b.db.get(reg.TefcaRegEntity, row.canonical_entity_id)
    envelope = await review_service.run_review(b.db, entity, user=None, source_record_id=row.source_record_id)
    review = (await b.db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.review_id == envelope["review_id"]))).scalars().one()
    assert _sam_requests(b) == []
    sam = review.verification_results["sources"]["sam_gov"]
    assert sam["status"] == "not_checked" and WORDS in sam["reason"]
    # the same marker the bulk path stores, so the completeness line reads the same on both paths
    assert sam["not_attempted"] == WORDS
    line = next(i for i in vc.completeness(review.verification_results)["incomplete"] if i["source"] == "sam_gov")
    assert line["outcome_label"] == "Not attempted - disabled for this run"
    assert "404" not in sam["reason"] and "API key configured" not in sam["reason"]
    # No stored extract evidence yet for this record on this path: still B3, the requirement not completed.
    assert (review.classification_bucket, review.classification_rule) == ("B3", "SOURCE-POLICY-M4")


async def test_switch_on__a_sam_api_recheck_is_refused_and_nothing_else_is(monkeypatch):
    """A source-recovery recheck for SAM_GOV targets every entity whose latest SAM row is UNAVAILABLE - with the
    API not routing or switched off, that is every entity. While the switch is on it would ask nothing and write
    "not attempted" again for each, so requesting or approving one is refused with that reason."""
    from app.Tefca import source_policy as policy_ids
    from app.tefca_registry.rce import rechecks
    monkeypatch.setenv(SWITCH, "1")
    with pytest.raises(rechecks.RecheckRefused, match="switched off for this run"):
        await rechecks.request_recheck(None, None, trigger_kind="source_recovery", source_id=policy_ids.SAM_GOV,
                                       trigger_ref="x", rationale="x", user=None)
    # another source is not refused by the switch (it reaches the next check)
    with pytest.raises(rechecks.RecheckRefused, match="trigger_ref is required"):
        await rechecks.request_recheck(None, None, trigger_kind="source_recovery", source_id=policy_ids.OIG_LEIE,
                                       trigger_ref="", rationale="x", user=None)
    monkeypatch.delenv(SWITCH)
    with pytest.raises(rechecks.RecheckRefused, match="trigger_ref is required"):
        await rechecks.request_recheck(None, None, trigger_kind="source_recovery", source_id=policy_ids.SAM_GOV,
                                       trigger_ref="", rationale="x", user=None)


async def test_switch_on__a_running_sam_recheck_job_writes_nothing(monkeypatch):
    """A SAM job approved BEFORE the switch went on (queued, resumed or reaped) is stopped at the top of every
    batch: nothing is read, nothing is written, the job and its pending items are left as they are."""
    from types import SimpleNamespace
    from app.Tefca import source_policy as policy_ids
    from app.tefca_registry.rce import recheck_models as rm
    from app.tefca_registry.rce import rechecks

    touched = []

    class _Db:
        async def execute(self, *a, **k):
            touched.append("execute")

        async def commit(self):
            touched.append("commit")

    def _job(source_id):
        return SimpleNamespace(id="job", state=rm.STATE_RUNNING, source_id=source_id, processed_count=0,
                               baseline_hash="x", batch_size=5)

    async def _sam_job(_db, _job_id):
        return job
    monkeypatch.setattr(rechecks, "_job_or_refuse", _sam_job)
    monkeypatch.setenv(SWITCH, "1")
    job = _job(policy_ids.SAM_GOV)
    with pytest.raises(rechecks.RecheckRefused, match="switched off for this run"):
        await rechecks.run_batch(_Db(), "job")
    assert touched == [] and job.state == rm.STATE_RUNNING and job.processed_count == 0
    # a job for another source is not stopped by the switch (it goes on to read its baseline)
    job = _job(policy_ids.OIG_LEIE)

    async def _baseline(_db, _job):
        touched.append("baseline")
        raise RuntimeError("reached the baseline read")
    monkeypatch.setattr(rechecks, "_current_baseline", _baseline)
    with pytest.raises(RuntimeError, match="reached the baseline read"):
        await rechecks.run_batch(_Db(), "job")
    assert touched == ["baseline"]


class _CycleDb:
    """The smallest database the top of a review cycle needs (as in test_official_delivery_workflow)."""

    class _None:
        def scalar(self):
            return None

        def scalars(self):
            return self

        def first(self):
            return None

    def __init__(self):
        self.added = []

    async def get(self, model, key):
        return object()

    async def execute(self, *a, **k):
        return self._None()

    async def commit(self):
        pass

    def add(self, row):
        self.added.append(row)


@pytest.mark.parametrize("state,why", [
    ("screening_flag_off", "extract screening is not enabled (ENABLE_SAM_EXTRACT_SCREENING)"),
    ("unavailable", "the extract is unavailable or was refused"),
    ("stale", "stale_source: extract edition "),
    ("ready", None),
])
async def test_interlock__with_the_api_switched_off_a_cycle_needs_a_ready_extract(monkeypatch, state, why):
    """With the live API off, the extract is the only SAM exclusion screen. A cycle is refused - before the
    reconciliation gate, before any sampling - unless extract screening is enabled, loaded and fresh. With the
    switch off, none of this is consulted."""
    import time
    from app.tefca_registry import review_cycle
    from app.tefca_registry.rce import reconciliation
    import app.tefca_registry.qhin_sampling as qs

    drawn, loads = [], []

    async def _not_reconciled(db, intake_id):
        return {"passed": False, "checks": [{"check": "D == A", "passed": False}]}

    async def _must_not_run(*a, **k):
        drawn.append(True)

    async def _load():
        loads.append(True)
        return state != "unavailable"
    monkeypatch.setattr(reconciliation, "reconcile_delivery", _not_reconciled)
    monkeypatch.setattr(qs, "finalize_plan", _must_not_run)
    monkeypatch.setattr(sam_extract, "_load_sam_extract", _load)
    published = datetime.now(timezone.utc).date() - timedelta(days=5 if state == "stale" else 0)
    for key, value in (("publication_date", published), ("row_count", 160_000), ("loaded_at", time.time())):
        monkeypatch.setitem(sam_extract._SAM_CACHE, key, value)
    if state == "screening_flag_off":
        monkeypatch.delenv("ENABLE_SAM_EXTRACT_SCREENING", raising=False)
    else:
        monkeypatch.setenv("ENABLE_SAM_EXTRACT_SCREENING", "true")
    intake = "0b6f1d0e-8a57-4c58-9a44-3f6f6f0d2a12"

    class _User:
        id, email = None, "pm@synthetic-test.docuaction.invalid"

    monkeypatch.setenv(SWITCH, "1")
    with pytest.raises(review_cycle.CycleRefused) as refused:
        await review_cycle._create_review_cycle(_CycleDb(), intake, user=_User())
    if why is None:
        assert "not passed reconciliation" in str(refused.value)        # the interlock let it through
    else:
        text = str(refused.value)
        assert text.startswith("The live SAM.gov API is switched off for this run (SAM_API_LIVE_ATTEMPTS_DISABLED)")
        assert why in text and "No sample was drawn or continued and nothing was classified" in text
    assert drawn == []

    loads.clear()
    monkeypatch.delenv(SWITCH)                                # switch off: the extract is not consulted here at all
    with pytest.raises(review_cycle.CycleRefused, match="not passed reconciliation"):
        await review_cycle._create_review_cycle(_CycleDb(), intake, user=_User())
    assert loads == []


async def test_the_cycle_records_the_sam_switch_state_and_one_extract_edition(monkeypatch):
    """What the cycle records is the SWITCH STATE it ran under - not a claim that a request was made - and, when
    the switch is on, the extract edition captured ONCE when the interlock passed. A later reload of the extract
    does not change what is recorded. (The real-cycle run is in test_sam_disabled_coverage_2026_11_26.py.)"""
    import time
    from datetime import date
    from app.tefca_registry import review_cycle
    for key, value in (("publication_date", date(2026, 11, 25)), ("row_count", 160_000),
                       ("loaded_at", time.time()), ("filename", EXTRACT_FILE), ("sha256", "0" * 64)):
        monkeypatch.setitem(sam_extract._SAM_CACHE, key, value)
    assert review_cycle._sam_live_api_state(False) == {"live_api_attempts_disabled": False}
    captured = sam_extract.loaded_edition()
    assert captured == {"extract_filename": EXTRACT_FILE, "publication_date": "2026-11-25",
                        "extract_sha256": "0" * 64, "list_rows_searched": 160_000}
    # the extract is reloaded mid-cycle with a newer edition ...
    monkeypatch.setitem(sam_extract._SAM_CACHE, "publication_date", date(2026, 11, 26))
    monkeypatch.setitem(sam_extract._SAM_CACHE, "sha256", "f" * 64)
    # ... and the recorded state still names the edition that was captured
    assert review_cycle._sam_live_api_state(True, captured) == {
        "live_api_attempts_disabled": True, "reason": WORDS, "setting": SWITCH, "extract_edition": captured}
    import inspect
    source = inspect.getsource(review_cycle._create_review_cycle)
    assert source.count("sam_extract_edition = _sam_extract.loaded_edition() if sam_api_disabled else None") == 1
    assert source.count('"sam_live_api": _sam_live_api_state(sam_api_disabled, sam_extract_edition)') == 2
