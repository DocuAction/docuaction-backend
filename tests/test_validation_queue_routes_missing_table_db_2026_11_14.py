"""The validation-queue routes when their table has not been applied (2026-11-14).

`tests/test_validation_queue_schema_gate_db_2026_11_14.py` proves the gate by
calling `_ensure_table` and `auto_queue_output` directly. These tests ask the
same questions of the application a caller actually reaches: the real
`app.main.app`, with its middleware, its authentication dependency, its
routers and its error handlers. Only two things are replaced: the database
session (pointed at a database of this test's own) and nothing else.

The three routes that need the table, by name:

    GET  /api/validation/queue              get_validation_queue
    POST /api/validation/review/{item_id}   review_item
    GET  /api/validation/stats              validation_stats

THE TABLE NEEDS AN OWNER-AUTHORIZED MIGRATION. `validation_queue` is created by
no Alembic revision; it has only ever been created by `create_all`. Where
schema mutation is not allowed the application must not create it while
serving a request, and until an owner-authorized migration creates it these
routes answer 409. The last test pins that no revision creates it today, so
that adding one is a deliberate, reviewed change.

The database used here is created for the test, holds only a `users` table (so
a caller can be authenticated) and is dropped afterwards. Nothing is created
in, or dropped from, the suite's database. Synthetic users only.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import uuid
from urllib.parse import unquote, urlparse

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import validation_routes as vr
from app.core import database, schema_guard

ROUTES = [
    ("GET", "/api/validation/queue", None),
    ("POST", f"/api/validation/review/{uuid.uuid4()}", {"action": "approve"}),
    ("GET", "/api/validation/stats", None),
]
#: Nothing a caller reads may carry these: not a traceback, not SQL, not a driver name.
FORBIDDEN_IN_BODY = ("Traceback", "sqlalchemy", "asyncpg", "SELECT ", "select ", "to_regclass",
                     "UndefinedTable", "relation \"", "File \"")


def _admin_parts():
    raw = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(raw)
    return {"host": parsed.hostname or "127.0.0.1", "port": parsed.port or 5432,
            "user": unquote(parsed.username or ""), "password": unquote(parsed.password or ""),
            "database": (parsed.path or "/postgres").lstrip("/")}


class _Site:
    """The real application over a database of this test's own."""

    def __init__(self, engine, app):
        self.engine, self.app = engine, app

    async def tables(self):
        async with self.engine.connect() as conn:
            rows = await conn.execute(text(
                "select tablename from pg_tables where schemaname = 'public'"))
            return {r[0] for r in rows}

    def client(self):
        # raise_app_exceptions=False: an unhandled error is reported as the 500
        # the caller would see, instead of being re-raised into the test.
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://testserver")

    async def headers(self, role="reviewer"):
        from app.core.security import create_access_token, hash_password
        from app.models.database import User

        async with AsyncSession(self.engine, expire_on_commit=False) as db:
            user = User(id=uuid.uuid4(), tenant_id="synthetic-validation-queue",
                        email=f"{role}-{uuid.uuid4().hex[:10]}@synthetic-test.docuaction.invalid",
                        password_hash=hash_password(uuid.uuid4().hex),
                        full_name=f"SYNTHETIC {role}", role=role, is_active=True,
                        is_verified=True, status="active", allowed_modules=[])
            db.add(user)
            await db.commit()
            token = create_access_token({"sub": str(user.id), "role": role},
                                        is_admin=(role == "admin"))
        return {"Authorization": f"Bearer {token}"}

    async def create_queue_table(self):
        """As the allowed-mutation path creates it: from the model's own metadata."""
        async with self.engine.begin() as conn:
            await conn.run_sync(vr.ValidationItem.__table__.create)


