"""Architecture v1.0 proofs — synthetic only.

PROOF 1 multi-jurisdiction entity · PROOF 2 same address / different role ·
PROOF 3 temporal relationship; plus the location-role model, typed
relationships, the source/question authority matrix (no voting), source
rights with human authorization, the Entity Evidence Profile, evidence
inquiry subjects, the 25K evidence plan and the IQVIA arrival protocol.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.entity_intelligence import authority_matrix as am
from app.core.entity_intelligence.assessment import SystemEvidenceAssessment, assess
from app.core.entity_intelligence.comparison import (Dimension, LocationSignal, NameSignal, RelationshipSignal,
                                                      compare_all, compare_locations, compare_relationships)
from app.core.entity_intelligence.delta import DeltaScope, DeltaType, compute_deltas
from app.core.entity_intelligence.evidence_plan import SourceRecord, plan_evidence
from app.core.entity_intelligence.normalize import NORMALIZATION_VERSION, normalize_address
from app.core.entity_intelligence.observations import (ADMINISTRATIVE_ROLES, CARE_SITE_ROLES, DeliveryPath,
                                                        EvidenceObservation, LocationRole, NameKind, ObservationType,
                                                        Provenance, RelationshipDirection, SourceAuthority,
                                                        location_observation, relationship_observation)
from app.core.entity_intelligence.ports import AcquisitionMode, DataRights, DataRightsClass, RightsStatus, StateRegistryCapability
from app.core.entity_intelligence.profile import (EntityEvidenceProfile, EvidenceInquiry, InquirySubjectType,
                                                   ProfileFacet)
from app.evidence_sources.iqvia_onekey import adapter as iqvia
from app.evidence_sources.nppes_v2.adapter import SOURCE_ID as NPPES
from ei_fixtures import BALTIMORE, FREDERICK, PROGRAM_SOURCE, delivered

A = SystemEvidenceAssessment
REGISTRY = "SYNTHETIC_STATE_REGISTRY"
ANNAPOLIS = {"line1": "7 SYNTHETIC AGENT ST", "line2": "", "city": "ANNAPOLIS", "state": "MD", "postal_code": "21401"}
BALTIMORE_CORP = {"line1": "100 SYNTHETIC WAY", "line2": "STE 900", "city": "BALTIMORE", "state": "MD", "postal_code": "21201"}


def prov(owner="synthetic"):
    return Provenance(source_owner=owner, delivery_path=DeliveryPath.FILE_DOWNLOAD, source_record_ref="s")


def nppes(name="ABC HEALTHCARE LLC", npi="9999900001", primary=BALTIMORE, additional=None, dba=None):
    common = dict(canonical_entity_id="ent-1", source_id=NPPES, source_authority=SourceAuthority.FEDERAL_REGISTRY,
                  provenance=prov("CMS NPPES"))
    out = [EvidenceObservation(observation_type=ObservationType.IDENTIFIER, role="NPI",
                               observed_value={"value": npi, "entity_type": "2"}, **common),
           EvidenceObservation(observation_type=ObservationType.NAME, role=NameKind.LEGAL_BUSINESS_NAME.value,
                               observed_value={"name": name}, **common),
           location_observation(role=LocationRole.PRIMARY_PRACTICE_LOCATION.value, raw_address=primary, **common)]
    if additional:
        out.append(location_observation(role=LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, raw_address=additional, **common))
    if dba:
        out.append(EvidenceObservation(observation_type=ObservationType.NAME, role=NameKind.DOING_BUSINESS_AS.value,
                                       observed_value={"name": dba, "type_code": "3"}, **common))
    return out


def registry(legal="ABC HEALTHCARE LLC", agent_addr=ANNAPOLIS, hq=BALTIMORE_CORP, domestic="DE", foreign=("MD",),
             foreign_valid=("2019-03-01", None), source=REGISTRY):
    common = dict(canonical_entity_id="ent-1", source_id=source, source_authority=SourceAuthority.STATE_REGISTRY,
                  provenance=prov("synthetic state registry"))
    out = [EvidenceObservation(observation_type=ObservationType.NAME, role=NameKind.LEGAL_BUSINESS_NAME.value,
                               observed_value={"name": legal}, **common),
           location_observation(role=LocationRole.REGISTERED_AGENT_ADDRESS.value, raw_address=agent_addr, **common),
           location_observation(role=LocationRole.CORPORATE_HEADQUARTERS.value, raw_address=hq, **common),
           relationship_observation(program_context="STATE_REGISTRY", kind="DOMESTIC_IN", subject=legal, obj=domestic, **common)]
    for st in foreign:
        out.append(relationship_observation(program_context="STATE_REGISTRY", kind="FOREIGN_QUALIFIED_IN", subject=legal,
                                            obj=st, valid_from=foreign_valid[0], valid_to=foreign_valid[1], **common))
    return out


def run(cur, sources, rels=None):
    comps = []
    for s in sources:
        comps += compare_all(cur, source_id=s, relationship_kinds=rels)
    return assess(comps, []), comps


# ── PROOF 1: multi-jurisdiction entity ─────────────────────────────────────

class TestProof1MultiJurisdictionEntity:
    def _current(self):
        return (delivered("ABC MOBILE CLINIC", FREDERICK, relationships=[{"kind": "TEFCA:MANAGED_BY_QHIN", "name": "SYNTHETIC QHIN"}])
                + nppes(additional=FREDERICK, dba="ABC MOBILE CLINIC") + registry())

    def test_no_false_name_conflict_dba_explained(self):
        res, comps = run(self._current(), [NPPES, REGISTRY], rels=["TEFCA:MANAGED_BY_QHIN"])
        names = {c.source_id: c.signal for c in comps if c.dimension is Dimension.NAME_IDENTITY}
        assert names[NPPES] is NameSignal.DBA_MATCH_IDENTIFIED
        assert names[REGISTRY] is NameSignal.NAME_CONFLICT or names[REGISTRY] is NameSignal.INSUFFICIENT_NAME_EVIDENCE
        # The registry carries only the legal name; the delivered DBA is explained by NPPES. Human review still required.
        assert res.requires_human_review

    def test_no_false_location_conflict_additional_location_explained(self):
        _, comps = run(self._current(), [NPPES, REGISTRY])
        locs = {c.source_id: c.signal for c in comps if c.dimension is Dimension.LOCATION_IDENTITY}
        assert locs[NPPES] is LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH
        assert locs[REGISTRY] is not LocationSignal.PRIMARY_LOCATION_MATCH

    def test_registered_agent_never_treated_as_site_of_care(self):
        cur = delivered("ABC HEALTHCARE LLC", ANNAPOLIS) + registry()
        res = compare_locations(cur, source_id=REGISTRY)
        assert res.signal is LocationSignal.ROLE_ASSIGNMENT_DIFFERS
        assert "REGISTERED_AGENT_ADDRESS" in res.explanation and "does not infer" in res.explanation

    def test_no_false_jurisdiction_conflict(self):
        cur = self._current()
        dom = compare_relationships(cur, source_id=REGISTRY, kind_code="STATE_REGISTRY:DOMESTIC_IN")
        fq = compare_relationships(cur, source_id=REGISTRY, kind_code="STATE_REGISTRY:FOREIGN_QUALIFIED_IN")
        assert dom.signal is not RelationshipSignal.RELATIONSHIP_CONFLICT
        assert fq.signal is not RelationshipSignal.RELATIONSHIP_CONFLICT
        qhin = compare_relationships(cur, source_id=REGISTRY, kind_code="TEFCA:MANAGED_BY_QHIN")
        assert qhin.signal is RelationshipSignal.RELATIONSHIP_NOT_COMPARABLE   # corporate ≠ program relationship

    def test_overall_is_explainable_variation_with_human_review(self):
        # Registry legal-name conflict against a DBA delivery is a genuine open question; the engine keeps it visible.
        res, _ = run(self._current(), [NPPES])
        assert res.assessment is A.EXPLAINABLE_VARIATION_IDENTIFIED and res.requires_human_review
        res2, _ = run(self._current(), [NPPES, REGISTRY])
        assert res2.assessment in (A.EXPLAINABLE_VARIATION_IDENTIFIED, A.CONFLICTING_EVIDENCE)
        assert res2.requires_human_review

    def test_profile_facets_and_inquiry(self):
        p = EntityEvidenceProfile.from_observations("ent-1", self._current())
        assert p.observations(ProfileFacet.BUSINESS_IDENTITY) and p.observations(ProfileFacet.LOCATION)
        assert any(o.role == "STATE_REGISTRY:FOREIGN_QUALIFIED_IN" for o in p.observations(ProfileFacet.RELATIONSHIPS))
        assert set(p.sources) == {PROGRAM_SOURCE, NPPES, REGISTRY}
        inq = EvidenceInquiry(InquirySubjectType.ORGANIZATION, {"name": "ABC MOBILE CLINIC"}, ["IDENTITY", "LOCATION"])
        assert inq.to_dict()["subject_type"] == "ORGANIZATION"


# ── PROOF 2: same address, different role ───────────────────────────────────

class TestProof2SameAddressDifferentRole:
    def test_role_assignment_differs_not_match_not_conflict(self):
        cur = delivered("ABC HEALTHCARE LLC", BALTIMORE) + nppes(primary=BALTIMORE) + registry(agent_addr=BALTIMORE, hq=ANNAPOLIS)
        n = compare_locations(cur, source_id=NPPES)
        r = compare_locations(cur, source_id=REGISTRY)
        assert n.signal is LocationSignal.PRIMARY_LOCATION_MATCH
        assert r.signal is LocationSignal.ROLE_ASSIGNMENT_DIFFERS
        assert r.signal not in (LocationSignal.PRIMARY_LOCATION_MATCH, LocationSignal.NORMALIZED_LOCATION_MATCH, LocationSignal.LOCATION_CONFLICT)
        res, _ = run(cur, [NPPES, REGISTRY])
        assert res.assessment is not A.CONFLICTING_EVIDENCE

    def test_care_role_at_same_address_wins_over_administrative_role(self):
        cur = delivered("S", BALTIMORE) + registry(agent_addr=BALTIMORE, hq=ANNAPOLIS) + [
            location_observation(canonical_entity_id="ent-1", source_id=REGISTRY, role=LocationRole.SITE_OF_CARE.value,
                                 raw_address=BALTIMORE, source_authority=SourceAuthority.STATE_REGISTRY, provenance=prov())]
        assert compare_locations(cur, source_id=REGISTRY).signal is LocationSignal.NORMALIZED_LOCATION_MATCH

    def test_role_sets_are_disjoint_and_cover_the_model(self):
        assert not (CARE_SITE_ROLES & ADMINISTRATIVE_ROLES)
        for r in ("CORPORATE_HEADQUARTERS", "PRINCIPAL_OFFICE", "PRIMARY_PRACTICE_LOCATION", "ADDITIONAL_PRACTICE_LOCATION",
                  "SITE_OF_CARE", "MOBILE_HOME_BASE", "REGISTERED_AGENT_ADDRESS", "MAILING_ADDRESS", "BRANCH_LOCATION",
                  "UNKNOWN_SOURCE_ROLE"):
            assert LocationRole[r]


# ── PROOF 3: temporal relationship ──────────────────────────────────────────

class TestProof3TemporalRelationship:
    def test_source_no_longer_reports_observation(self):
        prior = registry(foreign=("MD",))
        current = registry(foreign=())
        deltas = compute_deltas(prior, current)
        gone = [d for d in deltas if d.delta_type is DeltaType.SOURCE_NO_LONGER_REPORTS_OBSERVATION]
        assert len(gone) == 1 and gone[0].scope is DeltaScope.RELATIONSHIP and not gone[0].subject
        text = gone[0].explanation.lower()
        for banned in ("relationship_ended", "ended", "invalid", "adverse finding", "revoked", "terminated"):
            assert banned not in text.replace("does not establish the end of a relationship", "")
        assert "does not establish" in text

    def test_relationship_period_differs_is_not_contradiction(self):
        legal = "ABC HEALTHCARE LLC"
        d = [relationship_observation(canonical_entity_id="ent-1", source_id=PROGRAM_SOURCE, program_context="STATE_REGISTRY",
                                      kind="FOREIGN_QUALIFIED_IN", subject=legal, obj="MD", valid_from="2019-03-01", valid_to=None,
                                      source_authority=SourceAuthority.PROGRAM_DELIVERY, provenance=prov())]
        cur = d + registry(foreign_valid=("2019-03-01", "2025-12-31"))
        res = compare_relationships(cur, source_id=REGISTRY, kind_code="STATE_REGISTRY:FOREIGN_QUALIFIED_IN")
        assert res.signal is RelationshipSignal.RELATIONSHIP_PERIOD_DIFFERS
        assert "not an automatic contradiction" in res.explanation
        overall = assess([res], [])
        assert overall.assessment is not A.CONFLICTING_EVIDENCE

    def test_never_infers_ended_invalid_or_adverse(self):
        from app.core.entity_intelligence.explanations import TEMPLATES
        for key in ("DELTA_SOURCE_NO_LONGER_REPORTS", "RELATIONSHIP_PERIOD_DIFFERS", "ROLE_ASSIGNMENT_DIFFERS"):
            t = TEMPLATES[key].lower()
            for banned in ("registration invalid", "relationship ended", "adverse finding", "revoked", "no longer valid"):
                assert banned not in t


# ── typed relationships and address observations preserve everything ───────

class TestTypedRelationshipsAndAddresses:
    def test_relationship_preserves_all_fields(self):
        o = relationship_observation(canonical_entity_id="e", source_id=REGISTRY, program_context="STATE_REGISTRY",
                                     kind="FOREIGN_QUALIFIED_IN", subject="ABC Healthcare LLC", obj="Maryland",
                                     direction=RelationshipDirection.SUBJECT_TO_OBJECT, raw_relationship="Foreign Qualified: MD",
                                     valid_from="2019-03-01", valid_to=None, observed_at="2026-09-01", source_delivery_id="ed-1",
                                     source_authority=SourceAuthority.STATE_REGISTRY, provenance=prov())
        v = o.observed_value
        assert v["subject"] and v["relationship"] == "FOREIGN_QUALIFIED_IN" and v["object"] == "Maryland"
        assert v["direction"] == "SUBJECT_TO_OBJECT" and v["program_context"] == "STATE_REGISTRY"
        assert v["raw_relationship"] == "Foreign Qualified: MD" and "|foreign_qualified_in|" in v["normalized_relationship"]
        assert o.role == "STATE_REGISTRY:FOREIGN_QUALIFIED_IN" and o.effective_from == "2019-03-01" and o.observed_at == "2026-09-01"
        assert o.to_dict()["provenance"]["source_owner"] == "synthetic"

    def test_address_preserves_raw_and_carries_normalization_version(self):
        raw = {"line1": "100 North Main Street, Suite 200", "city": "Baltimore", "state": "md", "postal_code": "21201-1234"}
        o = location_observation(canonical_entity_id="e", source_id=NPPES, role=LocationRole.PRIMARY_PRACTICE_LOCATION.value,
                                 raw_address=raw, source_authority=SourceAuthority.FEDERAL_REGISTRY, provenance=prov())
        assert o.observed_value["raw_address"] == raw                      # untouched
        assert o.normalized_value["line1"] == "100 n main st ste 200"     # comparison key beside it
        assert o.normalized_value["normalization_version"] == NORMALIZATION_VERSION
        assert normalize_address(raw)["normalization_version"] == NORMALIZATION_VERSION

    def test_same_address_different_role_are_distinct_observations(self):
        a = location_observation(canonical_entity_id="e", source_id=REGISTRY, role=LocationRole.REGISTERED_AGENT_ADDRESS.value,
                                 raw_address=BALTIMORE, source_authority=SourceAuthority.STATE_REGISTRY, provenance=prov())
        b = location_observation(canonical_entity_id="e", source_id=NPPES, role=LocationRole.PRIMARY_PRACTICE_LOCATION.value,
                                 raw_address=BALTIMORE, source_authority=SourceAuthority.FEDERAL_REGISTRY, provenance=prov())
        assert a.normalized_value["street"] == b.normalized_value["street"] and a.role != b.role and a.content_hash != b.content_hash


# ── source / question authority matrix ─────────────────────────────────────

class TestAuthorityMatrix:
    def test_versioned_and_pending_human_approval(self):
        m = am.MATRIX_V1
        assert m.matrix_version == "1.0" and m.effective_date == date(2026, 9, 13) and m.supersedes_version is None
        assert m.change_reason and m.approved_by == "PENDING_HUMAN_APPROVAL"

    def test_nppes_supports_identity_questions_only(self):
        m, Q = am.MATRIX_V1, am.EvidenceQuestion
        for q in (Q.NPI_OBSERVATION, Q.ORGANIZATION_NAME, Q.OTHER_NAME_DBA, Q.PRACTICE_LOCATION):
            assert m.can_support("NPPES_V2", q)
        for q in (Q.LICENSURE, Q.CREDENTIALING, Q.CORPORATE_GOOD_STANDING, Q.TEFCA_ELIGIBILITY, Q.CONTRACT_COMPLIANCE):
            assert m.answer("NPPES_V2", q) is am.AuthorityAnswer.CANNOT_ALONE_ESTABLISH

    def test_ppef_five_file_model_and_no_tefca_eligibility(self):
        m, Q = am.MATRIX_V1, am.EvidenceQuestion
        assert m.can_support("CMS_PPEF", Q.MEDICARE_ENROLLMENT_OBSERVATION) and m.can_support("CMS_PPEF", Q.BENEFIT_REASSIGNMENT)
        assert m.can_support("CMS_PPEF", Q.ADDITIONAL_NPI) and m.can_support("CMS_PPEF", Q.PRACTICE_LOCATION)
        assert not m.can_support("CMS_PPEF", Q.TEFCA_ELIGIBILITY)

    def test_state_registry_supports_corporate_questions_not_healthcare(self):
        m, Q = am.MATRIX_V1, am.EvidenceQuestion
        for q in (Q.CORPORATE_LEGAL_NAME, Q.CORPORATE_REGISTRATION, Q.DOMESTIC_FOREIGN_ROLE, Q.REGISTERED_AGENT, Q.CORPORATE_STATUS):
            assert m.can_support("STATE_CORPORATE_REGISTRY", q)
        for q in (Q.LICENSURE, Q.PRACTICE_LOCATION, Q.SITE_OF_CARE, Q.MEDICARE_ENROLLMENT_OBSERVATION, Q.TEFCA_ELIGIBILITY):
            assert not m.can_support("STATE_CORPORATE_REGISTRY", q)

    def test_no_single_source_may_establish_the_never_alone_questions(self):
        m = am.MATRIX_V1
        for q in am.NO_SINGLE_SOURCE_QUESTIONS:
            assert m.sources_for(q) == [], q

    def test_unknown_pair_is_unknown_not_permitted(self):
        assert am.MATRIX_V1.answer("SYNTHETIC_NEW_SOURCE", am.EvidenceQuestion.NPI_OBSERVATION) is am.AuthorityAnswer.UNKNOWN
        assert not am.MATRIX_V1.can_support("SYNTHETIC_NEW_SOURCE", am.EvidenceQuestion.NPI_OBSERVATION)

    def test_no_numbers_anywhere(self):
        import inspect
        src = inspect.getsource(am)
        for banned in ("0.87", "87%", "= 0.", "weight=", "score=", "confidence="):
            assert banned not in src.lower()
        assert not any(hasattr(e, "weight") or hasattr(e, "score") for e in am.MATRIX_V1.entries)
        assert all(isinstance(e.answer, am.AuthorityAnswer) for e in am.MATRIX_V1.entries)


# ── source rights: human authorization ─────────────────────────────────────

class TestSourceRightsAuthorization:
    def test_statuses(self):
        for s in ("REVIEWED", "ASSUMED_PUBLIC_DOMAIN", "TERMS_REVIEW_REQUIRED", "PENDING", "NOT_PERMITTED", "UNKNOWN"):
            assert RightsStatus[s]

    @pytest.mark.parametrize("who", [None, "", "system", "Fable", "Claude", "DocuAction AI", "automation-bot"])
    def test_ai_or_system_cannot_self_authorize(self, who):
        with pytest.raises(ValueError):
            DataRights(DataRightsClass.PUBLIC, RightsStatus.REVIEWED, snapshot_retention_allowed=True, reviewed_by=who)
        with pytest.raises(ValueError):
            DataRights(DataRightsClass.PUBLIC, RightsStatus.REVIEWED, redistribution_allowed=True, reviewed_by=who)
        with pytest.raises(ValueError):
            DataRights(DataRightsClass.PUBLIC, RightsStatus.REVIEWED, historical_comparison_allowed=True, reviewed_by=who)

    def test_human_reviewer_with_reviewed_status_may_grant(self):
        r = DataRights(DataRightsClass.PUBLIC, RightsStatus.REVIEWED, snapshot_retention_allowed=True,
                       reviewed_by="AGT Program Manager (synthetic)", review_date="2026-09-13",
                       official_reference="synthetic terms", reference_version="v0")
        assert r.to_dict()["reviewed_by"].startswith("AGT")

    def test_assumed_public_domain_cannot_carry_retention_rights(self):
        with pytest.raises(ValueError):
            DataRights(DataRightsClass.PUBLIC, RightsStatus.ASSUMED_PUBLIC_DOMAIN, snapshot_retention_allowed=True,
                       reviewed_by="AGT Program Manager (synthetic)")

    def test_unknown_is_not_permitted(self):
        r = DataRights(DataRightsClass.RESTRICTED, RightsStatus.UNKNOWN)
        assert not any([r.storage_allowed, r.raw_storage_allowed, r.redistribution_allowed, r.snapshot_retention_allowed,
                        r.historical_comparison_allowed, r.client_display_allowed, r.derived_observation_permission])


# ── state provider contract: acquisition modes ─────────────────────────────

class TestStateProviderContract:
    def test_acquisition_modes(self):
        for m in ("OFFICIAL_API", "OFFICIAL_BULK_DATA", "PERMITTED_OFFICIAL_SEARCH", "OFFICIAL_DOCUMENT_RETRIEVAL",
                  "PAID_OFFICIAL_SERVICE", "CONTROLLED_MANUAL_VERIFICATION", "LICENSED_COMMERCIAL_SOURCE", "UNSUPPORTED"):
            assert AcquisitionMode[m]
        assert StateRegistryCapability("XX").acquisition_mode is AcquisitionMode.UNSUPPORTED

    def test_no_state_adapter_and_no_scraper_exists(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[1] / "app" / "evidence_sources"
        names = {p.name for p in root.iterdir() if p.is_dir()}
        assert not any("state" in n.lower() or "delaware" in n.lower() for n in names)


# ── IQVIA arrival protocol ─────────────────────────────────────────────────

class TestIqviaArrivalProtocol:
    def test_status_flags_and_steps(self):
        assert iqvia.ARRIVAL_STATUS == ("AWAITING_SCHEMA", "AWAITING_TERMS", "AWAITING_ACTUAL_DELIVERY")
        assert [s for _, s in iqvia.ARRIVAL_PROTOCOL][:3] == ["RECEIVE", "PRESERVE_ORIGINAL", "HASH"]
        assert iqvia.ARRIVAL_PROTOCOL[-1] == (10, "CONTROLLED_IMPLEMENTATION_AFTER_AUTHORIZATION")

    def test_operational_use_not_permitted_without_human_authorization(self):
        assert iqvia.operational_use_permitted() is False
        assert iqvia.operational_use_permitted(human_authorization_reference="AUTH-1") is False   # rights not reviewed
        assert iqvia.operational_use_permitted(human_authorization_reference="AUTH-1", rights_status=RightsStatus.REVIEWED) is True
        assert iqvia.DATA_RIGHTS.status is RightsStatus.TERMS_REVIEW_REQUIRED

    def test_observations_still_refused(self, monkeypatch):
        from ei_fixtures import enable_all
        enable_all(monkeypatch)
        with pytest.raises(iqvia.SchemaUnknown):
            iqvia.IQVIAOneKeyDeliveryAdapter().observations_for(canonical_entity_id="e", identifier="x", provenance=prov())


# ── 25K scale: source records != review cases ──────────────────────────────

class TestEvidencePlan:
    def test_dedup_by_npi_then_name_zip(self):
        recs = [SourceRecord("r1", npi="9999900001", name="SYNTHETIC ONE LLC", address=BALTIMORE),
                SourceRecord("r2", npi="9999900001", name="Synthetic One, L.L.C.", address=BALTIMORE),   # duplicate by NPI
                SourceRecord("r3", name="SYNTHETIC TWO INC", address=FREDERICK),
                SourceRecord("r4", name="synthetic two inc.", address=FREDERICK),                        # duplicate by name+zip
                SourceRecord("r5", name="SYNTHETIC THREE"),                                              # no key
                SourceRecord("r6", npi="9999900002", name="SYNTHETIC FOUR", address=BALTIMORE, taxonomy_medicare_relevant=False)]
        plan = plan_evidence(recs)
        assert plan.source_record_count == 6 and plan.canonical_candidate_count == 4 and plan.duplicate_record_count == 2
        # two NPI candidates × 3 questions + name-keyed r3 + record-only r5 (name lookup, no NPI) = 8
        assert plan.lookups_by_source["NPPES_V2"] == 3 * 2 + 1 + 1
        assert plan.lookups_by_source["CMS_PPEF"] == 1                # only the NPI candidate whose taxonomy is not known-irrelevant
        assert plan.normalizations_performed == 6
        assert plan.to_dict()["note"].startswith("25K source records != 25K review cases")

    def test_linear_and_bounded(self):
        import time
        recs = [SourceRecord(f"r{i}", npi=f"9999{i % 5000:06d}", name=f"SYNTHETIC {i % 5000}", address=BALTIMORE) for i in range(20000)]
        t = time.perf_counter(); plan = plan_evidence(recs); dt = time.perf_counter() - t
        assert plan.canonical_candidate_count == 5000 and plan.duplicate_record_count == 15000
        assert dt < 10.0
