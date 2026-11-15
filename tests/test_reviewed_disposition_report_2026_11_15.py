"""P-A: analyst/QA dispositions in the record-level report (synthetic, rolled back).

The automated bucket and rationale stay as persisted; the reviewed disposition is
reported in separate columns; only QA-approved decisions at or before the cutoff
are shown as approved; a HELD record stays HELD.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.reports.data import october_review_dataset as ds_mod
from app.reports.data import reviewed_disposition as rd
from app.tefca_registry import models as reg
from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401
from test_october_review_loader_db_2026_11_12 import (  # noqa: F401
    CUTOFF, HDR, T_OLD, _by_line, _col, _curate, _entity, _evidence, _load, _records, _review, _run,
)

pytestmark = pytest.mark.regression
ACTOR = uuid.uuid4()
QA_ACTOR = uuid.uuid4()          # QA is a different person: the database refuses a self-QA
BEFORE = datetime(2026, 11, 8, 12, 0, 0)
LATER = datetime(2026, 11, 8, 15, 0, 0)
AFTER_CUTOFF = datetime(2026, 11, 10, 9, 0, 0)


async def _event(db, review_id, seq, kind, at, *, determination=None, bucket=None, qa=None, supersedes=None):
    ev = reg.ReviewDecisionEvent(
        id=uuid.uuid4(), review_id=review_id, sequence_number=seq, event_type=kind,
        actor_user_id=QA_ACTOR if qa else ACTOR,
        actor_email=("qa" if qa else "analyst") + "@synthetic-test.docuaction.invalid",
        actor_role="qalead" if qa else "reviewer", occurred_at=at, determination=determination, determined_bucket=bucket,
        rationale="synthetic rationale for the test", qa_action=qa, qa_reason="synthetic qa reason" if qa else None,
        supersedes_decision_id=supersedes,
        supersession_reason="synthetic supersession reason" if supersedes else None)
    db.add(ev)
    await db.flush()
    return ev


async def test_only_qa_approved_visible_decisions_are_reported_and_nothing_else_moves(rolled_back_db):
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(7, arc="9.99.777.60"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ents = {}
    for i in range(1, 8):
        ents[i] = await _entity(db, f"d{i}")
        status = "HELD" if i == 7 else "CLEAN"
        await _curate(db, intake_id, recs[i], status, entity_id=ents[i], reason="blocking identity" if i == 7 else None)
        await _review(db, recs[i], ents[i], f"REV-9960-00000{i}", "B1", T_OLD)
    r = lambda i: f"REV-9960-00000{i}"                                           # noqa: E731
    # 2: determination only
    await _event(db, r(2), 1, "ANALYST_DETERMINATION", BEFORE, determination="CONFIRM")
    # 3: reclassify + QA approve
    await _event(db, r(3), 1, "ANALYST_DETERMINATION", BEFORE, determination="RECLASSIFY", bucket="B2")
    await _event(db, r(3), 2, "QA_REVIEW", LATER, qa="APPROVE")
    # 4: QA return
    await _event(db, r(4), 1, "ANALYST_DETERMINATION", BEFORE, determination="RECLASSIFY", bucket="B3")
    await _event(db, r(4), 2, "QA_REVIEW", LATER, qa="RETURN")
    # 5: QA approval AFTER the cutoff is invisible
    await _event(db, r(5), 1, "ANALYST_DETERMINATION", BEFORE, determination="CONFIRM")
    await _event(db, r(5), 2, "QA_REVIEW", AFTER_CUTOFF, qa="APPROVE")
    # 6: an approved determination that is later superseded, with no new QA
    first = await _event(db, r(6), 1, "ANALYST_DETERMINATION", BEFORE, determination="RECLASSIFY", bucket="B2")
    await _event(db, r(6), 2, "QA_REVIEW", BEFORE.replace(hour=13), qa="APPROVE")
    await _event(db, r(6), 3, "SUPERSEDING_DETERMINATION", LATER, determination="CONFIRM", supersedes=first.id)
    # 7: HELD record with an approved determination
    await _event(db, r(7), 1, "ANALYST_DETERMINATION", BEFORE, determination="CONFIRM")
    await _event(db, r(7), 2, "QA_REVIEW", LATER, qa="APPROVE")

    facts = _by_line(await _load(db, intake_id))
    rows = {i - 1: ds_mod.build_record_row(facts[i]) for i in range(1, 8)}
    D, T, S, B = ("Reviewed Disposition (QA-approved only)", "Reviewed Disposition Date",
                  "Analyst/QA Status", "Reviewed Disposition Basis")

    assert _col(rows[0], S) == rd.NOT_REVIEWED and _col(rows[0], D) == ""
    assert _col(rows[1], S) == rd.AWAITING_QA and _col(rows[1], D) == ""
    assert _col(rows[2], S) == rd.APPROVED and _col(rows[2], D) == "Reclassified B1 -> B2"
    assert _col(rows[2], T) == LATER.isoformat(timespec="seconds")
    assert _col(rows[3], S) == rd.QA_RETURNED and _col(rows[3], D) == "" and _col(rows[3], T) == ""
    assert _col(rows[4], S) == rd.AWAITING_QA and _col(rows[4], D) == "", "an after-cutoff approval is not visible"
    assert _col(rows[5], S) == rd.AWAITING_QA and _col(rows[5], D) == "", "a superseded approval is not reported"
    assert "superseded" not in _col(rows[5], D).lower()
    # The automated classification is untouched everywhere, including after a reclassification.
    for i in (0, 1, 2, 3, 4, 5):
        assert _col(rows[i], "Bucket") == "B1" and "rationale for REV-9960" in _col(rows[i], "Reasons")
    # HELD: still HELD, still B4 in the reporting bucket, the hold is stated not released.
    assert _col(rows[6], "Status") == "HELD" and _col(rows[6], "Bucket") == "B4"
    assert "remains HELD" in _col(rows[6], D)
    assert "analyst determination #1" in _col(rows[2], B)
    assert len({len(x) for x in rows.values()}) == 1 and len(rows[0]) == len(HDR)


def test_summarize_is_pure_and_refuses_to_invent_an_approval():
    class Ev:
        def __init__(self, **k):
            self.__dict__.update(dict(id=uuid.uuid4(), supersedes_decision_id=None, determination=None,
                                      determined_bucket=None, qa_action=None, occurred_at=BEFORE), **k)
    assert rd.summarize([], automated_bucket="B1")["qa_status"] == rd.NOT_REVIEWED
    esc = [Ev(sequence_number=1, event_type="ANALYST_DETERMINATION", determination="CONFIRM"),
           Ev(sequence_number=2, event_type="QA_REVIEW", qa_action="ESCALATE")]
    out = rd.summarize(esc, automated_bucket="B3")
    assert out["qa_status"] == rd.QA_ESCALATED and out["disposition"] == ""


async def test_sam_extract_evidence_reaches_its_own_column_and_never_relabels_the_api(rolled_back_db):
    """P-D: the loader read SAM_GOV only, so extract evidence (SAM_EXTRACT_SUPPLEMENTARY) never
    reached a report row. It now has its own column; an unavailable API stays 'unavailable'."""
    db = rolled_back_db
    intake_id, _ = await seed_intake(db, make_rows(3, arc="9.99.777.61"), with_job=False)
    await _run(db, intake_id)
    recs = await _records(db, intake_id)
    ents = {}
    for i in (1, 2, 3):
        ents[i] = await _entity(db, f"s{i}")
        await _curate(db, intake_id, recs[i], "CLEAN", entity_id=ents[i])
        await _review(db, recs[i], ents[i], f"REV-9961-00000{i}", "B1", T_OLD)
        await _evidence(db, ents[i], "SAM_GOV", "EXCLUSION_REVOCATION", "UNAVAILABLE", T_OLD,
                        note="api.sam.gov unreachable")
    edition = {"publication_date": "2026-11-05", "extract_filename": "SAM_Exclusions_Public_Extract_V2_26309.CSV",
               "extract_sha256": "ab" * 32, "candidate_count": 2, "candidate_match": True}
    await _evidence(db, ents[1], "SAM_EXTRACT_SUPPLEMENTARY", "EXCLUSION_REVOCATION", "REVIEW", T_OLD,
                    original_values=edition)
    await _evidence(db, ents[2], "SAM_EXTRACT_SUPPLEMENTARY", "EXCLUSION_REVOCATION", "PASS", T_OLD,
                    original_values={**edition, "candidate_count": 0, "candidate_match": False})
    # entity 3: API unavailable, no extract row at all

    facts = _by_line(await _load(db, intake_id))
    rows = {i: ds_mod.build_record_row(facts[i]) for i in (1, 2, 3)}
    X, API = ds_mod.SAM_EXTRACT_COLUMN, "SAM.gov Exclusions"
    for i in (1, 2, 3):
        assert "UNAVAILABLE" in _col(rows[i], API).upper(), "the API result is reported as it is"
        assert "extract" not in _col(rows[i], API).lower().replace("registration", "")
    assert "CANDIDATE MATCH (2)" in _col(rows[1], X) and "NOT a confirmed exclusion" in _col(rows[1], X)
    assert "edition 2026-11-05 (SAM_Exclusions_Public_Extract_V2_26309.CSV)" in _col(rows[1], X)
    assert "sha256 " + "ab" * 6 in _col(rows[1], X) and "checked " in _col(rows[1], X)
    assert "No candidate match in the extract" in _col(rows[2], X)
    assert _col(rows[3], X).startswith("Not established by this loader"), "no extract row: not a pass"
