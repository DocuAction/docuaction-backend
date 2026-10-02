"""Identity comparison rules — structured signals with controlled explanations."""
from __future__ import annotations

from app.core.entity_intelligence.comparison import (IdentifierSignal, LocationSignal, NameSignal,
                                                      RelationshipSignal, compare_all, compare_identifier,
                                                      compare_locations, compare_names,
                                                      compare_relationships)
from app.core.entity_intelligence.normalize import normalize_name, normalize_address
from app.core.entity_intelligence.observations import (DeliveryPath, EvidenceObservation, LocationRole,
                                                        NameKind, ObservationType, Provenance,
                                                        SourceAuthority)

from ei_fixtures import (BALTIMORE, BALTIMORE_ALT_STREET, FREDERICK, ROCKVILLE, delivered,
                               unavailable)

SRC = "NPPES_V2"
PROV = Provenance(source_owner="CMS NPPES", delivery_path=DeliveryPath.FILE_DOWNLOAD)


def src(kind: ObservationType, role: str, value: dict, entity_id="ent-1") -> EvidenceObservation:
    return EvidenceObservation(canonical_entity_id=entity_id, source_id=SRC, observation_type=kind,
                               observed_value=value, source_authority=SourceAuthority.FEDERAL_REGISTRY,
                               provenance=PROV, role=role)


def legal(name): return src(ObservationType.NAME, NameKind.LEGAL_BUSINESS_NAME.value, {"name": name})
def dba(name): return src(ObservationType.NAME, NameKind.DOING_BUSINESS_AS.value, {"name": name, "type_code": "3"})
def other(name): return src(ObservationType.NAME, NameKind.OTHER_NAME.value, {"name": name, "type_code": "5"})
def former(name): return src(ObservationType.NAME, NameKind.FORMER_LEGAL_BUSINESS_NAME.value, {"name": name, "type_code": "4"})
def npi(v, et="2"): return src(ObservationType.IDENTIFIER, "NPI", {"value": v, "entity_type": et})
def loc(role, a): return src(ObservationType.LOCATION, role, dict(a))


class TestNormalization:
    def test_formatting_only(self):
        assert normalize_name("Synthetic  Healthcare, Inc.") == normalize_name("SYNTHETIC HEALTHCARE INC")
        assert normalize_name("Synthetic Healthcare, L.L.C.") == normalize_name("SYNTHETIC HEALTHCARE LLC")
        assert normalize_name("ABC Healthcare Incorporated") == normalize_name("ABC HEALTHCARE INC")
        assert normalize_name("ABC Healthcare LLC") != normalize_name("ABC Mobile Clinic")
        a = normalize_address({"line1": "100 Synthetic Way, Suite 200", "city": "Baltimore", "state": "md", "postal_code": "21201-0000"})
        assert a["line1"] == "100 synthetic way ste 200" and a["state"] == "MD" and a["zip5"] == "21201"


