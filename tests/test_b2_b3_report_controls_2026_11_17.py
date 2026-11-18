"""Report-layer controls for QA-approved B4 -> B2 / B3 moves (synthetic, rolled back).

Owner authorization 2026-11-17, part (a) ONLY: report layer, no route, qa_gate,
frontend, migration or schema change. Evidence capture (part (b)) is NOT built
and is not tested here; manual B4 -> B1 stays blocked.

WHAT IS A FIXTURE AND WHAT IS EXERCISED
  Seeded directly as synthetic fixtures (rows written by this file):
    the delivery, its assessment run, the curated rows (CLEAN / HELD), the
    registry entities, each automated B4 review record INCLUDING its stored
    snapshot (`verification_results`), and the case assignment to the analyst.
  Exercised through the application's own functions:
    every analyst determination and every QA approval goes through the route
    handlers `review_routes.record_determination` and `review_routes.submit_qa`
    (called in-process: ownership check, `qa_gate`, the database segregation-
    of-duties trigger and the audit write all run). The HTTP layer, login and
    the role dependency are NOT exercised here. The report is produced by
    `october_review_loader.load_dataset`.

Because the events are written by the application, their time is the database
clock at the moment of the test, not a chosen instant. The "late" cutoff is
therefore after the test run and the "early" cutoff (9 Nov 2026) is before it.
"""
from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.reports.data import field_level_findings as flf
from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import october_review_loader as ld
from app.reports.data import reviewed_disposition as rd
from app.tefca_registry import models as reg
from app.tefca_registry import review_routes as rr
from app.tefca_registry.rce import models as m
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401
from test_october_review_loader_db_2026_11_12 import (  # noqa: F401
    CUTOFF, T_OLD, _curate, _entity, _records, _run,
)

pytestmark = pytest.mark.regression
COLS = ds_mod.RECORD_COLUMNS
AUTO = ds_mod.AUTOMATED_BUCKET_COLUMN
NPI = "Submitted NPI (original, verbatim)"
SUP = "Supplemental NPI (manual research - NOT an accepted correction)"
REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
# Verbatim means verbatim: quotes, angle brackets, a pipe and two spaces in a row must all survive.
RATIONALE = 'SYNTHETIC finding: delivered name ends "L.L.C."  reference shows <LLC> | formatting only, same entity'
QA_REASON = "SYNTHETIC QA reason: compared the two name strings; agrees it is a formatting difference only"

CLEAN_INPUT = {"sources": {"nppes": {"status": "verified", "disposition": "PASS"},
                           "oig_leie": {"status": "clear", "disposition": "NOT_FOUND"},
                           "sam_gov": {"status": "not_found", "disposition": "NOT_FOUND"},
                           "sam_extract": {"status": "clear", "disposition": "NOT_FOUND"}},
               "fields": {}, "confidence_score": None}


def _snapshot(**sources):
    inp = {"sources": {**CLEAN_INPUT["sources"], **sources}, "fields": {}, "confidence_score": None}
    return {"classifier_input": inp}


def _user(label, role):
    return SimpleNamespace(id=uuid.uuid4(), role=role,
                           email=f"{label}@synthetic-test.docuaction.invalid")


def _c(row, name):
    return row[COLS.index(name)]


def _rows(dataset):
    return {int(r[COLS.index("Source_Line")]) - 1: r for r in dataset["sheets"]["Records"]["rows"]}


def review_id(arc, i):
    return f"REV-{arc[-4:].replace('.', '')}-{i:06d}"


