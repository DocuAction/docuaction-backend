"""Report DA-ARC-2026-071 corrections (found on DEV 2026-10-10), pinned.

  C-1  a review row printed "Pending" beside a QA-approved timestamp: the report read the legacy
       `review_records.reviewer_resolution` column, which the decision-event workflow never writes. The
       determination and QA state now come from the append-only `review_decision_events`.
  C-2  SAM answered "unavailable" for every entity yet printed 100% coverage: coverage_pct is ATTEMPTED / eligible.
       The report now also states ANSWERED (verified + not found) so an attempt without an answer is never shown
       as completed screening.
  C-3  review history said rule version 2 while provenance said 3: the first is the rule set a record was
       CLASSIFIED under, the second the rule set in force when the report was generated. Both are now labelled.
  C-4  the CSV carried dispositions only although the PDF promises "CSV export of the same persisted evidence":
       findings, coverage and review records are now separately labelled annexes built from the same dataset.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

import pytest

from test_delivery_processing_report import (_csv_sections, _generate, artifact_root,  # noqa: F401
                                             rolled_back_db, seed_delivery)


def _sources(**kw):
    return {"state": "Partial", "sources": kw}


def test_attempted_is_not_answered():
    from app.reports.data.delivery_processing_data import _normalise_coverage

    out = _normalise_coverage(_sources(
        sam={"coverage_state": "Unavailable", "eligible": 4, "attempted": 4, "verified": 0, "not_found": 0,
             "unavailable": 4, "failed": 0, "coverage_pct": 100.0},
        leie={"coverage_state": "Complete", "eligible": 4, "attempted": 4, "verified": 2, "not_found": 2,
              "unavailable": 0, "failed": 0, "coverage_pct": 100.0}))
    by = {s["name"]: s for s in out["sources"]}
    assert by["sam"]["coverage_pct"] == 100.0          # attempted, unchanged for compatibility
    assert by["sam"]["answered"] == 0 and by["sam"]["answered_pct"] == 0.0
    assert by["leie"]["answered"] == 4 and by["leie"]["answered_pct"] == 100.0
    assert by["pecos"]["answered_pct"] is None        # never run: no figure, not 0 and not 100
    never = _normalise_coverage(_sources(pecos={"coverage_state": "Not Run", "eligible": 4, "attempted": 0, "verified": 0,
                                                "not_found": 0, "unavailable": 0, "failed": 0, "coverage_pct": 0.0}))
    assert {s_["name"]: s_ for s_ in never["sources"]}["pecos"]["answered_pct"] is None   # attempted == 0 -> no answered figure


def test_csv_annexes_are_labelled_and_agree_with_the_dataset():
    from app.reports.engine.csv_engine import delivery_processing_to_csv

    ds = {
        "delivery": {"job_id": "j", "intake_id": "i", "delivery_label": "L", "filename": "f", "sha256": "s"},
        "build": {}, "dispositions": {"counts": {}, "rows": []}, "reconciliation": {"available": False},
        "findings": {"available": True, "rows_shown": 1, "rows_total": 1, "total": 1, "open_high": 1,
                     "rows": [{"issue_code": "X-1", "line_number": 2, "rule_id": "NPI-002", "rule_version": "1.2.0",
                               "issue_type": "NPI_MALFORMED", "severity": "HIGH", "field_name": "NPI",
                               "correction_authority": "HUMAN_REQUIRED", "resolution": "OPEN",
                               "description": "=cmd|' /C calc'!A0"}]},
        "verification": {"sources": [{"name": "sam", "state": "Unavailable", "eligible": 4, "attempted": 4,
                                      "verified": 0, "not_found": 0, "unavailable": 4, "failed": 0,
                                      "coverage_pct": 100.0, "answered": 0, "answered_pct": 0.0}]},
        "analyst": {"available": True, "review_records_shown": 1, "review_records_total": 1,
                    "review_records": [{"review_id": "REV-1", "bucket": "B1", "classification_rule": "ROLL-004",
                                        "classification_rule_version": 2, "resolution": "CONFIRM",
                                        "determination_source": "decision_events", "reviewed_at": "2026-09-15T04:36:37",
                                        "qa_state": "APPROVED", "reportable_at": "2026-09-15T05:02:31"}]},
        "limitations": ["a limitation"],
    }
    text = delivery_processing_to_csv(ds, "DA-ARC-2026-999", "2026-10-10T00:00:00", rule_set_version=3)
    sec = _csv_sections(text)
    assert "# B1-B4 rule set in force at generation: 3" in text
    a = next(v for k, v in sec.items() if k.startswith("Annex A"))
    assert a[2][0] == "X-1" and a[2][-1].startswith("'=")          # formula-neutralised
    b = next(v for k, v in sec.items() if k.startswith("Annex B"))
    assert b[1][:3] == ["sam", "Unavailable", "4"] and b[1][8] == "100.0" and b[1][10] == "0.0"
    c = next(v for k, v in sec.items() if k.startswith("Annex C"))
    assert c[2][0] == "REV-1" and c[2][3] == "2" and c[2][4] == "CONFIRM" and c[2][7] == "APPROVED"


@pytest.mark.asyncio
async def test_a_qa_approved_review_is_not_printed_as_pending(rolled_back_db):
    from app.tefca_registry import models as reg
    from sqlalchemy import select

    ids = await seed_delivery(rolled_back_db)
    from app.tefca_registry.rce import models as m

    # THIS delivery's review record only (other tests' committed rows must never be picked up).
    review = (await rolled_back_db.execute(select(reg.ReviewRecord).where(
        reg.ReviewRecord.source_record_id.in_(
            select(m.RceSourceRecord.id).where(m.RceSourceRecord.source_intake_id == ids["intake_id"]))
    ))).scalars().first()
    assert review is not None and review.reviewer_resolution is None
    det_at, qa_at = datetime(2026, 9, 15, 4, 36, 37), datetime(2026, 9, 15, 5, 2, 31)
    rolled_back_db.add(reg.ReviewDecisionEvent(
        id=uuid.uuid4(), review_id=review.review_id, sequence_number=1, event_type="ANALYST_DETERMINATION",
        actor_user_id=uuid.uuid4(), actor_email="analyst@example.test", actor_role="reviewer",
        occurred_at=det_at, determination="CONFIRM", rationale="synthetic determination rationale"))
    rolled_back_db.add(reg.ReviewDecisionEvent(
        id=uuid.uuid4(), review_id=review.review_id, sequence_number=2, event_type="QA_REVIEW",
        actor_user_id=uuid.uuid4(), actor_email="qa@example.test", actor_role="qalead",
        occurred_at=qa_at, qa_action="APPROVE", qa_reason="synthetic QA", rationale="synthetic QA"))
    review.reportable_at = qa_at
    await rolled_back_db.flush()

    result = await _generate(rolled_back_db, job_id=str(ids["job_id"]))
    an = result["dataset"]["analyst"]
    row = next(r for r in an["review_records"] if r["review_id"] == review.review_id)
    assert row["resolution"] == "CONFIRM" and row["determination_source"] == "decision_events"
    assert row["qa_state"] == "APPROVED" and row["reportable_at"].startswith("2026-09-15T05:02:31")
    assert an["counts"]["qa_approved"] == 1 and an["counts"]["claimed"] == 0 and an["counts"]["qa_pending"] == 0
    html = result["html"]
    assert "Classified under rule" in html and "Approved 2026-09-15 05:02:31" in html
    assert "No determination yet" not in html.split("Review records")[1].split("</table>")[0]
    # the CSV annex agrees with the dataset it was built from
    sec = _csv_sections(result["csv"] if "csv" in result else "")
    if sec:
        c = next(v for k, v in sec.items() if k.startswith("Annex C"))
        assert any(r[0] == review.review_id and r[4] == "CONFIRM" and r[7] == "APPROVED" for r in c)


# ── the template must still render STORED datasets written before these fields existed ─────────────────────────
@pytest.mark.parametrize("name", ["060", "061", "067", "068"])
def test_stored_datasets_from_before_this_change_still_render(name):
    """A stored report is regenerated from its stored dataset on download. Those datasets carry no `answered_pct`,
    `determination_source` or `qa_state`; the template must tolerate that (strict undefined) and say nothing it
    cannot know: Answered shows a dash, never a number."""
    import copy
    import json
    from pathlib import Path

    from app.reports.branding import current_branding
    from app.reports.engine.template_engine import render_html
    from app.reports.generator import _marking_context

    d = copy.deepcopy(json.loads((Path(__file__).parent / "data" / "delivery_report" / f"ds_{name}.json")
                                 .read_text(encoding="utf-8")))
    ds, snap = d["dataset"], d["snapshot"]
    assert all("answered_pct" not in s_ for s_ in ds["verification"]["sources"])
    ctx = {k: v for k, v in ds.items() if k not in ("chart_list", "service_version", "review_cycle_id")}
    brand = current_branding()
    ctx.update(chart_images={}, branding={**brand.to_dict(), "agt_logo": brand.agt_logo,
                                          "government_logo": brand.government_logo},
               pdf_author=brand.prepared_by, pdf_keywords="t", document_status="Draft", reviewed_by=None,
               progress=None, annex=None)
    html = render_html("delivery_processing.html", {**ctx, "snapshot": snap, **_marking_context(snap)})
    assert "Classified under rule" in html and "Answered" in html


def test_reconstructed_071_payload_renders_the_corrected_facts():
    """`ds_071r.json` is rebuilt from the REGISTERED artifact of DA-ARC-2026-071 (its rendered findings, coverage and review
    rows) plus the decision-event evidence read from DEV for REV-2026-000248. It is NOT the frozen database payload (that
    needs a database read). It pins the four corrected facts on the real report's own numbers."""
    import copy
    import json
    from pathlib import Path

    from app.reports.branding import current_branding
    from app.reports.engine.csv_engine import delivery_processing_to_csv
    from app.reports.engine.template_engine import render_html
    from app.reports.generator import _marking_context

    d = copy.deepcopy(json.loads((Path(__file__).parent / "data" / "delivery_report" / "ds_071r.json")
                                 .read_text(encoding="utf-8")))
    ds, snap = d["dataset"], d["snapshot"]
    ctx = {k: v for k, v in ds.items() if k not in ("chart_list", "service_version", "review_cycle_id")}
    brand = current_branding()
    ctx.update(chart_images={}, branding={**brand.to_dict(), "agt_logo": brand.agt_logo,
                                          "government_logo": brand.government_logo},
               pdf_author=brand.prepared_by, pdf_keywords="t", document_status="Draft", reviewed_by=None, progress=None, annex=None)
    html = render_html("delivery_processing.html", {**ctx, "snapshot": snap, **_marking_context(snap)})
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    i = text.index("REV-2026-000248")
    row = text[i:i + 300]
    assert "CONFIRM" in row and "Approved 2026-09-15 05:02:31" in row and "Pending" not in row
    sam = [s_ for s_ in ds["verification"]["sources"] if s_["name"] == "sam"][0]
    assert sam["coverage_pct"] == 100.0 and sam["answered_pct"] == 0.0 and sam["unavailable"] == 4
    assert "Classified under rule" in html and "(v2)" in html and snap["b1_b4_rule_version"] == "3"
    csv_text = delivery_processing_to_csv(ds, "DA-ARC-2026-071R", "2026-10-10T00:00:00", rule_set_version=3)
    assert "## Annex A" in csv_text and "## Annex B" in csv_text and "## Annex C" in csv_text
    assert csv_text.count("\r\nDQ-") == 14                       # every one of the 14 findings is in Annex A