@pytest.fixture
async def site(db_required, monkeypatch):
    import asyncpg

    from app.core.database import get_db
    from app.main import app
    from app.models.database import User

    parts = _admin_parts()
    name = f"vqroute_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(**parts)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = (f"postgresql+asyncpg://{parts['user']}:{parts['password']}@{parts['host']}:"
           f"{parts['port']}/{name}")
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)          # so a caller can be authenticated

    async def _db():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _db                  # the ONLY dependency override
    monkeypatch.setattr(database, "_get_engine", lambda: engine)
    monkeypatch.setattr(vr, "_table_created", False)
    monkeypatch.setattr(vr, "_table_present", False)
    # Schema mutation is NOT allowed: production, startup flag unset.
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv(schema_guard.STARTUP_SCHEMA_FLAG, raising=False)
    assert schema_guard.schema_mutation_allowed() is False
    try:
        yield _Site(engine, app)
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        await engine.dispose()
        admin = await asyncpg.connect(**parts)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def _call(client, method, path, body, headers):
    if method == "GET":
        return await client.get(path, headers=headers)
    return await client.post(path, json=body, headers=headers)


def _no_internals(response):
    for fragment in FORBIDDEN_IN_BODY:
        assert fragment not in response.text, (fragment, response.text[:300])


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_a_route_that_needs_the_table_answers_409_and_creates_nothing(
        site, method, path, body):
    before = await site.tables()
    assert before == {"users"}
    headers = await site.headers("reviewer")
    async with site.client() as client:
        for _ in range(2):                       # asked again: the same answer, still nothing
            response = await _call(client, method, path, body, headers)
            assert response.status_code == 409, (method, path, response.status_code,
                                                 response.text[:300])
            payload = response.json()
            assert payload["code"] == "VALIDATION_QUEUE_NOT_APPLIED"
            assert "authorized migration" in payload["error"]
            assert "does not create tables" in payload["error"]
            assert payload.get("request_id")
            _no_internals(response)
            assert await site.tables() == before, "the request created nothing"
    assert vr._table_present is False and vr._table_created is False


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_authentication_is_decided_before_the_table_is_looked_for(
        site, method, path, body, monkeypatch):
    """A caller who is not authenticated gets the same refusal as before the
    gate existed, and learns nothing about the table: the check does not run."""
    looked = []
    real_engine = database._get_engine

    def _spy():
        looked.append(1)
        return real_engine()

    monkeypatch.setattr(database, "_get_engine", _spy)
    before = await site.tables()
    async with site.client() as client:
        anonymous = await _call(client, method, path, body, {})
        assert anonymous.status_code == 401, anonymous.text[:200]
        assert anonymous.json()["code"] == "UNAUTHORIZED"
        forged = await _call(client, method, path, body,
                             {"Authorization": "Bearer not-a-token"})
        assert forged.status_code == 401, forged.text[:200]
        assert forged.json()["code"] == "UNAUTHORIZED"
    assert looked == [], "the table was not looked for on behalf of an unauthenticated caller"
    assert await site.tables() == before


@pytest.mark.parametrize("method,path,body", ROUTES)
async def test_a_check_that_cannot_be_made_is_503_not_409(site, method, path, body, monkeypatch):
    headers = await site.headers("reviewer")
    before = await site.tables()

    def _broken():
        raise RuntimeError("synthetic: the engine cannot be reached SYNTHETIC_INTERNAL_DETAIL")

    monkeypatch.setattr(database, "_get_engine", _broken)
    async with site.client() as client:
        response = await _call(client, method, path, body, headers)
    assert response.status_code == 503, (response.status_code, response.text[:300])
    payload = response.json()
    # The application's handler replaces every 5xx body with a generic message
    # and the generic code; the specific code is in the server log only.
    assert payload["code"] == "SERVICE_UNAVAILABLE"
    assert payload["code"] != "VALIDATION_QUEUE_NOT_APPLIED"
    assert "SYNTHETIC_INTERNAL_DETAIL" not in response.text and "RuntimeError" not in response.text
    _no_internals(response)
    assert vr._table_present is False
    monkeypatch.undo()                            # the engine is reachable again for the count
    assert await site.tables() == before


