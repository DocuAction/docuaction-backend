"""GET /api/tefca/rce/iqvia/snapshots -- read-only snapshot discovery (IQ-01).

Two layers:

* Contract tests (no database): authz (401 / 403), the licensed-source gate,
  pagination bounds, the response shape and the append-only successor wording.
  The data-access helper is faked, so these run everywhere, every time.
* Query tests (real PostgreSQL; skipped with an explicit reason when no
  database is reachable -- the isolation-postgres CI job runs them).

Entirely synthetic ids and labels. Nothing here approves, imports or modifies a
snapshot: the only writes are the test's own fixture rows, removed afterwards.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.tefca_registry.rce import iqvia_routes as routes
from app.tefca_registry.rce import snapshot_models as sm

pytestmark = pytest.mark.regression

PATH = "/api/tefca/rce/iqvia/snapshots"


class _User:
    def __init__(self, role):
        self.id = str(uuid.uuid4()); self.email = f"{role}@test.local"; self.role = role
        self.is_active = True; self.status = "active"; self.tokens_revoked_at = None


class _Result:
    def __init__(self, user): self._user = user
    def scalar_one_or_none(self): return self._user
    def scalar(self): return None


class _Session:
    """Auth lookups succeed; any write is a test failure."""
    def __init__(self, user): self._user = user
    async def execute(self, *a, **k): return _Result(self._user)
    async def commit(self): raise AssertionError("list must never write")
    async def rollback(self): return None
    async def close(self): return None
    def add(self, *a, **k): raise AssertionError("list must never write")


@pytest.fixture
def as_role(client, monkeypatch):
    from app.core.config import settings
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app

    monkeypatch.setattr(settings, "ENABLE_IQVIA_SOURCES", True, raising=False)

    def make(role):
        user = _User(role)

        async def _override():
            yield _Session(user)

        app.dependency_overrides[get_db] = _override
        token = create_access_token({"sub": user.id, "role": role}, is_admin=(role == "admin"))
        return lambda path=PATH, **kw: client.get(
            path, headers={"Authorization": f"Bearer {token}"}, **kw)

    yield make
    from app.core.database import get_db as _gd
    from app.main import app as _app
    _app.dependency_overrides.pop(_gd, None)


def _snap(**over):
    now = datetime(2026, 10, 8, 8, 40, tzinfo=timezone.utc)
    base = dict(
        id=uuid.uuid4(), source_system=sm.SOURCE_IQVIA_AFFILIATION, snapshot_label="synthetic HCP_AFFIL",
        sha256="a" * 64, record_count=10, received_at=now, status=sm.SNAPSHOT_PENDING,
        approved_by=None, approved_role=None, approved_at=None, approval_ref=None,
        supersedes_snapshot_id=None, created_by="maker@test.local", created_at=now,
        metadata_={"import_status": "completed",
                   "import_summary": {"rows_read": 10, "rows_staged": 9, "rows_rejected": 1},
                   "reference_preflight": {"gate": "CLEAR"}})
    base.update(over)
    return sm.SourceSnapshot(**base)


@pytest.fixture
def fake_page(monkeypatch):
    calls = []
    payload = {"total": 0, "page": []}

    async def _fake(db, **kw):
        calls.append(kw)
        return payload["total"], payload["page"]

    monkeypatch.setattr(routes, "_fetch_snapshot_page", _fake)
    return calls, payload


# -- access control ----------------------------------------------------------

def test_unauthenticated_is_401(client):
    assert client.get(PATH).status_code in (401, 403)  # missing header: scheme-dependent
    r = client.get(PATH, headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


@pytest.mark.parametrize("role", ["contributor", "viewer"])
def test_below_reviewer_is_403(as_role, fake_page, role):
    r = as_role(role)()
    assert r.status_code == 403
    assert fake_page[0] == []  # the query never ran


def test_licensed_flag_off_is_403_with_availability(as_role, fake_page, monkeypatch):
    from app.core.config import settings
    get = as_role("reviewer")
    monkeypatch.setattr(settings, "ENABLE_IQVIA_SOURCES", False, raising=False)
    r = get()
    assert r.status_code == 403
    assert "not_configured" in r.text  # availability envelope survives the error handler
    assert fake_page[0] == []


@pytest.mark.parametrize("role", ["reviewer", "qalead", "admin"])
def test_reviewer_and_above_may_read(as_role, fake_page, role):
    r = as_role(role)()
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"


# -- pagination bounds -------------------------------------------------------

@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=-1", "offset=-1",
                                   "offset=1000001", "limit=abc"])
def test_pagination_out_of_bounds_is_422(as_role, fake_page, query):
    assert as_role("reviewer")(PATH + "?" + query).status_code == 422
    assert fake_page[0] == []


def test_pagination_defaults_and_maximum_are_passed_through(as_role, fake_page):
    get = as_role("reviewer")
    assert get().status_code == 200
    assert get(PATH + "?limit=100&offset=200").status_code == 200
    calls = fake_page[0]
    assert (calls[0]["limit"], calls[0]["offset"], calls[0]["include_successors"]) == (25, 0, False)
    assert (calls[1]["limit"], calls[1]["offset"]) == (100, 200)


def test_unknown_source_filter_is_422_and_known_one_maps_to_source_system(as_role, fake_page):
    get = as_role("reviewer")
    assert get(PATH + "?source=nope").status_code == 422
    assert get(PATH + "?source=affiliation").status_code == 200
    assert fake_page[0][-1]["source_system"] == sm.SOURCE_IQVIA_AFFILIATION


# -- shape and truthfulness --------------------------------------------------

def test_registration_row_with_approved_successor_is_not_changed_in_place(as_role, fake_page):
    root = _snap()
    approved_at = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    succ = _snap(id=uuid.uuid4(), status=sm.SNAPSHOT_APPROVED, supersedes_snapshot_id=root.id,
                 approved_by="qa@test.local", approved_role="qalead", approved_at=approved_at,
                 approval_ref="REF-1", created_by="qa@test.local")
    fake_page[1].update(total=1, page=[(root, succ)])
    body = as_role("reviewer")().json()
    item = body["items"][0]
    assert body["total"] == 1 and body["has_more"] is False
    assert item["row_kind"] == "registration"
    assert item["status"] == "PENDING"            # the original row is never edited
    assert item["predecessor_snapshot_id"] is None
    assert item["decision"]["successor_snapshot_id"] == str(succ.id)
    assert item["decision"]["status"] == "APPROVED"
    assert item["decision"]["approved_by"] == "qa@test.local"
    assert item["sha256"] == "a" * 64 and item["record_count"] == 10
    assert item["stored_counters"] == {
        "basis": "stored_import_counters", "rows_read": 10, "rows_staged": 9,
        "rows_rejected": 1, "reference_preflight_gate": "CLEAR"}
    assert "reconciles" not in item["stored_counters"]   # not stored, so not claimed here


def test_successor_row_points_back_to_its_predecessor(as_role, fake_page):
    root_id = uuid.uuid4()
    succ = _snap(status=sm.SNAPSHOT_APPROVED, supersedes_snapshot_id=root_id,
                 approved_by="qa@test.local", approved_at=datetime(2026, 10, 12, tzinfo=timezone.utc))
    fake_page[1].update(total=5, page=[(succ, None)])
    body = as_role("qalead")(PATH + "?include_successors=true&limit=1").json()
    item = body["items"][0]
    assert item["row_kind"] == "approval_successor"
    assert item["predecessor_snapshot_id"] == str(root_id)
    assert item["decision"] is None
    assert body["has_more"] is True and body["include_successors"] is True


def test_matching_and_affiliation_flag_are_separate_from_approval(as_role, fake_page, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ENABLE_IQVIA_AFFILIATION_CONSUMPTION", False, raising=False)
    fake_page[1].update(total=1, page=[(_snap(status=sm.SNAPSHOT_APPROVED,
                                              supersedes_snapshot_id=uuid.uuid4()), None)])
    item = as_role("reviewer")().json()["items"][0]
    assert item["status"] == "APPROVED"
    assert item["matching"]["supported"] is False
    assert item["affiliation_lookup"]["enabled"] is False
    assert item["affiliation_lookup"]["flag"] == "ENABLE_IQVIA_AFFILIATION_CONSUMPTION"


def test_missing_metadata_yields_none_not_invented_values():
    summary = routes._snapshot_summary(_snap(metadata_={}), None)
    assert summary["import_status"] is None
    assert summary["stored_counters"]["rows_read"] is None
    assert summary["stored_counters"]["reference_preflight_gate"] is None


# -- real queries (PostgreSQL; skipped locally without a database) -----------

@pytest.fixture
async def pg(db_required):
    from app.core.database import async_session_maker
    ids = []
    async with async_session_maker() as session:
        yield session, ids
    async with async_session_maker() as cleanup:
        from sqlalchemy import delete
        for snapshot_id, _order in sorted(ids, key=lambda x: x[1], reverse=True):
            await cleanup.execute(delete(sm.SourceSnapshot).where(sm.SourceSnapshot.id == snapshot_id))
        await cleanup.commit()


async def test_query_embeds_successor_and_hides_successor_rows_by_default(pg):
    session, ids = pg
    tag = uuid.uuid4().hex[:8]
    from app.tefca_registry.rce import source_matching as sx
    now = datetime.now(timezone.utc)
    root = await sx.register_snapshot(
        session, source_system=sm.SOURCE_IQVIA_HCO, label=f"synthetic {tag}", sha256=tag.ljust(64, "0"),
        record_count=1, received_at=now, created_by="maker@test.local",
        metadata={"import_status": "completed"})
    ids.append((root.id, 0))
    succ = sm.SourceSnapshot(
        id=uuid.uuid4(), source_system=root.source_system, snapshot_label=root.snapshot_label,
        sha256=root.sha256, record_count=1, received_at=now, status=sm.SNAPSHOT_APPROVED,
        approved_by="qa@test.local", approved_role="qalead", approved_at=now, approval_ref="R",
        supersedes_snapshot_id=root.id, metadata_={}, created_by="qa@test.local", correlation_id="t")
    session.add(succ)
    await session.commit()
    ids.append((succ.id, 1))

    total, page = await routes._fetch_snapshot_page(
        session, limit=100, offset=0, source_system=sm.SOURCE_IQVIA_HCO, include_successors=False)
    mine = [(r, s) for r, s in page if r.id == root.id]
    assert len(mine) == 1 and mine[0][1].id == succ.id
    assert all(r.supersedes_snapshot_id is None for r, _ in page)
    assert total >= 1

    total_all, page_all = await routes._fetch_snapshot_page(
        session, limit=100, offset=0, source_system=sm.SOURCE_IQVIA_HCO, include_successors=True)
    assert {root.id, succ.id} <= {r.id for r, _ in page_all}
    assert total_all >= total + 1
