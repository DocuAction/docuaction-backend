"""Phase 6 — which authoritative sources apply to which entity.

The distinction these tests defend is the one that decides whether a population
report is honest: an entity nobody asked about is not an entity a source
rejected. Every assertion below is about keeping those apart.
"""
from __future__ import annotations

import pytest

from app.Tefca.applicability import EntityCategory
from app.Tefca.source_applicability import (
    SOURCE_APPLICABILITY_VERSION,
    Source,
    SourceApplicability,
    build_matrix,
    population_summary,
)


def entity(**rce):
    """An entity in the canonical shape: RCE fields live under `_rce`.

    Built the way `entity_resolution.registry_entity_to_evidence_shape` builds
    it, because a test that invents its own shape proves nothing about the
    engine that reads the real one.
    """
    block = {"NPI": "", "sequoiaorgtype": "Participant", "partOf": "", **rce}
    return {
        "resourceType": "Organization",
        "name": rce.pop("name", "ACME HEALTH NETWORK"),
        "_rce": block,
    }


PROVIDER_TAXONOMY = {"enumeration_type": "NPI-2", "taxonomy_code": "282N00000X",
                     "taxonomy": "General Acute Care Hospital", "npi": "1234567893"}
PAYER_TAXONOMY = {"enumeration_type": "NPI-2", "taxonomy_code": "302F00000X",
                  "taxonomy": "Exclusive Provider Organization"}


class TestNppesApplicability:

    def test_an_npi_makes_nppes_required(self):
        matrix = build_matrix(entity(NPI="1234567893"))
        decision = matrix.of(Source.NPPES)
        assert decision.applicability is SourceApplicability.REQUIRED
        assert decision.lookup_kind == "NPI"
        assert decision.lookup_key == "1234567893"

    def test_no_npi_falls_back_to_a_name_search_and_says_so(self):
        matrix = build_matrix(entity(NPI=""))
        decision = matrix.of(Source.NPPES)
        assert decision.applicability is SourceApplicability.APPLICABLE
        assert decision.lookup_kind == "ORGANIZATION_NAME"
        assert "corroborates" in decision.rationale

    def test_neither_npi_nor_name_is_not_applicable_not_a_finding(self):
        blank = {"resourceType": "Organization", "name": "",
                 "_rce": {"NPI": "", "sequoiaorgtype": "Participant"}}
        decision = build_matrix(blank).of(Source.NPPES)
        assert decision.applicability is SourceApplicability.NOT_APPLICABLE
        assert "not a finding against the entity" in decision.rationale


class TestPpefApplicability:

    def test_no_npi_means_no_key_to_look_up(self):
        decision = build_matrix(entity(NPI="")).of(Source.CMS_PPEF_ENROLLMENT)
        assert decision.applicability is SourceApplicability.NOT_APPLICABLE
        assert "not the same as an absent enrolment" in decision.rationale

    def test_a_payer_does_not_enrol_as_a_provider(self):
        matrix = build_matrix(entity(NPI="1234567893"), nppes_data=PAYER_TAXONOMY)
        assert matrix.profile.entity_category == EntityCategory.PAYER
        decision = matrix.of(Source.CMS_PPEF_ENROLLMENT)
        assert decision.applicability is SourceApplicability.NOT_APPLICABLE
        assert "expected rather than informative" in decision.rationale

    def test_a_provider_with_medicare_relevance_is_required(self):
        matrix = build_matrix(entity(NPI="1234567893"), nppes_data=PROVIDER_TAXONOMY)
        decision = matrix.of(Source.CMS_PPEF_ENROLLMENT)
        assert decision.applicability is SourceApplicability.REQUIRED

    def test_before_nppes_answers_relevance_is_undetermined_not_assumed(self):
        """Applicability must not harden before the evidence that decides it."""
        matrix = build_matrix(entity(NPI="1234567893"))
        assert matrix.profile.medicare_relevance == "UNDETERMINED"
        assert matrix.of(Source.CMS_PPEF_ENROLLMENT).applicability is (
            SourceApplicability.APPLICABLE)

    @pytest.mark.parametrize("source", [Source.CMS_PPEF_PRACTICE_LOCATION,
                                        Source.CMS_PPEF_REASSIGNMENT])
    def test_sub_files_are_conditional_on_the_enrolment_match(self, source):
        decision = build_matrix(entity(NPI="1234567893")).of(source)
        assert decision.applicability is SourceApplicability.CONDITIONALLY_APPLICABLE
        assert decision.lookup_kind == "ENRLMT_ID"
        assert decision.should_query is False, (
            "a sub-file cannot be queried before the key that joins it exists")

    @pytest.mark.parametrize("source", [Source.CMS_PPEF_PRACTICE_LOCATION,
                                        Source.CMS_PPEF_REASSIGNMENT])
    def test_sub_files_follow_the_enrolment_into_not_applicable(self, source):
        decision = build_matrix(entity(NPI="")).of(source)
        assert decision.applicability is SourceApplicability.NOT_APPLICABLE


