"""Adversarial corpus A–R, no-voting proofs, explanation-template safety and
assessment-never-a-category proofs. Every case is synthetic.

Each case states: the situation, what a careless system would say, and what
this engine must say instead."""
from __future__ import annotations

import re

import pytest

from app.core.entity_intelligence.assessment import FORBIDDEN_TERMS, SystemEvidenceAssessment, assess
from app.core.entity_intelligence.comparison import (Dimension, IdentifierSignal, LocationSignal, NameSignal,
                                                      RelationshipSignal, compare_all)
from app.core.entity_intelligence.delta import compute_deltas, explain_deltas
from app.core.entity_intelligence.explanations import TEMPLATES, explain
from app.core.entity_intelligence.normalize import normalize_address, normalize_name
from app.core.entity_intelligence.observations import (DeliveryPath, EvidenceObservation, LocationRole, NameKind,
                                                        ObservationType, Provenance, SourceAuthority)
from app.core.entity_intelligence.service import EntityIntelligenceService
from app.evidence_sources.nppes_v2.adapter import SOURCE_ID as NPPES, NppesV2Adapter, NppesV2Bundle
from app.evidence_sources.nppes_v2.adapter import SIGNAL_DBA
from ei_fixtures import (BALTIMORE, BALTIMORE_ALT_STREET, FREDERICK, MAIN_HEADER, OTHER_NAME_HEADER, PL_HEADER,
                         ROCKVILLE, csv_text, delivered, enable_all, main_row, other_name_row, pl_row, unavailable)

A = SystemEvidenceAssessment
OTHER = "SYNTHETIC_SECOND_SOURCE"


def src(name=None, addr=None, npi="9999900001", source=NPPES, kind=NameKind.LEGAL_BUSINESS_NAME,
        role=LocationRole.PRIMARY_PRACTICE_LOCATION, authority=SourceAuthority.FEDERAL_REGISTRY, entity="ent-1",
        rel=None):
    prov = Provenance(source_owner="synthetic", delivery_path=DeliveryPath.FILE_DOWNLOAD, source_record_ref="s")
    common = dict(canonical_entity_id=entity, source_id=source, source_authority=authority, provenance=prov)
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
    if rel:
        out.append(EvidenceObservation(observation_type=ObservationType.RELATIONSHIP, role=rel["kind"],
                                       observed_value={"related_entity_name": rel["name"]}, **common))
    return out


def run(current, prior=None, sources=(NPPES,), rels=None):
    comps = []
    for s in sources:
        comps.extend(compare_all(current, source_id=s, identifier_system="NPI", relationship_kinds=rels))
    deltas = explain_deltas(compute_deltas(prior, current), comps) if prior is not None else []
    return assess(comps, deltas), comps


def sig(comps, dim):
    return [c.signal for c in comps if c.dimension is dim]


