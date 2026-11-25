"""Regression tests for the three review findings on the QA-approved reporting change (2026-11-17).

Owner authorization 2026-11-17: one bounded correction.
  1. A recorded move that is NOT applied shows its reason in the HTML, which is also what the PDF path renders.
  2. The provisional marker is the complete "PROVISIONAL B2" token; other flag text is kept and never duplicated.
  3. The baseline comparison explains only bucket differences that the transition counts support; an unexplained
     TOTAL, HELD or residual difference keeps its "investigate" warning.

SYNTHETIC facts only. No database, no network. The PDF check here proves which HTML is handed to the PDF engine;
it does NOT render a PDF.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import record_review_pdf_support as s
from app.reports.data import october_review_dataset as d

pytestmark = pytest.mark.regression

_spec = importlib.util.spec_from_file_location(
    "generate_record_review_package_corrections",
    Path(__file__).resolve().parents[1] / "scripts" / "generate_record_review_package.py")
pkg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pkg)

APPROVED = dict(reviewed_qa_status="Approved by QA", reviewed_date="2026-11-08T12:00:00",
                reviewed_basis="analyst determination #1; QA APPROVE #2 by a different recorded user")


def _facts():
    f = s.base14_facts()
    # record 6: automated B4, approved B4 -> B2, stored pending SAM candidate  => NOT applied
    f[5].update(APPROVED, approved_bucket="B2", approved_b2_candidate_evaluated=True,
                approved_b2_pending=["sam_gov"], reviewed_disposition="Reclassified B4 -> B2")
    # record 7: HELD, approved move to B3                                      => NOT applied
    f[6].update(APPROVED, approved_bucket="B3", reviewed_disposition="Reclassified B4 -> B3")
    # record 9: automated B4, approved B4 -> B1                                => NOT applied (blocked)
    f[8].update(APPROVED, approved_bucket="B1", approved_b1_reason="SYNTHETIC evidence gap",
                reviewed_disposition="Reclassified B4 -> B1")
    # record 10: automated B4, approved B4 -> B3                               => applied
    f[9].update(APPROVED, bucket="B4", status="CLEAN", approved_bucket="B3",
                reviewed_disposition="Reclassified B4 -> B3")
    return f


def _review_section(html: str) -> str:
    return html.split(d.PDF_REVIEW_HEADING, 1)[1]


def test_every_move_that_is_not_applied_shows_its_reason_in_the_html():
    facts = _facts()
    ds = s.build(facts, report_id="RECORD-REVIEW-SYNTHETIC-NOT-APPLIED")
    assert ds["summary"]["moves_not_applied_total"] == 3 and ds["summary"]["moves_applied"] == 1
    section = _review_section(d.to_html(ds))
    assert f"{d.PDF_NOT_APPLIED_HEADING} (3)" in section
    block = section.split(f"{d.PDF_NOT_APPLIED_HEADING} (3)", 1)[1].split("</table>", 1)[0]
    for i, needles in ((5, ("NOT applied", "pending exclusion candidate", "sam_gov", "does not see a candidate")),
                       (6, ("NOT applied", "remains HELD")),
                       (8, ("NOT applied", "does not establish B1", "SYNTHETIC evidence gap"))):
        rid = facts[i]["agt_record_id"]
        row = next(r for r in block.split("<tr") if rid in r)
        for needle in needles:
            assert needle in row, (rid, needle)
    assert facts[9]["agt_record_id"] not in block, "an applied move is not listed as not applied"
    assert block.count("<tr") == 1 + 3, "one header row and one row per move that was not applied"


def test_the_not_applied_block_is_absent_when_no_move_was_refused():
    html = d.to_html(s.build(s.base14_facts(), report_id="RECORD-REVIEW-SYNTHETIC-NO-MOVES"))
    assert d.PDF_NOT_APPLIED_HEADING not in html


def test_the_pdf_path_is_handed_the_same_html_including_the_reasons(monkeypatch):
    """Proves the shared path only: `to_pdf` gives the PDF engine exactly `to_html`. No PDF is rendered here."""
    from app.reports.engine import pdf_engine
    seen = {}

    def _capture(html, **kw):
        seen["html"] = html
        return b"%PDF-not-rendered"

    monkeypatch.setattr(pdf_engine, "render_pdf", _capture)
    ds = s.build(_facts(), report_id="RECORD-REVIEW-SYNTHETIC-NOT-APPLIED-PDF")
    assert d.to_pdf(ds) == b"%PDF-not-rendered"
    assert seen["html"] == d.to_html(ds)
    assert f"{d.PDF_NOT_APPLIED_HEADING} (3)" in seen["html"] and "pending exclusion candidate" in seen["html"]


def _flag(**fact):
    base = {"agt_record_id": "00000000-0000-0000-0000-00000000000a", "source_line": 2, "bucket": "B4",
            "status": "CLEAN", "approved_bucket": "B2", "approved_note": "",
            "approved_b2_candidate_evaluated": True, "approved_b2_pending": [], "approved_b2_undetermined": []}
    row = d.build_record_row({**base, **fact})
    assert row[d.RECORD_COLUMNS.index("Bucket")] == "B2"
    return row[d.RECORD_COLUMNS.index("Flag")]


def test_an_applied_b2_always_carries_the_complete_provisional_b2_marker_once():
    # other text that merely contains the word must not stand in for the marker
    flag = _flag(flag="PROVISIONAL address correction")
    tokens = [t.strip() for t in flag.split(";")]
    assert tokens[0] == "PROVISIONAL address correction", "the caller's text is kept as written"
    assert tokens.count(d.PROVISIONAL_B2_FLAG) == 1
    # already present (any letter case, own token): kept, not duplicated
    for existing in ("PROVISIONAL B2", "Q1 open; provisional b2"):
        flag = _flag(flag=existing)
        assert flag.startswith(existing)
        assert [t.strip().upper() for t in flag.split(";")].count(d.PROVISIONAL_B2_FLAG) == 1
    # the annotation path and the applied move together still give one marker
    flag = _flag(provisional=True)
    assert [t.strip() for t in flag.split(";")].count(d.PROVISIONAL_B2_FLAG) == 1
    flag = _flag()
    assert [t.strip() for t in flag.split(";")].count(d.PROVISIONAL_B2_FLAG) == 1


INVESTIGATE = "investigate before relying on the package"


def _summary(**transitions):
    moved = {k.replace("_", " -> "): v for k, v in transitions.items()}
    return {"unclassified_non_held_count": 0, "qa_approved_reclassifications": sum(moved.values()),
            "reclassification_transitions": moved}


def test_a_total_difference_is_never_explained_by_a_bucket_move():
    saved = {"buckets": {"B1": 0, "B2": 1, "B3": 0, "B4": 6}, "rows": 7, "held_in_b4": 1}
    diffs = {x["item"]: x for x in pkg.compare_baseline(
        saved, _summary(B4_B2=1), {"B2": 0, "B4": 7, "TOTAL": 8, "HELD": 2})}
    assert set(diffs) == {"B2", "B4", "TOTAL", "HELD"}
    for key in ("TOTAL", "HELD"):
        assert diffs[key]["explanation"].startswith("no recorded explanation") and INVESTIGATE in diffs[key]["explanation"]
        assert "reclassification" not in diffs[key]["explanation"]
    for key in ("B2", "B4"):
        assert "QA-approved reclassification" in diffs[key]["explanation"]
        assert INVESTIGATE not in diffs[key]["explanation"], "the move accounts for the whole difference"


def test_a_bucket_difference_larger_than_the_moves_keeps_the_warning_for_the_rest():
    saved = {"buckets": {"B1": 4, "B2": 3, "B3": 0, "B4": 6}, "rows": 13, "held_in_b4": 0}
    diffs = {x["item"]: x for x in pkg.compare_baseline(saved, _summary(B4_B2=1), {"B1": 5, "B2": 0})}
    assert "QA-approved reclassification" in diffs["B2"]["explanation"]
    assert "+2" in diffs["B2"]["explanation"] and INVESTIGATE in diffs["B2"]["explanation"]
    # B1 is not part of any recorded move: the moves explain nothing about it
    assert diffs["B1"]["explanation"].startswith("no recorded explanation")
    assert "reclassification" not in diffs["B1"]["explanation"]


# -- closeout 2026-11-17 (second review of the correction) -------------------------------------------------

SAME_PERSON_NOTE = ("RECLASSIFY to B2 recorded; the QA action is NOT an approval: the QA reviewer is "
                    "not recorded as a different person from the analyst")


def test_the_not_applied_rows_are_described_as_recorded_actions_never_as_independent_approval():
    facts = _facts()
    # record 3: a same-person QA action. It is listed, with its reason, and its bucket is unchanged.
    facts[2].update(bucket="B4", status="CLEAN", approved_note=SAME_PERSON_NOTE)
    ds = s.build(facts, report_id="RECORD-REVIEW-SYNTHETIC-SAME-PERSON")
    assert ds["summary"]["moves_not_applied"][d.NA_NOT_INDEPENDENT] == 1
    row = next(r for r in ds["sheets"]["Records"]["rows"] if r[0] == facts[2]["agt_record_id"]
               or facts[2]["agt_record_id"] in r)
    assert row[d.RECORD_COLUMNS.index("Bucket")] == "B4", "the bucket is unchanged"
    section = _review_section(d.to_html(ds))
    assert d.PDF_NOT_APPLIED_HEADING == "Recorded reclassification actions not applied"
    block = section.split(f"{d.PDF_NOT_APPLIED_HEADING} (4)", 1)[1].split("</table>", 1)[0]
    intro = block.split("<table>", 1)[0]
    assert "recorded reclassification action" in intro and "Its Bucket is unchanged" in intro
    assert "is not an independent approval" in intro
    for wrong in ("QA-approved reclassification", "the QA approval", "Approved moves"):
        assert wrong not in intro, wrong
    same = next(r for r in block.split("<tr") if facts[2]["agt_record_id"] in r)
    assert d.NA_NOT_INDEPENDENT in same and "not recorded as a different person" in same


def test_an_unexplained_remainder_keeps_its_warning_even_when_unclassified_records_exist():
    saved = {"buckets": {"B1": 4, "B2": 3, "B3": 1, "B4": 6}, "rows": 14, "held_in_b4": 0}
    summary = {**_summary(B4_B2=1), "unclassified_non_held_count": 1}
    diffs = {x["item"]: x for x in pkg.compare_baseline(
        saved, summary, {"B2": 0, "B3": 3, "B4": 7, "TOTAL": 15})}
    # B2: +3 against one recorded move (+1): context kept, move stated, the other +2 still flagged
    b2 = diffs["B2"]["explanation"]
    assert "NO usable persisted classification" in b2 and "QA-approved reclassification" in b2
    assert "+2" in b2 and INVESTIGATE in b2
    # B3: no move touches it; the unclassified context alone does not account for a number
    assert "NO usable persisted classification" in diffs["B3"]["explanation"]
    assert INVESTIGATE in diffs["B3"]["explanation"]
    # TOTAL: never explained by a move; context kept, warning kept
    assert "NO usable persisted classification" in diffs["TOTAL"]["explanation"]
    assert INVESTIGATE in diffs["TOTAL"]["explanation"] and "reclassification changes" not in diffs["TOTAL"]["explanation"]
    # B4: 7 -> 6 is exactly the one recorded move: fully explained, no warning, context still shown
    b4 = diffs["B4"]["explanation"]
    assert "QA-approved reclassification" in b4 and "NO usable persisted classification" in b4
    assert INVESTIGATE not in b4
