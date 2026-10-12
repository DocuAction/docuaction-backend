"""SoD exception fail-closed (WP4-SOD). DB-free.

A QA actor who made the determination is refused even when the request body
names an exception grantor and reason: nothing proves the named admin consented
(CWE-863). Unconditional - no feature flag. Independent QA is unchanged.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.tefca_registry import models as reg
from app.tefca_registry import qa_gate

E = reg.ReviewDecisionEvent


def run(coro):
    return asyncio.run(coro)


class FakeDb:
    """Minimal async session stand-in; `get` returns a configurable user."""

    def __init__(self, grantor=None):
        self.grantor, self.added = grantor, []

    async def get(self, model, key):
        return self.grantor

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


def _setup(monkeypatch, analyst_id):
    """Patch the three DB reads so the only live logic is the gate itself."""
    det = E(id=uuid.uuid4(), review_id="REV-1", sequence_number=1,
            event_type=E.ANALYST_DETERMINATION, actor_user_id=analyst_id,
            actor_email="a@example.invalid", actor_role="reviewer",
            determination="CONFIRM", occurred_at=datetime(2026, 10, 1))
    review = SimpleNamespace(review_id="REV-1", entity_id=None, reportable_at=None)

    async def _rev(db, rid):
        return review

    async def _ev(db, rid):
        return [det]

    async def _seq(db, rid):
        return 2
    monkeypatch.setattr(qa_gate, "_review_or_refuse", _rev)
    monkeypatch.setattr(qa_gate, "_events", _ev)
    monkeypatch.setattr(qa_gate, "_next_sequence", _seq)
    monkeypatch.setattr(qa_gate.reg_audit, "record", lambda *a, **k: None)


def _qa(db, actor, action="RETURN", **kw):
    """Call submit_qa_review as `actor` (a qalead)."""
    return qa_gate.submit_qa_review(
        db, "REV-1",
        user=SimpleNamespace(id=actor, email="q@example.invalid", role="qalead"),
        qa_action=action, qa_reason="long enough reason", **kw)


def _legacy_decision(determination_actor, actor, granted_by, reason):
    """The PRE-CHANGE gate logic (main bc5f7d71), reproduced for contrast only.
    Returns True when the legacy gate would let the QA event through."""
    if determination_actor == actor:
        return bool(granted_by and reason and granted_by != actor)
    return True


def _user(role="admin", active=True):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_active=active,
                           status="active" if active else "disabled")


@pytest.mark.parametrize("grantor_kind", ["active_admin", "non_admin", "nonexistent"])
def test_self_qa_exception_is_refused_whatever_the_grantor(monkeypatch, grantor_kind):
    """Old behaviour: every UUID != actor was accepted (legacy helper -> True).
    Now: all three are refused with SOD_EXCEPTION_NOT_AUTHORIZED."""
    actor = uuid.uuid4()
    grantor = {"active_admin": _user("admin"), "non_admin": _user("viewer"),
               "nonexistent": None}[grantor_kind]
    grantor_id = grantor.id if grantor else uuid.uuid4()
    assert _legacy_decision(actor, actor, grantor_id, "a long reason") is True
    _setup(monkeypatch, analyst_id=actor)
    db = FakeDb(grantor=grantor)
    with pytest.raises(qa_gate.QaGateRefused) as e:
        run(_qa(db, actor, sod_exception_granted_by=grantor_id,
                sod_exception_reason="a long enough reason"))
    assert e.value.code == qa_gate.SOD_EXCEPTION_NOT_AUTHORIZED
    assert "segregation of duties" in str(e.value)
    assert db.added == []  # no QA event was created


def test_self_qa_without_exception_keeps_sod_self_qa_code(monkeypatch):
    actor = uuid.uuid4()
    _setup(monkeypatch, analyst_id=actor)
    with pytest.raises(qa_gate.QaGateRefused) as e:
        run(_qa(FakeDb(), actor))
    assert e.value.code == qa_gate.SOD_SELF_QA


def test_self_qa_with_only_a_reason_or_only_a_grantor_is_refused(monkeypatch):
    actor = uuid.uuid4()
    _setup(monkeypatch, analyst_id=actor)
    for kw in ({"sod_exception_reason": "a long enough reason"},
               {"sod_exception_granted_by": uuid.uuid4()}):
        with pytest.raises(qa_gate.QaGateRefused) as e:
            run(_qa(FakeDb(), actor, **kw))
        assert e.value.code == qa_gate.SOD_EXCEPTION_NOT_AUTHORIZED


@pytest.mark.parametrize("action", ["APPROVE", "RETURN"])
def test_independent_qa_is_unchanged(monkeypatch, action):
    """Actor differs from the determination actor: ordinary QA still succeeds."""
    analyst, qa_actor = uuid.uuid4(), uuid.uuid4()
    _setup(monkeypatch, analyst_id=analyst)
    db = FakeDb()
    out = run(_qa(db, qa_actor, action=action))
    assert out["qa_action"] == action
    assert out["reportable"] is (action == "APPROVE")
    assert len(db.added) == 1 and db.added[0].sod_exception_granted_by is None


def test_route_returns_409_with_denial_code_header(monkeypatch):
    """The machine code reaches the client in X-Denial-Code (409 unchanged)."""
    from app.tefca_registry import review_routes as rr

    async def _boom(*a, **k):
        raise qa_gate.QaGateRefused("segregation of duties: x",
                                    code=qa_gate.SOD_EXCEPTION_NOT_AUTHORIZED)
    monkeypatch.setattr(qa_gate, "submit_qa_review", _boom)
    req = rr.QaReview(qa_action="RETURN", qa_reason="long enough reason",
                      sod_exception_granted_by=uuid.uuid4(),
                      sod_exception_reason="a long enough reason")
    with pytest.raises(HTTPException) as e:
        run(rr.submit_qa("REV-1", req, SimpleNamespace(headers={}, client=None),
                         db=FakeDb(), user=SimpleNamespace()))
    assert e.value.status_code == 409
    assert e.value.headers["X-Denial-Code"] == "SOD_EXCEPTION_NOT_AUTHORIZED"
