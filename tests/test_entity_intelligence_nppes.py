"""NPPES V2 parser and adapter — synthetic files, exact CMS layouts."""
from __future__ import annotations

import pytest

from app.core.entity_intelligence import FeatureDisabled
from app.core.entity_intelligence.observations import NameKind, LocationRole, ObservationType
from app.evidence_sources.nppes_v2 import (MAIN_FILE_COLUMNS, OTHER_NAME_COLUMNS, OTHER_ORG_NAME_TYPE_CODES,
                                           PRACTICE_LOCATION_COLUMNS, ENTITY_TYPE_CODES, NppesV2Adapter, SOURCE_ID)
from app.evidence_sources.nppes_v2.adapter import NppesV2Bundle, SIGNAL_DBA
from app.evidence_sources.nppes_v2.parser import (parse_main_file, parse_other_name_file,
                                                  parse_practice_location_file)
from app.evidence_sources.nppes_v2.schema import other_name_kind

from ei_fixtures import (BALTIMORE, FREDERICK, ROCKVILLE, MAIN_HEADER, OTHER_NAME_HEADER, PL_HEADER,
                               csv_text, enable_all, main_row, other_name_row, pl_row)

NPI = "9999900001"


def _bundle(**kw) -> NppesV2Bundle:
    main = csv_text(MAIN_HEADER, [
        main_row(NPI, "SYNTHETIC HEALTHCARE LLC", other="SYNTHETIC MOBILE CLINIC", other_code="3",
                 practice=BALTIMORE, mailing=ROCKVILLE),
        main_row("9999900002", "SYNTHETIC SOLO PROVIDER", entity_type="1"),      # Type 1, skipped
        main_row("9999900003", "SYNTHETIC RENAMED INC", other="SYNTHETIC OLD NAME CORP", other_code="4"),
        main_row("9999900004", "SYNTHETIC OTHER CO", other="SYNTHETIC ALIAS", other_code="5"),
        main_row("9999900005", "SYNTHETIC DUPLICATE NAME LLC"),
        main_row("9999900005", "SYNTHETIC DUPLICATE NAME LLC"),                  # duplicate row
    ])
    other = csv_text(OTHER_NAME_HEADER, [
        other_name_row(NPI, "SYNTHETIC FAMILY CARE", "3"),
        other_name_row(NPI, "SYNTHETIC LEGACY GROUP", "4"),
        other_name_row(NPI, "SYNTHETIC ALSO KNOWN AS", "5"),
    ])
    pl = csv_text(PL_HEADER, [pl_row(NPI, FREDERICK, "4105550100"), pl_row(NPI, ROCKVILLE)])
    return NppesV2Bundle.from_texts(main_text=main, other_name_text=other, practice_location_text=pl,
                                    dataset_version="SYNTHETIC_Weekly_V2", **kw)


class TestSchema:
    def test_layouts_match_the_readme(self):
        assert [c[0] for c in OTHER_NAME_COLUMNS] == ["NPI", "Provider Other Organization Name",
                                                       "Provider Other Organization Name Type Code", "Created Date"]
        assert len(PRACTICE_LOCATION_COLUMNS) == 10 and PRACTICE_LOCATION_COLUMNS[0][0] == "NPI"
        lbn = next(c for c in MAIN_FILE_COLUMNS if c[1].startswith("Provider Organization Name"))
        assert lbn[2] == 100                     # V2 extended length
        assert OTHER_ORG_NAME_TYPE_CODES["3"][0] == "Doing Business As"
        assert OTHER_ORG_NAME_TYPE_CODES["4"][0] == "Former Legal Business Name"
        assert OTHER_ORG_NAME_TYPE_CODES["5"][0] == "Other Name"
        assert ENTITY_TYPE_CODES == {"1": "Individual", "2": "Organization"}

    def test_only_code_3_is_dba(self):
        assert other_name_kind("3") == NameKind.DOING_BUSINESS_AS.value
        assert other_name_kind("4") == NameKind.FORMER_LEGAL_BUSINESS_NAME.value
        assert other_name_kind("5") == NameKind.OTHER_NAME.value
        assert other_name_kind("") == "UNKNOWN"