class TestNames:
    def test_direct_and_normalized(self):
        assert compare_names(delivered("SYNTHETIC HEALTHCARE LLC") + [legal("SYNTHETIC HEALTHCARE LLC")], source_id=SRC).signal is NameSignal.DIRECT_NAME_MATCH
        r = compare_names(delivered("Synthetic Healthcare, L.L.C.") + [legal("SYNTHETIC HEALTHCARE LLC")], source_id=SRC)
        assert r.signal is NameSignal.NORMALIZED_NAME_MATCH and "normalisation" in r.explanation

    def test_dba_identified_only_from_type_code_3(self):
        obs = delivered("SYNTHETIC MOBILE CLINIC") + [legal("SYNTHETIC HEALTHCARE LLC"), dba("Synthetic Mobile Clinic")]
        r = compare_names(obs, source_id=SRC)
        assert r.signal is NameSignal.DBA_MATCH_IDENTIFIED
        assert "classified as Doing Business As" in r.explanation and "SYNTHETIC HEALTHCARE LLC" in r.explanation
        assert r.explanation.endswith("Human review required.") and r.requires_human_review

    def test_non_dba_other_name_is_not_called_dba(self):
        obs = delivered("SYNTHETIC ALIAS") + [legal("SYNTHETIC OTHER CO"), other("SYNTHETIC ALIAS")]
        r = compare_names(obs, source_id=SRC)
        assert r.signal is NameSignal.OTHER_NAME_MATCH and "does not classify it as Doing Business As" in r.explanation

    def test_former_name(self):
        r = compare_names(delivered("SYNTHETIC OLD NAME CORP") + [legal("SYNTHETIC RENAMED INC"), former("SYNTHETIC OLD NAME CORP")], source_id=SRC)
        assert r.signal is NameSignal.FORMER_NAME_MATCH

    def test_multiple_other_names_same_kind_is_a_match(self):
        r = compare_names(delivered("SYNTHETIC FAMILY CARE") + [legal("SYNTHETIC HEALTHCARE LLC"), dba("SYNTHETIC MOBILE CLINIC"), dba("SYNTHETIC FAMILY CARE")], source_id=SRC)
        assert r.signal is NameSignal.DBA_MATCH_IDENTIFIED

    def test_conflict_and_ambiguity(self):
        assert compare_names(delivered("TOTALLY DIFFERENT ORG") + [legal("SYNTHETIC HEALTHCARE LLC"), dba("SYNTHETIC MOBILE CLINIC")], source_id=SRC).signal is NameSignal.NAME_CONFLICT
        r = compare_names(delivered("SYNTHETIC HEALTHCARE INC") + [legal("SYNTHETIC HEALTHCARE LLC")], source_id=SRC)
        assert r.signal is NameSignal.AMBIGUOUS_NAME and "suffix" in r.explanation
        r = compare_names(delivered("SYNTHETIC ALIAS") + [legal("X CO"), other("SYNTHETIC ALIAS"), dba("SYNTHETIC ALIAS")], source_id=SRC)
        assert r.signal is NameSignal.AMBIGUOUS_NAME

    def test_insufficient_and_unavailable(self):
        assert compare_names(delivered("SYNTHETIC X"), source_id=SRC).signal is NameSignal.INSUFFICIENT_NAME_EVIDENCE
        assert compare_names(delivered("") + [legal("A")], source_id=SRC).signal is NameSignal.INSUFFICIENT_NAME_EVIDENCE
        r = compare_names(delivered("SYNTHETIC X") + [unavailable(SRC)], source_id=SRC)
        assert r.signal is NameSignal.SOURCE_UNAVAILABLE and "fact about access" in r.explanation


class TestLocations:
    def test_primary_and_additional(self):
        obs = delivered("N", FREDERICK) + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE),
                                          loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK)]
        r = compare_locations(obs, source_id=SRC)
        assert r.signal is LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH
        assert "non-primary practice-location record" in r.explanation
        assert compare_locations(delivered("N", BALTIMORE) + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)], source_id=SRC).signal is LocationSignal.PRIMARY_LOCATION_MATCH

    def test_normalized_match_prefers_primary_over_mailing(self):
        obs = delivered("N", {"line1": "100 synthetic way, suite 200", "city": "baltimore", "state": "md", "postal_code": "21201"}) + [
            loc(LocationRole.MAILING_LOCATION.value, BALTIMORE), loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)]
        assert compare_locations(obs, source_id=SRC).signal is LocationSignal.PRIMARY_LOCATION_MATCH

    def test_mailing_only(self):
        assert compare_locations(delivered("N", ROCKVILLE) + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE), loc(LocationRole.MAILING_LOCATION.value, ROCKVILLE)], source_id=SRC).signal is LocationSignal.MAILING_LOCATION_MATCH

    def test_ambiguous_conflict_missing_unavailable(self):
        assert compare_locations(delivered("N", BALTIMORE_ALT_STREET) + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)], source_id=SRC).signal is LocationSignal.AMBIGUOUS_LOCATION
        assert compare_locations(delivered("N", ROCKVILLE) + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)], source_id=SRC).signal is LocationSignal.LOCATION_CONFLICT
        assert compare_locations(delivered("N") + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)], source_id=SRC).signal is LocationSignal.INSUFFICIENT_LOCATION_EVIDENCE
        assert compare_locations(delivered("N", {"line1": "1 X"}) + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE)], source_id=SRC).signal is LocationSignal.INSUFFICIENT_LOCATION_EVIDENCE
        assert compare_locations(delivered("N", BALTIMORE) + [unavailable(SRC)], source_id=SRC).signal is LocationSignal.SOURCE_UNAVAILABLE


