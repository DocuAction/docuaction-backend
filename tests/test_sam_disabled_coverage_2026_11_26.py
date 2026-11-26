"""Coverage and reports: "not attempted - disabled for this run" is its own count (2026-11-26).

Owner: "For #251, distinguish disabled/not-attempted from attempted/unavailable in coverage and reports."

While `SAM_API_LIVE_ATTEMPTS_DISABLED` is on, each record still gets a SAM_GOV evidence row (disposition
UNAVAILABLE, failure class NOT_ATTEMPTED_DISABLED). Such a row is NOT counted as attempted and NOT counted as
unavailable anywhere coverage is counted; it has its own count and label. Every other row is counted as before.

The same seven-record synthetic batch, through the real bulk writer and the real coverage queries.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.Tefca import sam_screening
from app.reports.data import delivery_processing_data as dpd
from app.reports.data import verification_drilldown as drill
from app.tefca_registry import models as reg
from app.tefca_registry import review_cycle as rc
from app.tefca_registry.rce import models as m
from app.tefca_registry.rce import prior_risk
from app.tefca_registry.rce import verification_coverage as vc
from rce_traceability_support import rolled_back_db  # noqa: F401 - fixture
from test_blank_npi_classified_on_submitted_values_2026_11_25 import _PM, _give_qhin, _real_cycle
from test_mandatory_sam_policy_cases_2026_11_26 import configuration  # noqa: F401 - fixture
from test_sam_extract_bulk_run_trace_2026_11_26 import BATCH, EXTRACT_FILE, _run, _today, batch  # noqa: F401

pytestmark = pytest.mark.asyncio

SWITCH = sam_screening.LIVE_API_DISABLED_ENV
KEY = vc.NOT_ATTEMPTED_DISABLED
LABEL = "Not attempted - live API disabled or stopped for this run"
N = len(BATCH)


async def _coverage(b):
    return await vc.coverage_for_intake(b.db, b.intake_id)


async def test_a_disabled_run_is_neither_attempted_nor_unavailable(batch, monkeypatch):
    b = batch
    monkeypatch.setenv(SWITCH, "1")
    await _run(b)
    cov = await _coverage(b)
    sam = cov["sources"]["sam"]
    # THE HEADLINE COUNTS: nothing attempted, nothing unavailable, seven not attempted - disabled.
    assert (sam["eligible"], sam["attempted"], sam["unavailable"], sam[KEY]) == (N, 0, 0, N)
    assert (sam["verified"], sam["not_found"], sam["failed"]) == (0, 0, 0)
    assert sam["not_attempted_disabled_label"] == LABEL and vc.NOT_ATTEMPTED_DISABLED_LABEL == LABEL
    assert sam["note"].startswith(f"{N} entity(ies): live API not attempted - disabled or stopped for this run (each record states which) - not counted "
                                  f"as attempted and not counted as unavailable.")
    assert sam["state"] == "Not Run" and sam["coverage_pct"] == 0.0        # the state vocabulary is unchanged
    # Every OTHER source is counted exactly as it is with the switch off: zero under the new key.
    for name in ("nppes", "leie", "pecos"):
        assert cov["sources"][name][KEY] == 0
    # THE STATE LAYER: eligible = attempted + not_attempted_disabled_only + not_attempted.
    state = cov["source_states"]["sources"]["sam"]
    assert (state["attempted"], state["unavailable"], state["attempted_not_answered"]) == (0, 0, 0)
    assert (state[KEY], state["not_attempted_disabled_only"], state["with_evidence_row"]) == (N, N, N)
    assert state["not_attempted"] == 0                 # "no evidence row at all" - these entities DO have a row
    assert state["attempted"] + state["not_attempted_disabled_only"] + state["not_attempted"] == state["eligible"]
    assert state["not_attempted_disabled_label"] == LABEL
    assert "'not attempted - disabled for this run'" in cov["source_states"]["aggregation_rule"]
    extract = cov["source_states"]["sources"]["sam_extract"]              # the extract screen is still counted
    assert (extract["attempted"], extract[KEY]) == (N, 0)
    # THE LEG BREAKDOWN: its own line, not "unavailable".
    legs = sam["screening_legs"]
    assert legs[KEY] == {"entities": N, "label": LABEL}
    assert (legs["leg_not_recorded"][KEY], legs["leg_not_recorded"]["unavailable"]) == (N, 0)
    assert legs["exclusion"]["unavailable"] == 0 and legs["registration_only"]["unavailable"] == 0
    # THE SQL COUNTS EQUAL THE PYTHON ORACLE, the new key included.
    oracle = vc._tally(await vc._verification_rows(b.db, b.intake_id), await vc._dimension_rows(b.db, b.intake_id))
    counts = await vc.coverage_counts(b.db, b.intake_id)
    for source in vc.SOURCES:
        assert counts[source] == {k: len(v) for k, v in oracle[source].items()}, source
    # THE DRILL-DOWN LISTS: seven under the new outcome, none under unavailable.
    listed = await drill.list_outcome_entities(b.db, str(b.intake_id), source="sam", outcome=KEY)
    assert listed["total"] == N and len(listed["items"]) == N
    assert (await drill.list_outcome_entities(b.db, str(b.intake_id), source="sam", outcome="unavailable"))[
        "total"] == 0
    with pytest.raises(drill.UnknownSourceOrOutcome, match="not_attempted_disabled"):
        await drill.list_outcome_entities(b.db, str(b.intake_id), source="sam", outcome="nonsense")


async def test_attempted_and_unavailable_keep_their_meaning_for_every_other_row(batch, monkeypatch):
    """Switch OFF, API not routing: seven attempted, seven unavailable, none disabled - exactly as on main. Then
    the switch goes ON for a second run of the same records: the earlier attempts are still attempts, and the
    disabled rows are counted beside them, not merged into them."""
    b = batch
    monkeypatch.delenv(SWITCH, raising=False)
    await _run(b)
    sam = (await _coverage(b))["sources"]["sam"]
    assert (sam["attempted"], sam["unavailable"], sam[KEY]) == (N, N, 0)
    assert sam["state"] == "Unavailable" and "disabled for this run" not in (sam.get("note") or "")
    before = await _coverage(b)

    monkeypatch.setenv(SWITCH, "1")
    await _run(b)
    after = await _coverage(b)
    sam = after["sources"]["sam"]
    assert (sam["attempted"], sam["unavailable"], sam[KEY]) == (N, N, N)
    state = after["source_states"]["sources"]["sam"]
    assert (state["attempted"], state["unavailable"], state[KEY], state["not_attempted_disabled_only"]) == (
        N, N, N, 0)
    for name in ("nppes", "leie", "pecos"):                                # other sources: untouched by the key
        for field in ("attempted", "verified", "not_found", "unavailable", "failed"):
            assert after["sources"][name][field] == before["sources"][name][field], (name, field)


async def test_the_delivery_report_states_it_separately(batch, monkeypatch):
    b = batch
    monkeypatch.setenv(SWITCH, "1")
    await _run(b)
    shaped = dpd._normalise_coverage(await _coverage(b))
    sam = next(s for s in shaped["sources"] if s["name"] == "sam")
    assert (sam["attempted"], sam["unavailable"], sam["not_attempted_disabled"]) == (0, 0, N)
    assert sam["screening_legs"][KEY]["entities"] == N
    assert all(s["not_attempted_disabled"] == 0 for s in shaped["sources"] if s["name"] != "sam")
    # the limitations section: its own sentence, and SAM is NOT listed among sources "never attempted"
    limits = []
    builder = SimpleNamespace(limit=limits.append, _SOURCE_LABELS=dpd.DeliveryProcessingDataService._SOURCE_LABELS)
    dpd.DeliveryProcessingDataService._note_coverage_limits(builder, shaped)
    # (the legacy PECOS proxy key has no rows on the bulk path and keeps its own, older "not run" sentence)
    assert len(limits) == 2 and limits[1].startswith(
        "No check was attempted at PECOS (legacy NPPES proxy) (0 attempted, 0% coverage at that source).")
    assert limits[:1] == [
        f"SAM.gov: the live API was not attempted - disabled or stopped for this run - for {N} eligible entity(ies). This is "
        f"not an outage, not a missing key and not a screening result; those entities are not counted as attempted "
        f"or as unavailable at SAM.gov, and this report draws no conclusion from SAM.gov for them."]
    # the printed report: a line of its own under the coverage table and in the leg table
    from jinja2 import Environment
    template = open("app/reports/templates/delivery_processing.html", encoding="utf-8").read()
    assert "not attempted &mdash; disabled or stopped for this run: {{ s.get('not_attempted_disabled')|number }}" in template
    assert "Live API not attempted &mdash; disabled or stopped for this run</th><td>{{ legs.not_attempted_disabled.entities" \
        in template
    assert Environment is not None


async def test_per_record_statements_say_not_attempted(batch, monkeypatch):
    b = batch
    monkeypatch.setenv(SWITCH, "1")
    out = await _run(b)
    clean = out["npi_clean_1"]
    # the B1 record's verification claim: the same gap, with the reason it was not asked
    gaps = prior_risk.exclusion_screening_gaps(clean.results["classifier_input"])
    assert {"control": "sam_gov", "gap": "UNAVAILABLE",
            "not_attempted": "not attempted - disabled for this run"} in gaps
    assert clean.results["verification_claim"]["exclusion_screening_incomplete"] == gaps
    monkeypatch.delenv(SWITCH)
    again = await _run(b)
    assert {"control": "sam_gov", "gap": "UNAVAILABLE"} in prior_risk.exclusion_screening_gaps(
        again["npi_clean_1"].results["classifier_input"])
    # the state word for one row, and the legacy registry report's standing SAM line
    assert vc.evidence_state("dimension", "UNAVAILABLE", not_attempted_disabled=True) == KEY
    assert vc.evidence_state("dimension", "UNAVAILABLE") == "unavailable"
    assert vc.is_not_attempted_disabled("SAM_GOV", {"failure_class": "NOT_ATTEMPTED_DISABLED"}) is True
    assert vc.is_not_attempted_disabled("SAM_GOV", {"failure_class": "NOT_ROUTING"}) is False
    assert vc.is_not_attempted_disabled("OIG_LEIE", {"failure_class": "NOT_ATTEMPTED_DISABLED"}) is False
    from app.tefca_registry import report_generator
    monkeypatch.setenv(SWITCH, "1")
    lines = report_generator.build_limitations({}, [])
    assert any(line.startswith("sam_gov: not attempted - disabled for this run") for line in lines)
    assert not any("returning 404" in line for line in lines)
    monkeypatch.delenv(SWITCH)
    assert any("returning 404" in line for line in report_generator.build_limitations({}, []))


async def test_a_real_cycle_records_the_sam_channel_on_its_audit_row_and_response(batch, monkeypatch):
    """A real review cycle (real plan, members, walk, writer) with the switch on: the response and the cycle's
    audit row both say the live API was not attempted, with the extract edition it relied on."""
    b = batch
    entities = [row.canonical_entity_id for row in (await b.db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == b.intake_id))).scalars().all()]
    await _give_qhin(b.db, entities)
    _real_cycle(monkeypatch, b.db)
    monkeypatch.setenv(SWITCH, "1")
    result = await rc._create_review_cycle(b.db, b.intake_id, user=_PM, full_population=True)
    expected = {"live_api_attempts_disabled": True, "reason": "not attempted - disabled for this run",
                "setting": SWITCH,
                "extract_edition": {"extract_filename": EXTRACT_FILE, "publication_date": _today(),
                                    "extract_sha256": "0" * 64, "list_rows_searched": 160_000}}
    assert result["sam_live_api"] == expected
    assert result["linked_this_call"] == N and result["complete"] is True
    assert [r for r in b.requests if r.host == "api.sam.gov"] == []
    audit = (await b.db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action == "review_cycle_members_verified"))).scalars().all()
    mine = [a for a in audit if (a.metadata_ or {}).get("source_intake_id") == str(b.intake_id)]
    assert mine and mine[-1].metadata_["sam_live_api"] == expected


async def test_a_sam_recheck_request_is_refused_through_the_route(batch, monkeypatch):
    from app.Tefca import source_policy as policy_ids
    from app.core.config import settings
    from app.tefca_registry.rce import preflight_shadow_routes as routes
    from app.tefca_registry.rce.recheck_models import RceRecheckJob
    b = batch
    monkeypatch.setattr(settings, "ENABLE_CONTROLLED_RECHECKS", True, raising=False)
    monkeypatch.setenv(SWITCH, "1")
    request = routes.RecheckRequest(trigger_kind="source_recovery", source_id=policy_ids.SAM_GOV,
                                    trigger_ref="synthetic recovery", rationale="synthetic")
    user = SimpleNamespace(id=uuid.uuid4(), email="pm@synthetic-test.docuaction.invalid", role="program_manager")
    with pytest.raises(HTTPException) as refused:
        await routes.request_recheck_route(str(b.intake_id), request, db=b.db, user=user)
    assert refused.value.status_code == 409
    assert "switched off for this run (SAM_API_LIVE_ATTEMPTS_DISABLED)" in refused.value.detail
    assert "not attempted - disabled for this run" in refused.value.detail
    assert (await b.db.execute(select(RceRecheckJob).where(
        RceRecheckJob.intake_id == b.intake_id))).scalars().all() == []      # no job was created
    assert [r for r in b.requests if r.host == "api.sam.gov"] == []