async def seed_b2_b3_examples(db, arc):
    """Seven synthetic records, every one automated B4. Returns (intake_id, records, analyst, qa).

    1  stored snapshot has NO pending candidate;        B4 -> B2, approved by a different user
    2  stored snapshot: SAM possible match (pending);   B4 -> B2, approved   (clear SAM extract stored beside it)
    3  stored snapshot: LEIE disposition REVIEW;        B4 -> B2, approved
    4  stored snapshot: SAM possible match (pending);   B4 -> B3, approved   (B3 is unaffected by the candidate)
    5  OLDER review: stores no snapshot; blank NPI;     B4 -> B2, approved
    6  HELD;                                            B4 -> B2, approved
    7  B4 -> B2, analyst determination only (the test attempts a same-user approval through the route)

    FIXTURES: everything up to and including the review rows and their assignment.
    APPLICATION FUNCTIONS: every determination and approval (route handlers -> qa_gate).
    """
    rows = make_rows(7, arc=arc, purposesofuse="T-TRTMNT,T-PH")
    rows[4]["NPI"] = ""
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    analyst, qa = _user("analyst-b2b3", "reviewer"), _user("qa-b2b3", "qalead")
    snapshots = {1: _snapshot(),
                 2: _snapshot(sam_gov={"status": "possible_match", "disposition": "REVIEW"}),
                 3: _snapshot(oig_leie={"status": "not_found", "disposition": "REVIEW"}),
                 4: _snapshot(sam_gov={"status": "possible_match", "disposition": "REVIEW"}),
                 5: None, 6: _snapshot(), 7: _snapshot()}
    for i in range(1, 8):
        ent = await _entity(db, f"b{i}")
        await _curate(db, intake_id, recs[i], "HELD" if i == 6 else "CLEAN", entity_id=ent,
                      reason="blocking identity" if i == 6 else None)
        db.add(reg.ReviewRecord(
            id=uuid.uuid4(), review_id=review_id(arc, i), entity_id=ent, source_record_id=recs[i].id,
            classification_bucket="B4", classification_rule="RULE-005", classification_rule_version=2,
            classification_rationale=f"rationale for {review_id(arc, i)}", created_at=T_OLD,
            verification_results=snapshots[i], assigned_to_user_id=analyst.id))
    await db.flush()
    target = {1: "B2", 2: "B2", 3: "B2", 4: "B3", 5: "B2", 6: "B2", 7: "B2"}
    for i in range(1, 8):
        await rr.record_determination(
            review_id(arc, i), rr.AnalystDetermination(determination="RECLASSIFY", determined_bucket=target[i],
                                                       rationale=RATIONALE), REQUEST, db=db, user=analyst)
    for i in range(1, 7):
        await rr.submit_qa(review_id(arc, i), rr.QaReview(qa_action="APPROVE", qa_reason=QA_REASON),
                           REQUEST, db=db, user=qa)
    return intake_id, recs, analyst, qa


def late_cutoff():
    return datetime.utcnow() + timedelta(days=2)


