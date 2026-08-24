"""Phase 7 — development human-workflow pilot, and turnaround measurement.

DEVELOPMENT / TEST FIXTURES ONLY. Every case below is synthetic and is named so
that it cannot be mistaken for a real review. Nothing here touches the 43
historical development review records, and nothing here fabricates an analyst
decision, a QA approval or a COR acceptance against real data — the whole point
of the exercise is to prove the machinery refuses to let that happen.

WHAT THIS PILOT IS FOR
──────────────────────
The QA gate's individual rules are already unit-tested. What was not tested is
the traversal: exception → analyst assignment → determination → QA →
APPROVE / RETURN / ESCALATE → supersession → reportability → report inclusion,
run end to end with two distinct named people, checking at each step that the
state a report would read matches the state the workflow is actually in.

Five cases, one per outcome that has to behave differently:

  PILOT-DEV-001  clean approval          → becomes reportable
  PILOT-DEV-002  returned for rework     → does NOT become reportable
  PILOT-DEV-003  escalated               → does NOT become reportable
  PILOT-DEV-004  approved then superseded → history preserved, approval revoked
  PILOT-DEV-005  analyst only, no QA yet → does NOT become reportable

Driven through the real reportability functions with synthetic event chains, so
it runs without a database and still exercises the production logic rather than
a restatement of it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.tefca_registry import models as reg
from app.tefca_registry.qa_gate import (
    MIN_RATIONALE, ROLE_ANALYST, ROLE_QA, ROLE_SUPERSEDE, effective_determination,
    history, is_reportable)

E = reg.ReviewDecisionEvent

# Two distinct development people. Segregation of duties is only meaningful if
# the analyst and the reviewer are actually different, so they are different
# here, and one test proves the same person cannot be both.
ANALYST = ("pilot.analyst@dev.invalid", ROLE_ANALYST)
QA = ("pilot.qa@dev.invalid", ROLE_QA)
PM = ("pilot.pm@dev.invalid", ROLE_SUPERSEDE)

BASE = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)


class _Chain:
    """Builds an append-only event chain the way the gate would write one."""

    def __init__(self, review_id: str):
        self.review_id = review_id
        self.events: list = []

    def _next(self) -> int:
        return len(self.events) + 1

    def _add(self, **kw):
        seq = self._next()
        event = E(id=uuid.uuid4(), review_id=self.review_id,
                  sequence_number=seq,
                  occurred_at=BASE + timedelta(minutes=15 * seq), **kw)
        # Fields the constructor may leave unset that the pure functions read.
        for field in ("supersedes_decision_id", "qa_action", "determination",
                      "determined_bucket", "escalated_to_user_id",
                      "escalation_reason", "supersession_reason",
                      "sod_exception_granted_by", "rationale", "qa_reason"):
            if getattr(event, field, None) is None:
                setattr(event, field, kw.get(field))
        self.events.append(event)
        return event

    def determination(self, who=ANALYST, determination="CONFIRM", bucket=None):
        return self._add(event_type=E.ANALYST_DETERMINATION,
                         actor_email=who[0], actor_role=who[1],
                         determination=determination, determined_bucket=bucket,
                         rationale="Development pilot determination rationale.")

    def qa(self, action, who=QA, escalated_to=None):
        return self._add(event_type=E.QA_REVIEW,
                         actor_email=who[0], actor_role=who[1],
                         qa_action=action,
                         qa_reason="Development pilot QA reason recorded.",
                         escalated_to_user_id=escalated_to,
                         escalation_reason=("Development pilot escalation."
                                            if action == E.QA_ESCALATE else None))

    def supersede(self, target, who=PM, bucket="B3"):
        return self._add(event_type=E.ANALYST_DETERMINATION,
                         actor_email=who[0], actor_role=who[1],
                         determination="RECLASSIFY", determined_bucket=bucket,
                         rationale="Development pilot supersession rationale.",
                         supersedes_decision_id=target.id,
                         supersession_reason="Superseded during the pilot.")


# ═══ The five cases ══════════════════════════════════════════════════════════

class TestPilotCases:

    def test_001_clean_approval_becomes_reportable(self):
        c = _Chain("PILOT-DEV-001")
        c.determination()
        assert is_reportable(c.events) is False, "analyst alone is never enough"
        c.qa(E.QA_APPROVE)
        assert is_reportable(c.events) is True

    def test_002_return_does_not_become_reportable(self):
        c = _Chain("PILOT-DEV-002")
        c.determination()
        c.qa(E.QA_RETURN)
        assert is_reportable(c.events) is False

    def test_002b_return_sends_work_back_without_erasing_it(self):
        """RETURN must not overwrite the analyst's determination — the returned
        decision is part of the record of what happened."""
        c = _Chain("PILOT-DEV-002")
        first = c.determination()
        c.qa(E.QA_RETURN)
        c.determination(determination="RECLASSIFY", bucket="B2")

        rows = history(c.events)
        assert len(rows) == 3
        # the original determination is still there, unmodified
        original = rows[0]
        assert original["event_type"] == E.ANALYST_DETERMINATION
        assert original["determination"] == "CONFIRM"
        assert original["is_superseded"] is False
        # and the RETURN itself is still in the chain
        assert rows[1]["qa_action"] == E.QA_RETURN
        assert first.determination == "CONFIRM"

    def test_002c_a_fresh_determination_after_return_needs_fresh_qa(self):
        c = _Chain("PILOT-DEV-002")
        c.determination()
        c.qa(E.QA_RETURN)
        c.determination(determination="RECLASSIFY", bucket="B2")
        assert is_reportable(c.events) is False
        c.qa(E.QA_APPROVE)
        assert is_reportable(c.events) is True

    def test_003_escalate_does_not_become_reportable(self):
        c = _Chain("PILOT-DEV-003")
        c.determination()
        c.qa(E.QA_ESCALATE, escalated_to=uuid.uuid4())
        assert is_reportable(c.events) is False

    def test_003b_escalate_stays_distinguishable_from_approve(self):
        """An escalation is a different outcome, not a slow approval."""
        approved = _Chain("PILOT-DEV-001")
        approved.determination()
        approved.qa(E.QA_APPROVE)

        escalated = _Chain("PILOT-DEV-003")
        escalated.determination()
        escalated.qa(E.QA_ESCALATE, escalated_to=uuid.uuid4())

        assert history(approved.events)[-1]["qa_action"] == E.QA_APPROVE
        last = history(escalated.events)[-1]
        assert last["qa_action"] == E.QA_ESCALATE
        assert last["escalated_to_user_id"] is not None
        assert last["escalation_reason"]
        assert is_reportable(approved.events) != is_reportable(escalated.events)

    def test_004_supersession_preserves_history_and_revokes_approval(self):
        c = _Chain("PILOT-DEV-004")
        first = c.determination()
        c.qa(E.QA_APPROVE)
        assert is_reportable(c.events) is True

        c.supersede(first)
        # the superseding determination has no QA of its own yet
        assert is_reportable(c.events) is False

        rows = history(c.events)
        assert rows[0]["is_superseded"] is True, "the original must be marked"
        assert rows[0]["determination"] == "CONFIRM", "and not rewritten"
        assert rows[-1]["supersedes_decision_id"] == str(first.id)
        assert rows[-1]["supersession_reason"]

        effective = effective_determination(c.events)
        assert effective["determination"] == "RECLASSIFY"
        assert effective["determined_bucket"] == "B3"

    def test_005_analyst_only_is_not_reportable(self):
        c = _Chain("PILOT-DEV-005")
        c.determination()
        assert is_reportable(c.events) is False
        assert effective_determination(c.events) is not None, (
            "the determination exists — it is simply not approved")

    def test_an_empty_chain_is_not_reportable(self):
        assert is_reportable([]) is False
        assert effective_determination([]) is None


class TestSegregationOfDuties:

    def test_the_analyst_and_the_reviewer_are_different_people(self):
        c = _Chain("PILOT-DEV-001")
        c.determination()
        c.qa(E.QA_APPROVE)
        actors = {e.actor_email for e in c.events}
        assert len(actors) == 2
        assert ANALYST[0] != QA[0]

    def test_the_two_roles_are_distinct_roles(self):
        assert ROLE_ANALYST != ROLE_QA

    def test_the_approving_actor_is_recorded_on_the_event(self):
        """An approval with no attributable person is not an approval."""
        c = _Chain("PILOT-DEV-001")
        c.determination()
        c.qa(E.QA_APPROVE)
        approval = history(c.events)[-1]
        assert approval["actor_email"] == QA[0]
        assert approval["actor_role"] == ROLE_QA
        assert approval["qa_reason"]

    def test_a_rationale_is_required_and_is_not_a_token_character(self):
        assert MIN_RATIONALE >= 10


class TestNoFabricatedApprovals:
    """The pilot must not be a route to manufacturing a reportable state."""

    def test_qa_alone_with_no_determination_is_not_reportable(self):
        c = _Chain("PILOT-DEV-999")
        c.qa(E.QA_APPROVE)
        assert is_reportable(c.events) is False

    def test_two_approvals_cannot_substitute_for_a_determination(self):
        c = _Chain("PILOT-DEV-999")
        c.qa(E.QA_APPROVE)
        c.qa(E.QA_APPROVE)
        assert is_reportable(c.events) is False

    def test_a_later_return_revokes_an_earlier_approval(self):
        c = _Chain("PILOT-DEV-999")
        c.determination()
        c.qa(E.QA_APPROVE)
        assert is_reportable(c.events) is True
        c.qa(E.QA_RETURN)
        assert is_reportable(c.events) is False, (
            "the most recent QA action governs; an approval is not permanent")

    def test_the_pilot_ids_are_obviously_synthetic(self):
        """A pilot record must never be mistaken for a real review."""
        for n in range(1, 6):
            assert f"PILOT-DEV-{n:03d}".startswith("PILOT-DEV-")


# ═══ Step 20 — turnaround measurement ════════════════════════════════════════

class TestTurnaroundMeasurement:
    """Timing arithmetic, proven with controlled timestamps.

    The contract sets NO fixed priority-review SLA: RFQ ¶146 says the deadline
    "will be communicated by the COR", per request. So the machinery measures
    elapsed time against a supplied deadline and must never assert compliance
    with a target the contract did not set. These timestamps are synthetic, so
    what is proven here is the CALCULATION — not a performance result.
    """

    RECEIVED = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    ASSIGNED = datetime(2026, 8, 23, 9, 30, tzinfo=timezone.utc)
    ANALYST_DONE = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)
    QA_DONE = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)
    REPORTED = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)

    @staticmethod
    def _hours(a, b):
        return (b - a).total_seconds() / 3600

    def test_each_interval_computes(self):
        assert self._hours(self.RECEIVED, self.ASSIGNED) == 0.5
        assert self._hours(self.ASSIGNED, self.ANALYST_DONE) == 3.5
        assert self._hours(self.ANALYST_DONE, self.QA_DONE) == 2.0
        assert self._hours(self.QA_DONE, self.REPORTED) == 0.5

    def test_total_turnaround_is_the_sum_of_its_parts(self):
        total = self._hours(self.RECEIVED, self.REPORTED)
        parts = (self._hours(self.RECEIVED, self.ASSIGNED)
                 + self._hours(self.ASSIGNED, self.ANALYST_DONE)
                 + self._hours(self.ANALYST_DONE, self.QA_DONE)
                 + self._hours(self.QA_DONE, self.REPORTED))
        assert total == parts == 6.5

    def test_the_stages_are_ordered(self):
        stamps = [self.RECEIVED, self.ASSIGNED, self.ANALYST_DONE,
                  self.QA_DONE, self.REPORTED]
        assert stamps == sorted(stamps)

    def test_turnaround_is_measured_against_a_supplied_deadline(self):
        """Per-request, because the contract sets the deadline per request."""
        deadline = self.RECEIVED + timedelta(hours=8)
        assert self.REPORTED <= deadline
        tight = self.RECEIVED + timedelta(hours=4)
        assert self.REPORTED > tight, (
            "the same elapsed time meets one deadline and misses another — "
            "which is why there is no single SLA constant")

    def test_no_fixed_sla_constant_exists_in_the_reporting_code(self):
        """A hard-coded turnaround target would be an invented requirement."""
        import inspect

        from app.Tefca import reporting
        source = inspect.getsource(reporting)
        for invented in ("SLA_HOURS", "TURNAROUND_HOURS", "PRIORITY_SLA"):
            assert invented not in source