class TestExclusionSources:

    def test_leie_applies_to_every_identifiable_entity(self):
        """Narrowing exclusion screening would create the blind spot it closes."""
        for data in (PAYER_TAXONOMY, PROVIDER_TAXONOMY, None):
            decision = build_matrix(entity(NPI="1234567893"),
                                    nppes_data=data).of(Source.OIG_LEIE)
            assert decision.applicability is SourceApplicability.REQUIRED

    def test_leie_uses_a_name_when_there_is_no_npi(self):
        decision = build_matrix(entity(NPI="")).of(Source.OIG_LEIE)
        assert decision.applicability is SourceApplicability.REQUIRED
        assert decision.lookup_kind == "NAME"

    def test_revocation_needs_an_npi(self):
        assert build_matrix(entity(NPI="")).of(
            Source.CMS_REVOCATION).applicability is SourceApplicability.NOT_APPLICABLE

    def test_a_payer_has_no_billing_privileges_to_revoke(self):
        decision = build_matrix(entity(NPI="1234567893"),
                                nppes_data=PAYER_TAXONOMY).of(Source.CMS_REVOCATION)
        assert decision.applicability is SourceApplicability.NOT_APPLICABLE
        assert "nothing that could be revoked" in decision.rationale


class TestSamIsNotGuessed:

    def test_no_uei_is_recorded_as_an_open_methodology_question(self):
        """Whether a TEFCA entity must appear in SAM is not ours to decide."""
        decision = build_matrix(entity(NPI="1234567893")).of(Source.SAM_GOV)
        assert decision.applicability is SourceApplicability.UNKNOWN_PENDING_METHODOLOGY
        assert decision.blocked_by == "D4"
        assert decision.should_query is False

    def test_a_delivered_uei_makes_sam_answerable(self):
        base = entity(NPI="1234567893")
        base["uei"] = "ZQGGHJH74DW7"
        decision = build_matrix(base).of(Source.SAM_GOV)
        assert decision.applicability is SourceApplicability.APPLICABLE
        assert decision.lookup_key == "ZQGGHJH74DW7"
        assert decision.blocked_by is None


class TestTheFourWayDistinction:

    def test_not_applicable_is_never_expressed_as_a_missing_result(self):
        """NOT_APPLICABLE, NO_MATCH, UNAVAILABLE and MATCH are four facts."""
        decision = build_matrix(entity(NPI="")).of(Source.CMS_PPEF_ENROLLMENT)
        assert decision.applicability is SourceApplicability.NOT_APPLICABLE
        assert decision.lookup_key is None
        # It carries a reason, so a report can say why nothing was asked.
        assert decision.rationale

    def test_only_required_and_applicable_are_queried(self):
        matrix = build_matrix(entity(NPI="1234567893"))
        for decision in matrix.decisions.values():
            expected = decision.applicability in (SourceApplicability.REQUIRED,
                                                  SourceApplicability.APPLICABLE)
            assert decision.should_query is expected

    def test_no_decision_states_a_conclusion_about_the_entity(self):
        from app.core.ingestion.quality import assert_not_a_disposition
        for data in (None, PROVIDER_TAXONOMY, PAYER_TAXONOMY):
            matrix = build_matrix(entity(NPI="1234567893"), nppes_data=data)
            for decision in matrix.decisions.values():
                assert_not_a_disposition(
                    decision.rationale,
                    where=f"{decision.source.value} rationale")


class TestPopulationSummary:

    def test_every_applicability_value_is_reported_separately(self):
        matrices = [build_matrix(entity(NPI="1234567893")),
                    build_matrix(entity(NPI=""))]
        summary = population_summary(matrices)
        assert summary["entities"] == 2
        for counts in summary["by_source"].values():
            assert set(counts) == {a.value for a in SourceApplicability}, (
                "rolling NOT_APPLICABLE into a 'not checked' bucket would hide "
                "the distinction the matrix exists to preserve")

    def test_blocked_methodology_is_counted(self):
        summary = population_summary([build_matrix(entity(NPI="1234567893"))])
        assert summary["blocked_by_methodology"] == 1

    def test_queryable_calls_are_counted_for_capacity_planning(self):
        summary = population_summary([build_matrix(entity(NPI="1234567893"))])
        assert summary["queryable_calls"] >= 2

    def test_the_matrix_is_versioned(self):
        matrix = build_matrix(entity(NPI="1234567893"))
        assert matrix.version == SOURCE_APPLICABILITY_VERSION
        assert matrix.to_dict()["version"] == SOURCE_APPLICABILITY_VERSION


class TestDeterminism:

    def test_the_same_entity_yields_the_same_matrix(self):
        """A population run must be reproducible from its inputs."""
        first = build_matrix(entity(NPI="1234567893"), nppes_data=PROVIDER_TAXONOMY)
        second = build_matrix(entity(NPI="1234567893"), nppes_data=PROVIDER_TAXONOMY)
        assert first.to_dict()["decisions"] == second.to_dict()["decisions"]

    def test_every_source_is_decided_for_every_entity(self):
        """A source left undecided would silently never be asked."""
        matrix = build_matrix(entity(NPI=""))
        assert set(matrix.decisions) == {s.value for s in Source}