async def test_approved_b2_and_b3_moves_follow_the_authorized_report_rules(rolled_back_db, caplog):
    db = rolled_back_db
    arc = "9.99.778.10"
    with caplog.at_level(logging.DEBUG):
        intake_id, recs, analyst, qa = await seed_b2_b3_examples(db, arc)
        ds = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=late_cutoff(), report_id="SYN-B2B3",
                                   annotations={str(recs[5].id): {"supplemental_npi": "1234567893"}})
    r = _rows(ds)

    # 1. approved B2, no stored pending candidate: B2, PROVISIONAL, still counted in B2
    assert (_c(r[1], "Bucket"), _c(r[1], AUTO)) == ("B2", "B4")
    assert "PROVISIONAL" in _c(r[1], "Flag") and "RECLASSIFIED (QA-approved) B4 -> B2" in _c(r[1], "Flag")
    assert "none in the stored review snapshot" in _c(r[1], "Bucket Basis")
    assert "does not see a candidate added after that review was saved" in _c(r[1], "Bucket Basis")

    # 2. approved B2 with a stored pending SAM / LEIE candidate: stays B4, with the snapshot limitation stated
    for line, source in ((2, "sam_gov"), (3, "oig_leie")):
        basis = _c(r[line], "Bucket Basis")
        assert (_c(r[line], "Bucket"), _c(r[line], AUTO)) == ("B4", "B4")
        assert "B4 -> B2 recorded but NOT applied" in basis and source in basis
        assert "reads the stored snapshot of the governing review only" in basis
        assert "does not see a candidate added after that review was saved" in basis
        assert "RECLASSIFIED" not in _c(r[line], "Flag") and "PROVISIONAL" not in _c(r[line], "Flag")
        assert _c(r[line], "Reviewed Disposition (QA-approved only)") == "Reclassified B4 -> B2"
        assert _c(r[line], "Analyst/QA Status") == rd.APPROVED
    assert "sam_extract" not in _c(r[2], "Bucket Basis"), "the clear SAM extract beside it clears nothing"

    # 3. approved B3 is unaffected by the same stored candidate, and is not marked provisional
    assert (_c(r[4], "Bucket"), _c(r[4], AUTO)) == ("B3", "B4")
    assert "RECLASSIFIED (QA-approved) B4 -> B3" in _c(r[4], "Flag") and "PROVISIONAL" not in _c(r[4], "Flag")

    # 4. older review with no stored snapshot: applied (not held at B4), flagged, check stated as not possible
    assert (_c(r[5], "Bucket"), _c(r[5], AUTO)) == ("B2", "B4") and "PROVISIONAL" in _c(r[5], "Flag")
    assert "NOT POSSIBLE, the governing review stores no classifier input" in _c(r[5], "Bucket Basis")
    for line in (1, 4, 5):
        assert ds_mod.NO_STRUCTURED_FINDING_FLAG in _c(r[line], "Flag"), line
    for line in range(1, 8):
        assert _c(r[line], ds_mod.MANUAL_CHECK_DATE_COLUMN) == "not recorded", line
        assert not any(ch.isdigit() for ch in _c(r[line], ds_mod.MANUAL_CHECK_DATE_COLUMN))

    # 5. attribution and both texts, verbatim, in their own columns - and never logged
    for line in range(1, 7):
        assert _c(r[line], ds_mod.ANALYST_COLUMN) == f"{analyst.email} (user id {analyst.id}; role reviewer)"
        assert _c(r[line], ds_mod.QA_REVIEWER_COLUMN) == f"{qa.email} (user id {qa.id}; role qalead)"
        assert _c(r[line], ds_mod.ANALYST_RATIONALE_COLUMN) == RATIONALE
        assert _c(r[line], ds_mod.QA_REASON_COLUMN) == QA_REASON
    assert _c(r[7], ds_mod.ANALYST_RATIONALE_COLUMN) == RATIONALE
    assert (_c(r[7], ds_mod.QA_REVIEWER_COLUMN), _c(r[7], ds_mod.QA_REASON_COLUMN)) == ("", "")
    logged = caplog.text
    assert RATIONALE not in logged and QA_REASON not in logged
    assert "delivered name ends" not in logged and "compared the two name strings" not in logged
    audit = (await db.execute(select(reg.TefcaRegAuditLog).where(
        reg.TefcaRegAuditLog.action.in_(("analyst_determination_recorded", "qa_approve"))))).scalars().all()
    assert audit and all("delivered name ends" not in str(a.metadata_) for a in audit)

    # 6. HELD stays B4 once; the researched NPI stays separate and decides nothing
    assert (_c(r[6], "Bucket"), _c(r[6], "Status")) == ("B4", "HELD")
    assert "NOT applied: the record remains HELD" in _c(r[6], "Bucket Basis") and "RECLASSIFIED" not in _c(r[6], "Flag")
    assert (_c(r[5], NPI), _c(r[5], SUP)) == ("", "1234567893")
    held = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == intake_id, m.RceCuratedRecord.record_status == "HELD"))).scalars().all()
    assert len(held) == 1, "the hold is still a hold after the report"

    # 7. counts reconcile; moves not applied are itemized by reason; the original B4 is kept for every record
    s = ds["summary"]
    assert s["buckets"] == {"B1": 0, "B2": 2, "B3": 1, "B4": 4} and s["automated_buckets"] == {"B1": 0, "B2": 0, "B3": 0, "B4": 7}
    assert sum(s["buckets"].values()) == s["unique_records"] == 7 and s["held_inside_b4"] == 1
    assert s["moves_applied"] == 3 and s["reclassification_transitions"] == {"B4 -> B2": 2, "B4 -> B3": 1}
    assert s["moves_not_applied"] == {ds_mod.NA_B2_CANDIDATE: 2, ds_mod.NA_HELD: 1} and s["moves_not_applied_total"] == 3
    assert s["moves_without_structured_finding"] == 6
    assert s["moves_applied"] + s["moves_not_applied_total"] == 6, "record 7 has no approval, so it is not a move"
    summary = dict(ds["sheets"]["Summary"]["rows"])
    assert summary[ds_mod.MOVES_APPLIED_ROW] == "3" and summary[ds_mod.MOVES_NOT_APPLIED_ROW] == "3"
    assert summary[f"  not applied: {ds_mod.NA_B2_CANDIDATE}"] == "2" and summary[f"  not applied: {ds_mod.NA_HELD}"] == "1"
    assert summary[ds_mod.MOVES_UNSTRUCTURED_ROW].startswith("6 (applied and flagged in the Flag column: 3; not applied: 3)")
    assert s["moves_without_structured_finding_applied"] == 3
    assert sum(ds_mod.NO_STRUCTURED_FINDING_FLAG in _c(row, "Flag") for row in r.values()) == 3
    assert any("does not see a candidate added after" in t for t in s["move_limitations"])
    assert "DRAFT / NOT FINAL - INDEPENDENT QA PENDING" in ds["banner"]
    assert all(_c(r[line], AUTO) == "B4" for line in range(1, 8))


