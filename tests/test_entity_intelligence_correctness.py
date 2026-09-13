"""Remediation proofs for audit findings AUD-20260913-09 / -10 / -11 (Lane D).

AUD-09  duplicate evidence != multiple candidate entities
AUD-10  a non-string source field never raises and never becomes a fabricated key
AUD-11  same address + unstated role is SOURCE_ROLE_UNKNOWN, never a match

Every case is synthetic. Feature flags are irrelevant: the comparison and
normalisation functions are pure and the service is not used here.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.entity_intelligence.assessment import SystemEvidenceAssessment as A, assess
from app.core.entity_intelligence.comparison import (AMBIGUOUS_SIGNALS, CORROBORATING_SIGNALS, Dimension,
                                                      IdentifierSignal, LocationSignal, NameSignal,
                                                      RelationshipSignal, compare_all, compare_identifier,
                                                      compare_locations, compare_names, compare_relationships,
                                                      deduplicate_observations, observation_identity_key)
from app.core.entity_intelligence.delta import DeltaType, compute_deltas
from app.core.entity_intelligence.explanations import TEMPLATES
from app.core.entity_intelligence.normalize import (NORMALIZATION_VERSION, ZIP_AMBIGUOUS_LEADING_ZERO,
                                                    ZIP_INCOMPLETE, ZIP_NO_DIGITS, ZIP_NON_US_FORMAT,
                                                    ZIP_NOT_STATED, ZIP_OK, ZIP_UNSUPPORTED_TYPE, address_is_usable,
                                                    as_text, normalize_address, normalize_name, normalize_state,
                                                    postal_comparison)
from app.core.entity_intelligence.observations import (ADMINISTRATIVE_ROLES, CARE_SITE_ROLES, DeliveryPath,
                                                        EvidenceObservation, LocationRole, NameKind, ObservationType,
                                                        Provenance, SourceAuthority, location_observation,
                                                        relationship_observation)
from ei_fixtures import BALTIMORE, BALTIMORE_ALT_STREET, FREDERICK, delivered

SRC = "NPPES_V2"
REG = "SYNTHETIC_STATE_REGISTRY"


def prov(ref="s"):
    return Provenance(source_owner="synthetic", delivery_path=DeliveryPath.FILE_DOWNLOAD, source_record_ref=ref)


def ident(value="9999900001", *, entity_type="2", record=None, source=SRC, effective_from=None, effective_to=None):
    return EvidenceObservation(canonical_entity_id="ent-1", source_id=source, observation_type=ObservationType.IDENTIFIER,
                               role="NPI", observed_value={"value": value, "entity_type": entity_type},
                               source_authority=SourceAuthority.FEDERAL_REGISTRY, provenance=prov(),
                               source_record_id=record, effective_from=effective_from, effective_to=effective_to)


def name(text, kind=NameKind.LEGAL_BUSINESS_NAME, *, source=SRC, record=None):
    return EvidenceObservation(canonical_entity_id="ent-1", source_id=source, observation_type=ObservationType.NAME,
                               role=kind.value, observed_value={"name": text},
                               source_authority=SourceAuthority.FEDERAL_REGISTRY, provenance=prov(), source_record_id=record)


def loc(role, addr=BALTIMORE, *, source=SRC, record=None, effective_from=None, effective_to=None):
    return location_observation(canonical_entity_id="ent-1", source_id=source, role=role, raw_address=addr,
                                source_authority=SourceAuthority.FEDERAL_REGISTRY, provenance=prov(),
                                effective_from=effective_from, effective_to=effective_to,
                                source_delivery_id=record)


def rel(obj, *, kind="QHIN_PARTICIPANT", program="TEFCA", source=SRC, valid_from=None, valid_to=None):
    return relationship_observation(canonical_entity_id="ent-1", source_id=source, program_context=program, kind=kind,
                                    subject="SYNTHETIC ORG", obj=obj, source_authority=SourceAuthority.FEDERAL_REGISTRY,
                                    provenance=prov(), valid_from=valid_from, valid_to=valid_to)


# ── AUD-09: duplicate evidence ──────────────────────────────────────────────

class TestDuplicateEvidenceIsNotMultipleEntities:
    def test_exact_duplicate_identifier_is_one_corroboration(self):
        r = compare_identifier(delivered("S") + [ident(), ident(), ident()], source_id=SRC)
        assert r.signal is IdentifierSignal.IDENTIFIER_CORROBORATED
        assert r.detail["duplicate_observations_collapsed"] == 2 and r.detail["source_record_count"] == 1

    def test_duplicate_rows_do_not_downgrade_the_assessment(self):
        cur = delivered("S LLC", BALTIMORE) + [ident(), ident(), name("S LLC"), name("S LLC"),
                                               loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value),
                                               loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value)]
        res = assess(compare_all(cur, source_id=SRC), [])
        assert res.assessment is A.EVIDENCE_CORROBORATES
        assert IdentifierSignal.MULTIPLE_CANDIDATE_ENTITIES.value not in res.basis

    def test_same_npi_in_two_enrollment_rows_is_one_entity(self):
        r = compare_identifier(delivered("S") + [ident(record="enrollment-1"), ident(record="enrollment-2")], source_id=SRC)
        assert r.signal is IdentifierSignal.IDENTIFIER_CORROBORATED
        assert r.detail["source_record_count"] == 2
        assert r.detail["source_record_ids"] == ["enrollment-1", "enrollment-2"]
        assert "duplicate_observations_collapsed" not in r.detail   # distinct records were NOT collapsed
        assert "not several entities" in r.explanation and r.explanation.endswith("Human review required.")

    def test_two_different_npis_for_the_entity_is_multiple_candidates(self):
        r = compare_identifier(delivered("S") + [ident("9999900001"), ident("9999900002")], source_id=SRC)
        assert r.signal is IdentifierSignal.MULTIPLE_CANDIDATE_ENTITIES
        assert r.detail["other_identifier_values"] == ["9999900002"] and len(r.candidate_observation_ids) == 2
        assert "not unique" in r.explanation

    def test_records_disagreeing_on_entity_type_are_multiple_candidates(self):
        r = compare_identifier(delivered("S") + [ident(entity_type="2", record="a"), ident(entity_type="1", record="b")],
                               source_id=SRC)
        assert r.signal is IdentifierSignal.MULTIPLE_CANDIDATE_ENTITIES
        assert r.detail["entity_types"] == ["1", "2"]

    def test_history_with_different_effective_periods_is_preserved(self):
        obs = [ident(effective_from="2020-01-01", effective_to="2024-12-31"), ident(effective_from="2025-01-01")]
        unique, collapsed = deduplicate_observations(obs)
        assert collapsed == 0 and len(unique) == 2
        r = compare_identifier(delivered("S") + obs, source_id=SRC)
        assert r.signal is IdentifierSignal.IDENTIFIER_CORROBORATED and r.detail["source_record_count"] == 2

    def test_identity_key_covers_source_subject_type_role_value_period_and_record(self):
        base = ident(record="r1")
        for changed in (replace(base, source_id="OTHER"), replace(base, canonical_entity_id="ent-2"),
                        replace(base, role="EIN"), replace(base, observed_value={"value": "9999900002", "entity_type": "2"}),
                        replace(base, effective_from="2020-01-01"), replace(base, effective_to="2020-01-01"),
                        replace(base, source_record_id="r2"), replace(base, source_delivery_id="d2"),
                        replace(base, provenance=prov("line 9"))):
            assert observation_identity_key(changed) != observation_identity_key(base)
        assert observation_identity_key(replace(base, observation_id="fresh-uuid")) == observation_identity_key(base)

    def test_distinct_locations_and_relationships_are_never_merged(self):
        two_sites = [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, BALTIMORE),
                     loc(LocationRole.ADDITIONAL_PRACTICE_LOCATION.value, FREDERICK)]
        assert deduplicate_observations(two_sites)[1] == 0
        two_rels = [rel("SYNTHETIC QHIN A"), rel("SYNTHETIC QHIN B")]
        assert deduplicate_observations(two_rels)[1] == 0
        same_rel_two_periods = [rel("SYNTHETIC QHIN A", valid_from="2024-01-01", valid_to="2024-12-31"),
                                rel("SYNTHETIC QHIN A", valid_from="2025-01-01")]
        assert deduplicate_observations(same_rel_two_periods)[1] == 0
        assert deduplicate_observations([rel("SYNTHETIC QHIN A"), rel("SYNTHETIC QHIN A")])[1] == 1

    def test_duplicate_names_do_not_create_ambiguity(self):
        cur = delivered("S CLINIC", BALTIMORE) + [name("S HEALTHCARE LLC"), name("S CLINIC", NameKind.DOING_BUSINESS_AS),
                                                  name("S CLINIC", NameKind.DOING_BUSINESS_AS)]
        assert compare_names(cur, source_id=SRC).signal is NameSignal.DBA_MATCH_IDENTIFIED

    def test_duplicate_relationship_is_corroborated_once(self):
        cur = delivered("S", BALTIMORE, relationships=[{"kind": "TEFCA:QHIN_PARTICIPANT", "name": "SYNTHETIC QHIN A"}]) + \
              [rel("SYNTHETIC QHIN A"), rel("SYNTHETIC QHIN A")]
        r = compare_relationships(cur, source_id=SRC, kind_code="TEFCA:QHIN_PARTICIPANT")
        assert r.signal is RelationshipSignal.RELATIONSHIP_CORROBORATED

    def test_dedup_is_stable_and_order_preserving(self):
        obs = [ident(record="r1"), name("A"), ident(record="r1"), name("B"), name("A")]
        unique, collapsed = deduplicate_observations(obs)
        assert collapsed == 2 and [o.observation_id for o in unique] == [obs[0].observation_id, obs[1].observation_id, obs[3].observation_id]
        assert deduplicate_observations(unique) == (unique, 0)


# ── AUD-10: postal / address normalisation ─────────────────────────────────

class TestPostalNormalizationNeverRaisesNeverInvents:
    @pytest.mark.parametrize("value,expected,status", [
        ("21201", "21201", ZIP_OK), ("21201-1234", "21201", ZIP_OK), ("212011234", "21201", ZIP_OK),
        (" 21201 ", "21201", ZIP_OK), ("02101", "02101", ZIP_OK), ("02101-0001", "02101", ZIP_OK),
        (21201, "21201", ZIP_OK), (212011234, "21201", ZIP_OK),
        (2101, "", ZIP_AMBIGUOUS_LEADING_ZERO), (101, "", ZIP_AMBIGUOUS_LEADING_ZERO),
        (212011, "", ZIP_INCOMPLETE),
        (None, "", ZIP_NOT_STATED), ("", "", ZIP_NOT_STATED), ("   ", "", ZIP_NOT_STATED),
        ("2120", "", ZIP_INCOMPLETE), ("abc", "", ZIP_NON_US_FORMAT), ("SW1A 1AA", "", ZIP_NON_US_FORMAT),
        ("M5V 3L9", "", ZIP_NON_US_FORMAT), ("-", "", ZIP_NO_DIGITS),
        ("２１２０１", "21201", ZIP_OK), ("21​201", "21201", ZIP_OK),
        (21201.0, "", ZIP_UNSUPPORTED_TYPE), (True, "", ZIP_UNSUPPORTED_TYPE), ({"zip": "21201"}, "", ZIP_UNSUPPORTED_TYPE),
        (["21201"], "", ZIP_UNSUPPORTED_TYPE), (b"21201", "", ZIP_UNSUPPORTED_TYPE),
    ])
    def test_postal_comparison_table(self, value, expected, status):
        assert postal_comparison(value) == (expected, status)

    def test_non_us_country_yields_no_zip_key_even_for_digits(self):
        assert postal_comparison("75008", "FR") == ("", ZIP_NON_US_FORMAT)
        assert postal_comparison("75008", "US") == ("75008", ZIP_OK)
        assert postal_comparison("75008", "usa ") == ("75008", ZIP_OK)

    def test_integer_zip_with_lost_leading_zero_is_not_repaired(self):
        n = normalize_address({"line1": "1 Synthetic Way", "city": "Boston", "state": "MA", "postal_code": 2101})
        assert n["zip5"] == "" and n["zip_status"] == ZIP_AMBIGUOUS_LEADING_ZERO
        # still comparable through city + state, never through an invented ZIP
        assert address_is_usable(n)
        assert normalize_address({"line1": "1 Synthetic Way", "postal_code": 2101})["zip5"] == ""

    def test_raw_source_value_is_preserved_beside_the_key(self):
        raw = {"line1": "1 Synthetic Way", "city": "Boston", "state": "MA", "postal_code": 2101}
        o = loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, raw)
        assert o.observed_value["raw_address"] == raw and o.observed_value["postal_code"] == 2101
        assert o.normalized_value["zip5"] == "" and o.normalized_value["normalization_version"] == NORMALIZATION_VERSION

    @pytest.mark.parametrize("field", ["line1", "line2", "city", "state", "postal_code", "country_code"])
    @pytest.mark.parametrize("junk", [None, 0, 12, -1, 1.5, True, False, b"bytes", ["x"], {"y": 1}, ("z",), set()])
    def test_any_field_type_never_raises(self, field, junk):
        n = normalize_address({**BALTIMORE, field: junk})
        assert set(n) >= {"line1", "line2", "street", "city", "state", "zip5", "zip_status", "country", "normalization_version"}
        assert all(isinstance(v, str) for v in n.values())

    def test_address_itself_may_be_garbage(self):
        for bad in (None, "100 Main St", 42, ["line1"], b""):
            n = normalize_address(bad)  # type: ignore[arg-type]
            assert n["line1"] == "" and n["zip5"] == "" and not address_is_usable(n)

    def test_unicode_contamination_is_folded_not_kept(self):
        n = normalize_address({"line1": "１００ Synthetic​ Way", "state": "ｍｄ", "postal_code": "２１２０１-１２３４"})
        assert n["line1"] == "100 synthetic way" and n["state"] == "MD" and n["zip5"] == "21201"

    def test_suite_and_unit_are_kept_in_the_street_key(self):
        a = normalize_address({"line1": "100 Synthetic Way, Suite 200", "postal_code": "21201"})
        b = normalize_address({"line1": "100 SYNTHETIC WAY", "line2": "STE 200", "postal_code": "21201-0000"})
        c = normalize_address({"line1": "100 SYNTHETIC WAY", "line2": "STE 300", "postal_code": "21201"})
        assert a["street"] == b["street"] and a["street"] != c["street"]

    def test_state_key_is_letters_only(self):
        assert normalize_state("md") == "MD" and normalize_state(12) == "" and normalize_state("1A") == "" and normalize_state(None) == ""

    def test_as_text_coerces_without_inventing(self):
        assert as_text(None) == "" and as_text(True) == "" and as_text(1.0) == "" and as_text({"a": 1}) == ""
        assert as_text(21201) == "21201" and as_text("x​y") == "xy" and as_text("Ａ") == "A"
        assert normalize_name({"name": "x"}) == "" and normalize_name(["x"]) == "" and normalize_name(7) == "7"

    def test_int_postal_end_to_end_comparison(self):
        cur = delivered("S LLC", dict(BALTIMORE, postal_code=212010000)) + [
            ident(), name("S LLC"), loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, dict(BALTIMORE, postal_code=21201))]
        res = assess(compare_all(cur, source_id=SRC), [])
        assert res.assessment is A.EVIDENCE_CORROBORATES

    def test_normalization_is_not_an_evidence_judgment(self):
        # a non-comparable ZIP produces INSUFFICIENT/AMBIGUOUS signals, never a conflict and never a match
        cur = delivered("S LLC", {"line1": "1 Synthetic Way", "postal_code": 2101}) + [
            ident(), name("S LLC"), loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, {"line1": "1 Synthetic Way", "postal_code": "02101"})]
        r = compare_locations(cur, source_id=SRC)
        assert r.signal is LocationSignal.INSUFFICIENT_LOCATION_EVIDENCE


# ── AUD-11: unknown location role ──────────────────────────────────────────

class TestSameAddressIsNotSameRole:
    def test_same_address_same_role_is_a_match(self):
        r = compare_locations(delivered("S", BALTIMORE) + [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value)], source_id=SRC)
        assert r.signal is LocationSignal.PRIMARY_LOCATION_MATCH and r.signal in CORROBORATING_SIGNALS

    @pytest.mark.parametrize("role", sorted(ADMINISTRATIVE_ROLES))
    def test_same_address_different_role_is_role_assignment_differs(self, role):
        r = compare_locations(delivered("S", BALTIMORE) + [loc(role, source=REG)], source_id=REG)
        assert r.signal is LocationSignal.ROLE_ASSIGNMENT_DIFFERS and r.signal in AMBIGUOUS_SIGNALS
        assert r.detail["matched_role"] == role

    @pytest.mark.parametrize("role", [LocationRole.UNKNOWN_SOURCE_ROLE.value, LocationRole.UNKNOWN.value, None, ""])
    def test_same_address_unknown_role_is_source_role_unknown(self, role):
        o = replace(loc(LocationRole.UNKNOWN_SOURCE_ROLE.value), role=role)
        r = compare_locations(delivered("S", BALTIMORE) + [o], source_id=SRC)
        assert r.signal is LocationSignal.SOURCE_ROLE_UNKNOWN
        assert r.signal not in CORROBORATING_SIGNALS and r.signal in AMBIGUOUS_SIGNALS
        assert r.detail["matched_role"] in (role, LocationRole.UNKNOWN_SOURCE_ROLE.value)
        assert "does not assign" in r.explanation and r.explanation.endswith("Human review required.")

    def test_unregistered_role_string_is_treated_as_unstated_not_as_care(self):
        o = replace(loc(LocationRole.UNKNOWN_SOURCE_ROLE.value), role="SOME_FUTURE_ROLE")
        assert compare_locations(delivered("S", BALTIMORE) + [o], source_id=SRC).signal is LocationSignal.SOURCE_ROLE_UNKNOWN

    def test_unknown_role_cannot_corroborate_the_assessment(self):
        cur = delivered("S LLC", BALTIMORE) + [ident(), name("S LLC"), loc(LocationRole.UNKNOWN_SOURCE_ROLE.value)]
        res = assess(compare_all(cur, source_id=SRC), [])
        assert res.assessment is A.EVIDENCE_PARTIALLY_CORROBORATES
        assert LocationSignal.SOURCE_ROLE_UNKNOWN.value in res.basis

    def test_unknown_role_never_silently_becomes_another_role(self):
        o = loc(LocationRole.UNKNOWN_SOURCE_ROLE.value)
        r = compare_locations(delivered("S", BALTIMORE) + [o], source_id=SRC)
        for promoted in (LocationSignal.PRIMARY_LOCATION_MATCH, LocationSignal.ADDITIONAL_PRACTICE_LOCATION_MATCH,
                         LocationSignal.NORMALIZED_LOCATION_MATCH, LocationSignal.MAILING_LOCATION_MATCH,
                         LocationSignal.ROLE_ASSIGNMENT_DIFFERS, LocationSignal.LOCATION_CONFLICT):
            assert r.signal is not promoted
        assert o.role == LocationRole.UNKNOWN_SOURCE_ROLE.value   # the observation itself is untouched

    def test_stated_care_role_at_same_address_is_not_hidden_by_an_unknown_one(self):
        cur = delivered("S", BALTIMORE) + [loc(LocationRole.UNKNOWN_SOURCE_ROLE.value), loc(LocationRole.SITE_OF_CARE.value)]
        assert compare_locations(cur, source_id=SRC).signal is LocationSignal.NORMALIZED_LOCATION_MATCH

    def test_normalized_same_address_different_role(self):
        variant = {"line1": "100 synthetic way, suite 200", "city": "baltimore", "state": "md", "postal_code": "21201-9999"}
        r = compare_locations(delivered("S", BALTIMORE) + [loc(LocationRole.REGISTERED_AGENT_ADDRESS.value, variant, source=REG)],
                              source_id=REG)
        assert r.signal is LocationSignal.ROLE_ASSIGNMENT_DIFFERS

    def test_historical_same_address_changed_role_is_reported_not_judged(self):
        prior = [loc(LocationRole.PRIMARY_PRACTICE_LOCATION.value, source=REG)]
        current = [loc(LocationRole.REGISTERED_AGENT_ADDRESS.value, source=REG)]
        deltas = compute_deltas(prior, current)
        types = {d.delta_type for d in deltas}
        assert DeltaType.ADDRESS_CHANGED not in types            # the address did not change, the stated role did
        assert DeltaType.SOURCE_NO_LONGER_REPORTS_OBSERVATION in types and DeltaType.NEW_VALUE in types
        text = " ".join(d.explanation for d in deltas).lower()
        disclaimer = "does not establish the end of a relationship, the lapse of a registration, or any adverse fact"
        assert disclaimer in text                                 # the only place "adverse" may appear
        text = text.replace(disclaimer, "")
        for banned in ("moved", "relocat", "closed", "ceased", "invalid", "adverse", "non-compliant", "ended"):
            assert banned not in text, banned
        assert compare_locations(delivered("S", BALTIMORE) + current, source_id=REG).signal is LocationSignal.ROLE_ASSIGNMENT_DIFFERS

    def test_role_classes_are_exhaustive_and_disjoint(self):
        assert not (CARE_SITE_ROLES & ADMINISTRATIVE_ROLES)
        for r in LocationRole:
            classes = [r.value in CARE_SITE_ROLES, r.value in ADMINISTRATIVE_ROLES,
                       r.value == LocationRole.MAILING_LOCATION.value,
                       r.value in (LocationRole.UNKNOWN_SOURCE_ROLE.value, LocationRole.UNKNOWN.value)]
            assert sum(classes) == 1, r

    def test_template_exists_and_makes_no_claim(self):
        t = TEMPLATES["SOURCE_ROLE_UNKNOWN"].lower()
        assert "does not assign" in t and "does not corroborate" in t
        for banned in ("is the practice", "is the headquarters", "is a site of care", "is the mailing"):
            assert banned not in t
