"""CHARACTERIZATION of the PM report-release endpoint as it behaves TODAY (2026-10-15). Not a statement of the required behavior.

These tests pin down what `POST /api/reports/{id}/release` actually does so that a later, approved change (decision D6: refuse self-release,
call the release gates) shows up as a deliberate, reviewed test change rather than a silent one. They assert nothing about whether the
current behavior is right:

  * the only checks on the path are the role dependency and the status machine; `release_gates.evaluate` is not called anywhere;
  * the report's generator may perform both release steps (PM_REVIEWED, then READY_FOR_DELIVERY);
  * every decision appends a history entry naming the actor and writes one audit row;
  * an illegal transition is refused (409) and nothing is written.

No database: `_stored` and the session are replaced by small stand-ins.
"""
import inspect
from types import SimpleNamespace

import pytest

from app.reports import routes

pytestmark = pytest.mark.asyncio


class _Db:
    def __init__(self):
        self.added, self.commits = [], 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _row(generated_by="pm@example.test"):
    return SimpleNamespace(report_type="delivery_processing", report_data={"generated_by": generated_by, "dataset": {"k": 1}})


@pytest.fixture
def stored(monkeypatch):
    row = _row()

    async def _stored(db, report_id):
        return row

    monkeypatch.setattr(routes, "_stored", _stored)
    import sqlalchemy.orm.attributes as attrs
    monkeypatch.setattr(attrs, "flag_modified", lambda *a, **k: None)   # the stand-in row is not an ORM instance
    return row


async def _release(db, user, action, note=""):
    req = routes.ReleaseRequest(action=action, note=note)
    return await routes.post_release("DA-ARC-2026-999", req, db=db, user=user)


async def test_generator_can_perform_both_release_steps_today(stored):
    pm = SimpleNamespace(id=1, email="pm@example.test")           # the same identity that generated the report
    db = _Db()
    first = await _release(db, pm, "PM_REVIEWED", "looked")
    second = await _release(db, pm, "READY_FOR_DELIVERY", "send")
    assert first["release"]["status"] == "PM_REVIEWED" and second["release"]["status"] == "READY_FOR_DELIVERY"
    history = second["release"]["history"]
    assert [h["actor"] for h in history] == ["pm@example.test", "pm@example.test"]   # no independence rule exists today
    assert stored.report_data["dataset"] == {"k": 1}                                   # only the release block is written
    assert db.commits == 2 and len(db.added) == 2 and {a.action for a in db.added} == {
        "REPORT_RELEASE_PM_REVIEWED", "REPORT_RELEASE_READY_FOR_DELIVERY"}


async def test_release_gates_are_not_called_on_the_release_path():
    source = inspect.getsource(routes.post_release)
    assert "release_gates" not in source and "evaluate(" not in source
    assert "release_gates" not in inspect.getsource(routes)          # nothing in the routes module imports or calls them


async def test_illegal_transition_is_refused_and_writes_nothing(stored):
    from fastapi import HTTPException

    pm = SimpleNamespace(id=1, email="pm@example.test")
    db = _Db()
    with pytest.raises(HTTPException) as exc:
        await _release(db, pm, "READY_FOR_DELIVERY")                  # a DRAFT cannot jump to READY
    assert exc.value.status_code == 409
    assert db.commits == 0 and db.added == [] and "release" not in stored.report_data


async def test_release_endpoint_requires_the_program_manager_role():
    dep = inspect.signature(routes.post_release).parameters["user"].default
    assert dep.dependency.__qualname__.startswith("require_role")      # the single access control on the path
