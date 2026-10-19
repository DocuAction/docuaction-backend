"""Phase 7 — release gates, report metrics, and the words a report may not use.

Most of these assert a REFUSAL again. A reporting system's value is not that it
can produce a number; it is that it cannot quietly produce a number that outruns
the evidence, and cannot label a draft as releasable.
"""
from __future__ import annotations

from app.reports.release_gates import (
    DRAFT_WATERMARK, RELEASE_LABEL, Gate, evaluate)
from app.Tefca.evidence_version import current_rule_version


def _all_open(**over):
    """Every gate open unless a test deliberately closes one."""
    kw = dict(evidence_rule_version=current_rule_version(),
              qa_approved_findings=0, asserted_findings=0,
              methodology_pending_ids=None, asserts_conclusion_on_pending=False,
              provenance_documented=True, report_rendered=True,
              render_errors=None)
    kw.update(over)
    return evaluate(**kw)


class TestReleaseGates:

    def test_all_five_gates_are_evaluated_every_time(self):
        d = _all_open()
        assert {r.gate for r in d.results} == set(Gate)

    def test_all_gates_open_yields_a_releasable_report(self):
        d = _all_open()
        assert d.is_cor_releasable is True
        assert d.label == RELEASE_LABEL

    def test_superseded_evidence_closes_the_evidence_gate(self):
        d = _all_open(evidence_rule_version="phase6-bulk-1.0.0")
        assert d.is_cor_releasable is False
        assert Gate.EVIDENCE in {r.gate for r in d.closed}

    def test_an_unknown_evidence_version_closes_the_gate(self):
        d = _all_open(evidence_rule_version="something-made-up")
        assert Gate.EVIDENCE in {r.gate for r in d.closed}

    def test_asserting_findings_without_qa_closes_the_qa_gate(self):
        d = _all_open(asserted_findings=5, qa_approved_findings=2)
        assert Gate.HUMAN_QA in {r.gate for r in d.closed}

    def test_asserting_no_findings_needs_no_qa(self):
        """A population report of observations asserts nothing a human must approve."""
        d = _all_open(asserted_findings=0, qa_approved_findings=0)
        assert Gate.HUMAN_QA not in {r.gate for r in d.closed}

    def test_disclosing_pending_methodology_is_fine(self):
        d = _all_open(methodology_pending_ids=["D4_ADDRESS_MATERIALITY"])
        assert Gate.METHODOLOGY not in {r.gate for r in d.closed}

    def test_concluding_on_pending_methodology_closes_the_gate(self):
        d = _all_open(methodology_pending_ids=["D4_ADDRESS_MATERIALITY"],
                      asserts_conclusion_on_pending=True)
        assert Gate.METHODOLOGY in {r.gate for r in d.closed}

    def test_undocumented_provenance_closes_the_gate_on_its_own(self):
        """The gate that cannot be cleared by engineering."""
        d = _all_open(provenance_documented=False)
        assert d.is_cor_releasable is False
        assert [r.gate for r in d.closed] == [Gate.PROVENANCE]

    def test_render_errors_close_the_report_qa_gate(self):
        d = _all_open(render_errors=["table 3 failed to paginate"])
        assert Gate.REPORT_QA in {r.gate for r in d.closed}

    def test_any_closed_gate_forces_the_draft_watermark(self):
        for over in ({"provenance_documented": False},
                     {"evidence_rule_version": "phase6-bulk-1.0.0"},
                     {"asserted_findings": 1, "qa_approved_findings": 0},
                     {"render_errors": ["boom"]}):
            d = _all_open(**over)
            assert d.label == DRAFT_WATERMARK, over

    def test_every_closed_gate_states_a_remedy(self):
        """A closed gate that does not say how to open it is a dead end."""
        d = _all_open(provenance_documented=False,
                      evidence_rule_version="phase6-bulk-1.0.0",
                      asserted_findings=3, qa_approved_findings=0,
                      render_errors=["x"])
        assert d.closed
        for r in d.closed:
            assert r.remedy, f"{r.gate} closed with no remedy"

    def test_the_watermark_text_is_unambiguous(self):
        """A reader must not have to infer status from an absence."""
        assert DRAFT_WATERMARK == "DRAFT — NOT FOR COR RELEASE"
        assert "NOT FOR COR RELEASE" in DRAFT_WATERMARK


