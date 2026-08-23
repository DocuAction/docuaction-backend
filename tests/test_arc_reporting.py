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

    def test_templates_do_not_hardcode_a_priority_volume_target(self):
        """The source material states no monthly volume or surge threshold."""
        text = self._read("docs/deliverables/templates/04_Priority_Review.md")
        assert "does not state one" in text
        for invented in ("20 per month", "20/month", "surge of"):
            assert invented not in text

    def test_b1_b4_is_not_presented_as_a_federal_taxonomy(self):
        text = self._read("docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md")
        assert "internal operational classification" in text

    def test_ppef_agreement_is_never_called_full_street_agreement(self):
        text = self._read("docs/deliverables/TEFCA_ARC_Review_Methodology_DRAFT.md")
        assert "no street line" in text
        assert "never reported as complete" in text or "is not, and is never reported as" in text