class TestAdversarialCorpus:
    def test_A_everything_agrees(self):
        res, _ = run(delivered("SYNTHETIC ORG LLC", BALTIMORE) + src("SYNTHETIC ORG LLC", BALTIMORE))
        assert res.assessment is A.EVIDENCE_CORROBORATES

    def test_B_delivered_dba_recorded_by_source(self):
        cur = delivered("SYNTHETIC CLINIC", BALTIMORE) + src("SYNTHETIC ORG LLC", BALTIMORE) + \
              src("SYNTHETIC CLINIC", kind=NameKind.DOING_BUSINESS_AS, npi=None)
        res, comps = run(cur)
        assert res.assessment is A.EXPLAINABLE_VARIATION_IDENTIFIED
        assert NameSignal.DBA_MATCH_IDENTIFIED in sig(comps, Dimension.NAME_IDENTITY)

    def test_C_delivered_former_legal_name_is_not_dba(self):
        cur = delivered("OLD SYNTHETIC NAME", BALTIMORE) + src("SYNTHETIC ORG LLC", BALTIMORE) + \
              src("OLD SYNTHETIC NAME", kind=NameKind.FORMER_LEGAL_BUSINESS_NAME, npi=None)
        res, comps = run(cur)
        assert NameSignal.FORMER_NAME_MATCH in sig(comps, Dimension.NAME_IDENTITY)
        assert NameSignal.DBA_MATCH_IDENTIFIED not in sig(comps, Dimension.NAME_IDENTITY)
        assert res.assessment is A.EXPLAINABLE_VARIATION_IDENTIFIED

    def test_D_other_name_type5_is_not_dba(self):
        cur = delivered("SYNTHETIC OTHER", BALTIMORE) + src("SYNTHETIC ORG LLC", BALTIMORE) + \
              src("SYNTHETIC OTHER", kind=NameKind.OTHER_NAME, npi=None)
        _, comps = run(cur)
        s = sig(comps, Dimension.NAME_IDENTITY)
        assert s == [NameSignal.OTHER_NAME_MATCH]
        text = [c.explanation for c in comps if c.dimension is Dimension.NAME_IDENTITY][0]
        assert "does not classify it as Doing Business As" in text

    def test_E_same_core_different_suffix_is_ambiguous(self):
        _, comps = run(delivered("SYNTHETIC ORG INC", BALTIMORE) + src("SYNTHETIC ORG LLC", BALTIMORE))
        assert sig(comps, Dimension.NAME_IDENTITY) == [NameSignal.AMBIGUOUS_NAME]

    def test_F_llc_vs_foundation_is_conflict_not_similarity(self):
        res, comps = run(delivered("SYNTHETIC ORG LLC", BALTIMORE) + src("SYNTHETIC ORG FOUNDATION", BALTIMORE))
        assert sig(comps, Dimension.NAME_IDENTITY) == [NameSignal.NAME_CONFLICT]
        assert res.assessment is A.CONFLICTING_EVIDENCE

    def test_G_additional_practice_location(self):
        cur = delivered("SYNTHETIC ORG LLC", FREDERICK) + src("SYNTHETIC ORG LLC", BALTIMORE) + \
              src(addr=FREDERICK, role=LocationRole.ADDITIONAL_PRACTICE_LOCATION, npi=None)
        res, comps = run(cur)
        assert LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH in sig(comps, Dimension.LOCATION_IDENTITY)
        assert res.assessment is A.EXPLAINABLE_VARIATION_IDENTIFIED

    def test_H_mailing_only_match_is_flagged_not_practice(self):
        cur = delivered("SYNTHETIC ORG LLC", ROCKVILLE) + src("SYNTHETIC ORG LLC", BALTIMORE) + \
              src(addr=ROCKVILLE, role=LocationRole.MAILING_LOCATION, npi=None)
        _, comps = run(cur)
        assert sig(comps, Dimension.LOCATION_IDENTITY) == [LocationSignal.MAILING_LOCATION_MATCH]

    def test_I_same_city_different_street_is_ambiguous(self):
        _, comps = run(delivered("S", BALTIMORE_ALT_STREET) + src("S", BALTIMORE))
        assert sig(comps, Dimension.LOCATION_IDENTITY) == [LocationSignal.AMBIGUOUS_LOCATION]

    def test_J_different_city_is_conflict(self):
        res, comps = run(delivered("S", FREDERICK) + src("S", BALTIMORE))
        assert sig(comps, Dimension.LOCATION_IDENTITY) == [LocationSignal.LOCATION_CONFLICT]
        assert res.assessment is A.CONFLICTING_EVIDENCE

    def test_K_no_source_record_is_insufficient_not_conflict(self):
        res, comps = run(delivered("S", BALTIMORE))
        assert sig(comps, Dimension.ORGANIZATION_IDENTITY) == [IdentifierSignal.MISSING_IDENTIFIER]
        assert res.assessment is A.INSUFFICIENT_EVIDENCE
        assert all(c.signal.value != "LOCATION_CONFLICT" for c in comps)

    def test_L_source_unavailable_is_not_no_match(self):
        res, comps = run(delivered("S", BALTIMORE) + [unavailable(NPPES)])
        assert res.assessment is A.SOURCE_UNAVAILABLE
        assert all(c.signal.value == "SOURCE_UNAVAILABLE" for c in comps)
        assert "fact about access" in comps[0].explanation

    def test_M_delivered_without_address_is_insufficient_location(self):
        _, comps = run(delivered("S", None) + src("S", BALTIMORE))
        assert sig(comps, Dimension.LOCATION_IDENTITY) == [LocationSignal.INSUFFICIENT_LOCATION_EVIDENCE]

    def test_N_delivered_without_npi_is_missing_identifier(self):
        _, comps = run(delivered("S", BALTIMORE, npi=None) + src("S", BALTIMORE))
        assert sig(comps, Dimension.ORGANIZATION_IDENTITY) == [IdentifierSignal.MISSING_IDENTIFIER]

    def test_O_two_source_records_same_npi_is_multiple_candidates(self):
        cur = delivered("S", BALTIMORE) + src("S", BALTIMORE) + src("S TWO", BALTIMORE_ALT_STREET)
        _, comps = run(cur)
        assert sig(comps, Dimension.ORGANIZATION_IDENTITY) == [IdentifierSignal.MULTIPLE_CANDIDATE_ENTITIES]

    def test_P_relationship_kinds_never_interchangeable(self):
        cur = delivered("S", BALTIMORE, relationships=[{"kind": "QHIN_PARTICIPANT", "name": "SYNTHETIC QHIN"}]) + \
              src("S", BALTIMORE, rel={"kind": "CORPORATE_PARENT", "name": "SYNTHETIC QHIN"})
        res, comps = run(cur, rels=["QHIN_PARTICIPANT"])
        assert sig(comps, Dimension.RELATIONSHIP_IDENTITY) == [RelationshipSignal.RELATIONSHIP_NOT_COMPARABLE]
        assert res.assessment is not A.CONFLICTING_EVIDENCE

    def test_Q_deactivated_npi_with_replacement_is_reported_not_judged(self, monkeypatch):
        enable_all(monkeypatch)
        main = csv_text(MAIN_HEADER, [main_row("9999900001", "SYNTHETIC ORG LLC", practice=BALTIMORE,
                                               deactivation="01/02/2026", replacement="9999900002")])
        obs = NppesV2Adapter(NppesV2Bundle.from_texts(main_text=main)).observations_for(
            canonical_entity_id="e", identifier="9999900001")
        ident = [o for o in obs if o.observation_type is ObservationType.IDENTIFIER][0]
        assert ident.observed_value["deactivation_date"] == "01/02/2026"
        assert ident.observed_value["replacement_npi"] == "9999900002"
        assert ident.effective_to == "01/02/2026"
        run_ = EntityIntelligenceService().evaluate(canonical_entity_id="e",
                                                    current=delivered("SYNTHETIC ORG LLC", BALTIMORE) + obs,
                                                    source_ids=[NPPES])
        text = str(run_.to_dict()).lower()
        for banned in ("not operating", "ceased", "closed", "defunct", "inactive entity"):
            assert banned not in text

    def test_R_mobile_facility_claim_is_never_inferred(self):
        """A delivered address that matches no NPPES location and no locality is
        a LOCATION_CONFLICT. The engine must not explain it away as a mobile
        facility; only a source that SAYS mobile may assign that role."""
        res, comps = run(delivered("SYNTHETIC MOBILE CLINIC LLC", ROCKVILLE) + src("SYNTHETIC MOBILE CLINIC LLC", BALTIMORE))
        assert sig(comps, Dimension.LOCATION_IDENTITY) == [LocationSignal.LOCATION_CONFLICT]
        blob = " ".join(c.explanation for c in comps).lower() + " ".join(res.open_questions).lower()
        assert "mobile" not in blob
        assert all(c.detail.get("matched_role") != LocationRole.MOBILE_FACILITY.value for c in comps)
        assert LocationRole.MOBILE_FACILITY.value not in [o.role for o in src("S", BALTIMORE)]