async def test_the_cutoff_hides_application_written_decisions_made_after_it(rolled_back_db):
    db = rolled_back_db
    intake_id, _recs, _a, _q = await seed_b2_b3_examples(db, "9.99.778.11")
    early = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="SYN-B2B3-EARLY")
    for row in _rows(early).values():
        assert _c(row, "Bucket") == "B4" and _c(row, "Analyst/QA Status") == rd.NOT_REVIEWED
        assert _c(row, ds_mod.ANALYST_COLUMN) == "" and _c(row, ds_mod.ANALYST_RATIONALE_COLUMN) == ""
        assert _c(row, ds_mod.MANUAL_CHECK_DATE_COLUMN) == ""
    s = early["summary"]
    assert s["buckets"] == {"B1": 0, "B2": 0, "B3": 0, "B4": 7} and s["moves_applied"] == 0 and s["moves_not_applied"] == {}
    assert ds_mod.MOVES_APPLIED_ROW not in dict(early["sheets"]["Summary"]["rows"])


async def test_a_same_user_approval_is_refused_by_the_route_and_moves_nothing(rolled_back_db):
    db = rolled_back_db
    arc = "9.99.778.12"
    intake_id, _recs, analyst, _qa = await seed_b2_b3_examples(db, arc)
    rid = review_id(arc, 7)
    with pytest.raises(HTTPException) as refused:
        await rr.submit_qa(rid, rr.QaReview(qa_action="APPROVE", qa_reason=QA_REASON), REQUEST, db=db,
                           user=SimpleNamespace(id=analyst.id, email=analyst.email, role="qalead"))
    assert refused.value.status_code == 409 and refused.value.headers["X-Denial-Code"] == "SOD_SELF_QA"
    events = (await db.execute(select(reg.ReviewDecisionEvent).where(
        reg.ReviewDecisionEvent.review_id == rid))).scalars().all()
    assert [ev.event_type for ev in events] == ["ANALYST_DETERMINATION"], "the self-approval was not stored"
    row = _rows(await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=late_cutoff(), report_id="SYN-SOD2"))[7]
    assert (_c(row, "Bucket"), _c(row, "Analyst/QA Status")) == ("B4", rd.AWAITING_QA)
    assert "RECLASSIFIED" not in _c(row, "Flag")


def test_the_decision_save_time_is_never_offered_as_a_manual_check_date():
    saved = datetime(2026, 11, 8, 12, 0, 0)

    class Ev:
        def __init__(self, seq, kind, **k):
            self.__dict__.update(dict(id=uuid.uuid4(), sequence_number=seq, event_type=kind, supersedes_decision_id=None,
                                      occurred_at=saved, actor_email="a@x.invalid", actor_user_id="u1",
                                      actor_role="reviewer", rationale="why", qa_reason=None), **k)
    out = rd.decision_details([Ev(1, "ANALYST_DETERMINATION"), Ev(2, "QA_REVIEW", qa_reason="agreed", actor_role="qalead")])
    assert out["manual_check_date"] == "not recorded" and "2026" not in "".join(out.values())
    assert (out["analyst_rationale"], out["qa_reason"]) == ("why", "agreed")
    assert rd.decision_details([]) == {"analyst": "", "qa_reviewer": "", "analyst_rationale": "", "qa_reason": "",
                                       "manual_check_date": ""}