class TestEvaluateFromReport:
    """app.reports.release_gates.evaluate_from_report: the gap this closes is
    that evaluate() existed with zero production callers. These prove the
    bridge from a stored report's own dataset fails CLOSED wherever a fact
    isn't actually wired yet, rather than opening a gate on an absence."""

    def test_a_dataset_with_no_qa_summary_fails_closed_not_vacuously_open(self):
        from app.reports.release_gates import Gate, evaluate_from_report

        d = evaluate_from_report("delivery_processing", {}, html="<html>x</html>")
        human_qa = next(r for r in d.results if r.gate is Gate.HUMAN_QA)
        assert human_qa.open is False, "an unwired dataset must not pass HUMAN_QA for free"
        assert d.is_cor_releasable is False

    def test_an_explicit_qa_summary_is_honoured(self):
        from app.reports.release_gates import Gate, evaluate_from_report

        dataset = {"qa_summary": {"qa_approved": 3, "asserted": 3},
                  "evidence_rule_version": current_rule_version()}
        d = evaluate_from_report("delivery_processing", dataset, html="<html>x</html>")
        assert next(r for r in d.results if r.gate is Gate.HUMAN_QA).open is True
        assert next(r for r in d.results if r.gate is Gate.EVIDENCE).open is True

    def test_disclosed_methodology_pending_closes_methodology_not_just_discloses_it(self):
        """A SOW-family report's B1-B4/No-Discrepancy classification IS a
        conclusion drawn on the methodology methodology_pending() discloses --
        COR acceptance of D2 is NOT MET today, so this must stay closed, not
        open on the theory that disclosure alone is enough."""
        from app.reports.release_gates import Gate, evaluate_from_report

        dataset = {"methodology_pending": {"disposition": "METHODOLOGY_PENDING"}}
        d = evaluate_from_report("sow_weekly", dataset, html="<html>x</html>")
        assert next(r for r in d.results if r.gate is Gate.METHODOLOGY).open is False
        assert d.is_cor_releasable is False

    def test_no_methodology_pending_key_leaves_methodology_open(self):
        from app.reports.release_gates import Gate, evaluate_from_report

        d = evaluate_from_report("delivery_processing", {}, html="<html>x</html>")
        assert next(r for r in d.results if r.gate is Gate.METHODOLOGY).open is True

    def test_no_html_fails_report_qa_closed(self):
        from app.reports.release_gates import Gate, evaluate_from_report

        d = evaluate_from_report("delivery_processing", {}, html=None)
        assert next(r for r in d.results if r.gate is Gate.REPORT_QA).open is False

    def test_a_non_numeric_stored_qa_count_fails_closed_instead_of_crashing(self):
        from app.reports.release_gates import Gate, evaluate_from_report

        for bad in (None, "not-a-number", object()):
            dataset = {"qa_summary": {"qa_approved": bad, "asserted": 3}}
            d = evaluate_from_report("delivery_processing", dataset, html="<html>x</html>")
            assert next(r for r in d.results if r.gate is Gate.HUMAN_QA).open is False

    def test_is_cor_releasable_requires_every_gate_exactly_once(self):
        """A decision missing gates (or carrying a duplicate) must never read
        as releasable merely because every gate it does carry is open."""
        from app.reports.release_gates import Gate, GateResult, ReleaseDecision

        partial = ReleaseDecision(results=[GateResult(Gate.EVIDENCE, True, "ok")])
        assert partial.is_cor_releasable is False
        duplicated = ReleaseDecision(results=[GateResult(g, True, "ok") for g in Gate]
                                     + [GateResult(Gate.EVIDENCE, True, "ok")])
        assert duplicated.is_cor_releasable is False

    def test_provenance_is_never_computed_here_evaluate_still_defaults_it_closed(self):
        """evaluate_from_report never asserts PROVENANCE true -- it is a
        documentary fact docs/OFFICIAL_FINDING_RELEASE_GATE.md reserves to a
        human, and no sender/transmittal/control-total record exists in this
        system today for any report that has ever been generated."""
        from app.reports.release_gates import Gate, evaluate_from_report

        dataset = {"qa_summary": {"qa_approved": 1, "asserted": 1},
                  "evidence_rule_version": current_rule_version()}
        d = evaluate_from_report("delivery_processing", dataset, html="<html>x</html>")
        assert next(r for r in d.results if r.gate is Gate.PROVENANCE).open is False
        assert d.is_cor_releasable is False