class TestNoVoting:
    def test_three_agree_one_conflicts_is_still_conflict(self):
        cur = delivered("S", BALTIMORE)
        for i, s in enumerate(["SRC_A", "SRC_B", "SRC_C"]):
            cur += src("S", BALTIMORE, source=s)
        cur += src("SOMEONE ELSE", FREDERICK, source="SRC_D")
        res, _ = run(cur, sources=["SRC_A", "SRC_B", "SRC_C", "SRC_D"])
        assert res.assessment is A.CONFLICTING_EVIDENCE
        assert any("SRC_D" in q for q in res.open_questions)

    def test_conflict_is_visible_not_suppressed(self):
        cur = delivered("S", BALTIMORE) + src("S", BALTIMORE, source="SRC_A") + src("OTHER", BALTIMORE, source="SRC_B")
        res, comps = run(cur, sources=["SRC_A", "SRC_B"])
        kept = [c for c in res.comparisons if c.signal is NameSignal.NAME_CONFLICT]
        assert kept and kept[0].source_id == "SRC_B"
        assert len(res.comparisons) == len(comps)

    def test_unavailable_plus_conflict_is_conflict_not_unavailable(self):
        cur = delivered("S", BALTIMORE) + [unavailable("SRC_A")] + src("OTHER", BALTIMORE, source="SRC_B")
        res, _ = run(cur, sources=["SRC_A", "SRC_B"])
        assert res.assessment is A.CONFLICTING_EVIDENCE

    def test_unavailable_plus_corroboration_is_partial(self):
        cur = delivered("S", BALTIMORE) + [unavailable("SRC_A")] + src("S", BALTIMORE, source="SRC_B")
        res, _ = run(cur, sources=["SRC_A", "SRC_B"])
        assert res.assessment is A.EVIDENCE_PARTIALLY_CORROBORATES

    def test_no_numeric_score_anywhere(self):
        cur = delivered("S", BALTIMORE) + src("S", BALTIMORE)
        res, comps = run(cur)
        d = res.to_dict()
        for key in ("score", "confidence", "probability", "weight", "similarity"):
            assert key not in str(d).lower()

    def test_authority_is_descriptive_not_weighted(self):
        """Swapping the authority class of a conflicting source never changes
        the outcome: there is no ranking to exploit."""
        outcomes = set()
        for auth in SourceAuthority:
            if auth is SourceAuthority.PROGRAM_DELIVERY:
                continue
            cur = delivered("S", BALTIMORE) + src("OTHER", BALTIMORE, source="SRC_B", authority=auth)
            res, _ = run(cur, sources=["SRC_B"])
            outcomes.add(res.assessment)
        assert outcomes == {A.CONFLICTING_EVIDENCE}