class TestIdentifiers:
    def test_states(self):
        assert compare_identifier(delivered("N") + [npi("9999900001")], source_id=SRC).signal is IdentifierSignal.IDENTIFIER_CORROBORATED
        r = compare_identifier(delivered("N") + [npi("9999900001")], source_id=SRC)
        assert "does not establish licensure" in r.explanation
        assert compare_identifier(delivered("N") + [npi("9999900001"), npi("9999900001")], source_id=SRC).signal is IdentifierSignal.MULTIPLE_CANDIDATE_ENTITIES
        assert compare_identifier(delivered("N") + [npi("9999900777")], source_id=SRC).signal is IdentifierSignal.IDENTIFIER_CONFLICT
        assert compare_identifier(delivered("N", npi=None) + [npi("9999900001")], source_id=SRC).signal is IdentifierSignal.MISSING_IDENTIFIER
        assert compare_identifier(delivered("N") + [unavailable(SRC)], source_id=SRC).signal is IdentifierSignal.SOURCE_UNAVAILABLE


class TestRelationships:
    def test_kinds_are_never_interchangeable(self):
        d = delivered("N", relationships=[{"kind": "PROGRAM_PARENT", "name": "SYNTHETIC QHIN ONE"}])
        corp = src(ObservationType.RELATIONSHIP, "CORPORATE_PARENT_HCO", {"related_entity_name": "SYNTHETIC HOLDINGS"})
        r = compare_relationships(d + [corp], source_id=SRC, kind_code="PROGRAM_PARENT")
        assert r.signal is RelationshipSignal.RELATIONSHIP_NOT_COMPARABLE and "not a program relationship" in r.explanation
        same = src(ObservationType.RELATIONSHIP, "PROGRAM_PARENT", {"related_entity_name": "Synthetic QHIN One"})
        assert compare_relationships(d + [same], source_id=SRC, kind_code="PROGRAM_PARENT").signal is RelationshipSignal.RELATIONSHIP_CORROBORATED
        diff = src(ObservationType.RELATIONSHIP, "PROGRAM_PARENT", {"related_entity_name": "SYNTHETIC QHIN TWO"})
        assert compare_relationships(d + [diff], source_id=SRC, kind_code="PROGRAM_PARENT").signal is RelationshipSignal.RELATIONSHIP_CONFLICT


class TestCompareAll:
    def test_one_result_per_dimension_with_serialisation(self):
        obs = delivered("SYNTHETIC MOBILE CLINIC", FREDERICK) + [npi("9999900001"), legal("SYNTHETIC HEALTHCARE LLC"),
                                                                dba("SYNTHETIC MOBILE CLINIC"),
                                                                loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE),
                                                                loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK)]
        results = compare_all(obs, source_id=SRC)
        assert [r.signal.value for r in results] == ["IDENTIFIER_CORROBORATED", "DBA_MATCH_IDENTIFIED", "ADDITIONAL_PRACTICE_LOCATION_MATCH"]
        assert all(r.to_dict()["requires_human_review"] is True for r in results)
