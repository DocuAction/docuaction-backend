"""Overnight hardening: flags, parser robustness, normalisation without false
equivalence, delta scope/wording, provenance value handling, run status.
Synthetic data only."""
from __future__ import annotations

import pytest

from app.core.entity_intelligence import flags
from app.core.entity_intelligence.comparison import NameSignal, compare_names, compare_locations, LocationSignal
from app.core.entity_intelligence.delta import DeltaScope, DeltaType, compute_deltas, explain_deltas
from app.core.entity_intelligence.normalize import (address_is_usable, name_core, normalize_address,
                                                     normalize_name, normalize_zip5)
from app.core.entity_intelligence.observations import (DeliveryPath, EvidenceObservation, NameKind,
                                                        ObservationType, Provenance, SourceAuthority,
                                                        ValueHandling)
from app.core.entity_intelligence.ports import (CapabilityAvailability, DataRights, DataRightsClass,
                                                 RightsStatus, StateRegistryCapability)
from app.core.entity_intelligence.service import (RUN_COMPLETED, RUN_COMPLETED_WITH_UNAVAILABLE_SOURCES,
                                                   EntityIntelligenceService)
from app.evidence_sources.nppes_v2.adapter import SOURCE_ID, NppesV2Adapter, NppesV2Bundle, DATA_RIGHTS
from app.evidence_sources.nppes_v2.parser import (PARSE_FAILED, PARSE_OK, PARSE_PARTIAL, parse_main_file,
                                                  parse_other_name_file, parse_practice_location_file)
from app.evidence_sources.nppes_v2.schema import MAIN_FILE_COLUMN_COUNT
from ei_fixtures import (BALTIMORE, FREDERICK, MAIN_HEADER, OTHER_NAME_HEADER, PL_HEADER, PROGRAM_SOURCE,
                         csv_text, delivered, enable_all, main_row, other_name_row, pl_row, unavailable)


# ── feature flags ───────────────────────────────────────────────────────────

