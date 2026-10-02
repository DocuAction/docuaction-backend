"""Expanded semantics: participation / program identity, absence semantics,
source authority as description not weight, multi-source variation with the
same-identifier guard, delta scopes, and rule/policy versioning. Synthetic only.

Invariants (section 30/38 of the sprint brief), each with a test:
    NPPES != enrollment · enrollment != credentialing · enrollment != TEFCA eligibility
    additional location != primary · CMS reassignment != TEFCA relationship
    corporate parent != TEFCA parent · absence != failure · unknown != conflict
    current dataset != historical evidence · delivered change != real-world change
    policy version changes · source effective-date behaviour · multi-source conflicts
    source applicability · source authority · no source voting
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.entity_intelligence.assessment import SystemEvidenceAssessment, assess, multi_source_name_variation
from app.core.entity_intelligence.comparison import (Dimension, LocationSignal, ParticipationSignal,
                                                      RelationshipSignal, compare_all, compare_participation)
from app.core.entity_intelligence.delta import DeltaScope, compute_deltas
from app.core.entity_intelligence.explanations import TEMPLATES
from app.core.entity_intelligence.normalize import normalize_address, normalize_name
from app.core.entity_intelligence.observations import (AbsenceReason, DeliveryPath, EvidenceApplicability,
                                                        EvidenceObservation, LocationRole, NameKind, ObservationType,
                                                        Provenance, SourceAuthority, absence)
from app.core.entity_intelligence.policy import (AuthorityLayer, PolicyRegister, RuleDefinition, RuleStatus,
                                                  RuleVersion, overlapping)
from app.core.entity_intelligence.rce_policy_register import REGISTER
from app.core.entity_intelligence.service import EntityIntelligenceService
from ei_fixtures import BALTIMORE, FREDERICK, PROGRAM_SOURCE, delivered, enable_all, unavailable

A = SystemEvidenceAssessment
NPPES = "NPPES_V2"
CMS = "SYNTHETIC_CMS_ENROLLMENT"       # a synthetic stand-in for a CMS public enrollment dataset
MEDICARE_ENROLLMENT = "MEDICARE:ENROLLMENT"
TEFCA_PARTICIPANT = "TEFCA:PARTICIPANT"


def prov(owner="synthetic"):
    return Provenance(source_owner=owner, delivery_path=DeliveryPath.FILE_DOWNLOAD, source_record_ref="s")


def src(source, *, npi="9999900001", name=None, kind=NameKind.LEGAL_BUSINESS_NAME, addr=None,
        role=LocationRole.PRIMARY_PRACTICE_LOCATION, authority=SourceAuthority.FEDERAL_REGISTRY,
        participation=None, rel=None, dataset_version="SYNTH-Q3", observed_at=None):
    common = dict(canonical_entity_id="ent-1", source_id=source, source_authority=authority, provenance=prov(),
                  source_delivery_id=dataset_version, observed_at=observed_at)
    out = []
    if npi:
        out.append(EvidenceObservation(observation_type=ObservationType.IDENTIFIER, role="NPI",
                                       observed_value={"value": npi, "entity_type": "2"}, **common))
    if name:
        out.append(EvidenceObservation(observation_type=ObservationType.NAME, role=kind.value,
                                       observed_value={"name": name}, normalized_value={"name": normalize_name(name)}, **common))
    if addr:
        out.append(EvidenceObservation(observation_type=ObservationType.LOCATION, role=role.value,
                                       observed_value=dict(addr), normalized_value=normalize_address(addr), **common))
    if participation:
        out.append(EvidenceObservation(observation_type=ObservationType.PROGRAM_PARTICIPATION, role=participation,
                                       observed_value={"status": "observed", "kind": "SYNTHETIC_ENROLLMENT_OBSERVED"}, **common))
    if rel:
        out.append(EvidenceObservation(observation_type=ObservationType.RELATIONSHIP, role=rel["kind"],
                                       observed_value={"related_entity_name": rel["name"]}, **common))
    return out


def delivered_participation(role=TEFCA_PARTICIPANT, status="observed"):
    return [EvidenceObservation(canonical_entity_id="ent-1", source_id=PROGRAM_SOURCE,
                                observation_type=ObservationType.PROGRAM_PARTICIPATION, role=role,
                                observed_value={"status": status}, source_authority=SourceAuthority.PROGRAM_DELIVERY,
                                provenance=Provenance(source_owner="Synthetic program", delivery_path=DeliveryPath.OPERATOR_UPLOAD))]


def run(cur, sources, rels=None, roles=None, prior=None):
    comps = []
    for s in sources:
        comps += compare_all(cur, source_id=s, relationship_kinds=rels, participation_roles=roles)
    return assess(comps, []), comps


class TestParticipationDimension:
    def test_medicare_enrollment_is_not_tefca_participation(self):
        cur = delivered("S", BALTIMORE) + delivered_participation(TEFCA_PARTICIPANT) + \
              src(CMS, name="S", addr=BALTIMORE, participation=MEDICARE_ENROLLMENT,
                  authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        res = compare_participation(cur, source_id=CMS, participation_role=TEFCA_PARTICIPANT)
        assert res.signal is ParticipationSignal.PARTICIPATION_NOT_COMPARABLE
        assert "enrollment in one program is not participation in another" in res.explanation

    def test_same_program_kind_is_observed(self):
        cur = delivered("S", BALTIMORE) + delivered_participation(MEDICARE_ENROLLMENT) + \
              src(CMS, participation=MEDICARE_ENROLLMENT, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        res = compare_participation(cur, source_id=CMS, participation_role=MEDICARE_ENROLLMENT)
        assert res.signal is ParticipationSignal.PARTICIPATION_OBSERVED
        assert "does not establish eligibility, licensure or compliance" in res.explanation

    def test_enrollment_observed_never_claims_credentialing_or_tefca_eligibility(self):
        text = TEMPLATES["PARTICIPATION_OBSERVED"].lower()
        for banned in ("credential", "licensed", "eligible for tefca", "tefca eligib", "compliant", "verified"):
            assert banned not in text

    def test_absence_is_not_found_with_reason_never_conflict(self):
        cur = delivered("S", BALTIMORE) + delivered_participation(MEDICARE_ENROLLMENT) + [
            absence(canonical_entity_id="ent-1", source_id=CMS, observation_type=ObservationType.PROGRAM_PARTICIPATION,
                    role=MEDICARE_ENROLLMENT, reason=AbsenceReason.NOT_FOUND,
                    source_authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT, provenance=prov(),
                    applicability=EvidenceApplicability.CONDITIONALLY_APPLICABLE, dataset_version="SYNTH-Q3")]
        res = compare_participation(cur, source_id=CMS, participation_role=MEDICARE_ENROLLMENT)
        assert res.signal is ParticipationSignal.PARTICIPATION_EVIDENCE_NOT_FOUND
        assert "a statement about the dataset, not about the entity" in res.explanation
        assert "NOT_FOUND" in res.explanation and "CONDITIONALLY_APPLICABLE" in res.explanation and "SYNTH-Q3" in res.explanation
        overall, _ = run(cur, [CMS], roles=[MEDICARE_ENROLLMENT])
        assert overall.assessment is not A.CONFLICTING_EVIDENCE

    @pytest.mark.parametrize("reason", list(AbsenceReason))
    def test_every_absence_reason_is_insufficient_not_adverse(self, reason):
        cur = delivered("S", BALTIMORE) + delivered_participation(MEDICARE_ENROLLMENT) + [
            absence(canonical_entity_id="ent-1", source_id=CMS, observation_type=ObservationType.PROGRAM_PARTICIPATION,
                    role=MEDICARE_ENROLLMENT, reason=reason, source_authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT,
                    provenance=prov())]
        res = compare_participation(cur, source_id=CMS, participation_role=MEDICARE_ENROLLMENT)
        assert res.signal is ParticipationSignal.PARTICIPATION_EVIDENCE_NOT_FOUND
        for banned in ("not enrolled", "fail", "non-compliant", "invalid"):
            assert banned not in res.explanation.lower()

    def test_source_unavailable_is_not_not_found(self):
        cur = delivered("S", BALTIMORE) + delivered_participation(MEDICARE_ENROLLMENT) + [unavailable(CMS)]
        res = compare_participation(cur, source_id=CMS, participation_role=MEDICARE_ENROLLMENT)
        assert res.signal is ParticipationSignal.SOURCE_UNAVAILABLE

    def test_participation_conflict_when_same_kind_differs(self):
        cur = delivered("S", BALTIMORE) + delivered_participation(MEDICARE_ENROLLMENT, status="active") + \
              src(CMS, participation=MEDICARE_ENROLLMENT, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        for o in cur:
            if o.source_id == CMS and o.observation_type is ObservationType.PROGRAM_PARTICIPATION:
                o.observed_value["status"] = "revoked-synthetic"
        res = compare_participation(cur, source_id=CMS, participation_role=MEDICARE_ENROLLMENT)
        assert res.signal is ParticipationSignal.PARTICIPATION_CONFLICT

    def test_no_delivered_participation_is_context_not_corroboration(self):
        cur = delivered("S", BALTIMORE) + src(CMS, participation=MEDICARE_ENROLLMENT,
                                              authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        res = compare_participation(cur, source_id=CMS, participation_role=MEDICARE_ENROLLMENT)
        assert res.signal is ParticipationSignal.INSUFFICIENT_PARTICIPATION_EVIDENCE

    def test_service_passes_roles_through(self, monkeypatch):
        enable_all(monkeypatch)
        cur = delivered("S", BALTIMORE) + delivered_participation(MEDICARE_ENROLLMENT) + \
              src(CMS, name="S", addr=BALTIMORE, participation=MEDICARE_ENROLLMENT,
                  authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        run_ = EntityIntelligenceService().evaluate(canonical_entity_id="e", current=cur, source_ids=[CMS],
                                                    participation_roles=[MEDICARE_ENROLLMENT])
        dims = {c.dimension for c in run_.comparisons}
        assert Dimension.PARTICIPATION_IDENTITY in dims
        assert run_.assessment.assessment is A.EVIDENCE_CORROBORATES

    def test_no_program_vocabulary_in_core_enums(self):
        """Core enums carry no TEFCA/Medicare/QHIN member: program vocabularies
        live in adapters and in observation roles, never in Core."""
        import enum
        import inspect
        import app.core.entity_intelligence.comparison as c
        import app.core.entity_intelligence.delta as d
        import app.core.entity_intelligence.observations as o
        for mod in (c, o, d):
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if issubclass(cls, enum.Enum) and cls.__module__ == mod.__name__:
                    for m in cls:
                        for banned in ("QHIN", "TEFCA", "MEDICARE", "PECOS", "NPPES"):
                            assert banned not in m.name and banned not in str(m.value), (cls.__name__, m)


class TestRelationshipVocabulariesStayDistinct:
    def test_cms_reassignment_is_not_tefca_relationship(self):
        cur = delivered("S", BALTIMORE, relationships=[{"kind": "TEFCA:QHIN_PARTICIPANT", "name": "SYNTHETIC QHIN"}]) + \
              src(CMS, rel={"kind": "MEDICARE:REASSIGNS_BENEFITS_TO", "name": "SYNTHETIC QHIN"},
                  authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        _, comps = run(cur, [CMS], rels=["TEFCA:QHIN_PARTICIPANT"])
        sig = [c.signal for c in comps if c.dimension is Dimension.RELATIONSHIP_IDENTITY]
        assert sig == [RelationshipSignal.RELATIONSHIP_NOT_COMPARABLE]

    def test_corporate_parent_is_not_tefca_parent(self):
        cur = delivered("S", BALTIMORE, relationships=[{"kind": "TEFCA:PART_OF", "name": "SYNTHETIC PARENT"}]) + \
              src("SYNTHETIC_COMMERCIAL", rel={"kind": "COMMERCIAL:CORPORATE_PARENT", "name": "SYNTHETIC PARENT"},
                  authority=SourceAuthority.RCE_PROVIDED_THIRD_PARTY)
        _, comps = run(cur, ["SYNTHETIC_COMMERCIAL"], rels=["TEFCA:PART_OF"])
        assert [c.signal for c in comps if c.dimension is Dimension.RELATIONSHIP_IDENTITY] == \
               [RelationshipSignal.RELATIONSHIP_NOT_COMPARABLE]

    def test_additional_location_is_not_primary(self):
        cur = delivered("S", FREDERICK) + src(NPPES, name="S", addr=BALTIMORE) + \
              src(NPPES, npi=None, addr=FREDERICK, role=LocationRole.ADDITIONAL_PRACTICE_LOCATION)
        _, comps = run(cur, [NPPES])
        sig = [c.signal for c in comps if c.dimension is Dimension.LOCATION_IDENTITY]
        assert sig == [LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH]
        assert sig != [LocationSignal.PRIMARY_LOCATION_MATCH]


class TestMultiSourceCases:
    """Sprint brief section 19, cases A-D, plus the section 18 guard."""

    def _dba_both(self, cms_npi="9999900001"):
        return (delivered("SYNTHETIC MOBILE CLINIC", BALTIMORE)
                + src(NPPES, name="SYNTHETIC HEALTHCARE LLC", addr=BALTIMORE)
                + src(NPPES, npi=None, name="SYNTHETIC MOBILE CLINIC", kind=NameKind.DOING_BUSINESS_AS)
                + src(CMS, npi=cms_npi, name="SYNTHETIC HEALTHCARE LLC", addr=BALTIMORE,
                      authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
                + src(CMS, npi=None, name="SYNTHETIC MOBILE CLINIC", kind=NameKind.DOING_BUSINESS_AS,
                      authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT))

    def test_A_multi_source_name_variation_corroboration(self):
        res, comps = run(self._dba_both(), [NPPES, CMS])
        assert res.assessment is A.EXPLAINABLE_VARIATION_IDENTIFIED
        assert len(res.cross_source_notes) == 1
        note = res.cross_source_notes[0]
        assert "NPPES_V2 and SYNTHETIC_CMS_ENROLLMENT" in note and "source-stated DBA" in note
        assert note.endswith("Human review required.")

    def test_A_guard_different_npi_in_second_source_produces_no_cross_source_note(self):
        res, comps = run(self._dba_both(cms_npi="9999900002"), [NPPES, CMS])
        assert res.cross_source_notes == []
        assert multi_source_name_variation(comps) is None

    def test_A_guard_multiple_candidates_blocks_note(self):
        cur = self._dba_both() + src(CMS, name="SYNTHETIC OTHER ENTITY", authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        res, comps = run(cur, [NPPES, CMS])
        assert res.cross_source_notes == []

    def test_A_single_source_produces_no_cross_source_note(self):
        cur = delivered("SYNTHETIC MOBILE CLINIC", BALTIMORE) + src(NPPES, name="SYNTHETIC HEALTHCARE LLC", addr=BALTIMORE) + \
              src(NPPES, npi=None, name="SYNTHETIC MOBILE CLINIC", kind=NameKind.DOING_BUSINESS_AS)
        res, _ = run(cur, [NPPES])
        assert res.assessment is A.EXPLAINABLE_VARIATION_IDENTIFIED and res.cross_source_notes == []

    def test_B_identifier_conflict_across_sources(self):
        cur = delivered("S", BALTIMORE, npi="1111111111") + src(NPPES, npi="1111111111", name="S", addr=BALTIMORE) + \
              src(CMS, npi="2222222222", name="S", addr=BALTIMORE, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        res, comps = run(cur, [NPPES, CMS])
        assert res.assessment is A.CONFLICTING_EVIDENCE
        assert any(c.source_id == CMS and c.signal.value == "IDENTIFIER_CONFLICT" for c in comps)
        assert any(c.source_id == NPPES and c.signal.value == "IDENTIFIER_CORROBORATED" for c in comps)  # visible, not outvoted

    def test_C_location_evidence_differs_by_source_no_majority(self):
        cur = delivered("S", FREDERICK) + src(NPPES, name="S", addr=BALTIMORE) + \
              src(NPPES, npi=None, addr=FREDERICK, role=LocationRole.ADDITIONAL_PRACTICE_LOCATION) + \
              src(CMS, name="S", addr=BALTIMORE, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        res, comps = run(cur, [NPPES, CMS])
        by_source = {c.source_id: c.signal for c in comps if c.dimension is Dimension.LOCATION_IDENTITY}
        assert by_source[NPPES] is LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH
        assert by_source[CMS] is LocationSignal.LOCATION_CONFLICT
        assert res.assessment is A.CONFLICTING_EVIDENCE          # one conflict is enough; no vote
        assert any(CMS in q for q in res.open_questions)

    def test_D_absent_cms_record_is_not_found_not_not_enrolled(self):
        cur = delivered("S", BALTIMORE) + delivered_participation(MEDICARE_ENROLLMENT) + src(NPPES, name="S", addr=BALTIMORE) + [
            absence(canonical_entity_id="ent-1", source_id=CMS, observation_type=ObservationType.PROGRAM_PARTICIPATION,
                    role=MEDICARE_ENROLLMENT, reason=AbsenceReason.NOT_FOUND,
                    source_authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT, provenance=prov(), dataset_version="SYNTH-Q3")]
        res, comps = run(cur, [NPPES, CMS], roles=[MEDICARE_ENROLLMENT])
        part = [c for c in comps if c.dimension is Dimension.PARTICIPATION_IDENTITY and c.source_id == CMS][0]
        assert part.signal is ParticipationSignal.PARTICIPATION_EVIDENCE_NOT_FOUND
        assert res.assessment in (A.EVIDENCE_PARTIALLY_CORROBORATES, A.INSUFFICIENT_EVIDENCE)
        assert "not enrolled" not in str(res.to_dict()).lower()


class TestSourceAuthorityIsDescriptive:
    def test_new_authority_classes_exist(self):
        for name in ("RCE_GOVERNING_MATERIAL", "FEDERAL_PROGRAM_ENROLLMENT", "FEDERAL_EXCLUSION_OR_INTEGRITY",
                     "RCE_PROVIDED_THIRD_PARTY", "DOCUACTION_HISTORICAL", "PRIOR_HUMAN_DETERMINATION"):
            assert SourceAuthority[name]

    def test_authority_never_changes_outcome(self):
        outcomes = set()
        for auth in SourceAuthority:
            if auth is SourceAuthority.PROGRAM_DELIVERY:
                continue
            cur = delivered("S", BALTIMORE) + src(CMS, name="OTHER", addr=BALTIMORE, authority=auth)
            res, _ = run(cur, [CMS])
            outcomes.add(res.assessment)
        assert outcomes == {A.CONFLICTING_EVIDENCE}

    def test_no_numeric_weight_attribute(self):
        assert not any(hasattr(a, "weight") or hasattr(a, "score") for a in SourceAuthority)


class TestCurrentDatasetIsNotHistory:
    def test_observations_carry_edition_and_date(self):
        o = src(CMS, participation=MEDICARE_ENROLLMENT, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT,
                dataset_version="SYNTH-Q3", observed_at="2026-07-17")[-1]
        d = o.to_dict()
        assert d["source_delivery_id"] == "SYNTH-Q3" and d["observed_at"] == "2026-07-17"

    def test_edition_change_without_value_change_is_evidence_side_unchanged(self):
        prior = src(CMS, participation=MEDICARE_ENROLLMENT, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT, dataset_version="Q2")
        current = src(CMS, participation=MEDICARE_ENROLLMENT, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT, dataset_version="Q3")
        deltas = compute_deltas(prior, current)
        part = [d for d in deltas if d.observation_type is ObservationType.PROGRAM_PARTICIPATION]
        assert part and all(d.scope is DeltaScope.PROGRAM_ENROLLMENT and not d.subject for d in part)
        assert all(d.delta_type.value == "UNCHANGED" for d in part)

    def test_delivered_change_is_not_a_real_world_claim(self):
        deltas = compute_deltas(delivered("SYNTHETIC HEALTHCARE LLC"), delivered("SYNTHETIC MOBILE CLINIC"))
        d = [x for x in deltas if x.observation_type is ObservationType.NAME][0]
        assert d.scope is DeltaScope.DELIVERED_VALUE
        assert "not a real-world event" in d.explanation
        assert "legal name" not in d.explanation.lower()

    def test_relationship_delta_scope(self):
        prior = src(CMS, rel={"kind": "MEDICARE:REASSIGNS_BENEFITS_TO", "name": "A"}, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        current = src(CMS, rel={"kind": "MEDICARE:REASSIGNS_BENEFITS_TO", "name": "B"}, authority=SourceAuthority.FEDERAL_PROGRAM_ENROLLMENT)
        d = [x for x in compute_deltas(prior, current) if x.observation_type is ObservationType.RELATIONSHIP][0]
        assert d.scope is DeltaScope.RELATIONSHIP


class TestPolicyVersioning:
    def test_which_vetting_rule_applied_on_the_review_date(self):
        assert [v.version for v in REGISTER.applicable("RCE.XP_VETTING", date(2026, 7, 15))] == ["1.0"]
        assert [v.version for v in REGISTER.applicable("RCE.XP_VETTING", date(2026, 8, 3))] == ["2.0"]
        assert [v.version for v in REGISTER.applicable("RCE.XP_VETTING", date(2024, 1, 1))] == []

    def test_approved_future_is_not_effective_before_its_date(self):
        vs = {v.version: v for v in REGISTER.versions if v.rule_id == "RCE.DIRECTORY_REQUIREMENTS"}
        assert vs["1.1"].status_on(date(2026, 9, 12)) is RuleStatus.APPROVED_FUTURE
        assert vs["1.1"].status_on(date(2026, 9, 14)) is RuleStatus.EFFECTIVE
        assert vs["1.0"].status_on(date(2026, 9, 14)) is RuleStatus.SUPERSEDED
        assert not vs["1.1"].applies_on(date(2026, 9, 12))

    def test_drafts_and_proposals_never_apply(self):
        for v in REGISTER.proposals():
            for d in (date(2026, 1, 1), date(2026, 9, 12), date(2030, 1, 1)):
                assert not v.applies_on(d), v.rule_id
        kyp = [v for v in REGISTER.versions if v.rule_id == "RCE.KYP_PROPOSAL"][0]
        assert kyp.status is RuleStatus.UNDER_CONSIDERATION and not kyp.is_contract_requirement

    def test_rce_material_is_never_a_contract_requirement(self):
        assert all(not v.is_contract_requirement for v in REGISTER.versions)
        contract = RuleVersion("X", "1", RuleStatus.EFFECTIVE, AuthorityLayer.EXECUTED_CONTRACT, "SOW", effective_from=date(2026, 7, 1))
        assert contract.is_contract_requirement

    def test_tier2_cms_directory_evidence_window(self):
        vs = REGISTER.applicable("RCE.XP_VETTING.TIER2_CMS_DIRECTORY", date(2026, 10, 1))
        assert [v.version for v in vs] == ["2.0"]
        assert REGISTER.applicable("RCE.XP_VETTING.TIER2_CMS_DIRECTORY", date(2027, 1, 1)) == []

    def test_register_has_no_overlaps_and_every_entry_is_cited_and_verified(self):
        for d in (date(2026, 7, 15), date(2026, 8, 15), date(2026, 9, 12), date(2026, 10, 1)):
            assert overlapping(REGISTER, d) == {}
        for v in REGISTER.versions:
            assert v.citation_url and v.citation_url.startswith("RCE-published")
            assert v.last_verified == date(2026, 9, 12)
            assert v.authority_layer is AuthorityLayer.RCE_GOVERNING_MATERIAL

    def test_register_rejects_mismatched_versions(self):
        reg = PolicyRegister()
        with pytest.raises(ValueError):
            reg.add(RuleDefinition("A", "P", "t", ""), RuleVersion("B", "1", RuleStatus.DRAFT, AuthorityLayer.DOCUACTION_PROPOSAL, "d"))

    def test_status_report_carries_status_on_date(self):
        rows = REGISTER.status_report(date(2026, 9, 12))
        by = {(r["rule_id"], r["version"]): r["status_on_date"] for r in rows}
        assert by[("RCE.XP_VETTING", "1.0")] == "SUPERSEDED" and by[("RCE.XP_VETTING", "2.0")] == "EFFECTIVE"
        assert by[("RCE.KYP_PROPOSAL", "2026-02")] == "UNDER_CONSIDERATION"