def test_a_stored_candidate_is_found_in_both_snapshot_shapes_and_unknown_origin_is_never_called_none():
    def review(sources, *, bulk):
        vr = {"classifier_input": {"sources": sources, "fields": {}}} if bulk else {"sources": sources, "fields": {}}
        return SimpleNamespace(verification_results=vr)
    clean = {"status": "not_found", "disposition": "NOT_FOUND"}
    # delivery (bulk) shape: disposition beside the status
    assert rd.stored_pending_candidates(review({"sam_gov": {"status": "not_found", "disposition": "REVIEW"},
                                                "oig_leie": clean}, bulk=True))["pending"] == ["sam_gov"]
    # single-record shape: the disposition is under persisted_evidence / persisted_evidence_seen, state not_found
    single = review({"sam_gov": {"status": "not_found", "persisted_evidence": {"disposition": "REVIEW"}},
                     "oig_leie": {"status": "clear", "persisted_evidence_seen": {"disposition": "REVIEW"}}}, bulk=False)
    assert rd.stored_pending_candidates(single) == {"evaluated": True, "pending": ["oig_leie", "sam_gov"],
                                                    "undetermined": []}
    assert rd.stored_pending_candidates(review({"sam_extract": {"status": "possible_match"}}, bulk=False))["pending"] \
        == ["sam_extract"]
    # not_found with no disposition anywhere: cannot be told from a pending hit - reported, never "none"
    unknown = rd.stored_pending_candidates(review({"sam_gov": {"status": "not_found"}, "oig_leie": "not_found",
                                                   "nppes": {"status": "not_found"}}, bulk=False))
    assert unknown == {"evaluated": True, "pending": [], "undetermined": ["oig_leie", "sam_gov"]}
    assert rd.stored_pending_candidates(review({"sam_gov": clean}, bulk=True)) == {
        "evaluated": True, "pending": [], "undetermined": []}
    assert rd.stored_pending_candidates(SimpleNamespace(verification_results=None)) == {
        "evaluated": False, "pending": [], "undetermined": []}

    row = ds_mod.build_record_row({
        "agt_record_id": "00000000-0000-0000-0000-000000000009", "source_line": 2, "bucket": "B4", "status": "CLEAN",
        "approved_bucket": "B2", "approved_note": "", "approved_b2_candidate_evaluated": True,
        "approved_b2_pending": [], "approved_b2_undetermined": ["sam_gov"]})
    basis = _c(row, "Bucket Basis")
    assert _c(row, "Bucket") == "B2" and "NOT DETERMINABLE for sam_gov" in basis
    assert "none in the stored review snapshot" not in basis


def test_the_dataset_rules_for_b2_hold_for_any_fact_and_the_field_export_is_untouched():
    def fact(i, **k):
        return dict({"agt_record_id": f"00000000-0000-0000-0000-{i:012d}", "source_line": i + 1, "bucket": "B4",
                     "status": "CLEAN", "bucket_basis": "Persisted classification: rule RULE-005 v2",
                     "approved_bucket": "B2", "approved_note": ""}, **k)
    ds = ds_mod.build_dataset([
        fact(1, approved_b2_candidate_evaluated=True, approved_b2_pending=[]),
        fact(2, approved_b2_candidate_evaluated=True, approved_b2_pending=["sam_gov"]),
        fact(3),                                             # a fact that says nothing about the candidate check
        fact(4, approved_bucket="B1"),                       # manual B4 -> B1 stays blocked
    ], report_id="SYN-PURE")
    rows = {int(r[COLS.index("Source_Line")]) - 1: r for r in ds["sheets"]["Records"]["rows"]}
    assert [_c(rows[i], "Bucket") for i in (1, 2, 3, 4)] == ["B2", "B4", "B2", "B4"]
    assert "NOT POSSIBLE" in _c(rows[3], "Bucket Basis") and "PROVISIONAL" in _c(rows[3], "Flag")
    assert ds["summary"]["moves_not_applied"] == {ds_mod.NA_B1: 1, ds_mod.NA_B2_CANDIDATE: 1}
    assert ds["summary"]["buckets"] == {"B1": 0, "B2": 2, "B3": 0, "B4": 2}

    new = {ds_mod.ANALYST_COLUMN, ds_mod.QA_REVIEWER_COLUMN, ds_mod.ANALYST_RATIONALE_COLUMN,
           ds_mod.QA_REASON_COLUMN, ds_mod.MANUAL_CHECK_DATE_COLUMN}
    assert new <= set(COLS) and COLS[-1] == AUTO
    assert len(flf.FIELD_HEADERS) == 18 and not (new & set(flf.FIELD_HEADERS))
    assert flf.FIELD_HEADERS[0] == "Snapshot_Date" and flf.FIELD_HEADERS[-1] == "Purpose_of_Use"