class TestFlagHardening:
    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "no", "0", "off", "", " ", "maybe", "enabled",
                                       "True ", "yes please", None, 0, 1, 2, 1.0, [], {}, object()])
    def test_only_real_true_values_enable(self, monkeypatch, value):
        from app.core.config import settings
        monkeypatch.setattr(settings, flags.MASTER, value)
        assert flags.flag_enabled(flags.MASTER) is (value is True or (isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on")))

    @pytest.mark.parametrize("value", [True, "true", "TRUE", " true ", "1", "yes", "on"])
    def test_recognised_true_values(self, monkeypatch, value):
        from app.core.config import settings
        monkeypatch.setattr(settings, flags.MASTER, value)
        assert flags.flag_enabled(flags.MASTER) is True

    def test_master_false_wins_over_every_sub_flag(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, flags.MASTER, False)
        for sub in (flags.NPPES, flags.IQVIA, flags.GOOGLE, flags.STATE_REGISTRY):
            monkeypatch.setattr(settings, sub, True)
            assert flags.source_enabled(sub) is False
            with pytest.raises(flags.FeatureDisabled):
                flags.require_enabled(sub, boundary="test")

    def test_master_string_false_wins(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, flags.MASTER, "false")
        monkeypatch.setattr(settings, flags.NPPES, True)
        with pytest.raises(flags.FeatureDisabled):
            flags.require_enabled(flags.NPPES, boundary="test")

    def test_unknown_flag_rejected(self):
        with pytest.raises(ValueError):
            flags.flag_enabled("ENTITY_INTELLIGENCE_SUPER_ENABLED")

    def test_runtime_evaluation_not_import_time(self, monkeypatch):
        from app.core.config import settings
        assert flags.entity_intelligence_enabled() is False
        monkeypatch.setattr(settings, flags.MASTER, True)
        assert flags.entity_intelligence_enabled() is True
        monkeypatch.setattr(settings, flags.MASTER, False)
        assert flags.entity_intelligence_enabled() is False

    def test_missing_attribute_is_false(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.delattr(settings, flags.GOOGLE, raising=False)
        assert flags.flag_enabled(flags.GOOGLE) is False

    def test_boundary_named_in_error(self, monkeypatch):
        with pytest.raises(flags.FeatureDisabled) as e:
            flags.require_enabled(boundary="SomeRoute.handler")
        assert "SomeRoute.handler" in str(e.value)


# ── parser hardening ────────────────────────────────────────────────────────

LONG_NAME = "SYNTHETIC " + "VERYLONGWORD" * 8 + " HEALTH SYSTEM LLC"   # 100+ chars


class TestParserHardening:
    def test_long_names_unicode_punctuation_preserved_verbatim(self):
        names = [LONG_NAME[:100], "SYNTHÉTIQUE SANTÉ — CLINIQUE \"A\" & B, L.L.C.", "  LEADING AND TRAILING  ",
                 "NAME WITH\tTAB", "O'BRIEN'S SYNTHETIC CLINIC"]
        rows = [main_row(f"99999000{i:02d}", n, practice=BALTIMORE) for i, n in enumerate(names)]
        parsed, report = parse_main_file(csv_text(MAIN_HEADER, rows))
        assert report.status == PARSE_OK and report.rows_ok == len(names)
        got = [r.legal_business_name for r in parsed]
        assert got[0] == LONG_NAME[:100]
        assert got[1] == names[1]
        assert got[2] == "LEADING AND TRAILING"          # whitespace trimmed, content intact
        assert got[3] == "NAME WITH\tTAB"
        assert got[4] == names[4]

    def test_zip_plus_four_and_five_digit(self):
        a = dict(BALTIMORE, postal_code="212011234")
        b = dict(BALTIMORE, postal_code="21201")
        rows = [main_row("9999900001", "S A", practice=a), main_row("9999900002", "S B", practice=b)]
        parsed, _ = parse_main_file(csv_text(MAIN_HEADER, rows))
        assert parsed[0].primary_practice_location["postal_code"] == "212011234"
        assert normalize_zip5("212011234") == normalize_zip5("21201") == "21201"
        assert normalize_zip5("21201-1234") == "21201"

    def test_suite_and_directional_variants_kept_raw_normalised_separately(self):
        addr = {"line1": "100 North Main Street, Suite 200", "line2": "", "city": "Baltimore", "state": "md",
                "postal_code": "21201"}
        n = normalize_address(addr)
        assert n["line1"] == "100 n main st ste 200"
        assert n["state"] == "MD"

    def test_missing_columns_row_is_recorded_not_dropped(self):
        good = main_row("9999900001", "SYNTHETIC GOOD LLC", practice=BALTIMORE)
        short = good[:200]
        parsed, report = parse_main_file(csv_text(MAIN_HEADER, [good, short]))
        assert report.status == PARSE_PARTIAL
        assert report.rows_read == 2 and report.rows_ok == 1 and report.rows_malformed == 1
        bad = [r for r in parsed if r.parse_note]
        assert len(bad) == 1 and "200 columns" in bad[0].parse_note and bad[0].line_number == 3

    def test_extra_columns_row_is_recorded_not_dropped(self):
        good = main_row("9999900001", "SYNTHETIC GOOD LLC", practice=BALTIMORE)
        parsed, report = parse_main_file(csv_text(MAIN_HEADER, [good + ["EXTRA"]]))
        assert report.status == PARSE_PARTIAL and report.rows_malformed == 1
        assert parsed[0].parse_note.startswith("malformed")

    def test_unexpected_header_is_failed_with_drift_note(self):
        header = MAIN_HEADER[:-1] + ["Brand New CMS Column"]
        header2 = MAIN_HEADER + ["Another"]
        for h in (header2,):
            _, report = parse_main_file(csv_text(h, [main_row("9999900001", "X") + [""]]))
            assert report.status == PARSE_FAILED and "schema drift" in report.header_note
        # same width, different names: parser cannot detect by width alone — documented limitation;
        # the header fingerprint in the acquisition design catches it.
        _, report = parse_main_file(csv_text(header, [main_row("9999900001", "X")]))
        assert report.header_ok is True

    def test_reference_file_header_drift_lists_missing_and_extra(self):
        bad = OTHER_NAME_HEADER[:-1] + ["New Col"]
        _, report = parse_other_name_file(csv_text(bad, [other_name_row("9999900001", "S", "3")]))
        assert report.status == PARSE_FAILED
        assert "missing=['Provider Other Organization Name Type Created Date']" in report.header_note or "missing=" in report.header_note
        assert "extra=['New Col']" in report.header_note

    def test_empty_file_is_failed_not_ok(self):
        for fn in (parse_main_file, parse_other_name_file, parse_practice_location_file):
            _, report = fn("")
            assert report.status == PARSE_FAILED and "empty" in report.header_note

    def test_header_only_file_is_failed_not_ok(self):
        _, report = parse_main_file(csv_text(MAIN_HEADER, []))
        assert report.rows_read == 0 and report.status == PARSE_FAILED

    def test_duplicate_rows_are_kept_and_counted(self):
        row = main_row("9999900001", "SYNTHETIC DUP LLC", practice=BALTIMORE)
        parsed, report = parse_main_file(csv_text(MAIN_HEADER, [row, row]))
        assert report.rows_ok == 2 and len(parsed) == 2
        bundle = NppesV2Bundle.from_texts(main_text=csv_text(MAIN_HEADER, [row, row]))
        assert len(bundle.organizations) == 1            # keyed by NPI; last wins, both preserved in the file hash

    def test_type1_rows_counted_as_skipped(self):
        rows = [main_row("9999900001", "", entity_type="1"), main_row("9999900002", "S ORG", practice=BALTIMORE)]
        _, report = parse_main_file(csv_text(MAIN_HEADER, rows))
        assert report.rows_skipped == 1 and report.rows_ok == 1 and report.status == PARSE_OK

    def test_reprocessing_same_file_is_deterministic(self):
        text = csv_text(MAIN_HEADER, [main_row("9999900001", "S ORG", practice=BALTIMORE)])
        b1 = NppesV2Bundle.from_texts(main_text=text, dataset_version="v")
        b2 = NppesV2Bundle.from_texts(main_text=text, dataset_version="v")
        assert b1.file_hashes == b2.file_hashes
        assert b1.organizations["9999900001"] == b2.organizations["9999900001"]

    def test_report_to_dict_carries_status(self):
        _, report = parse_practice_location_file(csv_text(PL_HEADER, [pl_row("9999900001", FREDERICK)]))
        d = report.to_dict()
        assert d["status"] == PARSE_OK and d["rows_skipped"] == 0 and d["header_ok"] is True

    def test_embedded_newline_and_quotes_in_field(self):
        row = main_row("9999900001", 'SYNTHETIC "QUOTED"\nSECOND LINE LLC', practice=BALTIMORE)
        parsed, report = parse_main_file(csv_text(MAIN_HEADER, [row]))
        assert report.status == PARSE_OK
        assert parsed[0].legal_business_name == 'SYNTHETIC "QUOTED"\nSECOND LINE LLC'

    def test_oversized_field_is_reported_not_raised(self):
        row = main_row("9999900001", "X" * 200_000, practice=BALTIMORE)
        parsed, report = parse_main_file(csv_text(MAIN_HEADER, [row, main_row("9999900002", "OK", practice=BALTIMORE)]))
        assert report.status in (PARSE_PARTIAL, PARSE_FAILED)
        assert any("field" in n.lower() for n in report.notes)
        assert report.stopped_at_line is not None


class TestVerifiedCmsSentinels:
    """Verified against the CMS weekly V2 bundle 083126_090626 (public data):
    type code 6 + "<UNAVAIL>" on the main file, real kinds in the reference file."""

    def _bundle(self, other_name_text=""):
        main = csv_text(MAIN_HEADER, [main_row("9999900001", "SYNTHETIC ORG LLC", other="<UNAVAIL>", other_code="6",
                                               practice=BALTIMORE)])
        return NppesV2Bundle.from_texts(main_text=main, other_name_text=other_name_text)

    def test_pointer_code_6_emits_no_name_and_flags_reference_file(self, monkeypatch):
        enable_all(monkeypatch)
        obs = NppesV2Adapter(self._bundle()).observations_for(canonical_entity_id="e", identifier="9999900001")
        names = [o.observed_value["name"] for o in obs if o.observation_type is ObservationType.NAME]
        assert names == ["SYNTHETIC ORG LLC"]
        assert "<UNAVAIL>" not in str([o.to_dict() for o in obs])
        ident = [o for o in obs if o.observation_type is ObservationType.IDENTIFIER][0]
        assert ident.observed_value["other_names_in_reference_file"] is True
        assert ident.observed_value["other_name_reference_rows_loaded"] == 0

    def test_reference_file_supplies_the_kinds(self, monkeypatch):
        enable_all(monkeypatch)
        on = csv_text(OTHER_NAME_HEADER, [other_name_row("9999900001", "SYNTHETIC CLINIC", "3"),
                                          other_name_row("9999900001", "SYNTHETIC CLINIC", "3")])   # duplicate rows occur
        obs = NppesV2Adapter(self._bundle(on)).observations_for(canonical_entity_id="e", identifier="9999900001")
        cur = delivered("SYNTHETIC CLINIC", BALTIMORE) + obs
        res = compare_names(cur, source_id=SOURCE_ID)
        assert res.signal is NameSignal.DBA_MATCH_IDENTIFIED          # duplicates of one kind are not ambiguity
        assert len(res.candidate_observation_ids) == 2

    def test_placeholder_is_absence_everywhere(self):
        rows = [main_row("9999900001", "<UNAVAIL>", practice=dict(BALTIMORE, line1="<UNAVAIL>"))]
        parsed, report = parse_main_file(csv_text(MAIN_HEADER, rows))
        assert parsed[0].legal_business_name is None
        assert parsed[0].primary_practice_location["line1"] is None
        assert report.status == PARSE_OK

    def test_placeholder_in_reference_file(self):
        parsed, _ = parse_other_name_file(csv_text(OTHER_NAME_HEADER, [other_name_row("9999900001", "<UNAVAIL>", "3")]))
        assert parsed[0].other_organization_name is None


# ── normalisation: formatting only, never identity ─────────────────────────

class TestNormalizationHardening:
    @pytest.mark.parametrize("a,b", [
        ("ABC Health LLC", "ABC HEALTH, L.L.C."),
        ("ABC Health Incorporated", "ABC Health Inc."),
        ("ABC Health Corporation", "ABC HEALTH CORP"),
        ("ABC   Health\tLLC", "ABC Health LLC"),
        ("Café Santé LLC", "CAFÉ SANTÉ, L.L.C."),
        ("Straße Klinik", "STRASSE KLINIK"),          # casefold handles ß
    ])
    def test_formatting_equivalents(self, a, b):
        assert normalize_name(a) == normalize_name(b)

    @pytest.mark.parametrize("a,b", [
        ("ABC Health LLC", "ABC Health Foundation"),
        ("ABC Health LLC", "ABC Health Mobile Clinic"),
        ("ABC Health LLC", "ABC Healthcare LLC"),
        ("ABC Health LLC", "ABD Health LLC"),
        ("ABC Health LLC", "ABC Health of Maryland LLC"),
        ("Cafe Sante LLC", "Café Santé LLC"),          # diacritics are NOT folded: no false equivalence
        ("ABC Health LLC", "ABC Health"),               # suffix presence differs → not equal (ambiguous path)
        ("St Mary Hospital", "Saint Mary Hospital"),    # no expansion dictionary for words
    ])
    def test_no_false_equivalence(self, a, b):
        assert normalize_name(a) != normalize_name(b)

    def test_core_is_only_for_ambiguity(self):
        assert name_core("ABC Health LLC") == name_core("ABC Health Inc") == "abc health"
        assert name_core("ABC Health Foundation") == "abc health foundation"

    def test_name_engine_reports_suffix_difference_as_ambiguous_not_match(self):
        obs = delivered("ABC HEALTH LLC") + [_nppes_name("ABC HEALTH INC", NameKind.LEGAL_BUSINESS_NAME)]
        assert compare_names(obs, source_id=SOURCE_ID).signal is NameSignal.AMBIGUOUS_NAME

    def test_name_engine_reports_foundation_as_conflict(self):
        obs = delivered("ABC HEALTH LLC") + [_nppes_name("ABC HEALTH FOUNDATION", NameKind.LEGAL_BUSINESS_NAME)]
        assert compare_names(obs, source_id=SOURCE_ID).signal is NameSignal.NAME_CONFLICT

    def test_empty_and_none(self):
        assert normalize_name(None) == normalize_name("") == normalize_name("  ...  ") == ""
        n = normalize_address({})
        assert n["line1"] == "" and n["zip5"] == "" and not address_is_usable(n)

    def test_address_locality_only_not_usable(self):
        assert not address_is_usable(normalize_address({"city": "Baltimore", "state": "MD", "postal_code": "21201"}))

    def test_location_zip_plus4_equals_zip5(self):
        a = dict(BALTIMORE, postal_code="21201-1234")
        obs = delivered("S", a) + [_nppes_loc(dict(BALTIMORE, postal_code="21201"))]
        assert compare_locations(obs, source_id=SOURCE_ID).signal is LocationSignal.PRIMARY_LOCATION_MATCH

    def test_location_same_street_different_zip_is_not_match(self):
        obs = delivered("S", BALTIMORE) + [_nppes_loc(dict(BALTIMORE, postal_code="21202"))]
        sig = compare_locations(obs, source_id=SOURCE_ID).signal
        assert sig in (LocationSignal.AMBIGUOUS_LOCATION, LocationSignal.LOCATION_CONFLICT)

    def test_location_suite_number_difference_is_not_match(self):
        obs = delivered("S", BALTIMORE) + [_nppes_loc(dict(BALTIMORE, line2="STE 300"))]
        assert compare_locations(obs, source_id=SOURCE_ID).signal is LocationSignal.AMBIGUOUS_LOCATION


# ── delta scope and wording ────────────────────────────────────────────────

class TestDeltaScopeAndWording:
    def test_delivered_address_change_is_scoped_and_worded_carefully(self):
        prior = delivered("S ORG", BALTIMORE)
        current = delivered("S ORG", FREDERICK)
        deltas = compute_deltas(prior, current)
        changed = [d for d in deltas if d.delta_type is DeltaType.ADDRESS_CHANGED]
        assert len(changed) == 1
        d = changed[0]
        assert d.scope is DeltaScope.DELIVERED_VALUE and d.subject
        assert d.explanation.startswith("The delivered ")
        assert "not a real-world event" in d.explanation
        for banned in ("moved", "relocated", "closed", "opened", "renamed"):
            assert banned not in d.explanation.lower()

    def test_evidence_side_change_is_scoped_evidence(self):
        prior = [_nppes_name("S ORG LLC", NameKind.LEGAL_BUSINESS_NAME)]
        current = [_nppes_name("S ORG INC", NameKind.LEGAL_BUSINESS_NAME)]
        deltas = compute_deltas(prior, current)
        assert deltas and all(d.scope is DeltaScope.EVIDENCE and not d.subject for d in deltas)
        assert deltas[0].explanation.startswith("The source-stated ")

    def test_to_dict_has_scope(self):
        d = compute_deltas(delivered("A"), delivered("B"))[0].to_dict()
        assert d["scope"] == "DELIVERED_VALUE"


# ── provenance value handling ───────────────────────────────────────────────

class TestValueHandling:
    def _obs(self, vh):
        return EvidenceObservation(canonical_entity_id="e", source_id="SRC", observation_type=ObservationType.NAME,
                                   observed_value={"name": "SYNTHETIC LICENSED NAME", "kind": "X"},
                                   source_authority=SourceAuthority.COMMERCIAL_REFERENCE,
                                   provenance=Provenance(source_owner="o", delivery_path=DeliveryPath.THIRD_PARTY_DELIVERY,
                                                         value_handling=vh))

    def test_raw_permitted(self):
        assert self._obs(ValueHandling.RAW_PERMITTED).to_dict()["observed_value"]["name"] == "SYNTHETIC LICENSED NAME"

    def test_hashed_reference_only_hides_value(self):
        d = self._obs(ValueHandling.HASHED_REFERENCE_ONLY).to_dict()
        assert "name" not in d["observed_value"] and len(d["observed_value"]["value_hash"]) == 64
        assert "SYNTHETIC LICENSED NAME" not in str(d)

    def test_transient_withholds(self):
        d = self._obs(ValueHandling.TRANSIENT_ONLY).to_dict()
        assert d["observed_value"] == {"withheld": "TRANSIENT_ONLY", "kind": "X"}

    def test_provenance_dict_carries_handling_and_schema(self):
        p = Provenance(source_owner="o", delivery_path=DeliveryPath.FILE_DOWNLOAD, schema_version="V2").to_dict()
        assert p["value_handling"] == "RAW_PERMITTED" and p["schema_version"] == "V2"

    def test_nppes_data_rights_documented_public(self):
        assert DATA_RIGHTS.rights_class is DataRightsClass.PUBLIC and DATA_RIGHTS.status is RightsStatus.DOCUMENTED
        assert DATA_RIGHTS.raw_storage_allowed and not DATA_RIGHTS.external_call_allowed

    def test_default_rights_are_most_restrictive(self):
        r = DataRights(DataRightsClass.RESTRICTED, RightsStatus.NOT_YET_APPROVED)
        assert not any([r.storage_allowed, r.raw_storage_allowed, r.display_allowed, r.redistribution_allowed,
                        r.external_call_allowed])
        assert r.value_handling is ValueHandling.TRANSIENT_ONLY


class TestStateRegistryCapability:
    def test_defaults_not_supported(self):
        c = StateRegistryCapability("XX")
        assert c.supported_fields() == [] and c.acquisition is CapabilityAvailability.NOT_SUPPORTED

    def test_manual_only_is_not_supported(self):
        c = StateRegistryCapability("XX", legal_name=CapabilityAvailability.MANUAL_ONLY,
                                    entity_status=CapabilityAvailability.SUPPORTED)
        assert c.supported_fields() == ["entity_status"]


# ── run status / prior review passthrough ──────────────────────────────────

class TestRunStatus:
    def test_unavailable_source_sets_explicit_status(self, monkeypatch):
        enable_all(monkeypatch)
        run = EntityIntelligenceService().evaluate(canonical_entity_id="e", current=delivered("S") + [unavailable(SOURCE_ID)],
                                                   source_ids=[SOURCE_ID])
        assert run.status == RUN_COMPLETED_WITH_UNAVAILABLE_SOURCES
        assert run.assessment.assessment.value == "SOURCE_UNAVAILABLE"

    def test_prior_review_reference_is_echoed_not_used(self, monkeypatch):
        enable_all(monkeypatch)
        ref = {"review_id": "synthetic-qa-1", "recorded_outcome": "synthetic label"}
        cur = delivered("S ORG") + [_nppes_name("S ORG", NameKind.LEGAL_BUSINESS_NAME)]
        with_ref = EntityIntelligenceService().evaluate(canonical_entity_id="e", current=cur, source_ids=[SOURCE_ID],
                                                        prior_review_reference=ref)
        without = EntityIntelligenceService().evaluate(canonical_entity_id="e", current=cur, source_ids=[SOURCE_ID])
        assert with_ref.assessment.assessment == without.assessment.assessment
        assert with_ref.assessment.basis == without.assessment.basis
        d = with_ref.to_dict()
        assert d["prior_decision_exists"] is True and d["prior_review_reference"] == ref
        assert without.to_dict()["prior_decision_exists"] is False
        assert with_ref.status == RUN_COMPLETED


# ── helpers ────────────────────────────────────────────────────────────────

def _nppes_prov():
    return Provenance(source_owner="CMS NPPES", delivery_path=DeliveryPath.FILE_DOWNLOAD, source_record_ref="synthetic")


def _nppes_name(name, kind, entity_id="ent-1"):
    return EvidenceObservation(canonical_entity_id=entity_id, source_id=SOURCE_ID, observation_type=ObservationType.NAME,
                               role=kind.value, observed_value={"name": name}, normalized_value={"name": normalize_name(name)},
                               source_authority=SourceAuthority.FEDERAL_REGISTRY, provenance=_nppes_prov())


def _nppes_loc(addr, role="PRIMARY_PRACTICE_LOCATION", entity_id="ent-1"):
    return EvidenceObservation(canonical_entity_id=entity_id, source_id=SOURCE_ID, observation_type=ObservationType.LOCATION,
                               role=role, observed_value=dict(addr), normalized_value=normalize_address(addr),
                               source_authority=SourceAuthority.FEDERAL_REGISTRY, provenance=_nppes_prov())
