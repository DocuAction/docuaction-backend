"""Field-level findings export (F3): headers, crosswalk, vocabularies, reconciliation. Synthetic."""
from __future__ import annotations

import csv
import pytest
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


# ── model-review follow-up (the `imran` reviewer agent; model review, not QA) ──────────────────

def test_a_delivery_assessed_twice_exports_only_the_selected_runs_findings():
    out = build([fact(1)], [issue(1, "NPI_REQUIRED", "NPI", run_id="run-1"),
                            issue(1, "NPI_REQUIRED", "NPI", run_id="run-2", created_at=datetime(2026, 11, 9, 9, 0, 0)),
                            issue(1, "SAM_EXTRACT_CANDIDATE", run_id=None)], run_id="run-2")
    assert len(out["rows"]) == 2 and out["reconciliation"]["issues_from_other_runs"] == 1
    assert col(out["rows"][0], "Reference_Source_Date") == "2026-11-09"
    unscoped = build([fact(1)], [issue(1, "NPI_REQUIRED", "NPI", run_id="run-1"),
                                 issue(1, "NPI_REQUIRED", "NPI", run_id="run-2")])
    assert len(unscoped["rows"]) == 2, "without a selected run nothing is filtered (the caller must pass it)"


def test_internal_normalisation_labels_are_not_reference_sources():
    for label in ("ZERO_PAD", "WHITESPACE_NORMALISE", "OBSERVED_FREQUENCY", "tefca_entity_identifiers"):
        r = build([fact(1)], [issue(1, "ZIP_LEADING_ZERO_STRIPPED", "address_postalCode",
                                    suggested_value="02101", suggested_source=label)])["rows"][0]
        assert (col(r, "Reference_Value"), col(r, "Reference_Source")) == ("", ""), label
    r = build([fact(1)], [issue(1, "ZIP_LEADING_ZERO_STRIPPED", "address_postalCode",
                                suggested_value="02101", suggested_source="sam.gov")])["rows"][0]
    assert (col(r, "Reference_Value"), col(r, "Reference_Source")) == ("02101", "SAM.gov")


def test_a_fixed_field_id_is_not_used_when_the_finding_concerns_another_column():
    """A fixed Field_ID is still withheld when the finding names an unrelated delivered column."""
    r = build([fact(1)], [issue(1, "DUPLICATE_HCID", "name", original_value="dup")])
    row = r["rows"][0]
    assert col(row, "Field_ID") == "" and "concerns 'name', not HCID" in col(row, "Field_Reason")
    assert col(row, "Submitted_Value") == "dup" and len(r["mapping_exceptions"]) == 1
    ok = build([fact(1)], [issue(1, "DOA_NOT_AN_OID", "partOf")])["rows"][0]
    assert col(ok, "Field_ID") == "partof"


def test_doa_not_an_oid_follows_the_f33_field_id_and_keeps_the_delivered_source_column_traceable():
    """F3.3 maps DOA_NOT_AN_OID to Field_ID `partof`; the finding originates in the delivered `doa` column.
    Field_ID follows F3.3 (not blank), Submitted_Value stays the delivered doa value, the source column and the
    pending confirmation are stated in Field_Reason, and a separate (non-contract) exception records it."""
    r = build([fact(1)], [issue(1, "DOA_NOT_AN_OID", "doa", original_value="not-an-oid")])
    row = r["rows"][0]
    assert len(row) == 18 and r["headers"] == HEADERS_F31
    assert col(row, "Field_ID") == "partof"
    assert col(row, "Submitted_Value") == "not-an-oid"
    reason = col(row, "Field_Reason")
    assert "Source column: doa" in reason and "F3.3" in reason and "confirmation" in reason
    assert r["reconciliation"]["field_id_blank"] == 0 and r["reconciliation"]["field_id_source_column_differs"] == 1
    assert [e["kind"] for e in r["mapping_exceptions"]] == ["source_column_differs"]
    assert r["mapping_exceptions"][0]["source_column"] == "doa" and r["mapping_exceptions"][0]["field_id"] == "partof"
    # the finding already on partof carries no such note
    plain = build([fact(1)], [issue(1, "DOA_NOT_AN_OID", "partOf")])
    assert "Source column" not in col(plain["rows"][0], "Field_Reason") and plain["mapping_exceptions"] == []


# ── CodeRabbit on #231 (32b67c2): XLSX truncation and run pinning ────────────────────────────────────────────

def test_an_xlsx_cell_over_excels_limit_is_refused_not_silently_shortened():
    from openpyxl import load_workbook
    import io as _io
    long = "x" * (fl.XLSX_CELL_LIMIT + 5)
    out = build([fact(1)], [issue(1, "NPI_REQUIRED", "NPI", original_value=long)])
    with pytest.raises(fl.FieldExportRefused) as exc:
        fl.to_xlsx(out)
    assert "Submitted_Value" in str(exc.value) and "CSV" in str(exc.value)
    csv_text = fl.to_csv(out).decode("utf-8-sig")
    assert long in csv_text, "the CSV carries the full text"
    ok = build([fact(1)], [issue(1, "NPI_REQUIRED", "NPI", original_value="y" * fl.XLSX_CELL_LIMIT)])
    ws = load_workbook(_io.BytesIO(fl.to_xlsx(ok))).active
    assert len(ws.cell(row=2, column=fl.FIELD_HEADERS.index("Submitted_Value") + 1).value) == fl.XLSX_CELL_LIMIT


def test_the_field_export_is_pinned_to_the_run_the_record_level_export_used(monkeypatch, tmp_path):
    """A run completing between the two loads must not give the field-level file a different run."""
    import asyncio, uuid
    from types import SimpleNamespace as NS2
    from scripts import generate_record_review_package as pkg
    from app.reports.data import october_review_loader as loader
    seen = {}

    class _Ctx2:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    async def fake_load_dataset(db, **kw):
        return {"report_id": kw["report_id"], "sheets": {"Records": {"columns": [], "rows": []}}, "summary": {"complete": True, "unique_records": 0, "buckets": {"B1": 0, "B2": 0, "B3": 0, "B4": 0}, "held_inside_b4": 0, "unclassified_non_held_count": 0, "unclassified_non_held_ids": []},
                "load_meta": {"assessment_run_id": "RUN-A"}, "banner": "b"}

    async def fake_load_facts(db, **kw):
        seen["run_id"] = kw.get("run_id")
        return {"facts": [], "meta": {"intake_id": str(uuid.uuid4()), "assessment_run_id": kw.get("run_id") or "RUN-B-A-LATER-RUN"}}

    async def fake_issues(*_a, **_k):
        return []

    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx2())
    monkeypatch.setattr(loader, "load_dataset", fake_load_dataset)
    monkeypatch.setattr(loader, "load_record_facts", fake_load_facts)
    monkeypatch.setattr(fl, "load_issues", fake_issues)
    monkeypatch.setattr(loader, "_cutoff", lambda v: datetime(2099, 1, 1))

    async def no_intake(*_a, **_k):
        return None

    monkeypatch.setattr(_Ctx2, "__aenter__", lambda self: _async_obj())
    args = NS2(intake=str(uuid.uuid4()), cutoff="2099-01-01T00:00:00", out=str(tmp_path / "o"), run_id=None, formats="csv", baseline=None, classification="DRAFT")
    asyncio.run(pkg.build(args))
    assert seen["run_id"] == "RUN-A", seen


async def _async_obj():
    class _D:
        async def get(self, *_a, **_k):
            return None
    return _D()
