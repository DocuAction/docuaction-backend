"""An independently QA-approved reclassification is the record's REPORTING bucket.

Owner authorization 2026-11-16 (synthetic testing only; not QA acceptance, not a
release authorization). Methodology revision 1.3-A (DRAFT). Synthetic rows only;
every database test runs inside a rolled-back transaction.

What is demonstrated
  * approved reclassification of a B4 record to B1/B2/B3 -> final bucket and
    totals; automated bucket, basis, decision events and status are preserved
    and nothing is written; an approved move the methodology does not describe
    (from a non-B4 bucket, or into B4) is stated and NOT applied;
  * pending, returned, superseded and after-cutoff decisions never move a bucket;
  * a HELD record stays HELD, in B4, counted once, whatever was approved;
  * a researched NPI never assigns B1, never fills the submitted blank and never
    removes the missing-NPI field finding;
  * automated totals and reporting totals are the same records and the same total;
  * record CSV, summary and the separate 18-column field export come from one
    dataset and one cutoff.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.reports.data import field_level_findings as flf
from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import october_review_loader as ld
from app.reports.data import reviewed_disposition as rd
from app.tefca_registry import models as reg
from app.tefca_registry import qa_gate
from app.tefca_registry.rce import models as m
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401
from scripts import generate_record_review_package as pkg
from test_october_review_loader_db_2026_11_12 import (  # noqa: F401
    CUTOFF, T_OLD, _curate, _entity, _records, _review, _run,
)
from test_supplemental_npi_research_2026_11_15 import REVIEW_IDS, _enter, _seed, analyst, qa_user
from test_treatment_b4_journey_2026_11_15 import _Ctx, _fingerprint, _issue

pytestmark = pytest.mark.regression
ANALYST, QA = uuid.uuid4(), uuid.uuid4()
BEFORE = datetime(2026, 11, 8, 12, 0, 0)
LATER = datetime(2026, 11, 8, 15, 0, 0)
AFTER_CUTOFF = datetime(2026, 11, 10, 9, 0, 0)
COLS = ds_mod.RECORD_COLUMNS
AUTO = ds_mod.AUTOMATED_BUCKET_COLUMN


async def _event(db, review_id, seq, kind, at, *, supersedes=None, **kw):
    qa = kind == "QA_REVIEW"
    ev = reg.ReviewDecisionEvent(
        id=uuid.uuid4(), review_id=review_id, sequence_number=seq, event_type=kind,
        actor_user_id=QA if qa else ANALYST,
        actor_email=("qa" if qa else "analyst") + "@synthetic-test.docuaction.invalid",
        actor_role="qalead" if qa else "reviewer", occurred_at=at, rationale="synthetic rationale for the test",
        qa_reason="synthetic qa reason" if qa else None, supersedes_decision_id=supersedes,
        supersession_reason="synthetic supersession reason" if supersedes else None, **kw)
    db.add(ev)
    await db.flush()
    return ev


def _rows(dataset):
    return {int(r[COLS.index("Source_Line")]) - 1: r for r in dataset["sheets"]["Records"]["rows"]}


def _c(row, name):
    return row[COLS.index(name)]


async def _scenarios(db, arc):
    """Eleven synthetic records. Automated: 1-5 B4, 6 B1 (HELD), 7 B1, 8 B4, 9 B2, 10-11 B4."""
    intake_id, _ = await seed_intake(db, make_rows(11, arc=arc), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    auto = dict(AUTOMATED)
    r = lambda i: f"REV-{arc[-4:].replace('.', '')}-{i:06d}"                               # noqa: E731
    for i in range(1, 12):
        ent = await _entity(db, f"q{i}")
        await _curate(db, intake_id, recs[i], "HELD" if i == 6 else "CLEAN", entity_id=ent,
                      reason="blocking identity" if i == 6 else None)
        await _review(db, recs[i], ent, r(i), auto[i], T_OLD)
    D, Q, S = "ANALYST_DETERMINATION", "QA_REVIEW", "SUPERSEDING_DETERMINATION"
    # 1: approved reclassification B4 -> B1
    await _event(db, r(1), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B1")
    await _event(db, r(1), 2, Q, LATER, qa_action="APPROVE")
    # 2: pending QA
    await _event(db, r(2), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B1")
    # 3: rejected by QA (RETURN)
    await _event(db, r(3), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B2")
    await _event(db, r(3), 2, Q, LATER, qa_action="RETURN")
    # 4: approved, then superseded by a new determination that QA has not approved
    first = await _event(db, r(4), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B1")
    await _event(db, r(4), 2, Q, BEFORE.replace(hour=13), qa_action="APPROVE")
    await _event(db, r(4), 3, S, LATER, determination="RECLASSIFY", determined_bucket="B3", supersedes=first.id)
    # 5: approved only AFTER the cutoff
    await _event(db, r(5), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B1")
    await _event(db, r(5), 2, Q, AFTER_CUTOFF, qa_action="APPROVE")
    # 6: HELD, with an approved reclassification
    await _event(db, r(6), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B2")
    await _event(db, r(6), 2, Q, LATER, qa_action="APPROVE")
    # 7: approved CONFIRM
    await _event(db, r(7), 1, D, BEFORE, determination="CONFIRM")
    await _event(db, r(7), 2, Q, LATER, qa_action="APPROVE")
    # 8: approved B4 -> B3, superseded by B4 -> B2, which QA then approved: the later one governs
    old = await _event(db, r(8), 1, D, BEFORE.replace(hour=9), determination="RECLASSIFY", determined_bucket="B3")
    await _event(db, r(8), 2, Q, BEFORE.replace(hour=10), qa_action="APPROVE")
    await _event(db, r(8), 3, S, BEFORE, determination="RECLASSIFY", determined_bucket="B2", supersedes=old.id)
    await _event(db, r(8), 4, Q, LATER, qa_action="APPROVE")
    # 9: an approved move INTO B4 from B2 - not a move the methodology describes: stated, not applied
    await _event(db, r(9), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B4")
    await _event(db, r(9), 2, Q, LATER, qa_action="APPROVE")
    # 10: approved, then RETURNED by a later QA event: the latest QA event decides
    await _event(db, r(10), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B1")
    await _event(db, r(10), 2, Q, BEFORE.replace(hour=13), qa_action="APPROVE")
    await _event(db, r(10), 3, Q, LATER, qa_action="RETURN")
    # 11: escalated by QA
    await _event(db, r(11), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket="B1")
    await _event(db, r(11), 2, Q, LATER, qa_action="ESCALATE", escalated_to_user_id=uuid.uuid4(),
                 escalation_reason="synthetic escalation reason")
    return intake_id


AUTOMATED = {1: "B4", 2: "B4", 3: "B4", 4: "B4", 5: "B4", 6: "B1", 7: "B1", 8: "B4", 9: "B2", 10: "B4", 11: "B4"}


async def test_only_an_eligible_qa_approved_reclassification_moves_the_reporting_bucket(rolled_back_db):
    db = rolled_back_db
    intake_id = await _scenarios(db, "9.99.777.80")
    before = await _fingerprint(db, intake_id)
    ds = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="SYN-QA-BUCKET")
    assert await _fingerprint(db, intake_id) == before, "building the report changed stored history"
    rows, s = _rows(ds), ds["summary"]

    final = {i: _c(rows[i], "Bucket") for i in rows}
    assert final == {1: "B1", 2: "B4", 3: "B4", 4: "B4", 5: "B4", 6: "B4", 7: "B1", 8: "B2", 9: "B2", 10: "B4", 11: "B4"}
    # the original automated bucket is kept, separately, for every record
    assert {i: _c(rows[i], AUTO) for i in rows} == AUTOMATED
    for i in rows:
        assert "Persisted classification: rule RULE-001 v2" in _c(rows[i], "Bucket Basis") or i == 6
        assert "rationale for REV-" in _c(rows[i], "Reasons")
    # 1: applied, attributable and marked
    assert "Reporting annotation: QA-approved reclassification B4 -> B1" in _c(rows[1], "Bucket Basis")
    assert "in-app QA APPROVE #2 by a different recorded user" in _c(rows[1], "Bucket Basis")
    assert "RECLASSIFIED (QA-approved) B4 -> B1" in _c(rows[1], "Flag")
    assert _c(rows[1], "Reviewed Disposition (QA-approved only)") == "Reclassified B4 -> B1"
    # 2-5: nothing moved and nothing claimed
    assert _c(rows[2], "Analyst/QA Status") == rd.AWAITING_QA
    assert _c(rows[3], "Analyst/QA Status") == rd.QA_RETURNED
    assert _c(rows[4], "Analyst/QA Status") == rd.AWAITING_QA, "the superseded approval does not stand"
    assert _c(rows[5], "Analyst/QA Status") == rd.AWAITING_QA, "an approval after the cutoff is not visible"
    assert _c(rows[10], "Analyst/QA Status") == rd.QA_RETURNED, "a later RETURN ends the earlier approval"
    assert _c(rows[11], "Analyst/QA Status") == rd.QA_ESCALATED
    for i in (2, 3, 4, 5, 7, 10, 11):
        assert "RECLASSIFIED" not in _c(rows[i], "Flag") and "reclassification" not in _c(rows[i], "Bucket Basis")
    # 6: HELD stays HELD, B4, counted once; the approval is stated as NOT applied
    assert (_c(rows[6], "Status"), _c(rows[6], "Bucket")) == ("HELD", "B4")
    assert "recorded but NOT applied: the record remains HELD" in _c(rows[6], "Bucket Basis")
    assert "persisted classification B1 kept in its own column" in _c(rows[6], "Bucket Basis")
    assert "RECLASSIFIED" not in _c(rows[6], "Flag")
    # 8: the superseding, approved determination governs (B2, not the superseded B3)
    assert "RECLASSIFIED (QA-approved) B4 -> B2" in _c(rows[8], "Flag")
    # 9: approved, but not a B4 -> B1/B2/B3 move: stated and not applied
    assert "B2 -> B4 recorded but NOT applied: the methodology provides only" in _c(rows[9], "Bucket Basis")
    assert "RECLASSIFIED" not in _c(rows[9], "Flag")

    # totals: every record once; automated and reporting totals reconcile
    assert s["buckets"] == {"B1": 2, "B2": 2, "B3": 0, "B4": 7}
    assert s["automated_buckets"] == {"B1": 1, "B2": 1, "B3": 0, "B4": 9}
    assert sum(s["buckets"].values()) == sum(s["automated_buckets"].values()) == s["unique_records"] == 11
    assert s["held_inside_b4"] == 1 and s["unclassified_non_held_count"] == 0
    assert s["reclassification_transitions"] == {"B4 -> B1": 1, "B4 -> B2": 1} and s["qa_approved_reclassifications"] == 2
    ids = [r[COLS.index("AGT_Record_ID")] for r in ds["sheets"]["Records"]["rows"]]
    assert len(ids) == len(set(ids)) == 11, "no record lost or duplicated"

    # the CSV is the same dataset: its rows recount to the summary and its first line states the move
    lines = list(csv.reader(io.StringIO(ds_mod.to_csv(ds).decode("utf-8-sig"))))
    assert "B1 2, B2 2, B3 0, B4 7, Not classified 0 = 11 (1 HELD inside B4; 2 moved by QA-approved reclassification)" in lines[0][0]
    recount = {b: 0 for b in ds_mod.BUCKETS}
    for row in lines[2:]:
        recount[row[lines[1].index("Bucket")]] += 1
    assert recount == s["buckets"]
    summary = dict(ds["sheets"]["Summary"]["rows"])
    assert summary[ds_mod.RECLASSIFIED_SUMMARY_ROW] == "2" and summary["  moved B4 -> B1"] == "1"
    assert summary[ds_mod.AUTOMATED_SUMMARY_ROW] == "B1 1 / B2 1 / B3 0 / B4 9"
    page = ds_mod.to_html(ds)
    assert "kept in its own Excel/CSV column" in page and "RECLASSIFIED (QA-approved) B4 -&gt; B1" in page
    # the workbook is the same dataset too
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(ds_mod.to_xlsx(ds)))["Records"]
    grid = [[c.value for c in row] for row in sheet.iter_rows()]
    head = next(i for i, row in enumerate(grid) if "AGT_Record_ID" in row)
    xl = {row[grid[head].index("AGT_Record_ID")]: row for row in grid[head + 1:] if row[grid[head].index("AGT_Record_ID")]}
    one = xl[_c(rows[1], "AGT_Record_ID")]
    assert (one[grid[head].index("Bucket")], one[grid[head].index(AUTO)]) == ("B1", "B4") and len(xl) == 11

    # stored classification, status and history are exactly as they were
    stored = {int(r.review_id.split("-")[-1]): (r.classification_bucket, r.reclassified_to) for r in (
        await db.execute(select(reg.ReviewRecord).where(reg.ReviewRecord.review_id.like("REV-780-%")))).scalars().all()}
    assert stored == {i: (b, None) for i, b in AUTOMATED.items()}


async def test_the_same_delivery_at_a_later_cutoff_shows_the_later_approval_and_still_reconciles(rolled_back_db):
    db = rolled_back_db
    intake_id = await _scenarios(db, "9.99.777.81")
    early = (await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="SYN-A"))["summary"]
    late = (await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=datetime(2026, 11, 11), report_id="SYN-B"))["summary"]
    assert early["buckets"] == {"B1": 2, "B2": 2, "B3": 0, "B4": 7}
    assert late["buckets"] == {"B1": 3, "B2": 2, "B3": 0, "B4": 6}, "record 5's approval is visible at the later cutoff"
    assert early["automated_buckets"] == late["automated_buckets"]
    assert sum(late["buckets"].values()) == late["unique_records"] == 11


async def test_a_researched_npi_never_assigns_b1_and_the_missing_npi_finding_survives_an_approved_move(
        rolled_back_db, monkeypatch, tmp_path):
    db = rolled_back_db
    intake_id, recs = await _seed(db, "9.99.781.80")          # line 1 HELD, line 2 CLEAN automated B4; both NPI blank
    held, clean = recs[1], recs[2]
    review_id = REVIEW_IDS[intake_id]
    run = (await db.execute(select(m.RceIngestionRun).where(m.RceIngestionRun.source_intake_id == intake_id))).scalars().one()
    db.add_all([_issue(intake_id, clean, "NPI_REQUIRED", "NPI", run.id, 1),
                _issue(intake_id, held, "NPI_REQUIRED", "NPI", run.id, 2)])
    await db.flush()
    ghania, izaan, bilal = analyst("ghania"), analyst("izaan"), qa_user("bilal")
    await _enter(db, intake_id, clean, user=ghania)
    await _enter(db, intake_id, held, user=ghania, researched_npi="1245319599")

    import app.core.database as database
    monkeypatch.setattr(database, "async_session_maker", lambda: _Ctx(db))

    async def package(name):
        out = tmp_path / name
        args = SimpleNamespace(intake=str(intake_id), cutoff="2099-01-01T00:00:00", out=str(out), run_id=None,
                               formats="csv", baseline=None, classification="DRAFT")
        assert await pkg.build(args) == 0
        man = json.loads(next(out.glob("*.MANIFEST.json")).read_text(encoding="utf-8"))
        assert man["self_check_failures"] == []
        lines = list(csv.reader(io.StringIO(next(p for p in out.glob("*.csv") if "field-findings" not in p.name)
                                            .read_bytes().decode("utf-8-sig"))))
        cols = lines[1]
        by_line = {int(r[cols.index("Source_Line")]): dict(zip(cols, r)) for r in lines[2:]}
        fl = list(csv.reader(io.StringIO(next(out.glob("*-field-findings.csv")).read_bytes().decode("utf-8-sig"))))
        return lines[0][0], by_line[clean.line_number], by_line[held.line_number], fl

    NPI, SUP = "Submitted NPI (original, verbatim)", "Supplemental NPI (manual research - NOT an accepted correction)"

    # A. research only: no bucket, no status and no submitted value moves
    head, b4, hold, fl = await package("research-only")
    assert (b4["Bucket"], b4[AUTO], b4[NPI], b4[SUP]) == ("B4", "B4", "", "1234567893")
    assert (hold["Bucket"], hold["Status"], hold[NPI], hold[SUP]) == ("B4", "HELD", "", "1245319599")
    assert "B1 0, B2 0, B3 0, B4 2" in head and "moved by QA-approved" not in head
    assert fl[0] == flf.FIELD_HEADERS and len(fl[0]) == 18
    assert sorted(r[flf.FIELD_HEADERS.index("NPI")] for r in fl[1:]) == ["", ""] and len(fl) == 3

    # B. analyst reclassifies to B1, a DIFFERENT person approves: only then does the reporting bucket move
    await qa_gate.record_analyst_determination(db, review_id, user=izaan, determination="RECLASSIFY", determined_bucket="B1",
                                               rationale="synthetic: identity and location confirmed against the researched NPI")
    head, b4, hold, fl = await package("pending")
    assert b4["Bucket"] == "B4" and b4["Analyst/QA Status"] == rd.AWAITING_QA, "pending QA never moves a bucket"
    with pytest.raises(qa_gate.QaGateRefused):
        await qa_gate.submit_qa_review(db, review_id, user=izaan, qa_action="APPROVE", qa_reason="approving my own determination")
    await qa_gate.submit_qa_review(db, review_id, user=bilal, qa_action="APPROVE", qa_reason="synthetic QA test action - workflow only")
    await db.flush()            # the gate sets reportable_at on the session; make it visible before the fingerprint
    before = await _fingerprint(db, intake_id)
    head, b4, hold, fl = await package("approved")
    assert await _fingerprint(db, intake_id) == before
    assert (b4["Bucket"], b4[AUTO]) == ("B1", "B4") and "RECLASSIFIED (QA-approved) B4 -> B1" in b4["Flag"]
    assert (b4[NPI], b4[SUP]) == ("", "1234567893"), "the submitted blank is never filled by the researched NPI"
    assert "automated Treatment B4 candidate" in b4["Reasons"] and "rule RULE-006 v4" in b4["Bucket Basis"]
    assert (hold["Bucket"], hold["Status"], hold[NPI]) == ("B4", "HELD", "")
    assert "B1 1, B2 0, B3 0, B4 1, Not classified 0 = 2 (1 HELD inside B4; 1 moved by QA-approved reclassification)" in head
    # the separate field-level export still reports the missing submitted NPI for both records
    F = lambda row, name: row[flf.FIELD_HEADERS.index(name)]                               # noqa: E731
    assert len(fl) == 3 and {F(r, "Issue_Type") for r in fl[1:]} == {"MISSING_ENTRY"}
    assert {F(r, "NPI") for r in fl[1:]} == {""} and {F(r, "Field_ID") for r in fl[1:]} == {"NPI"}
    stored = (await db.execute(select(reg.ReviewRecord).where(reg.ReviewRecord.review_id == review_id))).scalars().one()
    assert stored.classification_bucket == "B4" and stored.reclassified_to is None


# ── pure rules (no database) ─────────────────────────────────────────────────

class _Ev:
    def __init__(self, seq, kind, **k):
        self.__dict__.update(dict(id=uuid.uuid4(), sequence_number=seq, event_type=kind, supersedes_decision_id=None,
                                  determination=None, determined_bucket=None, qa_action=None, occurred_at=BEFORE,
                                  actor_user_id=None), **k)


def _pair(analyst_id, qa_id, bucket="B1", action="APPROVE"):
    return [_Ev(1, "ANALYST_DETERMINATION", determination="RECLASSIFY", determined_bucket=bucket, actor_user_id=analyst_id),
            _Ev(2, "QA_REVIEW", qa_action=action, actor_user_id=qa_id)]


def test_an_approval_that_is_not_independent_is_never_applied():
    same = rd.final_bucket_decision(_pair(ANALYST, ANALYST), automated_bucket="B4")
    assert same["bucket"] == "" and "not recorded as a different person" in same["note"]
    assert rd.final_bucket_decision(_pair(ANALYST, None), automated_bucket="B4")["bucket"] == ""
    assert rd.final_bucket_decision(_pair(None, QA), automated_bucket="B4")["bucket"] == ""
    ok = rd.final_bucket_decision(_pair(ANALYST, QA), automated_bucket="B4")
    assert ok["bucket"] == "B1" and ok["note"].startswith("QA-approved reclassification B4 -> B1")
    for action in ("RETURN", "ESCALATE"):
        assert rd.final_bucket_decision(_pair(ANALYST, QA, action=action), automated_bucket="B4") == {"bucket": "", "note": ""}
    assert rd.final_bucket_decision(_pair(ANALYST, QA, bucket="B9"), automated_bucket="B4")["bucket"] == ""
    assert rd.final_bucket_decision(_pair(ANALYST, QA, bucket="B4"), automated_bucket="B4") == {"bucket": "", "note": ""}
    assert rd.final_bucket_decision([], automated_bucket="B4") == {"bucket": "", "note": ""}


def _f(i, bucket="B1", status="CLEAN", **kw):
    base = dict(source_line=i, agt_record_id=f"agt-{i}", organization=f"Org {i}", bucket=bucket, status=status,
                nppes="n", pecos="p", leie="l", sam="s", reasons="r")
    base.update(kw)
    return base


def test_held_and_unclassified_records_are_never_moved_and_totals_reconcile():
    facts = [_f(1, "B4", approved_bucket="B1", approved_note="QA-approved reclassification B4 -> B1 (x)"),
             _f(2, "B4"),
             _f(3, "B1", status="HELD", held_reason="blocking identity", approved_bucket="B2"),
             _f(4, None, approved_bucket="B1"),
             _f(5, "B3", approved_bucket="B3"),
             _f(6, "B2", approved_note="QA-approved reclassification to B1 NOT applied: the QA reviewer is not "
                                       "recorded as a different person from the analyst"),
             _f(7, "B2", approved_bucket="B4"),
             _f(8, "B4", status="REJECTED", approved_bucket="B1"),
             _f(9, "B1", approved_bucket="B3")]
    ds = ds_mod.build_dataset(facts, report_id="R")
    rows = {r[COLS.index("AGT_Record_ID")]: r for r in ds["sheets"]["Records"]["rows"]}
    assert [_c(rows[f"agt-{i}"], "Bucket") for i in range(1, 10)] == [
        "B1", "B4", "B4", ds_mod.NOT_CLASSIFIED, "B3", "B2", "B2", "B4", "B1"]
    assert [_c(rows[f"agt-{i}"], AUTO) for i in range(1, 10)] == [
        "B4", "B4", "B1", ds_mod.NO_PERSISTED_BUCKET, "B3", "B2", "B2", "B4", "B1"]
    assert "B2 -> B4 recorded but NOT applied: the methodology provides only" in _c(rows["agt-7"], "Bucket Basis")
    assert "NOT applied: record status is REJECTED" in _c(rows["agt-8"], "Bucket Basis")
    assert "B1 -> B3 recorded but NOT applied" in _c(rows["agt-9"], "Bucket Basis")
    assert _c(rows["agt-3"], "Status") == "HELD" and "NOT applied: the record remains HELD" in _c(rows["agt-3"], "Bucket Basis")
    assert "NOT applied: the record has no persisted classification" in _c(rows["agt-4"], "Bucket Basis")
    assert "NOT applied: the QA reviewer is not recorded" in _c(rows["agt-6"], "Bucket Basis")
    s = ds["summary"]
    assert s["buckets"] == {"B1": 2, "B2": 2, "B3": 1, "B4": 3} and s["automated_buckets"] == {"B1": 1, "B2": 2, "B3": 1, "B4": 4}
    assert sum(s["buckets"].values()) + s["unclassified_non_held_count"] == s["unique_records"] == 9
    assert s["reclassification_transitions"] == {"B4 -> B1": 1} and s["held_inside_b4"] == 1 and s["complete"] is False


def test_a_dataset_with_no_reclassification_is_unchanged_apart_from_the_new_column():
    ds = ds_mod.build_dataset([_f(1, "B1"), _f(2, "B4"), _f(3, None, status="HELD", held_reason="x")], report_id="R")
    s = ds["summary"]
    assert s["buckets"] == s["automated_buckets"] == {"B1": 1, "B2": 0, "B3": 0, "B4": 2}
    assert s["qa_approved_reclassifications"] == 0 and s["reclassification_transitions"] == {}
    assert not any("reclassif" in r[0].lower() for r in ds["sheets"]["Summary"]["rows"])
    assert "moved by QA-approved" not in ds_mod.to_csv(ds).decode("utf-8-sig").splitlines()[0]
    assert COLS[-1] == AUTO and len(COLS) == len(set(COLS))
