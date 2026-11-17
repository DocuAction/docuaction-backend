"""Validation examples for the QA-approved reporting bucket (synthetic, rolled back).

Companion to test_qa_approved_reporting_bucket_2026_11_16.py. Owner request of
2026-11-16: show a B4 -> B1 example beside its evidence, a B4 -> B3 example with
cutoff handling and HELD protection, and that a same-user approval cannot move a
bucket at the write path or at the report path.

KNOWN GAP, ASSERTED AS CURRENT BEHAVIOUR (not as correct behaviour): the report
applies an approved B4 -> B1 reclassification WITHOUT checking that the record's
stored evidence meets the B1 rule (RULE-001: NPPES verified, OIG LEIE clear,
PECOS verified, NPI check digit not flagged; no SAM exclusion). Record 2 below has
a blank submitted NPI and no source evidence and is still reported B1 once QA
approves. The test pins that so the gap cannot change silently; closing it is a
policy decision that has not been made.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import october_review_loader as ld
from app.reports.data import reviewed_disposition as rd
from app.tefca_registry import models as reg
from app.tefca_registry.rce import models as m
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401
from test_october_review_loader_db_2026_11_12 import (  # noqa: F401
    CUTOFF, T_OLD, _curate, _entity, _evidence, _records, _review, _run,
)

pytestmark = pytest.mark.regression
ANALYST, QA = uuid.uuid4(), uuid.uuid4()
BEFORE = datetime(2026, 11, 8, 12, 0, 0)
LATER = datetime(2026, 11, 8, 15, 0, 0)
AFTER_CUTOFF = datetime(2026, 11, 10, 9, 0, 0)
LATE_CUTOFF = datetime(2026, 11, 11)
COLS = ds_mod.RECORD_COLUMNS
AUTO = ds_mod.AUTOMATED_BUCKET_COLUMN
SOURCES = ("NPPES", "PECOS/CMS Enrollment", "OIG LEIE", "SAM.gov Exclusions")


def _c(row, name):
    return row[COLS.index(name)]


def _rows(dataset):
    return {int(r[COLS.index("Source_Line")]) - 1: r for r in dataset["sheets"]["Records"]["rows"]}


async def _event(db, review_id, seq, kind, at, *, actor=None, **kw):
    qa = kind == "QA_REVIEW"
    ev = reg.ReviewDecisionEvent(
        id=uuid.uuid4(), review_id=review_id, sequence_number=seq, event_type=kind,
        actor_user_id=actor or (QA if qa else ANALYST),
        actor_email=("qa" if qa else "analyst") + "@synthetic-test.docuaction.invalid",
        actor_role="qalead" if qa else "reviewer", occurred_at=at, rationale="synthetic rationale for the test",
        qa_reason="synthetic qa reason" if qa else None, **kw)
    db.add(ev)
    await db.flush()
    return ev


def review_id(arc, i):
    return f"REV-{arc[-4:].replace('.', '')}-{i:06d}"


async def seed_validation_examples(db, arc):
    """Six synthetic records, every one automated B4.

    1  submitted NPI, all four sources recorded PASS; B4 -> B1, QA approved
    2  blank submitted NPI, no source evidence;       B4 -> B1, QA approved   (the gap)
    3  B4 -> B3, QA approved before the cutoff
    4  B4 -> B3, QA approved AFTER the cutoff
    5  HELD;     B4 -> B3, QA approved
    6  B4 -> B1, analyst determination only (the same-user QA insert is attempted by the test)
    """
    rows = make_rows(6, arc=arc, purposesofuse="T-TRTMNT,T-PH")
    rows[0]["NPI"] = "1234567893"                     # synthetic, check-digit valid
    rows[1]["NPI"] = ""
    intake_id, _ = await seed_intake(db, rows, with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    D, Q = "ANALYST_DETERMINATION", "QA_REVIEW"
    for i in range(1, 7):
        ent = await _entity(db, f"v{i}")
        await _curate(db, intake_id, recs[i], "HELD" if i == 5 else "CLEAN", entity_id=ent,
                      reason="blocking identity" if i == 5 else None)
        await _review(db, recs[i], ent, review_id(arc, i), "B4", T_OLD, rule="RULE-006" if i == 2 else "RULE-005")
        if i == 1:
            for source, dim in (("NPPES", "IDENTITY"), ("CMS_PPEF_ENROLLMENT", "MEDICARE_ENROLLMENT"),
                                ("OIG_LEIE", "EXCLUSION_REVOCATION"), ("SAM_GOV", "EXCLUSION_REVOCATION")):
                await _evidence(db, ent, source, dim, "PASS", T_OLD)
    target = {1: "B1", 2: "B1", 3: "B3", 4: "B3", 5: "B3", 6: "B1"}
    for i in range(1, 7):
        await _event(db, review_id(arc, i), 1, D, BEFORE, determination="RECLASSIFY", determined_bucket=target[i])
    for i, at in ((1, LATER), (2, LATER), (3, LATER), (4, AFTER_CUTOFF), (5, LATER)):
        await _event(db, review_id(arc, i), 2, Q, at, qa_action="APPROVE")
    return intake_id


async def test_b4_to_b1_is_reported_beside_the_evidence_and_the_evidence_gap_is_pinned(rolled_back_db):
    db = rolled_back_db
    intake_id = await seed_validation_examples(db, "9.99.777.90")
    rows = _rows(await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="SYN-VAL"))

    # 1: the reader can check the B1 rule against the row - every source cell carries its recorded result
    one = rows[1]
    assert (_c(one, "Bucket"), _c(one, AUTO)) == ("B1", "B4") and _c(one, "Submitted NPI (original, verbatim)") != ""
    for col in SOURCES:
        assert _c(one, col).startswith("PASS"), col
    assert "rule RULE-005 v2" in _c(one, "Bucket Basis") and "QA-approved reclassification B4 -> B1" in _c(one, "Bucket Basis")

    # 2: THE GAP - blank submitted NPI, no evidence for any source, and the report still says B1 after QA approval
    two = rows[2]
    assert _c(two, "Submitted NPI (original, verbatim)") == ""
    assert not any(_c(two, col).startswith("PASS") for col in SOURCES), "no source evidence exists for this record"
    assert (_c(two, "Bucket"), _c(two, AUTO)) == ("B1", "B4"), (
        "current behaviour: QA approval alone moves the reporting bucket; the report does not check B1 evidence")
    assert "rule RULE-006 v2" in _c(two, "Bucket Basis"), "the automated Treatment-B4 basis stays visible"


async def test_b4_to_b3_respects_the_cutoff_and_never_moves_a_held_record(rolled_back_db):
    db = rolled_back_db
    intake_id = await seed_validation_examples(db, "9.99.777.91")
    early = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="SYN-EARLY")
    late = await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=LATE_CUTOFF, report_id="SYN-LATE")
    e, l = _rows(early), _rows(late)

    assert (_c(e[3], "Bucket"), _c(e[3], AUTO)) == ("B3", "B4") and "RECLASSIFIED (QA-approved) B4 -> B3" in _c(e[3], "Flag")
    assert _c(e[3], "Reviewed Disposition (QA-approved only)") == "Reclassified B4 -> B3"
    # 4: approved after the early cutoff - invisible then, applied at the later cutoff
    assert _c(e[4], "Bucket") == "B4" and _c(e[4], "Analyst/QA Status") == rd.AWAITING_QA
    assert _c(l[4], "Bucket") == "B3" and _c(l[4], "Analyst/QA Status") == rd.APPROVED
    # 5: HELD - approved, never applied, at either cutoff
    for rows in (e, l):
        assert (_c(rows[5], "Bucket"), _c(rows[5], "Status")) == ("B4", "HELD")
        assert "NOT applied: the record remains HELD" in _c(rows[5], "Bucket Basis")
        assert "RECLASSIFIED" not in _c(rows[5], "Flag")
    assert early["summary"]["buckets"] == {"B1": 2, "B2": 0, "B3": 1, "B4": 3}
    assert late["summary"]["buckets"] == {"B1": 2, "B2": 0, "B3": 2, "B4": 2}
    for s in (early["summary"], late["summary"]):
        assert s["automated_buckets"] == {"B1": 0, "B2": 0, "B3": 0, "B4": 6}
        assert sum(s["buckets"].values()) == s["unique_records"] == 6 and s["held_inside_b4"] == 1
    held = (await db.execute(select(m.RceCuratedRecord).where(
        m.RceCuratedRecord.source_intake_id == intake_id, m.RceCuratedRecord.record_status == "HELD"))).scalars().all()
    assert len(held) == 1, "the hold is still a hold after both reports"


async def test_a_same_user_approval_is_refused_at_the_write_path_and_moves_nothing(rolled_back_db):
    db = rolled_back_db
    arc = "9.99.777.92"
    intake_id = await seed_validation_examples(db, arc)
    rid = review_id(arc, 6)
    with pytest.raises(Exception) as refused:
        async with db.begin_nested():
            await _event(db, rid, 2, "QA_REVIEW", LATER, actor=ANALYST, qa_action="APPROVE")
    assert "segregation of duties" in str(refused.value).lower()
    events = (await db.execute(select(reg.ReviewDecisionEvent).where(
        reg.ReviewDecisionEvent.review_id == rid))).scalars().all()
    assert [ev.event_type for ev in events] == ["ANALYST_DETERMINATION"], "the self-approval was not stored"
    row = _rows(await ld.load_dataset(db, intake_id=intake_id, evidence_cutoff=CUTOFF, report_id="SYN-SOD"))[6]
    assert _c(row, "Bucket") == "B4" and _c(row, "Analyst/QA Status") == rd.AWAITING_QA


def test_a_same_user_approval_that_somehow_exists_is_ignored_at_the_report_path():
    class Ev:
        def __init__(self, seq, kind, **k):
            self.__dict__.update(dict(id=uuid.uuid4(), sequence_number=seq, event_type=kind, supersedes_decision_id=None,
                                      determination=None, determined_bucket=None, qa_action=None, occurred_at=BEFORE), **k)
    events = [Ev(1, "ANALYST_DETERMINATION", determination="RECLASSIFY", determined_bucket="B3", actor_user_id=ANALYST),
              Ev(2, "QA_REVIEW", qa_action="APPROVE", actor_user_id=ANALYST)]
    decision = rd.final_bucket_decision(events, automated_bucket="B4")
    assert decision["bucket"] == "" and "NOT applied" in decision["note"]
    fact = dict(source_line=1, agt_record_id="agt-1", bucket="B4", status="CLEAN",
                **{"approved_" + k: v for k, v in decision.items()})
    ds = ds_mod.build_dataset([fact], report_id="R")
    row = ds["sheets"]["Records"]["rows"][0]
    assert _c(row, "Bucket") == "B4" and "RECLASSIFIED" not in _c(row, "Flag")
    assert "not recorded as a different person" in _c(row, "Bucket Basis")
    assert ds["summary"]["buckets"]["B4"] == 1 and ds["summary"]["qa_approved_reclassifications"] == 0