async def test_where_the_table_exists_the_routes_answer_normally(site):
    """The gate refuses an ABSENT table, never a present one."""
    await site.create_queue_table()
    before = await site.tables()
    assert before == {"users", "validation_queue"}
    headers = await site.headers("reviewer")
    async with site.client() as client:
        queue = await client.get("/api/validation/queue", headers=headers)
        assert queue.status_code == 200, queue.text[:300]
        assert queue.json() == {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "items": []}
        stats = await client.get("/api/validation/stats", headers=headers)
        assert stats.status_code == 200, stats.text[:300]
        assert stats.json()["total"] == 0 and stats.json()["by_risk"]["critical"] == 0
        review = await client.post(f"/api/validation/review/{uuid.uuid4()}",
                                   json={"action": "approve"}, headers=headers)
        assert review.status_code == 404, review.text[:300]      # no such item; not the gate
        assert review.json()["code"] == "NOT_FOUND"
    assert vr._table_present is True
    assert await site.tables() == before


async def test_a_queued_item_is_read_and_reviewed_through_the_routes_once_the_table_exists(site):
    """End to end on a present table: the internal queue call writes one item, the
    list returns it and the review route changes it. Proves the gate's positive
    path is the working queue, not merely an empty answer."""
    from app.core.security import decode_token

    await site.create_queue_table()
    headers = await site.headers("reviewer")
    user_id = decode_token(headers["Authorization"].split()[1])["sub"]
    async with AsyncSession(site.engine, expire_on_commit=False) as db:
        queued = await vr.auto_queue_output(
            db, user_id, "synthetic-validation-queue", "doc-SYN-7", "synthetic.txt",
            "out-SYN-7", "summary", confidence=0.10, domain="healthcare",
            content_preview="synthetic preview")
    assert queued is not None
    async with site.client() as client:
        queue = (await client.get("/api/validation/queue", headers=headers)).json()
        assert queue["total"] == 1 and queue["items"][0]["document_id"] == "doc-SYN-7"
        item_id = queue["items"][0]["id"]
        review = await client.post(f"/api/validation/review/{item_id}",
                                   json={"action": "approve", "notes": "synthetic"},
                                   headers=headers)
        assert review.status_code == 200 and review.json()["status"] == "approved"


async def test_a_route_that_queues_internally_is_not_failed_by_the_missing_table(
        site, monkeypatch, caplog):
    """`meeting_routes` and `intelligence_routes` call `auto_queue_output` after
    producing an output. Both need an AI provider and document storage to be
    exercised end to end, which this test does not stand up. What it does prove,
    with the real function and the real (absent) table: the call those routes
    make returns None instead of raising, creates nothing, and says in the log
    which output was not queued."""
    import inspect
    import logging

    from app.api import intelligence_routes, meeting_routes

    for module in (intelligence_routes, meeting_routes):
        assert "auto_queue_output(" in inspect.getsource(module), module.__name__

    before = await site.tables()
    with caplog.at_level(logging.WARNING, logger=vr.logger.name):
        async with AsyncSession(site.engine, expire_on_commit=False) as db:
            out = await vr.auto_queue_output(
                db, "synthetic-user", "synthetic-validation-queue", "doc-SYN-8",
                "synthetic.txt", "out-SYN-8", "meeting_summary", confidence=0.10,
                domain="healthcare", content_preview="SYNTHETIC PREVIEW NOT TO BE LOGGED")
            # The caller's session is still usable afterwards: its own work is not lost.
            assert (await db.execute(text("select 1"))).scalar() == 1
    assert out is None
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "doc-SYN-8" in logged and "out-SYN-8" in logged
    assert "VALIDATION_QUEUE_NOT_APPLIED" in logged
    assert "SYNTHETIC PREVIEW" not in logged
    assert await site.tables() == before


def test_no_alembic_revision_creates_the_validation_queue_table():
    """Migrations stay blocked: `validation_queue` needs an owner-authorized
    migration, and none exists. This pins today's fact. If a revision that
    names the table is added, this test fails on purpose - update it in the
    same reviewed change, together with the owner authorization that allows
    the migration to run."""
    versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    revisions = sorted(versions.glob("*.py"))
    assert len(revisions) > 20, "the Alembic revisions were found"
    naming = [p.name for p in revisions
              if re.search(r"\bvalidation_queue\b", p.read_text(encoding="utf-8"))]
    assert naming == [], naming
    assert vr.ValidationItem.__tablename__ == "validation_queue"
    assert json.dumps(naming) == "[]"
