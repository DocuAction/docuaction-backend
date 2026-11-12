"""HELD counted once inside B4; one dataset feeds Excel, CSV and PDF-HTML."""
import csv
import io

import pytest

from app.reports.data import october_review_dataset as d


def _f(i, bucket="B1", status="ACTIVE", **kw):
    base = dict(source_line=i, agt_record_id=f"agt-{i}", qhin_number="0012",
                organization=f"Org {i}", bucket=bucket, status=status,
                nppes="Verified", pecos="Enrollment found", leie="Clean",
                sam="Screened", submitted_address="1 Main", reasons="r")
    base.update(kw)
    return base


def _facts():
    return [_f(1, "B1"), _f(2, "B1"), _f(3, "B2", provisional=True),
            _f(4, "B3"), _f(5, "B4"),
            _f(6, None, status="HELD", held_reason="blocking identity issue"),
            _f(7, None, status="HELD", held_reason="other blocker")]


def test_held_is_counted_once_inside_b4_and_keeps_status_and_reason():
    ds = d.build_dataset(_facts(), report_id="R", findings_count=1336)
    s = ds["summary"]
    assert s["buckets"] == {"B1": 2, "B2": 1, "B3": 1, "B4": 3}   # 1 + 2 HELD
    assert s["held_inside_b4"] == 2
    assert sum(s["buckets"].values()) == s["unique_records"] == 7
    rows = {r[1]: r for r in ds["sheets"]["Records"]["rows"]}
    st, bk, basis = (d.RECORD_COLUMNS.index(c) for c in ("Status", "Bucket", "Bucket Basis"))
    assert rows["agt-6"][st] == "HELD" and rows["agt-6"][bk] == "B4"
    assert "blocking identity issue" in rows["agt-6"][basis]


def test_findings_are_separate_from_bucket_totals():
    s = d.build_dataset(_facts(), report_id="R", findings_count=1336)["summary"]
    assert s["field_findings"] == 1336 and s["findings_counted_in_buckets"] is False
    assert s["unique_records"] == 7


def test_totals_are_recounted_never_forced():
    ds = d.build_dataset(_facts()[:3], report_id="R")
    assert ds["summary"]["unique_records"] == 3


def test_duplicate_agt_record_id_is_refused():
    with pytest.raises(d.DatasetRefused):
        d.build_dataset([_f(1), _f(1)], report_id="R")


def test_unclassified_non_held_record_is_incomplete_not_guessed():
    """Superseded: this used to refuse the whole build. The dataset is now
    built and reported INCOMPLETE; the record gets NO bucket (see the
    dedicated incomplete-mode tests below)."""
    ds = d.build_dataset([_f(1, None), _f(2, "B1")], report_id="R")
    assert ds["summary"]["complete"] is False
    assert ds["summary"]["buckets"] == {"B1": 1, "B2": 0, "B3": 0, "B4": 0}


def test_usps_default_entity_type_and_provisional_wording():
    row = d.build_record_row(_f(3, "B2", provisional=True, entity_type="1"))
    c = d.RECORD_COLUMNS.index
    assert row[c("USPS Address Check")] == "Not checked—unavailable"
    assert row[c("Entity_Type (NPPES Entity Type Code)")] == d.ENTITY_TYPE_NOT_ESTABLISHED
    assert "PROVISIONAL B2" in row[c("Flag")]
    ok = d.build_record_row(_f(3, entity_type="2", submitted_npi="1234567893",
                               entity_type_basis=d.ENTITY_TYPE_BASIS_OK))
    assert ok[c("Entity_Type (NPPES Entity Type Code)")] == "2"


def test_supplemental_npi_never_fills_submitted_blank():
    row = d.build_record_row(_f(1, submitted_npi="", supplemental_npi="1234567893"))
    c = d.RECORD_COLUMNS.index
    assert row[c("Submitted NPI (original, verbatim)")] == ""
    assert row[c("Supplemental NPI (manual research - NOT an accepted correction)")] == "1234567893"


def test_retired_blanket_pecos_wording_is_refused():
    with pytest.raises(d.DatasetRefused):
        d.build_record_row(_f(1, pecos="lost-identifier/outage family"))


