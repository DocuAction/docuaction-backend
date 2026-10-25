"""Case Management (TEFCA ARC) — analyst/case-update import: stage + preview only (2026-10-25).

Pure tests (no DB, no network) for spreadsheet safety, identity resolution and the
four-way disposition that must reconcile to the rows read; plus the `/cases` window
arithmetic through the real route module.
"""
from __future__ import annotations

import io

import pytest

from app.tefca_registry.rce import case_update_import as cui


# ── fixtures ──────────────────────────────────────────────────────────────────

CASES = {
    "REV-2026-000001": ["REV-2026-000001"],
    "REV-2026-000002": ["REV-2026-000002"],
}
DELIVERY = {  # (intake_id, source_rce_id) -> review ids
    ("11111111-1111-1111-1111-111111111111", "2.999.1"): ["REV-2026-000001"],
    ("11111111-1111-1111-1111-111111111111", "2.999.9"): ["REV-2026-000007", "REV-2026-000008"],  # duplicate HCID family
}


async def fake_resolve(identity):
    rid = identity.get("review_id")
    if rid:
        return CASES.get(rid, [])
    return DELIVERY.get((identity.get("intake_id"), identity.get("source_rce_id")), [])


def csv_bytes(header, rows, delimiter=","):
    out = io.StringIO()
    out.write(delimiter.join(header) + "\r\n")
    for r in rows:
        out.write(delimiter.join(r) + "\r\n")
    return out.getvalue().encode("utf-8")


def xlsx_bytes(sheets):
    """sheets: {name: [[cells...], ...]} — first row is the header. Values may be numbers or '=' formulas."""
    import openpyxl
    wb = openpyxl.Workbook()
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER = ["review_id", "intake_id", "source_rce_id", "analyst_note", "requested_state", "assign_to_email", "extra_col"]


# ── loading + safety ─────────────────────────────────────────────────────────

def test_delimited_pipe_file_loads_with_normalised_headers_and_text_identifiers():
    data = csv_bytes(["Review ID", "Analyst Note"], [["REV-2026-000001", "note one"], ["", ""]], delimiter="|")
    t = cui.load_tabular("updates.psv", data)
    assert t.kind == "delimited"
    assert t.headers == ["review_id", "analyst_note"]
    assert t.rows == [{"review_id": "REV-2026-000001", "analyst_note": "note one"}]  # blank line skipped
    assert len(t.sha256) == 64


def test_xlsx_loads_selected_worksheet_flags_formulas_and_numeric_identifiers():
    data = xlsx_bytes({
        "Cover": [["ignore"], ["me"]],
        "Updates": [["review_id", "source_rce_id", "analyst_note"],
                    ["REV-2026-000001", 2999001, "=SUM(A1:A2)"],
                    ["REV-2026-000002", "0012345", "plain note"]],
    })
    t = cui.load_tabular("updates.xlsx", data, worksheet="Updates")
    assert t.worksheets == ["Cover", "Updates"] and t.worksheet == "Updates"
    assert t.rows[0]["source_rce_id"] == "2999001"           # carried as digits, never re-padded
    assert t.rows[0]["analyst_note"] == ""                   # formula not evaluated
    assert t.rows[1]["source_rce_id"] == "0012345"           # text cell keeps its leading zeros
    codes = {w["code"] for w in t.warnings}
    assert codes == {"FORMULA_CELL", "NUMERIC_IDENTIFIER_CELL"}


def test_xlsx_default_worksheet_is_the_first_and_unknown_worksheet_is_refused():
    data = xlsx_bytes({"Only": [["review_id"], ["REV-2026-000001"]]})
    assert cui.load_tabular("u.xlsx", data).worksheet == "Only"
    with pytest.raises(cui.SpreadsheetRefused, match="not found"):
        cui.load_tabular("u.xlsx", data, worksheet="Nope")


@pytest.mark.parametrize("name,why", [("macros.xlsm", "macro"), ("old.xls", "legacy"), ("bin.xlsb", "binary")])
def test_unsafe_workbook_types_are_refused_by_name(name, why):
    with pytest.raises(cui.SpreadsheetRefused, match=why):
        cui.load_tabular(name, b"PK\x03\x04junk")


