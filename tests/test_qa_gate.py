"""
The QA gate (B2 / Phase 3) — immutable analyst and QA decision events.

WHAT THESE PIN
──────────────
That nothing is ever overwritten, that an analyst cannot QA their own
determination, that a determination cannot become reportable without a standing
QA approval, and that the 43 existing system recommendations gain no fabricated
human history.

WHAT THEY DELIBERATELY DO NOT PIN
Any queue or tier behaviour. Which tier receives which bucket is Decision D3 and
is unresolved; this module records decisions and assigns no work.

The pure-logic tests run without a database. The enforcement tests that exercise
the SoD trigger and the CHECK constraints need Postgres and skip with a named
reason when there is none — the pattern this suite already uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.tefca_registry import models as reg
from app.tefca_registry.qa_gate import (
    MIN_RATIONALE,
    ROLE_ANALYST,
    ROLE_QA,
    ROLE_SOD_EXCEPTION,
    ROLE_SUPERSEDE,
    QaGateRefused,
    effective_determination,
    history,
    is_reportable,
)

E = reg.ReviewDecisionEvent


def _event(seq, event_type, actor, *, qa_action=None, determination=None,
           bucket=None, supersedes=None):
    """An in-memory event. No database, no session."""
    return E(
        id=uuid.uuid4(), review_id="REV-2026-000001", sequence_number=seq,
        event_type=event_type, actor_user_id=actor,
        actor_email=f"{actor}@example.gov", actor_role="reviewer",
        occurred_at=datetime(2026, 8, 25, 10, seq),
        determination=determination, determined_bucket=bucket,
        rationale="a rationale long enough to satisfy the constraint",
        qa_action=qa_action, qa_reason="a qa reason long enough",
        supersedes_decision_id=supersedes,
        supersession_reason="superseded for a stated reason" if supersedes else None,
    )


ANALYST = uuid.uuid4()
QA = uuid.uuid4()
PM = uuid.uuid4()


# ── reportability ────────────────────────────────────────────────────────────

def test_system_recommendation_alone_is_not_reportable():
    """The 43 existing rows have no events. None may be reportable."""
    assert is_reportable([]) is False
    assert effective_determination([]) is None


def test_analyst_determination_alone_is_not_reportable():
    events = [_event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM")]
    assert is_reportable(events) is False


def test_qa_approve_makes_it_reportable():
    events = [
        _event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM"),
        _event(2, E.QA_REVIEW, QA, qa_action=E.QA_APPROVE),
    ]
    assert is_reportable(events) is True


def test_qa_return_does_not_make_it_reportable():
    events = [
        _event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM"),
        _event(2, E.QA_REVIEW, QA, qa_action=E.QA_RETURN),
    ]
    assert is_reportable(events) is False


def test_return_then_new_determination_requires_fresh_qa():
    """The RETURN loop. A prior approval cannot carry over to a new decision."""
    events = [
        _event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM"),
        _event(2, E.QA_REVIEW, QA, qa_action=E.QA_RETURN),
        _event(3, E.ANALYST_DETERMINATION, ANALYST, determination="RECLASSIFY",
               bucket="B2"),
    ]
    assert is_reportable(events) is False, "the new determination has no QA yet"
    events.append(_event(4, E.QA_REVIEW, QA, qa_action=E.QA_APPROVE))
    assert is_reportable(events) is True


def test_approval_is_revoked_by_a_later_return():
    events = [
        _event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM"),
        _event(2, E.QA_REVIEW, QA, qa_action=E.QA_APPROVE),
        _event(3, E.QA_REVIEW, QA, qa_action=E.QA_RETURN),
    ]
    assert is_reportable(events) is False


def test_escalation_does_not_make_it_reportable():
    events = [
        _event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM"),
        _event(2, E.QA_REVIEW, QA, qa_action=E.QA_ESCALATE),
    ]
    assert is_reportable(events) is False


# ── nothing is overwritten ───────────────────────────────────────────────────

def test_supersession_preserves_the_original_event():
    first = _event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM")
    events = [
        first,
        _event(2, E.QA_REVIEW, QA, qa_action=E.QA_ESCALATE),
        _event(3, E.SUPERSEDING_DETERMINATION, PM, determination="RECLASSIFY",
               bucket="B3", supersedes=first.id),
    ]
    effective = effective_determination(events)
    assert effective["determined_bucket"] == "B3"
    assert effective["event_type"] == E.SUPERSEDING_DETERMINATION

    # The superseded event is still present, still readable, and marked.
    chain = history(events)
    assert len(chain) == 3
    original = next(h for h in chain if h["sequence_number"] == 1)
    assert original["is_superseded"] is True
    assert original["determination"] == "CONFIRM", "the original text is intact"
    assert original["actor_email"] == f"{ANALYST}@example.gov"


def test_history_includes_superseded_events():
    """Precedence is expressed, never concealed."""
    first = _event(1, E.ANALYST_DETERMINATION, ANALYST, determination="CONFIRM")
    events = [first,
              _event(2, E.SUPERSEDING_DETERMINATION, PM, determination="CONFIRM",
                     supersedes=first.id)]
    chain = history(events)
    assert [h["sequence_number"] for h in chain] == [1, 2]
    assert sum(1 for h in chain if h["is_superseded"]) == 1


def test_there_is_no_override_field_or_modify_action():
    """A decision is superseded by a new event, never modified in place."""
    columns = set(E.__table__.columns.keys())
    assert not any("override" in c for c in columns)
    assert "supersedes_decision_id" in columns
    assert "supersession_reason" in columns
    # MODIFY is not part of the vocabulary.
    assert {E.QA_APPROVE, E.QA_RETURN, E.QA_ESCALATE} == {"APPROVE", "RETURN", "ESCALATE"}


def test_actor_role_is_captured_on_the_event():
    """The authority a decision was made under must survive a later role change."""
    assert "actor_role" in E.__table__.columns
    assert E.__table__.columns["actor_role"].nullable is False


# ── schema invariants ────────────────────────────────────────────────────────

def test_database_enforces_the_decision_invariants():
    names = {c.name for c in E.__table__.constraints if getattr(c, "name", None)}
    for expected in ("ck_review_event_qa_action",
                     "ck_review_event_qa_action_vocab",
                     "ck_review_event_escalation_complete",
                     "ck_review_event_supersession_reason",
                     "ck_review_event_rationale",
                     "ck_review_event_type",
                     "uq_review_event_seq"):
        assert expected in names, f"{expected} is not enforced by the database"


def test_reportable_at_is_nullable_with_no_default():
    """NULL on all 43 existing rows, correctly — the gate is not back-dated."""
    col = reg.ReviewRecord.__table__.columns.get("reportable_at")
    assert col is not None
    assert col.nullable is True
    assert col.server_default is None


def test_rbac_ladder_matches_the_approved_design():
    from app.core.security import ROLE_HIERARCHY

    assert ROLE_HIERARCHY[ROLE_ANALYST] == 4
    assert ROLE_HIERARCHY[ROLE_QA] == 6
    assert ROLE_HIERARCHY[ROLE_SUPERSEDE] == 7
    assert ROLE_HIERARCHY[ROLE_SOD_EXCEPTION] == 8
    # QA sits ABOVE the analyst, and superseding above QA.
    assert ROLE_HIERARCHY[ROLE_QA] > ROLE_HIERARCHY[ROLE_ANALYST]
    assert ROLE_HIERARCHY[ROLE_SUPERSEDE] > ROLE_HIERARCHY[ROLE_QA]


def test_endpoints_are_registered_and_role_gated():
    from app.tefca_registry.review_routes import router

    paths = {r.path for r in router.routes}
    assert "/api/tefca/arc/reviews/{review_id}/determination" in paths
    assert "/api/tefca/arc/reviews/{review_id}/qa" in paths
    assert "/api/tefca/arc/reviews/{review_id}/supersede" in paths
    assert "/api/tefca/arc/reviews/{review_id}/history" in paths
    # NOT under /reviews/ — `/reviews/{review_id}` is registered earlier and
    # would swallow the literal path.
    assert "/api/tefca/arc/qa-queue" in paths
    assert "/api/tefca/arc/reviews/qa-queue" not in paths


def test_min_rationale_matches_the_check_constraint():
    assert MIN_RATIONALE == 10


# ── database-backed enforcement ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sod_trigger_refuses_self_review(db_required):
    """The database refuses a QA event from the analyst, not just the service.

    This is the check that survives a future code path bypassing qa_gate.
    """
    import sqlalchemy as sa
    from app.core.database import async_session_maker

    async with async_session_maker() as session:
        review_id = (await session.execute(
            sa.text("select review_id from review_records order by review_id limit 1")
        )).scalar()
        if review_id is None:
            pytest.skip("no review_records rows to exercise the trigger against")

        actor = uuid.uuid4()
        base = dict(review_id=review_id, actor_user_id=actor,
                    actor_email="analyst@example.gov", actor_role="reviewer",
                    rationale="a rationale long enough to pass the constraint")
        try:
            await session.execute(sa.text(
                "insert into review_decision_events "
                "(id, review_id, sequence_number, event_type, actor_user_id, "
                " actor_email, actor_role, rationale) "
                "values (:id, :review_id, 9001, 'ANALYST_DETERMINATION', "
                ":actor_user_id, :actor_email, :actor_role, :rationale)"),
                {"id": uuid.uuid4(), **base})

            with pytest.raises(Exception) as exc:
                await session.execute(sa.text(
                    "insert into review_decision_events "
                    "(id, review_id, sequence_number, event_type, actor_user_id, "
                    " actor_email, actor_role, rationale, qa_action, qa_reason) "
                    "values (:id, :review_id, 9002, 'QA_REVIEW', :actor_user_id, "
                    ":actor_email, :actor_role, :rationale, 'APPROVE', 'ok reason')"),
                    {"id": uuid.uuid4(), **base})
            assert "segregation of duties" in str(exc.value).lower()
        finally:
            await session.rollback()


@pytest.mark.asyncio
async def test_no_fabricated_history_for_existing_determinations(db_required):
    """The 43 system recommendations must have no events and no reportable_at."""
    import sqlalchemy as sa
    from app.core.database import async_session_maker

    async with async_session_maker() as session:
        events = (await session.execute(
            sa.text("select count(*) from review_decision_events"))).scalar()
        reportable = (await session.execute(sa.text(
            "select count(*) from review_records where reportable_at is not null"
        ))).scalar()
        resolved = (await session.execute(sa.text(
            "select count(*) from review_records where reviewer_resolution is not null"
        ))).scalar()
    assert events == 0, "no human decision may be fabricated for historical rows"
    assert reportable == 0, "the QA gate must not be back-dated"
    assert resolved == 0