def test_required_columns_present():
    for col in ("QHIN Number", "AGT_Record_ID", "Status", "Issue Type",
                "Issue Severity", "Recommendation", "Report Clarifications",
                "Evidence Reference", "QA Comments (verbatim)",
                "QA Comments - Izaan (verbatim)", "Submitted Address",
                "Proposed Corrected Address (AI-Assisted)", "NPPES",
                "PECOS/CMS Enrollment", "OIG LEIE", "SAM.gov Exclusions",
                "USPS Address Check"):
        assert col in d.RECORD_COLUMNS


def test_one_dataset_three_renderings_agree():
    import openpyxl
    ds = d.build_dataset(_facts(), report_id="R", findings_count=5)
    xl = openpyxl.load_workbook(io.BytesIO(d.to_xlsx(ds)))
    assert xl["Records"].max_row >= 7 + 1
    text = d.to_csv(ds).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))[2:]
    assert len(rows) == 7 and rows[0][1] == "agt-1"
    assert "B4 3" in text.splitlines()[0] and "3 HELD" not in text.splitlines()[0]
    page = d.to_html(ds)
    assert "agt-6" in page and "DRAFT" in page


def test_csv_neutralises_formula_cells():
    ds = d.build_dataset([_f(1, organization="=cmd()")], report_id="R")
    assert "'=cmd()" in d.to_csv(ds).decode("utf-8-sig")


def test_held_with_padded_status_still_counts_once_in_b4():
    ds = d.build_dataset([_f(1, None, status=" held ", held_reason="x")], report_id="R")
    assert ds["summary"]["buckets"]["B4"] == 1 and ds["summary"]["held_inside_b4"] == 1


def test_held_without_reason_is_surfaced_and_negative_findings_refused():
    ds = d.build_dataset([_f(1, None, status="HELD")], report_id="R")
    assert ds["summary"]["held_without_recorded_reason"] == 1
    with pytest.raises(d.DatasetRefused):
        d.build_dataset([_f(1)], report_id="R", findings_count=-1)
    with pytest.raises(d.DatasetRefused):
        d.build_record_row(_f(1, pecos="Lost-Identifier/Outage Family"))


# ── 2026-11-12: new columns, attribution, identity link, incomplete mode ─────

def _col(row, name):
    return row[d.RECORD_COLUMNS.index(name)]


def test_second_comment_column_is_named_for_its_author_and_old_name_is_gone():
    assert d.IZAAN_COMMENTS_COLUMN == "QA Comments - Izaan (verbatim)"
    assert "QA Comments 2 (separate column)" not in d.RECORD_COLUMNS
    assert d.RECORD_COLUMNS.index(d.ORIGINAL_COMMENTS_COLUMN) + 1 == \
        d.RECORD_COLUMNS.index(d.IZAAN_COMMENTS_COLUMN)


def test_izaan_text_without_an_attributable_source_is_refused():
    with pytest.raises(d.DatasetRefused, match="attributable source"):
        d.build_record_row(_f(1, qa_comments_izaan="please check"))
    with pytest.raises(d.DatasetRefused, match="attributable source"):
        d.build_record_row(_f(1, qa_comments_izaan="x",
                              qa_comments_izaan_source="   "))
    # a dict naming only ONE locator is not an attributable source either
    with pytest.raises(d.DatasetRefused):
        d.build_record_row(_f(1, qa_comments_izaan="x",
                              qa_comments_izaan_source={"file": "a.xlsx"}))


