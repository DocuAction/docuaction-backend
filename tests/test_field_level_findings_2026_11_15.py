"""Field-level findings export (F3): headers, crosswalk, vocabularies, reconciliation. Synthetic."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from types import SimpleNamespace as NS

from app.reports.data import field_level_findings as fl

HEADERS_F31 = ["Snapshot_Date", "Source_Line", "QHIN_ID", "HCID", "TEFCA_ID", "Entity_Type", "NPI", "AGT_Record_ID",
               "Field_ID", "Field_Discrepancy_Class", "Submitted_Value", "Reference_Value", "Reference_Source",
               "Reference_Source_Date", "B2_Subclassifications", "Issue_Type", "Field_Reason", "Purpose_of_Use"]
WHEN = datetime(2026, 11, 8, 9, 30, 0)


def fact(i, **o):
    f = {"agt_record_id": f"agt-{i}", "source_line": i, "qhin_number": "2.16.840.1.113883.3.0001",
         "hcid": f"hc{i}", "tefca_id": f"TEF{i}", "participation_level": "Participant", "submitted_npi": "0123456789",
         "purpose_of_use": "T-TRTMNT,T-PH"}
    f.update(o)
    return f


def issue(i, itype, field=None, **o):
    d = dict(issue_type=itype, field_name=field, original_value="orig", suggested_value="", suggested_source="",
             description=f"{itype} on record {i}", run_id="run-1", created_at=WHEN, source_record_id=f"agt-{i}")
    d.update(o)
    return NS(**d)


def build(facts, issues, **kw):
    return fl.build_field_rows(facts, issues, **kw)


def col(row, name):
    return row[fl.FIELD_HEADERS.index(name)]


def test_the_eighteen_headers_in_the_specified_order_and_spelling():
    assert fl.FIELD_HEADERS == HEADERS_F31
    out = build([fact(1)], [issue(1, "SHARED_TEFCAID")])
    assert out["headers"] == HEADERS_F31 and len(out["rows"][0]) == 18
    parsed = list(csv.reader(io.StringIO(fl.to_csv(out).decode("utf-8-sig"))))
    assert parsed[0] == HEADERS_F31 and len(parsed) == 2


def test_the_crosswalk_matches_the_methodology_table_exactly():
    expect = {  # F3.3, read from the controlled file's table cells
        "ADDRESS_TEXT_IS_A_LABEL": "B2_OTHER", "COLUMN_EMPTY_IN_DELIVERY": "MISSING_ENTRY", "DOA_NOT_AN_OID": "DOA_NOT_AN_OID",
        "DUPLICATE_HCID": "DUPLICATE_HCID", "EMBEDDED_TAB": "B2_WHITE_SPACE",
        "IDENTIFIER_EXISTING_VALUE_CONFLICT": "IDENTIFIER_EXISTING_VALUE_CONFLICT", "INACTIVE_NEW_ENTRANT": "INACTIVE_RECORD",
        "INACTIVE_RECORD": "INACTIVE_RECORD", "MISSING_PURPOSES_OF_USE": "MISSING_ENTRY", "MULTIPLE_NPI_IN_ONE_FIELD": "MULTI_ENTRY",
        "NODE_TYPE_IS_NOT_HIERARCHY": "NODE_TYPE_IS_NOT_HIERARCHY", "NON_PROVIDER_ORGANISATION": "NON_PROVIDER_ORGANISATION",
        "NPI_APPLICABILITY_UNRESOLVED": "NPI_APPLICABILITY_UNRESOLVED", "NPI_CHECKSUM_INVALID": "NPI_INVALID",
        "NPI_EXISTING_VALUE_CONFLICT": "NPI_INVALID", "NPI_NOT_SUPPLIED": "MISSING_ENTRY", "NPI_REQUIRED": "MISSING_ENTRY",
        "PARTICIPANT_PARENT_IS_QHIN": "PARTICIPANT_PARENT_IS_QHIN", "PURPOSE_TOKEN_VARIANT": "PURPOSE_TOKEN_VARIANT",
        "SAM_EXTRACT_CANDIDATE": "SAM_EXTRACT_CANDIDATE", "SHARED_TEFCAID": "DUPLICATE_TEFCAID",
        "SUBPARTICIPANT_PARENTED_TO_QHIN": "SUBPARTICIPANT_PARENTED_TO_QHIN", "TEST_RECORD_SUSPECTED": "TEST_RECORD_SUSPECTED",
        "UNKNOWN_PURPOSE_TOKEN": "UNKNOWN_PURPOSE_TOKEN", "ZIP_LEADING_ZERO_STRIPPED": "B2_OTHER", "ZIP_STATE_MISMATCH": "B2_OTHER"}
    assert {k: v[0] for k, v in fl.CROSSWALK.items()} == expect
    assert "NAIC_ON_NON_PAYER" not in fl.CROSSWALK and "NAIC_ON_NON_PAYER" in fl.EXCLUDED_ISSUE_TYPES
    for _k, (_t, rule) in fl.CROSSWALK.items():
        for fid in (rule if isinstance(rule, tuple) else (rule,)):
            assert fid in (fl.ACTUAL, None) or fid in fl.FIELD_IDS


def test_fixed_field_ids_actual_field_and_not_specified():
    out = build([fact(1)], [issue(1, "SHARED_TEFCAID"), issue(1, "NPI_REQUIRED"),
                            issue(1, "EMBEDDED_TAB", "address_line"), issue(1, "EMBEDDED_TAB", "bogus_field"),
                            issue(1, "TEST_RECORD_SUSPECTED"), issue(1, "ZIP_STATE_MISMATCH", "address_state"),
                            issue(1, "ZIP_STATE_MISMATCH", "name")])
    r = out["rows"]
    assert (col(r[0], "Field_ID"), col(r[0], "Issue_Type")) == ("TEFCAID", "DUPLICATE_TEFCAID")
    assert (col(r[1], "Field_ID"), col(r[1], "Issue_Type")) == ("NPI", "MISSING_ENTRY")
    assert col(r[2], "Field_ID") == "address_line" and col(r[2], "B2_Subclassifications") == "WHITE_SPACE"
    assert col(r[3], "Field_ID") == "" and "Field_ID not established" in col(r[3], "Field_Reason")
    assert col(r[4], "Field_ID") == "" and "No Field_ID is specified" in col(r[4], "Field_Reason")
    assert col(r[5], "Field_ID") == "address_state" and col(r[6], "Field_ID") == ""
    assert out["reconciliation"]["field_id_blank"] == 3 and len(out["mapping_exceptions"]) == 3
    assert all(col(x, "Field_ID") in fl.FIELD_IDS | {""} for x in r)


def test_naic_is_excluded_and_counted_unmapped_types_make_no_row_and_nothing_is_manufactured():
    out = build([fact(1), fact(2)], [issue(1, "NAIC_ON_NON_PAYER"), issue(1, "BRAND_NEW_CODE"),
                                     issue(1, "BRAND_NEW_CODE"), issue(1, "NPI_REQUIRED"),
                                     issue(9, "NPI_REQUIRED", source_record_id="not-in-this-delivery")])
    rc = out["reconciliation"]
    assert (rc["issues_read"], rc["excluded_naic"], rc["rows"]) == (5, 1, 1)
    assert rc["issue_types_not_in_crosswalk"] == {"BRAND_NEW_CODE": 2} and rc["issues_without_record"] == 1
    assert (rc["records"], rc["records_with_findings"], rc["records_without_findings"]) == (2, 1, 1)


def test_one_record_many_findings_no_dedup_and_field_id_repeats():
    out = build([fact(1)], [issue(1, "NPI_REQUIRED"), issue(1, "NPI_CHECKSUM_INVALID"), issue(1, "NPI_NOT_SUPPLIED")])
    assert len(out["rows"]) == 3 and [col(r, "Field_ID") for r in out["rows"]] == ["NPI"] * 3
    assert out["reconciliation"]["records_with_findings"] == 1


def test_identifiers_npi_and_purpose_are_verbatim_and_never_filled():
    f = fact(1, submitted_npi="", hcid="0012", tefca_id="00TEF", qhin_number="01.2", purpose_of_use=" T-TRTMNT ,T-PH ")
    f["supplemental_npi"] = "1999999999"
    r = build([f], [issue(1, "NPI_REQUIRED")])["rows"][0]
    assert col(r, "NPI") == "" and "1999999999" not in "".join(r)
    assert (col(r, "HCID"), col(r, "TEFCA_ID"), col(r, "QHIN_ID")) == ("0012", "00TEF", "01.2")
    assert col(r, "Purpose_of_Use") == " T-TRTMNT ,T-PH "


def test_entity_type_is_participation_level_not_the_nppes_type():
    assert fl.entity_type("Participant") == "Participant" and fl.entity_type("Sub-Participant") == "Subparticipant"
    assert fl.entity_type("subparticipant") == "Subparticipant"
    for bad in (None, "", "QHIN", "Individual", "2"):
        assert fl.entity_type(bad) == ""
    r = build([fact(1, participation_level="QHIN")], [issue(1, "NPI_REQUIRED")])["rows"][0]
    assert col(r, "Entity_Type") == "" and "Entity_Type blank" in col(r, "Field_Reason")


def test_field_discrepancy_class_is_blank_and_reference_values_need_an_attributable_source():
    rows = build([fact(1)], [issue(1, "ZIP_LEADING_ZERO_STRIPPED", "address_postalCode", suggested_value="02101", suggested_source="IQVIA"),
                             issue(1, "ZIP_LEADING_ZERO_STRIPPED", "address_postalCode", suggested_value="02101", suggested_source="")])["rows"]
    assert all(col(r, "Field_Discrepancy_Class") == "" for r in rows)
    assert (col(rows[0], "Reference_Value"), col(rows[0], "Reference_Source")) == ("02101", "IQVIA")
    assert (col(rows[1], "Reference_Value"), col(rows[1], "Reference_Source")) == ("", "")


def test_reference_source_date_is_the_retained_check_event_never_another_date():
    out = build([fact(1)], [issue(1, "NPI_REQUIRED"), issue(1, "NPI_REQUIRED", run_id=None)], snapshot_date="2026-10-30")
    a, b = out["rows"]
    assert col(a, "Reference_Source_Date") == "2026-11-08" and col(a, "Snapshot_Date") == "2026-10-30"
    assert col(b, "Reference_Source_Date") == "" and "no run/check event is retained" in col(b, "Field_Reason")
    blank = build([fact(1)], [issue(1, "NPI_REQUIRED")])
    assert col(blank["rows"][0], "Snapshot_Date") == "" and blank["reconciliation"]["snapshot_date_blank"] is True


def test_b2_subclassification_only_from_supported_issue_types():
    out = build([fact(1)], [issue(1, "ZIP_LEADING_ZERO_STRIPPED", "address_postalCode"), issue(1, "EMBEDDED_TAB", "name"),
                            issue(1, "NPI_REQUIRED")])
    assert [col(r, "B2_Subclassifications") for r in out["rows"]] == ["OTHER", "WHITE_SPACE", ""]
    assert all(col(r, "B2_Subclassifications") in fl.B2_VALUES | {""} for r in out["rows"])
