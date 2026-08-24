"""Phase 7.5B — the contract's report families, on canonical evidence.

The SOW families used to read `tefca_reviews` with one-off SQL and take
`review.status` as the discrepancy category, consulting neither the canonical
evidence selector nor the reportability gate. A system recommendation no human
had approved was counted in a contractual category exactly as if a QA reviewer
had signed it off.

These tests pin the two things that fixes: the Government's words on the page,
and the gate in front of the count.

DEVELOPMENT/TEST DATA. Nothing here is an ONC finding.
"""
from __future__ import annotations

import inspect

import pytest

from app.reports.data.sow_report_data import (
    BUCKET_TO_GOVERNMENT_CATEGORY, GOVERNMENT_CATEGORIES,
    GOVERNMENT_CATEGORY_LABELS, GOVERNMENT_CATEGORY_NUMBER, SOW_FAMILIES,
    SowReportDataService, category_for_bucket, empty_stratification,
    government_label)


class TestGovernmentTerminology:
    """The four categories are quoted from the solicitation, not paraphrased."""

    def test_there_are_exactly_four(self):
        assert len(GOVERNMENT_CATEGORIES) == 4

    def test_the_order_follows_the_solicitations_numbering(self):
        assert GOVERNMENT_CATEGORIES == (
            "no_discrepancy", "minor_administrative", "inexplicable",
            "non_compliant")
        assert [GOVERNMENT_CATEGORY_NUMBER[c] for c in GOVERNMENT_CATEGORIES] == [1, 2, 3, 4]

    @pytest.mark.parametrize("category,label", [
        ("no_discrepancy", "No discrepancies identified"),
        ("minor_administrative", "Minor or administrative discrepancies"),
        ("inexplicable", "Inexplicable discrepancies"),
        ("non_compliant", "Non-compliant discrepancies"),
    ])
    def test_labels_are_the_contract_wording(self, category, label):
        """¶136 / ¶137 / ¶142, where the identical sentence appears three times."""
        assert government_label(category) == label

    def test_an_unknown_category_raises_rather_than_falling_back(self):
        """A silent fallback is how internal vocabulary reaches a COR."""
        with pytest.raises(ValueError):
            government_label("B2")
        with pytest.raises(ValueError):
            government_label("made_up")

    def test_internal_shorthand_is_never_a_report_label(self):
        for bucket in ("B1", "B2", "B3", "B4"):
            assert bucket not in GOVERNMENT_CATEGORY_LABELS.values()
            assert not any(bucket in label
                           for label in GOVERNMENT_CATEGORY_LABELS.values())

    def test_the_bucket_mapping_is_total_and_ordered(self):
        assert set(BUCKET_TO_GOVERNMENT_CATEGORY) == {"B1", "B2", "B3", "B4"}
        assert [BUCKET_TO_GOVERNMENT_CATEGORY[b] for b in ("B1", "B2", "B3", "B4")] \
            == list(GOVERNMENT_CATEGORIES)

    def test_bucket_lookup_is_forgiving_of_case_but_not_of_nonsense(self):
        assert category_for_bucket("b3") == "inexplicable"
        assert category_for_bucket("B9") is None
        assert category_for_bucket(None) is None

    def test_the_mapping_is_labelled_as_agt_methodology_not_contract(self):
        """Getting this backwards in either direction is a contract problem."""
        from app.reports.data import sow_report_data

        source = inspect.getsource(sow_report_data)
        assert "AGT METHODOLOGY" in source
        assert "¶124" in source


class TestEveryFamilyExists:

    def test_all_eight_deliverables_are_covered(self):
        assert set(SOW_FAMILIES) == {"D3.1", "D3.2", "D4.1", "D4.2",
                                     "D5.1", "D5.2", "D6.1", "D6.2"}

    def test_each_maps_to_a_real_method(self):
        for deliverable, method in SOW_FAMILIES.items():
            assert callable(getattr(SowReportDataService, method, None)), \
                f"{deliverable} maps to missing method {method}"

    def test_the_api_exposes_the_same_set(self):
        from app.reports.routes import SOW_DELIVERABLES

        assert set(SOW_DELIVERABLES) == set(SOW_FAMILIES)


# ── the gate, driven through the real service with a stubbed record set ──────

class _Record:
    def __init__(self, bucket=None, reportable=False, reclassified_to=None):
        self.classification_bucket = bucket
        self.reclassified_to = reclassified_to
        self.reportable_at = "2026-08-24T00:00:00Z" if reportable else None


class _Canonical:
    """Stands in for ReportDataService."""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.evidence_scope = {"rule_version": "phase6-bulk-1.1.0",
                               "observations_read": len(self._rows),
                               "observations_reported": len(self._rows),
                               "collapsed_duplicates": 0}

    async def _dimension_rows(self, _cycle=None):
        return self._rows


def _service(records, rows=None):
    svc = SowReportDataService(db=None, canonical=_Canonical(rows))

    async def _records(_cycle=None):
        return records
    svc._review_records = _records
    return svc