def test_both_comment_sources_are_verbatim_separate_and_attributed():
    orig = "  Original: leading\nnewline,  double  space \u2014 \u00e9 =x  "
    izaan = "Izaan: \"quoted\", tab\there; trailing space "
    src = {"file": "izaan_comments.xlsx", "sha256": "ab" * 32,
           "sheet": "S1", "column": "F"}
    f = _f(1, qa_comments=orig, qa_comments_source="ONC workbook, sheet October Records",
           qa_comments_izaan=izaan, qa_comments_izaan_source=src)
    row = d.build_record_row(f)
    assert _col(row, d.ORIGINAL_COMMENTS_COLUMN) == orig        # not stripped
    assert _col(row, d.IZAAN_COMMENTS_COLUMN) == izaan          # not stripped
    assert orig not in _col(row, d.IZAAN_COMMENTS_COLUMN)
    assert izaan not in _col(row, d.ORIGINAL_COMMENTS_COLUMN)
    ds = d.build_dataset([f, _f(2)], report_id="R")
    cs = ds["summary"]["comment_sources"]
    assert len(cs["izaan"]) == 1 and "izaan_comments.xlsx" in cs["izaan"][0]
    assert cs["original"] == ["ONC workbook, sheet October Records"]
    other = [r for r in ds["sheets"]["Records"]["rows"] if r[1] == "agt-2"][0]
    assert _col(other, d.IZAAN_COMMENTS_COLUMN) == ""        # no comment invented


def test_comment_is_never_a_bucket_status_or_basis():
    f = _f(1, "B3", qa_comments="APPROVED - QA OK, release",
           qa_comments_izaan="B1", qa_comments_izaan_source="f.xlsx; sheet=S1")
    row = d.build_record_row(f)
    assert _col(row, "Bucket") == "B3"
    assert _col(row, "Status") == "ACTIVE"
    assert "APPROVED" not in _col(row, "Bucket Basis")


def test_new_columns_use_honest_record_grain_wording_when_absent():
    row = d.build_record_row(_f(1))
    for col in ("Issue Type", "Issue Severity", "Recommendation"):
        assert _col(row, col) == d.NOT_AT_RECORD_GRAIN
    assert _col(row, "Evidence Reference") == d.NOT_ESTABLISHED
    assert _col(row, "Report Clarifications") == ""
    given = d.build_record_row(_f(1, recommendation="Confirm NPI", report_clarifications="note",
                                  evidence_reference="record=agt-1; evidence=e1"))
    assert _col(given, "Recommendation") == "Confirm NPI"
    assert _col(given, "Report Clarifications") == "note"
    assert _col(given, "Evidence Reference") == "record=agt-1; evidence=e1"


@pytest.mark.parametrize("kw", [
    dict(entity_type="2", submitted_npi="", entity_type_basis=d.ENTITY_TYPE_BASIS_OK),  # blank submitted NPI
    dict(entity_type="2", submitted_npi="  ", entity_type_basis=d.ENTITY_TYPE_BASIS_OK),
    dict(entity_type="2", submitted_npi="1234567893", entity_type_basis="NPPES"),       # not an established link
    dict(entity_type="2", submitted_npi="1234567893"),                                  # no basis at all
    dict(entity_type="", submitted_npi="1234567893", entity_type_basis=d.ENTITY_TYPE_BASIS_OK),
])
def test_entity_type_requires_an_established_submitted_npi_identity_link(kw):
    row = d.build_record_row(_f(1, **kw))
    assert _col(row, "Entity_Type (NPPES Entity Type Code)") == d.ENTITY_TYPE_NOT_ESTABLISHED


def test_bucket_basis_keeps_persisted_decision_and_annotation_distinguishable():
    persisted = d.build_record_row(_f(1, "B2", bucket_basis="Persisted classification: rule RULE-003 v1; review REV-1"))
    assert _col(persisted, "Bucket Basis").startswith("Persisted classification")
    assert "Reporting annotation" not in _col(persisted, "Bucket Basis")
    annotated = d.build_record_row(_f(1, "B2", bucket_basis="Persisted classification: rule RULE-003 v1",
                                      provisional=True,
                                      bucket_annotation="provisional B2 (Q1 identity admissibility pending)"))
    b = _col(annotated, "Bucket Basis")
    assert b.startswith("Persisted classification") and "; Reporting annotation: provisional B2" in b
    held = d.build_record_row(_f(1, None, status="HELD", held_reason="blocking identity issue"))
    hb = _col(held, "Bucket Basis")
    assert hb.startswith("Reporting annotation: HELD counted once in B4")
    assert "no persisted classification" in hb and "Persisted classification:" not in hb


