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


def test_unclassified_non_held_record_is_refused_not_guessed():
    with pytest.raises(d.DatasetRefused):
        d.build_dataset([_f(1, None)], report_id="R")


def test_usps_default_entity_type_and_provisional_wording():
    row = d.build_record_row(_f(3, "B2", provisional=True, entity_type="1"))
    c = d.RECORD_COLUMNS.index
    assert row[c("USPS Address Check")] == "Not checked—unavailable"
    assert row[c("Entity_Type (NPPES Entity Type Code)")] == d.ENTITY_TYPE_NOT_ESTABLISHED
    assert "PROVISIONAL B2" in row[c("Flag")]
    ok = d.build_record_row(_f(3, entity_type="2", entity_type_basis="NPPES"))
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
                "Issue Severity", "QA Comments (verbatim)",
                "QA Comments 2 (separate column)", "Submitted Address",
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