class TestReportabilityGate:

    @pytest.mark.asyncio
    async def test_an_unapproved_record_is_pending_not_a_category_count(self):
        """The defect this closes: legacy counted it as a finding."""
        s = await _service([_Record("B3", reportable=False)]).stratification()
        assert s["reportable"]["inexplicable"] == 0
        assert s["pending_qa"]["inexplicable"] == 1
        assert s["reportable_total"] == 0

    @pytest.mark.asyncio
    async def test_an_approved_record_counts(self):
        s = await _service([_Record("B3", reportable=True)]).stratification()
        assert s["reportable"]["inexplicable"] == 1
        assert s["pending_qa"]["inexplicable"] == 0

    @pytest.mark.asyncio
    async def test_a_reclassification_wins_over_the_system_bucket(self):
        """A human's determination outranks the system's recommendation."""
        s = await _service([
            _Record("B1", reportable=True, reclassified_to="B4")]).stratification()
        assert s["reportable"]["non_compliant"] == 1
        assert s["reportable"]["no_discrepancy"] == 0

    @pytest.mark.asyncio
    async def test_nothing_is_dropped_between_the_two_buckets(self):
        records = [_Record("B1", True), _Record("B2", False),
                   _Record("B3", False), _Record(None, False),
                   _Record(None, True)]
        s = await _service(records).stratification()
        assert s["reportable_total"] + s["pending_qa_total"] == len(records)
        assert s["records_considered"] == len(records)

    @pytest.mark.asyncio
    async def test_an_unclassifiable_record_is_counted_not_discarded(self):
        s = await _service([_Record(None, False)]).stratification()
        assert s["unclassified_pending"] == 1

    @pytest.mark.asyncio
    async def test_empty_population_yields_zeros_not_absent_keys(self):
        """A missing key reads as "not measured"; a zero reads as "none"."""
        s = await _service([]).stratification()
        assert s["reportable"] == empty_stratification()
        assert set(s["reportable"]) == set(GOVERNMENT_CATEGORIES)

    @pytest.mark.asyncio
    async def test_the_gate_is_stated_in_the_payload(self):
        s = await _service([]).stratification()
        assert "QA approval" in s["gate"]


class TestEveryFamilyCarriesTheCanonicalEnvelope:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", sorted(set(SOW_FAMILIES.values())))
    async def test_family_reports_its_evidence_version_and_scope(self, method):
        svc = _service([_Record("B2", False)])
        data = (await getattr(svc, method)(case_id=None)
                if method == "priority_status"
                else await getattr(svc, method)())
        assert data["evidence_rule_version"] == "phase6-bulk-1.1.0"
        assert data["evidence_scope"]["rule_version"] == "phase6-bulk-1.1.0"
        assert set(data["stratification"]["reportable"]) == set(GOVERNMENT_CATEGORIES)
        assert "methodology_pending" in data
        assert "source_limitations" in data

    @pytest.mark.asyncio
    async def test_no_family_bypasses_the_canonical_selector(self):
        """One place decides what evidence a report may see."""
        from app.reports.data import sow_report_data

        source = inspect.getsource(sow_report_data)
        # the only raw select is for review_records; no evidence table is queried
        assert "TEFCADimensionEvidence" not in source
        assert "tefca_dimension_evidence" not in source

    @pytest.mark.asyncio
    async def test_closeout_is_a_framework_and_says_so(self):
        """Populating closeout findings before the work exists is fabrication."""
        data = await _service([]).closeout_framework()
        assert data["populated"] is False
        assert data["sections"]
        assert "unlimited rights" in data["rights_note"]

    @pytest.mark.asyncio
    async def test_priority_lists_the_five_required_elements(self):
        """¶147, in the order it names them."""
        data = await _service([]).priority_status(case_id="PILOT-DEV-001")
        assert data["required_content"] == [
            "The identified issue",
            "Root cause, if determined",
            "The severity or impact",
            "Recommendations to prevent reoccurrence",
            "Resolution",
        ]

    @pytest.mark.asyncio
    async def test_priority_does_not_assert_a_fixed_sla(self):
        data = await _service([]).priority_status(case_id="PILOT-DEV-001")
        assert "no fixed contractual SLA" in data["turnaround"]["basis"]

    @pytest.mark.asyncio
    async def test_the_final_report_states_the_confidence_floor_as_contractual(self):
        data = await _service([]).retrospective_final()
        assert "CONTRACT REQUIREMENT" in data["sampling"]["confidence_floor"]
        assert "AGT METHODOLOGY" in data["sampling"]["parameters_status"]

    @pytest.mark.asyncio
    async def test_task4_scope_is_new_entrants_only(self):
        data = await _service([]).ongoing_biweekly()
        assert "new entrants only" in data["scope_note"]


class TestSourceLimitations:

    @pytest.mark.asyncio
    async def test_an_unavailable_source_is_a_fact_about_the_lookup(self):
        class _Row:
            observation_result = "SOURCE_UNAVAILABLE"
            source = "SAM_GOV"

        svc = _service([], rows=[_Row(), _Row()])
        limits = await svc.source_limitations()
        assert limits["sources_unavailable"]["SAM_GOV"] == 2
        assert limits["observations_affected"] == 2
        assert "says nothing about the entity" in limits["note"]

    @pytest.mark.asyncio
    async def test_limitations_are_derived_not_hard_coded(self):
        """No standing list of known outages — it comes from the evidence."""
        svc = _service([], rows=[])
        assert (await svc.source_limitations())["sources_unavailable"] == {}
