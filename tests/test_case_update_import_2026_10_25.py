"""Case Management (TEFCA ARC) — analyst/case-update import: stage + preview only (2026-10-25).

Pure tests (no DB, no network) for spreadsheet safety, identity resolution and the
four-way disposition that must reconcile to the rows read; plus the `/cases` window
arithmetic through the real route module.
"""
from __future__ import annotations

import io

import pytest
import uuid

from app.tefca_registry.rce import case_update_import as cui
from rce_traceability_support import rolled_back_db  # noqa: F401


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


async def test_build_preview_reports_idempotency_key_and_apply_availability():
    data = csv_bytes(["review_id", "analyst_note"], [["REV-2026-000001", "a"], ["REV-2026-000001", "a"]])
    res = await cui.build_preview("u.csv", data, purpose="case_updates", worksheet=None,
                                  resolve=fake_resolve, uploaded_by="analyst@example.invalid")
    # one accepted note-only row: nothing assignable -> apply not available, note staged
    assert res["apply"]["available"] is False
    assert res["apply"]["staged_not_applied"] == 1 and "staged" in res["apply"]["reason"]
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

def test_cases_routes_are_registered():
    from app.tefca_registry import review_routes as rr
    paths = {r.path: r for r in rr.router.routes}
    assert "/api/tefca/arc/cases" in paths
    assert "/api/tefca/arc/cases/import/preview" in paths
    assert "/api/tefca/arc/cases/import/apply" in paths  # ownership-only apply, exactly once per key


# ── apply plan + durable idempotency (2026-10-25) ────────────────────────────

PREVIEW = {
    "rows_read": 5, "reconciles": True,
    "rows": [
        {"row": 1, "disposition": "accepted", "matched_review_ids": ["REV-2026-000001"], "changes": {"assign_to_email": "sup@example.invalid"}, "reasons": []},
        {"row": 2, "disposition": "accepted", "matched_review_ids": ["REV-2026-000002"], "changes": {"analyst_note": "n"}, "reasons": []},
        {"row": 3, "disposition": "accepted", "matched_review_ids": ["REV-2026-000003"], "changes": {"assign_to_email": "sup@example.invalid", "proposed_correction_field": "address"}, "reasons": []},
        {"row": 4, "disposition": "needs_review", "matched_review_ids": ["REV-2026-000001"], "changes": {"requested_state": "Released"}, "reasons": ["workflow value requested"]},
        {"row": 5, "disposition": "rejected", "matched_review_ids": [], "changes": {}, "reasons": ["no existing case matches this identity"]},
    ],
}


def test_plan_apply_partitions_rows_and_reconciles():
    plan = cui.plan_apply(PREVIEW)
    assert [a["row"] for a in plan["assignments"]] == [1, 3]
    assert [x["row"] for x in plan["staged_not_applied"]] == [2, 3]          # row 3 is both: assign applied, correction staged
    assert [x["row"] for x in plan["not_applicable"]] == [4, 5]
    assert plan["reconciles"] is True
    assert all("case_followup_events" in x["reason"] for x in plan["staged_not_applied"])


def test_describe_apply_states_what_is_and_is_not_applied():
    d = cui.describe_apply(PREVIEW)
    assert d["available"] is True and d["assignments_ready"] == 2 and d["staged_not_applied"] == 2
    assert "requested_state" in d["never_applied"]
    assert "case_followup_events" in d["unresolved_business_choice"]
    none = cui.describe_apply({"rows_read": 1, "rows": [{"row": 1, "disposition": "unchanged", "matched_review_ids": ["REV-2026-000001"], "changes": {}, "reasons": []}]})
    assert none["available"] is False and "nothing is applicable" in none["reason"]


class TestApplyIdempotency:
    """Durable, database-backed: a second apply of the same key (retry or concurrent duplicate)
    applies nothing and reports the first outcome; the assignment service is the one that writes."""

    async def test_second_apply_of_same_key_is_refused_and_changes_nothing(self, rolled_back_db, monkeypatch):
        from types import SimpleNamespace
        from sqlalchemy import select
        from app.tefca_registry import case_assignment as ca
        from app.tefca_registry import models as reg
        calls = []

        async def fake_assign(db, review_id, *, user, to_user_id, reason=None, override_reason=None, ip_address=None):
            calls.append((review_id, str(to_user_id), reason))
            return {"review_id": review_id, "previous_owner": None, "state": "CLAIMED"}
        monkeypatch.setattr(ca, "assign", fake_assign)

        async def resolve_user_id(email):
            return uuid.UUID("11111111-1111-1111-1111-111111111111") if email == "sup@example.invalid" else None

        actor = SimpleNamespace(id=uuid.uuid4(), email="senior@example.invalid", role="senior_analyst")
        key = "k" * 64
        first = await cui.apply_case_updates(rolled_back_db, preview=PREVIEW, actor_user=actor, idempotency_key=key,
                                             resolve_user_id=resolve_user_id)
        assert first["already_applied"] is False
        assert first["counts"] == {"rows_read": 5, "applied": 2, "refused": 0, "staged_not_applied": 2, "not_applicable": 2, "reconciles": True}
        assert len(calls) == 2 and all("case-update import" in c[2] for c in calls)

        second = await cui.apply_case_updates(rolled_back_db, preview=PREVIEW, actor_user=actor, idempotency_key=key,
                                              resolve_user_id=resolve_user_id)
        assert second["already_applied"] is True and second["original"]["applied"] == 2
        assert len(calls) == 2, "a retry must not call the assignment service again"
        audits = (await rolled_back_db.execute(select(reg.TefcaRegAuditLog).where(
            reg.TefcaRegAuditLog.action == cui.AUDIT_APPLIED_ACTION,
            reg.TefcaRegAuditLog.metadata_["idempotency_key"].astext == key))).scalars().all()
        assert len(audits) == 1
        assert audits[0].metadata_["staged_not_applied"][0]["content"] == {"analyst_note": "n"}

    async def test_unknown_assignee_and_service_refusal_are_reported_not_applied(self, rolled_back_db, monkeypatch):
        from types import SimpleNamespace
        from app.tefca_registry import case_assignment as ca

        async def refusing_assign(db, review_id, *, user, to_user_id, **kw):
            raise ca.AssignmentRefused(f"{review_id} is APPROVED; reassigning it would imply the settled determination is back in play")
        monkeypatch.setattr(ca, "assign", refusing_assign)

        async def resolve_user_id(email):
            return None if email.startswith("nobody") else uuid.uuid4()

        preview = {"rows_read": 2, "rows": [
            {"row": 1, "disposition": "accepted", "matched_review_ids": ["REV-2026-000009"], "changes": {"assign_to_email": "nobody@example.invalid"}, "reasons": []},
            {"row": 2, "disposition": "accepted", "matched_review_ids": ["REV-2026-000010"], "changes": {"assign_to_email": "sup@example.invalid"}, "reasons": []},
        ]}
        actor = SimpleNamespace(id=uuid.uuid4(), email="senior@example.invalid", role="senior_analyst")
        out = await cui.apply_case_updates(rolled_back_db, preview=preview, actor_user=actor, idempotency_key="r" * 64,
                                           resolve_user_id=resolve_user_id)
        assert out["counts"]["applied"] == 0 and out["counts"]["refused"] == 2 and out["counts"]["reconciles"] is True
        assert "active account" in out["refused"][0]["reason"] and "APPROVED" in out["refused"][1]["reason"]