BANNED_CLAIMS = ["is compliant", "non-compliant", "noncompliant", "fraud", "fraudulent", "legal identity",
                 "legally", "is operating", "currently operating", "in operation", "credentialed", "licensed to",
                 "approved", "rejected", "passes", "fails", "the analyst has", "qa approved", "verified identity",
                 "confirms the organization", "confirms the organisation", "has moved", "relocated", "renamed",
                 "closed", "is valid", "is invalid", "is legitimate", "is a real"]
ALLOWED_NEGATIONS = ["does not establish licensure or program compliance"]


class TestExplanationTemplateSafety:
    @pytest.mark.parametrize("key", sorted(TEMPLATES))
    def test_template_makes_no_banned_claim(self, key):
        text = TEMPLATES[key].lower()
        for allowed in ALLOWED_NEGATIONS:
            text = text.replace(allowed, "")
        for claim in BANNED_CLAIMS:
            assert claim not in text, (key, claim)

    @pytest.mark.parametrize("key", sorted(TEMPLATES))
    def test_every_template_renders_with_no_facts(self, key):
        text = explain(key)
        assert "{" not in text and "}" not in text
        if not key.startswith("DELTA_") and not key.endswith("_VARIATION"):
            assert text.endswith("Human review required.")

    def test_unstated_for_missing_facts(self):
        assert "unstated" in explain("NAME_CONFLICT", source="X")

    def test_hostile_fact_values_are_not_interpreted(self):
        text = explain("DIRECT_NAME_MATCH", source="{system}")
        assert text.count("{") == 1      # the braces arrive as data, not as a format directive

    def test_templates_never_mention_contract_categories(self):
        blob = " ".join(TEMPLATES.values()).upper()
        for term in FORBIDDEN_TERMS:
            assert re.search(rf"\b{term}\b", blob) is None, term


class TestAssessmentNeverACategory:
    def test_closed_vocabulary(self):
        assert {a.value for a in A} == {"EVIDENCE_CORROBORATES", "EVIDENCE_PARTIALLY_CORROBORATES",
                                        "EXPLAINABLE_VARIATION_IDENTIFIED", "CONFLICTING_EVIDENCE",
                                        "INSUFFICIENT_EVIDENCE", "SOURCE_UNAVAILABLE"}

    def test_every_result_requires_human_review(self):
        for cur in (delivered("S", BALTIMORE) + src("S", BALTIMORE), delivered("S", BALTIMORE),
                    delivered("S", BALTIMORE) + [unavailable(NPPES)]):
            res, _ = run(cur)
            assert res.requires_human_review is True
            assert all(c.requires_human_review for c in res.comparisons)

    def test_no_mapping_table_to_categories_exists(self):
        import app.core.entity_intelligence.assessment as m
        import inspect
        source = inspect.getsource(m).upper()
        for term in ("CATEGORY_1", "CATEGORY 1", "CONTRACT_CATEGORY", "SOW_CATEGORY", "DISPOSITION"):
            assert term not in source

    def test_corroboration_wording_disclaims(self):
        _, comps = run(delivered("S", BALTIMORE) + src("S", BALTIMORE))
        ident = [c for c in comps if c.dimension is Dimension.ORGANIZATION_IDENTITY][0]
        assert "does not establish licensure or program compliance" in ident.explanation

    def test_dba_signal_emitted_only_for_code_3(self, monkeypatch):
        enable_all(monkeypatch)
        main = csv_text(MAIN_HEADER, [main_row("9999900001", "SYNTHETIC ORG LLC", practice=BALTIMORE)])
        on = csv_text(OTHER_NAME_HEADER, [other_name_row("9999900001", "DBA NAME", "3"),
                                          other_name_row("9999900001", "FORMER NAME", "4"),
                                          other_name_row("9999900001", "OTHER NAME", "5"),
                                          other_name_row("9999900001", "WEIRD", "9")])
        obs = NppesV2Adapter(NppesV2Bundle.from_texts(main_text=main, other_name_text=on)).observations_for(
            canonical_entity_id="e", identifier="9999900001")
        names = {o.observed_value["name"]: o for o in obs if o.observation_type is ObservationType.NAME}
        assert names["DBA NAME"].observed_value.get("signal") == SIGNAL_DBA
        for n in ("FORMER NAME", "OTHER NAME", "WEIRD", "SYNTHETIC ORG LLC"):
            assert names[n].observed_value.get("signal") is None
        assert names["WEIRD"].role == NameKind.UNKNOWN.value