async def test_a_failed_export_never_writes_the_analyst_or_qa_text_to_the_log(rolled_back_db, monkeypatch, caplog):
    """Closeout 2026-11-17 (tests only). Synthetic export failures at three points; fake texts must stay out of the
    log and out of the job's stored failure reason.

    What this does NOT prove: `run_record_review_export_job` logs the exception text by design
    (`logger.exception`). A library error whose own message quotes a cell value would therefore be logged. The one
    such error known here (openpyxl's illegal-character error quotes the value) is exercised below with a real
    control character and does not occur, because the workbook engine strips those characters first.
    """
    import uuid as _uuid

    import openpyxl.workbook.workbook as wbmod

    from app.reports import routes as report_routes
    from app.reports.data import artifact_registry
    from app.reports.data.export_job_model import ReportExportJob
    from app.reports.export_runner import run_record_review_export_job

    db = rolled_back_db
    fake_rationale = "SYNTHETIC-LEAK-MARKER-ANALYST fake finding text " + chr(11) + " with a control character"
    fake_reason = "SYNTHETIC-LEAK-MARKER-QA fake approval reason text"
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "RATIONALE", fake_rationale)
    monkeypatch.setattr(module, "QA_REASON", fake_reason)
    lead = SimpleNamespace(email="qalead@synthetic-test.docuaction.invalid")
    registered = []

    async def _register(_db, **kw):
        registered.append(kw)
        return {"artifact_id": _uuid.uuid4(), "artifact_version": 1, "rendered_sha256": "f" * 64,
                "size_bytes": len(kw["content"])}

    async def _register_fails(_db, **_kw):
        raise RuntimeError("synthetic registration failure")

    def _save_fails(self, _target):
        raise OSError(28, "synthetic: no space left on device")

    async def _export(fmt):
        out = await report_routes.export_record_review(
            report_routes.RecordReviewExportRequest(intake_id=str(intake_id), format=fmt,
                                                    evidence_cutoff=late_cutoff().isoformat()), db=db, user=lead)
        job = await db.get(ReportExportJob, _uuid.UUID(out["job_id"]))
        state = await run_record_review_export_job(db, job)
        return state, (await db.get(ReportExportJob, job.id)).error_reason or ""

    with caplog.at_level(logging.DEBUG):
        intake_id, _recs, _a, _q = await seed_b2_b3_examples(db, "9.99.778.13")
        await db.commit()                      # the runner rolls back on failure; keep the seeded rows (savepoint)
        # the texts really are in the dataset being exported
        ds = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=late_cutoff(), report_id="SYN-LEAK")
        assert _c(_rows(ds)[1], ds_mod.ANALYST_RATIONALE_COLUMN) == fake_rationale
        assert _c(_rows(ds)[1], ds_mod.QA_REASON_COLUMN) == fake_reason

        # 1. the renderer fails while writing the workbook
        monkeypatch.setattr(artifact_registry, "finalize_artifact", _register)
        with monkeypatch.context() as mp:
            mp.setattr(wbmod.Workbook, "save", _save_fails)
            state1, reason1 = await _export("xlsx")
        # 2. registration fails after a complete CSV holding both texts was rendered
        monkeypatch.setattr(artifact_registry, "finalize_artifact", _register_fails)
        state2, reason2 = await _export("csv")
        # 3. a control character in the analyst text does NOT make the workbook fail (it would quote the cell)
        monkeypatch.setattr(artifact_registry, "finalize_artifact", _register)
        state3, reason3 = await _export("xlsx")

    assert (state1, state2) == (ReportExportJob.STATE_FAILED, ReportExportJob.STATE_FAILED)
    assert "OSError" in reason1 and "RuntimeError" in reason2
    assert state3 == ReportExportJob.STATE_SUCCEEDED and reason3 == "" and len(registered) == 1
    logged = caplog.text
    assert "record-review export job" in logged and "synthetic registration failure" in logged, "failures were logged"
    assert logged.count("Traceback") >= 2
    for text in (logged, reason1, reason2):
        assert "SYNTHETIC-LEAK-MARKER" not in text
        assert "fake finding text" not in text and "fake approval reason text" not in text