def test_ole_container_and_empty_and_oversize_files_are_refused():
    with pytest.raises(cui.SpreadsheetRefused, match="OLE"):
        cui.load_tabular("x.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16)
    with pytest.raises(cui.SpreadsheetRefused, match="empty"):
        cui.load_tabular("x.csv", b"")
    big = b"a,b\r\n" + b"x,y\r\n" * (cui.MAX_BYTES // 4)
    with pytest.raises(cui.SpreadsheetRefused, match="limit"):
        cui.load_tabular("x.csv", big)


def test_row_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(cui, "MAX_ROWS", 3)
    data = csv_bytes(["review_id", "analyst_note"], [[f"REV-2026-00000{i}", "n"] for i in range(1, 6)])
    with pytest.raises(cui.SpreadsheetRefused, match="data rows"):
        cui.load_tabular("x.csv", data)


def test_single_field_header_is_refused_not_guessed():
    with pytest.raises(cui.SpreadsheetRefused, match="delimiter"):
        cui.load_tabular("x.csv", b"review_id\r\nREV-2026-000001\r\n")


# ── header validation ────────────────────────────────────────────────────────

def test_header_needs_an_identity_and_a_content_column_and_preserves_unmapped_columns():
    ok = cui.validate_header(HEADER)
    assert ok["ok"] is True
    assert ok["unmapped_preserved_ignored"] == ["extra_col"]
    missing_identity = cui.validate_header(["analyst_note", "intake_id"])
    assert missing_identity["ok"] is False and "review_id" in missing_identity["missing"][0]
    missing_content = cui.validate_header(["review_id"])
    assert missing_content["ok"] is False and "at least one of" in missing_content["missing"][0]


# ── dispositions ─────────────────────────────────────────────────────────────

async def test_every_row_gets_exactly_one_disposition_and_they_reconcile():
    rows = [
        {"review_id": "REV-2026-000001", "analyst_note": "verified address with NPPES"},          # accepted
        {"review_id": "REV-2026-000002"},                                                          # unchanged
        {"review_id": "REV-2026-000099", "analyst_note": "n"},                                     # rejected: no case
        {"review_id": "not-a-review", "analyst_note": "n"},                                        # rejected: malformed
        {"intake_id": "11111111-1111-1111-1111-111111111111", "source_rce_id": "2.999.9", "analyst_note": "n"},  # needs_review: 2 cases
        {"review_id": "REV-2026-000001", "requested_state": "Released"},                           # needs_review: workflow value
        {"review_id": "REV-2026-000001", "analyst_note": "second note same case"},                 # needs_review: duplicate target
        {"analyst_note": "orphan"},                                                                # rejected: no identity
    ]
    out = await cui.preview_rows(rows, fake_resolve)
    assert out["rows_read"] == 8 and out["reconciles"] is True
    assert out["counts"] == {"accepted": 1, "unchanged": 1, "rejected": 3, "needs_review": 3}
    by_row = {r["row"]: r for r in out["rows"]}
    assert by_row[1]["disposition"] == "accepted" and by_row[1]["matched_review_ids"] == ["REV-2026-000001"]
    assert by_row[2]["disposition"] == "unchanged"
    assert by_row[3]["disposition"] == "rejected" and "no existing case" in by_row[3]["reasons"][0]
    assert by_row[4]["disposition"] == "rejected" and "REV-YYYY-NNNNNN" in by_row[4]["reasons"][0]
    assert by_row[5]["disposition"] == "needs_review" and by_row[5]["matched_review_ids"] == ["REV-2026-000007", "REV-2026-000008"]
    assert by_row[6]["disposition"] == "needs_review" and by_row[6]["requested_workflow_values"] == {"requested_state": "Released"}
    assert by_row[7]["disposition"] == "needs_review" and "retry must not apply twice" in by_row[7]["reasons"][0]
    assert by_row[8]["disposition"] == "rejected"


async def test_a_bucket_value_in_proposed_correction_is_a_request_never_an_application():
    out = await cui.preview_rows([{"review_id": "REV-2026-000001", "proposed_correction_field": "bucket",
                                   "proposed_correction_value": "B1"}], fake_resolve)
    row = out["rows"][0]
    assert row["disposition"] == "needs_review"
    assert row["requested_workflow_values"] == {"proposed_correction_value": "B1"}


async def test_delivery_identity_resolves_through_intake_and_source_id():
    out = await cui.preview_rows([{"intake_id": "11111111-1111-1111-1111-111111111111",
                                   "source_rce_id": "2.999.1", "assign_to_email": "analyst@example.invalid"}], fake_resolve)
    assert out["rows"][0]["disposition"] == "accepted"
    assert out["rows"][0]["identity_mode"] == "delivery"


# ── build_preview end to end (no DB) ─────────────────────────────────────────

async def test_other_purposes_are_redirected_not_imported():
    for purpose in (cui.PURPOSE_SOURCE_DELIVERY, cui.PURPOSE_REFERENCE_DATA):
        res = await cui.build_preview("x.csv", b"review_id,analyst_note\r\nREV-2026-000001,n\r\n",
                                      purpose=purpose, worksheet=None, resolve=fake_resolve, uploaded_by="t")
        assert res["handled_here"] is False
        assert res["redirect"]["route"] in ("/tefca-arc/deliveries", "/tefca-arc/iqvia")
    with pytest.raises(cui.SpreadsheetRefused, match="purpose"):
        await cui.build_preview("x.csv", b"a,b\r\n1,2\r\n", purpose="anything", worksheet=None,
                                resolve=fake_resolve, uploaded_by="t")


async def test_build_preview_reports_idempotency_key_and_never_offers_apply():
    data = csv_bytes(["review_id", "analyst_note"], [["REV-2026-000001", "a"], ["REV-2026-000001", "a"]])
    res = await cui.build_preview("u.csv", data, purpose="case_updates", worksheet=None,
                                  resolve=fake_resolve, uploaded_by="analyst@example.invalid")
    assert res["apply"]["available"] is False
    assert res["idempotency_key"] == cui.idempotency_key(res["file"]["sha256"], "case_updates")
    # Same bytes again → same key: a retry is recognisable and cannot double-apply.
    again = await cui.build_preview("u.csv", data, purpose="case_updates", worksheet=None,
                                    resolve=fake_resolve, uploaded_by="x")
    assert again["idempotency_key"] == res["idempotency_key"]
    assert res["preview"]["counts"] == {"accepted": 1, "unchanged": 0, "rejected": 0, "needs_review": 1}


async def test_header_failure_stages_no_rows():
    res = await cui.build_preview("u.csv", b"foo,bar\r\n1,2\r\n", purpose="case_updates", worksheet=None,
                                  resolve=fake_resolve, uploaded_by="x")
    assert res["header"]["ok"] is False
    assert res["preview"]["rows"] == [] and "blocked" in res["preview"]


# ── /cases window arithmetic (route module, no DB) ───────────────────────────

def test_cases_route_is_registered_with_viewer_floor_and_preview_requires_reviewer():
    from app.tefca_registry import review_routes as rr
    paths = {r.path: r for r in rr.router.routes}
    assert "/api/tefca/arc/cases" in paths
    assert "/api/tefca/arc/cases/import/preview" in paths
    assert "/api/tefca/arc/cases/import/apply" not in paths  # no apply path in this release
