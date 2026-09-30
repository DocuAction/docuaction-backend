"""Issue-level analyst decisions are part of the exception row.

Release-gate defect D (2026-09-30, synthetic delivery SEQ-012 A, request
d288b6bc-8ed0-4dbe-8839-5d757ab873b8): after `POST /issues/{id}/dispositions`
with DEFER the API answered 200, the issue moved to UNDER_REVIEW with
`resolved_at`/`resolved_by` set and `resolution_notes = "[DEFER] <reason>"` —
but the reason reached the exceptions row ONLY inside that string, and the
record-level `disposition_history` (rce_disposition_events) carried nothing,
because a non-resolving decision writes no record event. The drawer therefore
showed "Decided … by …" with no decision, comment or history.

Pinned here: every analyst decision is exposed on the row as `issue_decisions`
(decision, reason, actor, decided_at, correlation_id) from the audit trail, the
newest as `latest_decision`, and the parse of the issue's own notes is the
fallback when the audit trail has no row.
"""
from __future__ import annotations

import pytest
from support_delivery_api import headers_for, seed_delivery

from app.tefca_registry.rce.exception_ledger import latest_decision_from_notes

BASE = "/api/tefca/rce"


class TestNotesFallback:
    def test_transition_notes_parse_to_a_decision(self):
        got = latest_decision_from_notes(
            "[DEFER] SYNTHETIC: deferred pending evidence",
            actor="reviewer@synthetic.invalid", decided_at="2026-09-30T17:10:20+00:00")
        assert got == {"id": None, "decision": "DEFER",
                       "reason": "SYNTHETIC: deferred pending evidence",
                       "actor": "reviewer@synthetic.invalid",
                       "decided_at": "2026-09-30T17:10:20+00:00",
                       "correlation_id": None, "source": "issue"}

    def test_free_text_notes_are_not_a_decision(self):
        assert latest_decision_from_notes("no bracketed decision", actor="a", decided_at=None) is None
        assert latest_decision_from_notes(None, actor="a", decided_at=None) is None
        assert latest_decision_from_notes("", actor="a", decided_at=None) is None


@pytest.mark.usefixtures("db_required")
class TestIssueDecisionsOnTheRow:
    def test_a_non_resolving_decision_is_exposed_with_its_reason(self, client):
        d = seed_delivery(state="SUCCEEDED", issues=1)
        intake = d["intake_id"]
        before = client.get(f"{BASE}/deliveries/{intake}/exceptions",
                            headers=headers_for("reviewer")).json()["items"]
        assert before and before[0]["issue_decisions"] == [] and before[0]["latest_decision"] is None
        issue_id = before[0]["issue_id"]

        reason = "SYNTHETIC-QA-DISPLAY-CHECK: deferred to verify the row carries the decision"
        r = client.post(f"{BASE}/issues/{issue_id}/dispositions",
                        headers=headers_for("reviewer"),
                        json={"decision": "DEFER", "reason": reason})
        assert r.status_code == 200, r.text

        after = client.get(f"{BASE}/deliveries/{intake}/exceptions",
                           headers=headers_for("reviewer")).json()["items"]
        row = next(x for x in after if x["issue_id"] == issue_id)
        assert row["status"] == "UNDER_REVIEW"
        assert row["decided_at"] and row["actor"]
        # The record-level history is NOT where this decision lives …
        assert all(h.get("disposition") != "DEFER" for h in row["disposition_history"])
        # … the issue-level list is, with the full reason, the actor and the time.
        assert row["issue_decisions"], row
        latest = row["latest_decision"]
        assert latest == row["issue_decisions"][-1]
        assert latest["decision"] == "DEFER"
        assert latest["reason"] == reason
        assert latest["actor"] == row["actor"]
        assert latest["decided_at"]
        assert latest["source"] in ("audit", "issue")
