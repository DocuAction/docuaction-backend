"""SoD exception fail-closed - proof through the REAL route and the REAL service.

`tests/test_sod_exception_fail_closed.py` calls `qa_gate.submit_qa_review`
directly and keeps a copy of the pre-change gate logic "for contrast". A copy of
old logic proves nothing about the code that ships. This file instead sends HTTP
requests through the FastAPI app (`POST /api/tefca/arc/reviews/{id}/qa`), so the
real JWT decode, the real `require_role("qalead")` dependency, the real
`review_routes.submit_qa` route and the real `qa_gate.submit_qa_review` all run.
Nothing in `qa_gate` or `review_routes` is monkeypatched.

Only the database session is replaced (`app.core.database.get_db` dependency
override, the repo's established pattern, see test_pecos_retry_plan_endpoint.py).
The stand-in is a small STATEFUL in-memory store that answers exactly the four
statements the code path issues (user lookup, review lookup, event list, max
sequence) and records what is added. It does not implement the database trigger;
the trigger is covered separately by the database migration tests.

The same file is meant to be run against `main` (bc5f7d71) to show the defect: the
self-QA-with-supplied-grantor cases are ACCEPTED (HTTP 200) there. Constants are
therefore spelled out as strings and nothing is imported from `qa_gate` that only
exists on the fix branch.

DB-free. No real account, no credential, no network.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.tefca_registry import models as reg

E = reg.ReviewDecisionEvent
URL = "/api/tefca/arc/reviews/{rid}/qa"
RID = "REV-2026-000001"
LONG = "a reason that is comfortably longer than ten characters"

SELF_QA = "SOD_SELF_QA"
NOT_AUTHORIZED = "SOD_EXCEPTION_NOT_AUTHORIZED"


class _Result:
    """The slice of a SQLAlchemy Result the code path touches."""

    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = list(rows or []), scalar

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class Store:
    """In-memory stand-in for the four tables the QA route reads and writes."""

    def __init__(self, users, review, events):
        self.users = users              # {uuid: SimpleNamespace}
        self.review = review
        self.events = list(events)
        self.audit = []                 # TefcaRegAuditLog rows staged by the service
        self.commits = 0
        self.gets = []                  # every db.get(...) the code path issued


class Session:
    def __init__(self, store, caller_id):
        self.s, self.caller_id = store, caller_id

    async def execute(self, stmt, *a, **k):
        sql = str(stmt).lower()
        if "max(review_decision_events.sequence_number)" in sql:
            seq = max((e.sequence_number for e in self.s.events), default=0)
            return _Result(scalar=seq)
        if "from review_decision_events" in sql:
            return _Result(sorted(self.s.events, key=lambda e: e.sequence_number))
        if "from review_records" in sql:
            return _Result([self.s.review])
        if "from users" in sql:
            return _Result([self.s.users[self.caller_id]])
        raise AssertionError(f"unexpected statement on the QA path: {sql[:120]}")

    async def get(self, model, key):
        self.s.gets.append((model.__name__, key))
        return None

    def add(self, obj):
        if isinstance(obj, E):
            self.s.events.append(obj)
        else:
            self.s.audit.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.s.commits += 1

    async def rollback(self):
        pass

    async def close(self):
        pass


def _user(role, **kw):
    return SimpleNamespace(id=uuid.uuid4(), email=f"{role}-{uuid.uuid4().hex[:6]}@example.invalid",
                           role=role, is_active=True, status="active",
                           tokens_revoked_at=None, **kw)


def _determination(actor, seq=1):
    return E(id=uuid.uuid4(), review_id=RID, sequence_number=seq,
             event_type=E.ANALYST_DETERMINATION, actor_user_id=actor.id,
             actor_email=actor.email, actor_role="reviewer",
             determination="CONFIRM", rationale=LONG,
             occurred_at=datetime(2026, 10, 1))


@pytest.fixture
def world(client):
    """Build a store and return `post(caller, body)`; clears the override after."""
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app

    state = {}

    def build(analyst, extra_users=()):
        review = SimpleNamespace(review_id=RID, entity_id=None, reportable_at=None)
        users = {u.id: u for u in (analyst, *extra_users)}
        store = Store(users, review, [_determination(analyst)])
        state["store"] = store
        return store

    def post(caller, body):
        store = state["store"]
        store.users[caller.id] = caller

        async def _override():
            yield Session(store, caller.id)

        app.dependency_overrides[get_db] = _override
        token = create_access_token({"sub": str(caller.id), "role": caller.role})
        return client.post(URL.format(rid=RID), json=body,
                           headers={"Authorization": f"Bearer {token}"})

    yield SimpleNamespace(build=build, post=post)
    app.dependency_overrides.pop(get_db, None)


def _body(action="RETURN", **extra):
    return {"qa_action": action, "qa_reason": LONG, **extra}


# (1) self-QA with no exception fields -------------------------------------------

def test_self_qa_without_exception_fields_is_refused(world):
    analyst = _user("qalead")          # a qalead who also made the determination
    store = world.build(analyst)
    r = world.post(analyst, _body("APPROVE"))
    assert r.status_code == 409, r.text
    assert r.headers["X-Denial-Code"] == SELF_QA
    assert "segregation of duties" in r.text
    assert len(store.events) == 1 and store.review.reportable_at is None
    assert store.audit == [] and store.commits == 0


# (2) self-QA with a supplied grantor of every kind -------------------------------

@pytest.mark.parametrize("grantor_kind", ["active_admin", "non_admin", "nonexistent"])
@pytest.mark.parametrize("action", ["APPROVE", "RETURN"])
def test_self_qa_with_supplied_grantor_is_refused(world, grantor_kind, action):
    analyst = _user("qalead")
    grantor = {"active_admin": _user("admin"), "non_admin": _user("viewer"),
               "nonexistent": None}[grantor_kind]
    grantor_id = grantor.id if grantor else uuid.uuid4()
    store = world.build(analyst, extra_users=[grantor] if grantor else [])
    r = world.post(analyst, _body(action, sod_exception_granted_by=str(grantor_id),
                                  sod_exception_reason=LONG))
    assert r.status_code == 409, r.text
    assert r.headers["X-Denial-Code"] == NOT_AUTHORIZED
    # The refusal must not depend on the grantor: nobody looked the grantor up.
    assert store.gets == []
    assert len(store.events) == 1, "no QA event may be created"
    assert store.review.reportable_at is None
    assert store.audit == [] and store.commits == 0


@pytest.mark.parametrize("extra", [
    {"sod_exception_reason": LONG},
    {"sod_exception_granted_by": str(uuid.uuid4())},
])
def test_self_qa_with_only_half_an_exception_is_refused(world, extra):
    analyst = _user("qalead")
    store = world.build(analyst)
    r = world.post(analyst, _body("APPROVE", **extra))
    assert r.status_code == 409, r.text
    assert r.headers["X-Denial-Code"] == NOT_AUTHORIZED
    assert len(store.events) == 1


def test_self_qa_naming_oneself_as_grantor_is_refused(world):
    analyst = _user("admin")
    store = world.build(analyst)
    r = world.post(analyst, _body("APPROVE", sod_exception_granted_by=str(analyst.id),
                                  sod_exception_reason=LONG))
    assert r.status_code == 409 and len(store.events) == 1
    assert r.headers["X-Denial-Code"] == NOT_AUTHORIZED


# (3) independent QA still works ---------------------------------------------------

@pytest.mark.parametrize("action,reportable", [("APPROVE", True), ("RETURN", False)])
def test_independent_qa_approve_and_return_work(world, action, reportable):
    analyst, qa = _user("reviewer"), _user("qalead")
    store = world.build(analyst)
    r = world.post(qa, _body(action))
    assert r.status_code == 200, r.text
    assert "X-Denial-Code" not in r.headers
    assert r.json()["qa_action"] == action
    assert (store.review.reportable_at is not None) is reportable
    ev = store.events[-1]
    assert ev.event_type == E.QA_REVIEW and ev.actor_user_id == qa.id
    assert ev.actor_user_id != store.events[0].actor_user_id
    assert store.commits == 1 and len(store.audit) == 1


# (4) unauthenticated grantor fields on independent QA are not persisted ----------

def test_independent_qa_does_not_persist_unauthenticated_grant(world):
    analyst, qa = _user("reviewer"), _user("qalead")
    admin = _user("admin")
    store = world.build(analyst, extra_users=[admin])
    r = world.post(qa, _body("APPROVE", sod_exception_granted_by=str(admin.id),
                             sod_exception_reason=LONG))
    assert r.status_code == 200, r.text
    ev = store.events[-1]
    assert ev.sod_exception_granted_by is None, "false grant attribution stored on event"
    assert ev.sod_exception_reason is None
    meta = store.audit[0].metadata_
    assert meta["sod_exception"] is False
    assert str(admin.id) not in repr(meta) and str(admin.id) not in repr(ev.__dict__)


# (5) the code describes the refusal accurately -----------------------------------

def test_codes_match_message_text(world):
    analyst = _user("qalead")
    world.build(analyst)
    plain = world.post(analyst, _body("APPROVE"))
    world.build(analyst)
    claimed = world.post(analyst, _body("APPROVE", sod_exception_granted_by=str(uuid.uuid4()),
                                        sod_exception_reason=LONG))
    assert plain.headers["X-Denial-Code"] == SELF_QA
    assert claimed.headers["X-Denial-Code"] == NOT_AUTHORIZED
    assert "self-attested" not in plain.text and "grant" not in plain.text.lower()
    assert "self-attested exception grant is not accepted" in claimed.text
    # Neither message invites the caller to retry with an exception.
    assert "requires an admin grant" not in plain.text + claimed.text


def test_unauthenticated_request_is_rejected_before_the_service(client):
    r = client.post(URL.format(rid=RID), json=_body("APPROVE"))
    assert r.status_code in (401, 403)


def test_a_viewer_cannot_reach_the_qa_route(world):
    analyst, viewer = _user("reviewer"), _user("viewer")
    store = world.build(analyst)
    r = world.post(viewer, _body("APPROVE"))
    assert r.status_code == 403 and len(store.events) == 1
