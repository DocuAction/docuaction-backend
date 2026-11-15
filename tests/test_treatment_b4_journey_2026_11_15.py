"""Synthetic journey: Treatment B4 candidate -> analyst determination -> SEPARATE QA test action ->
Stephen's record-level report and 18-column field export (rolled-back transaction, synthetic rows).

The QA step proves the WORKFLOW only; it is a test action by a distinct synthetic actor and is NOT
independent QA acceptance. Original classifications and submitted NPIs are preserved throughout.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.reports.data import field_level_findings as flf
from app.reports.data import october_review_dataset as ds
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from rce_traceability_support import SYN, make_rows, rolled_back_db, seed_entity, seed_intake  # noqa: F401
from scripts import generate_record_review_package as pkg

pytestmark = pytest.mark.regression
T_DONE = datetime(2026, 11, 8, 9, 0, 0)
ANALYST, QA = uuid.uuid4(), uuid.uuid4()


class _Ctx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_a):
        return False


def _issue(intake_id, rec, itype, field, run_id, n):
    return m.RceIssue(
        id=uuid.uuid4(), issue_code=f"DQ-20261108-{uuid.uuid4().int % 10**6:06d}", source_intake_id=intake_id,
        source_record_id=rec.id, run_id=run_id, rule_id="BR-NPI-TRTMNT-001", rule_version="1.6.0",
        issue_type=itype, severity="HIGH", field_name=field, original_value="", correction_authority="HUMAN_REQUIRED",
        description=f"synthetic {itype} finding {n}", created_at=datetime(2026, 11, 8, 8, 0, 0))


async def _event(db, review_id, seq, kind, at, **kw):
    qa = kind == "QA_REVIEW"
    db.add(reg.ReviewDecisionEvent(
        id=uuid.uuid4(), review_id=review_id, sequence_number=seq, event_type=kind,
        actor_user_id=QA if qa else ANALYST, actor_email=("qa" if qa else "analyst") + "@synthetic-test.docuaction.invalid",
        actor_role="qalead" if qa else "reviewer", occurred_at=at, rationale="synthetic rationale for the journey",
        qa_reason="synthetic qa reason" if qa else None, **kw))
    await db.flush()


PRESERVED_TABLES = ("review_records", "review_decision_events", "rce_issues", "rce_curated_records",
                    "rce_source_records", "tefca_dimension_evidence")


async def _fingerprint(db, intake_id):
    """Row count + md5 of every row's text, for the tables the report reads. Report generation is
    read-only: the fingerprint must not change."""
    out = {}
    for tbl in PRESERVED_TABLES:
        out[tbl] = (await db.execute(text(
            f"select count(*), coalesce(md5(string_agg(t::text, '|' order by t::text)), '') from {tbl} t"))).one()
    return out


async def test_treatment_b4_journey_to_stephens_exports(rolled_back_db, monkeypatch, tmp_path):
    db = rolled_back_db
    rows = make_rows(3, arc="9.99.779.70", purposesofuse="T-TRTMNT,T-PH")        # combined purposes, active, Participant
    rows[2]["purposesofuse"] = "T-PYMNT"
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    run = m.RceIngestionRun(
        id=uuid.uuid4(), source_intake_id=intake_id, rule_set_version="t", rule_config_hash="0" * 64,
        started_at=datetime(2026, 11, 7), completed_at=T_DONE, run_status="COMPLETE",
        assessment_metadata={"assessment_date": "2026-11-08", "source": "pinned_param"})
    db.add(run)
    await db.flush()
    recs = {r.line_number - 1: r for r in (await db.execute(
        select(m.RceSourceRecord).where(m.RceSourceRecord.source_intake_id == intake_id))).scalars().all()}
    ents = {}
    for i, bucket, rule in ((1, "B4", "RULE-006"), (2, "B3", "RULE-003"), (3, "B1", "RULE-001")):
        ents[i] = await seed_entity(db, oid=f"{SYN}-J-{i}-{uuid.uuid4().hex[:8]}", name=f"{SYN} journey {i}")
        db.add(m.RceCuratedRecord(id=uuid.uuid4(), source_intake_id=intake_id, source_record_id=recs[i].id,
                                  record_status="CLEAN", rce_org_oid=recs[i].source_rce_id, transformation_version="t",
                                  canonical_entity_id=ents[i]))
        db.add(reg.ReviewRecord(id=uuid.uuid4(), review_id=f"REV-9970-00000{i}", entity_id=ents[i], source_record_id=recs[i].id,
                                classification_bucket=bucket, classification_rule=rule, classification_rule_version=4,
                                classification_rationale=f"automated rationale {i}", created_at=datetime(2026, 11, 8, 10)))
    await db.flush()
    # Findings: record 1 NPI_REQUIRED (+ an out-of-scope NAIC finding), record 2 ZIP_LEADING_ZERO_STRIPPED.
    db.add_all([_issue(intake_id, recs[1], "NPI_REQUIRED", "NPI", run.id, 1),
                _issue(intake_id, recs[1], "NAIC_ON_NON_PAYER", "name", run.id, 2),
                _issue(intake_id, recs[2], "ZIP_LEADING_ZERO_STRIPPED", "address_postalCode", run.id, 3)])
    await db.flush()
    # Analyst determination on 1 (confirm B4), then a SEPARATE QA test action by a different actor.
    await _event(db, "REV-9970-000001", 1, "ANALYST_DETERMINATION", datetime(2026, 11, 8, 11), determination="CONFIRM")
    await _event(db, "REV-9970-000001", 2, "QA_REVIEW", datetime(2026, 11, 8, 12), qa_action="APPROVE")
    # Record 2: analyst reclassifies, QA returns: never reported as approved.
    await _event(db, "REV-9970-000002", 1, "ANALYST_DETERMINATION", datetime(2026, 11, 8, 11), determination="RECLASSIFY", determined_bucket="B2")
    await _event(db, "REV-9970-000002", 2, "QA_REVIEW", datetime(2026, 11, 8, 12), qa_action="RETURN")

    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx(db))
    before = await _fingerprint(db, intake_id)
    out = tmp_path / "journey"
    args = SimpleNamespace(intake=str(intake_id), cutoff="2099-01-01T00:00:00", out=str(out), run_id=None,
                           formats="xlsx,csv", baseline=None, classification="DRAFT")
    assert await pkg.build(args) == 0
    man = json.loads(next(out.glob("*.MANIFEST.json")).read_text(encoding="utf-8"))
    assert man["self_check_failures"] == [] and "INDEPENDENT QA PENDING" in man["status"]
    assert await _fingerprint(db, intake_id) == before, "generating the reports changed stored history"

    # ── record-level master (grain A) ───────────────────────────────────────
    csv_path = next(p for p in out.glob("*.csv") if "field-findings" not in p.name)
    lines = list(csv.reader(io.StringIO(csv_path.read_bytes().decode("utf-8-sig"))))
    cols, data = lines[1], lines[2:]
    by_line = {int(r[cols.index("Source_Line")]): r for r in data}
    C = lambda row, name: row[cols.index(name)]                                   # noqa: E731
    first, second, third = by_line[2], by_line[3], by_line[4]
    assert (C(first, "Bucket"), C(first, "Submitted NPI (original, verbatim)")) == ("B4", ""), "original class and blank NPI preserved"
    assert C(first, "Analyst/QA Status") == "Approved by QA" and C(first, "Reviewed Disposition (QA-approved only)") == "Confirmed B4"
    assert "automated rationale 1" in C(first, "Reasons")
    assert C(second, "Bucket") == "B3" and C(second, "Reviewed Disposition (QA-approved only)") == ""
    assert C(second, "Analyst/QA Status") == "Returned by QA; not approved"
    assert C(third, "Analyst/QA Status") == "No analyst determination recorded" and C(third, "Bucket") == "B1"
    assert len(data) == 3

    # ── field-level export (grain B): exactly the 18 headers, reconciled separately ─
    fcsv = next(out.glob("*-field-findings.csv"))
    flines = list(csv.reader(io.StringIO(fcsv.read_bytes().decode("utf-8-sig"))))
    assert flines[0] == flf.FIELD_HEADERS and len(flines[0]) == 18
    F = lambda row, name: row[flf.FIELD_HEADERS.index(name)]                      # noqa: E731
    frows = flines[1:]
    assert len(frows) == 2, "NAIC_ON_NON_PAYER is excluded; nothing is manufactured for the passing record"
    npi = next(r for r in frows if F(r, "Field_ID") == "NPI")
    assert (F(npi, "Issue_Type"), F(npi, "Entity_Type"), F(npi, "NPI"), F(npi, "Purpose_of_Use")) == (
        "MISSING_ENTRY", "Participant", "", "T-TRTMNT,T-PH")
    assert F(npi, "Field_Discrepancy_Class") == "" and F(npi, "Reference_Source_Date") == "2026-11-08"
    zip_row = next(r for r in frows if F(r, "Field_ID") == "address_postalCode")
    assert (F(zip_row, "Issue_Type"), F(zip_row, "B2_Subclassifications")) == ("B2_OTHER", "OTHER")
    rc = man["field_findings"]["reconciliation"]
    assert (rc["records"], rc["rows"], rc["excluded_naic"], rc["records_with_findings"], rc["records_without_findings"]) == (3, 2, 1, 2, 1)
    # Both artifacts are hashed and the xlsx reopens with the same 18 text headers.
    from openpyxl import load_workbook
    ws = load_workbook(next(out.glob("*-field-findings.xlsx"))).active
    assert [c.value for c in ws[1]] == flf.FIELD_HEADERS and ws.max_row == 3
    assert {f["format"] for f in man["files"]} >= {"csv", "xlsx", "field-findings-csv", "field-findings-xlsx"}