class TestMetricContract:
    """A number without a denominator is not reportable."""

    def _metric(self, **over):
        from app.reports.data.arc_population_report import Metric
        kw = dict(label="x", observations=10_426, entities=9_032,
                  denominator=23_566, denominator_label="delivered records",
                  calculation="observations vs distinct entities")
        kw.update(over)
        return Metric(**kw)

    def test_a_metric_carries_observations_and_entities_separately(self):
        """10,426 observations are 9,032 entities. Reporting one as the other
        overstates the affected population by the overlap."""
        m = self._metric()
        assert m.observations != m.entities
        d = m.to_dict()
        assert d["observations"] == 10_426 and d["entities"] == 9_032

    def test_percentage_is_computed_from_entities_not_observations(self):
        m = self._metric()
        assert m.entity_pct == round(9032 / 23566 * 100, 2)
        assert m.entity_pct < 100, "an entity rate cannot exceed the population"

    def test_a_metric_cannot_be_built_without_a_denominator_label(self):
        import dataclasses
        from app.reports.data.arc_population_report import Metric
        required = {f.name for f in dataclasses.fields(Metric) if f.default is
                    dataclasses.MISSING and f.default_factory is dataclasses.MISSING}
        for f in ("denominator", "denominator_label", "calculation",
                  "observations", "entities", "label"):
            assert f in required, f"{f} must be mandatory on a reported metric"

    def test_a_metric_records_the_evidence_version_it_came_from(self):
        assert self._metric().to_dict()["evidence_version"] == current_rule_version()

    def test_zero_denominator_yields_no_percentage_rather_than_a_crash(self):
        assert self._metric(denominator=0).entity_pct is None


class TestProhibitedLanguage:
    """Words the deliverables may not apply to a methodology-pending condition."""

    FORBIDDEN = ("failed", "non-compliant", "noncompliant", "invalid",
                 "inaccurate", "not verified", "unverified", "arc failure")

    def _read(self, path):
        import io
        return io.open(path, encoding="utf-8").read().lower()

    def test_methodology_draft_does_not_conclude_on_address_conflicts(self):
        text = self._read("docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md")
        # The words may appear only in the sentence that forbids them.
        assert "not described as failed" in text or "are not described as" in text
        assert "pending cor decision" in text

    def test_decision_register_records_no_decided_item(self):
        text = self._read("docs/deliverables/COR_Decision_Register.md")
        assert "pending cor decision" in text
        assert text.count("pending cor decision") >= 10, (
            "every decision must be explicitly pending, not silently decided")

    def test_deliverable_drafts_carry_the_draft_watermark(self):
        for p in ("docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md",
                  "docs/deliverables/COR_Decision_Register.md"):
            assert "draft — not for cor release" in self._read(p)

    def test_priority_template_states_the_contract_volume_and_no_invented_sla(self):
        """CORRECTED. This test previously asserted that the source material
        "does not state one" — meaning a monthly volume. The solicitation does
        state one: an anticipated average of twenty priority reviews per month,
        with the capability to exceed it. The template said otherwise and a test
        was holding it there.

        What must not be invented is a TURNAROUND target. The contract sets the
        priority deadline per request, communicated by the COR, and establishes
        no standing service level. So the rule is: state the Government's volume
        expectation, and assert no SLA.
        """
        import re

        # Normalised for wrapping AND for blockquote markers: these phrases sit
        # inside a "> " block and wrap mid-sentence, so the raw text contains
        # "per > month". A test that breaks on re-wrapping tests the line width.
        raw = self._read("docs/deliverables/templates/04_Priority_Review.md")
        text = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*>\s?", "", raw))
        # the contract's own volume expectation, stated
        assert "twenty priority reviews per month" in text
        assert "capability to exceed" in text
        # and it is not mistaken for a turnaround target
        assert "not a turnaround target" in text
        assert "no standing service level" in text
        for invented in ("24-hour", "24 hour", "sla of", "within 24"):
            assert invented not in text

    def test_b1_b4_is_not_presented_as_a_federal_taxonomy(self):
        text = self._read("docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md")
        assert "internal operational classification" in text

    def test_ppef_agreement_is_never_called_full_street_agreement(self):
        text = self._read("docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md")
        assert "no street line" in text
        assert "never reported as complete" in text or "is not, and is never reported as" in text
