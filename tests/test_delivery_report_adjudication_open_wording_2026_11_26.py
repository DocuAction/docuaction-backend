"""The delivery report's "Did processing finish?" cell with and without `classification_may_continue` (2026-11-26).

* A STORED dataset from before 2026-11-26 has no such key. It must render exactly as it was issued (the
  "Records reconciled; N HIGH/CRITICAL finding(s) remain undecided." wording) - the absence is read as "not
  stated", never as permission to continue classification or to release.
* An outcome built today states the key explicitly: False everywhere except the one adjudication-only branch.
* Only an explicit True shows the adjudication-open detail, which discloses the open count and that the delivery
  cannot be approved.
* A non-boolean value is not treated as True.
"""
from __future__ import annotations

import copy
import re

from app.reports.engine.template_engine import render_html
from app.tefca_registry.rce import status_model
from test_delivery_report_layout_2026_10_07 import _fixture, _render_context

LEGACY = "Records reconciled; 1 HIGH/CRITICAL finding(s) remain undecided."


def _cell(html: str) -> str:
    match = re.search(r"<b>Did processing finish\?</b></td>\s*<td>.*?</td>\s*<td class=\"q\">(.*?)</td>", html, re.S)
    assert match, "the 'Did processing finish?' row is missing"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _html(d):
    return render_html("delivery_processing.html", _render_context(d))


def _open_high(d) -> int:
    return d["dataset"]["findings"]["open_high"]


def test_a_stored_dataset_without_the_key_renders_as_issued():
    d = _fixture("061")
    outcome = d["dataset"]["outcome"]
    assert "classification_may_continue" not in outcome and outcome["code"] == "COMPLETED_WITH_EXCEPTIONS"
    assert _cell(_html(d)) == (f"Records reconciled; {_open_high(d)} HIGH/CRITICAL finding(s) remain undecided.")


def test_the_other_stored_datasets_render_without_error():
    for name in ("060", "067", "068", "071", "071r"):
        d = _fixture(name)
        assert "classification_may_continue" not in d["dataset"]["outcome"], name
        _cell(_html(d))                                         # StrictUndefined: would raise on a missing key


def test_an_explicit_false_reads_exactly_like_a_stored_dataset():
    legacy = _fixture("061")
    stated = copy.deepcopy(legacy)
    stated["dataset"]["outcome"]["classification_may_continue"] = False
    assert _cell(_html(stated)) == _cell(_html(legacy))


def test_only_an_explicit_true_shows_the_adjudication_detail():
    d = _fixture("061")
    detail = ("Processing completed and every population closes. Reconciliation has NOT passed: 4 default-routed "
              "determination(s) await an analyst resolution. Classification may continue - adjudication open (4). "
              "The delivery cannot be approved until they are adjudicated.")
    d["dataset"]["outcome"].update(classification_may_continue=True, detail=detail, adjudication_open=4,
                                   approvable=False)
    cell = _cell(_html(d))
    assert cell == detail
    assert "adjudication open (4)" in cell and "cannot be approved" in cell


def test_a_non_boolean_value_is_not_permission():
    for value in ("true", 1, "yes", None):
        d = _fixture("061")
        d["dataset"]["outcome"].update(classification_may_continue=value, detail="SHOULD NOT SHOW")
        assert _cell(_html(d)) == f"Records reconciled; {_open_high(d)} HIGH/CRITICAL finding(s) remain undecided."


def test_every_outcome_built_today_states_the_key():
    built = [
        status_model.processing_outcome(job_state="FAILED", job_stage="PARSING", snapshot=None),
        status_model.processing_outcome(job_state="SUCCEEDED", job_stage="READY_FOR_REVIEW",
                                        snapshot={"id": "s", "passed": True,
                                                  "equation": {"received": 1, "accounted": 1}}),
        status_model.processing_outcome(job_state="SUCCEEDED", job_stage="RECONCILIATION",
                                        snapshot={"id": "s", "passed": False,
                                                  "failure_reason": "populations do not close",
                                                  "checks": [{"check": "A: every delivered line stored",
                                                              "passed": False}],
                                                  "equation": {"received": 2, "accounted": 1}}),
    ]
    for out in built:
        assert out["classification_may_continue"] is False, out["code"]