def test_frozen_071_payload_never_prints_pending_beside_an_approval():
    """`ds_071.json` is the ORIGINAL stored payload of DA-ARC-2026-071 as read from the DEV database (synthetic data, retrieved
    2026-10-10 under a temporary read-only firewall rule). It predates the decision-event fields: REV-2026-000248 carries
    `reportable_at` but `resolution` is null and nothing says who determined it. A report regenerated from it must not print
    "Pending" or "No determination yet" beside the approval; it says what the stored dataset cannot tell."""
    import copy
    import json
    import re
    from pathlib import Path

    from app.reports.branding import current_branding
    from app.reports.engine.csv_engine import delivery_processing_to_csv
    from app.reports.engine.template_engine import render_html
    from app.reports.generator import _marking_context

    d = copy.deepcopy(json.loads((Path(__file__).parent / "data" / "delivery_report" / "ds_071.json").read_text(encoding="utf-8")))
    ds, snap = d["dataset"], d["snapshot"]
    assert ds["analyst"]["counts"]["claimed"] == 3 and ds["analyst"]["counts"]["qa_approved"] == 1   # the stored (legacy) double count
    ctx = {k: v for k, v in ds.items() if k not in ("chart_list", "service_version", "review_cycle_id")}
    brand = current_branding()
    ctx.update(chart_images={}, branding={**brand.to_dict(), "agt_logo": brand.agt_logo, "government_logo": brand.government_logo},
               pdf_author=brand.prepared_by, pdf_keywords="t", document_status="Draft", reviewed_by=None, progress=None, annex=None)
    html = render_html("delivery_processing.html", {**ctx, "snapshot": snap, **_marking_context(snap)})
    text = re.sub(r"<[^>]+>", " ", html)
    row = text[text.index("REV-2026-000248"):][:300]
    assert "Not recorded in this stored dataset" in row and "Approved 2026-09-15 05:02:31" in row
    assert "Pending" not in row and "No determination yet" not in row
    other = text[text.index("REV-2026-000251"):][:300]
    assert "No determination yet" in other                       # negative control: unapproved rows keep the plain wording
    csv_text = delivery_processing_to_csv(ds, "DA-ARC-2026-071F", "2026-10-10T00:00:00", rule_set_version=3)
    assert "Not recorded in this stored dataset" in csv_text