class TestParser:
    def test_valid_records_and_type1_skip(self):
        rows, report = parse_main_file(csv_text(MAIN_HEADER, [main_row(NPI, "SYNTHETIC HEALTHCARE LLC", practice=BALTIMORE),
                                                              main_row("9999900002", "X", entity_type="1")]))
        assert report.header_ok and report.rows_read == 2 and report.rows_ok == 1
        assert rows[0].legal_business_name == "SYNTHETIC HEALTHCARE LLC"
        assert rows[0].primary_practice_location["city"] == "BALTIMORE"

    def test_malformed_record_is_recorded_not_dropped(self):
        text = csv_text(MAIN_HEADER, [main_row(NPI, "OK LLC")]) + '"9999900009","2","short"\n'
        rows, report = parse_main_file(text)
        assert report.rows_malformed == 1
        bad = [r for r in rows if r.parse_note]
        assert bad and bad[0].npi == "9999900009" and "malformed" in bad[0].parse_note

    def test_missing_optional_fields_are_none(self):
        rows, _ = parse_main_file(csv_text(MAIN_HEADER, [main_row(NPI, "SYNTHETIC LLC")]))
        assert rows[0].other_organization_name is None and rows[0].deactivation_date is None
        assert rows[0].primary_practice_location["line1"] is None

    def test_wrong_header_is_flagged(self):
        _, report = parse_other_name_file('"NPI","Something Else"\n"1","x"\n')
        assert report.header_ok is False and "header differs" in report.header_note

    def test_reference_files(self):
        names, r = parse_other_name_file(csv_text(OTHER_NAME_HEADER, [other_name_row(NPI, "A", "3"), other_name_row(NPI, "B", "5")]))
        assert r.header_ok and len(names) == 2 and names[1].type_code == "5"
        locs, r2 = parse_practice_location_file(csv_text(PL_HEADER, [pl_row(NPI, FREDERICK, "4105550100")]))
        assert r2.header_ok and locs[0].address["city"] == "FREDERICK" and locs[0].telephone == "4105550100"

    def test_duplicate_rows_are_kept_by_the_parser(self):
        rows, _ = parse_main_file(csv_text(MAIN_HEADER, [main_row(NPI, "DUP LLC"), main_row(NPI, "DUP LLC")]))
        assert len(rows) == 2


class TestAdapter:
    def test_gated_off_by_default(self):
        with pytest.raises(FeatureDisabled):
            NppesV2Adapter(_bundle()).observations_for(canonical_entity_id="e", identifier=NPI, provenance=None)

    def test_gated_by_the_subordinate_flag(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "ENTITY_INTELLIGENCE_ENABLED", True)
        monkeypatch.setattr(settings, "NPPES_IDENTITY_CORROBORATION_ENABLED", False)
        with pytest.raises(FeatureDisabled):
            NppesV2Adapter(_bundle()).observations_for(canonical_entity_id="e", identifier=NPI, provenance=None)

    def test_observations_with_kinds_roles_and_provenance(self, monkeypatch):
        enable_all(monkeypatch)
        obs = NppesV2Adapter(_bundle()).observations_for(canonical_entity_id="ent-1", identifier=NPI, provenance=None)
        by = {}
        for o in obs:
            by.setdefault((o.observation_type, o.role), []).append(o)
        assert len(by[(ObservationType.IDENTIFIER, "NPI")]) == 1
        assert by[(ObservationType.IDENTIFIER, "NPI")][0].observed_value["entity_type"] == "2"
        assert by[(ObservationType.NAME, NameKind.LEGAL_BUSINESS_NAME.value)][0].observed_value["name"] == "SYNTHETIC HEALTHCARE LLC"
        dbas = by[(ObservationType.NAME, NameKind.DOING_BUSINESS_AS.value)]
        assert {o.observed_value["name"] for o in dbas} == {"SYNTHETIC MOBILE CLINIC", "SYNTHETIC FAMILY CARE"}
        assert all(o.observed_value["signal"] == SIGNAL_DBA for o in dbas)
        former = by[(ObservationType.NAME, NameKind.FORMER_LEGAL_BUSINESS_NAME.value)]
        assert [o.observed_value["name"] for o in former] == ["SYNTHETIC LEGACY GROUP"]
        assert "signal" not in former[0].observed_value           # not labelled DBA
        other = by[(ObservationType.NAME, NameKind.OTHER_NAME.value)]
        assert [o.observed_value["name"] for o in other] == ["SYNTHETIC ALSO KNOWN AS"]
        assert len(by[(ObservationType.LOCATION, LocationRole.PRIMARY_PRACTICE_LOCATION.value)]) == 1
        assert len(by[(ObservationType.LOCATION, LocationRole.ADDITIONAL_PRACTICE_LOCATION.value)]) == 2
        assert len(by[(ObservationType.LOCATION, LocationRole.MAILING_LOCATION.value)]) == 1
        # provenance
        p = dbas[0].provenance
        assert p.source_owner.startswith("CMS NPPES") and p.delivery_path.value == "FILE_DOWNLOAD"
        assert p.source_version.dataset_version == "SYNTHETIC_Weekly_V2" and p.source_file_sha256
        assert p.source_record_ref.endswith("Provider Other Organization Name")
        assert all(o.source_id == SOURCE_ID for o in obs)
        assert all(o.to_dict()["provenance"]["source_version"]["retrieval_method"] == "DOWNLOAD" for o in obs)

    def test_missing_npi_yields_no_observations(self, monkeypatch):
        enable_all(monkeypatch)
        assert NppesV2Adapter(_bundle()).observations_for(canonical_entity_id="e", identifier="0000000000", provenance=None) == []

    def test_no_network_code_in_the_adapter(self):
        import inspect
        import app.evidence_sources.nppes_v2.adapter as a
        import app.evidence_sources.nppes_v2.parser as p
        src = inspect.getsource(a) + inspect.getsource(p)
        for word in ("httpx", "requests", "urllib", "aiohttp", "socket"):
            assert word not in src