def test_incomplete_dataset_lists_affected_ids_and_assigns_no_bucket():
    facts = [_f(1, "B1"), _f(2, None), _f(3, None, status="REJECTED"),
             _f(4, None, status="HELD", held_reason="r"), _f(5, "B3")]
    ds = d.build_dataset(facts, report_id="R")
    s = ds["summary"]
    assert s["complete"] is False
    assert s["unclassified_non_held_count"] == 2
    assert s["unclassified_non_held_ids"] == ["agt-2", "agt-3"]      # HELD is NOT unclassified
    assert s["buckets"] == {"B1": 1, "B2": 0, "B3": 1, "B4": 1}      # 1 HELD in B4
    assert sum(s["buckets"].values()) + 2 == s["unique_records"] == 5
    rows = {r[1]: r for r in ds["sheets"]["Records"]["rows"]}
    assert _col(rows["agt-2"], "Bucket") == d.NOT_CLASSIFIED
    assert _col(rows["agt-3"], "Bucket") == d.NOT_CLASSIFIED
    assert _col(rows["agt-3"], "Status") == "REJECTED"
    assert _col(rows["agt-4"], "Bucket") == "B4"
    assert "Unclassified" in ds["sheet_order"]
    assert ds["sheets"]["Unclassified"]["rows"] == [
        ["agt-2", "2", "ACTIVE"], ["agt-3", "3", "REJECTED"]]
    assert "INCOMPLETE" in ds["banner"] and "2 record(s)" in ds["banner"]


def test_complete_dataset_has_no_unclassified_sheet_and_says_complete():
    ds = d.build_dataset(_facts(), report_id="R")
    assert ds["summary"]["complete"] is True
    assert ds["summary"]["unclassified_non_held_ids"] == []
    assert "Unclassified" not in ds["sheet_order"] and "INCOMPLETE" not in ds["banner"]


def test_excel_csv_and_html_reconcile_cell_for_cell_including_incomplete_banner():
    import openpyxl
    facts = [_f(1, "B1", qa_comments="orig one",
                qa_comments_izaan="iz one", qa_comments_izaan_source="f.xlsx; sheet=S"),
             _f(2, None), _f(3, None, status="HELD", held_reason="blk"),
             _f(4, "B2", provisional=True, recommendation="Check", evidence_reference="rec=a4")]
    ds = d.build_dataset(facts, report_id="R", findings_count=7)
    want = ds["sheets"]["Records"]["rows"]
    # Excel: every cell equals the dataset (identifiers stay text)
    wb = openpyxl.load_workbook(io.BytesIO(d.to_xlsx(ds)))
    assert "Unclassified" in wb.sheetnames
    ws = wb["Records"]
    header_row = next(i for i, r in enumerate(ws.iter_rows(values_only=True), 1)
                      if r and r[0] == "Source_Line")
    got = [["" if v is None else str(v) for v in r]
           for r in ws.iter_rows(min_row=header_row + 1, values_only=True)]
    assert got == want
    # CSV: first line is the banner (with INCOMPLETE), then headers, then rows
    text = d.to_csv(ds).decode("utf-8-sig")
    lines = list(csv.reader(io.StringIO(text)))
    assert "INCOMPLETE" in lines[0][0] and "Field findings counted separately: 7" in lines[0][0]
    assert lines[1] == d.RECORD_COLUMNS
    assert lines[2:] == want
    # PDF source HTML: banner + every record id
    page = d.to_html(ds)
    assert "DATASET INCOMPLETE" in page
    for r in want:
        assert r[1] in page
    # the three renderings agree on counts
    assert len(lines[2:]) == len(got) == len(want) == 4


def test_findings_not_included_is_stated_never_shown_as_zero():
    ds = d.build_dataset(_facts(), report_id="R", findings_count=None)
    assert ds["summary"]["field_findings"] is None
    rows = dict(ds["sheets"]["Summary"]["rows"])
    assert rows["Field findings (separate; not in bucket totals)"] == d.FINDINGS_NOT_INCLUDED
    first = d.to_csv(ds).decode("utf-8-sig").splitlines()[0]
    assert "not included in this export" in first and "counted separately: 0" not in first
    zero = d.build_dataset(_facts(), report_id="R", findings_count=0)
    assert zero["summary"]["field_findings"] == 0      # a real zero is still a zero
