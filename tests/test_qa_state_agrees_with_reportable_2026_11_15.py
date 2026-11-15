"""Independent guard (qa_gate.py was in no CodeRabbit diff): over EVERY short event sequence, `qa_state` says APPROVED exactly when
`is_reportable` says the determination in force is reportable, and the `standing_qa_event` marker is set on exactly the QA event
that is the last word on that determination. Pure, synthetic."""
import itertools
import uuid
from types import SimpleNamespace

import pytest

from app.tefca_registry import qa_gate as g
E = g.E

pytestmark = pytest.mark.regression


def _ev(seq, kind, supersedes=None, qa=None):
    return SimpleNamespace(id=uuid.uuid4(), sequence_number=seq, event_type=kind, supersedes_decision_id=supersedes, qa_action=qa,
                           actor_email="x@synthetic-test.docuaction.invalid", actor_role="reviewer", actor_user_id=None,
                           determination="CONFIRM", determined_bucket="B2", rationale="synthetic", occurred_at=None, reason=None,
                           qa_reason=None, escalated_to_user_id=None, escalation_reason=None, sod_exception_granted_by=None, supersession_reason=None)


def _sequences():
    steps = ["DET", "SUPER", "APPROVE", "RETURN", "ESCALATE"]
    for n in range(0, 6):
        for combo in itertools.product(steps, repeat=n):
            yield combo


def _build(combo):
    events, last_det = [], None
    for i, step in enumerate(combo, start=1):
        if step == "DET":
            e = _ev(i, E.ANALYST_DETERMINATION)
            last_det = e
        elif step == "SUPER":
            e = _ev(i, E.SUPERSEDING_DETERMINATION, supersedes=(last_det.id if last_det else None))
            last_det = e
        else:
            e = _ev(i, E.QA_REVIEW, qa={"APPROVE": E.QA_APPROVE, "RETURN": E.QA_RETURN, "ESCALATE": E.QA_ESCALATE}[step])
        events.append(e)
    return events


def test_qa_state_and_the_standing_marker_agree_with_is_reportable_for_every_short_sequence():
    checked = 0
    for combo in _sequences():
        events = _build(combo)
        state = g.qa_state(events)
        assert (state == g.QA_STATE_APPROVED) == g.is_reportable(events), combo
        marks = [h for h in g.history(events) if h["standing_qa_event"]]
        determination = g._latest_determination(events)
        after = g._qa_after(events, determination) if determination else []
        assert len(marks) == (1 if after else 0), combo
        if after:
            assert marks[0]["sequence_number"] == after[-1].sequence_number, combo
        checked += 1
    assert checked > 3000
