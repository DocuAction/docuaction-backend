"""Request-time table creation obeys the same gate as startup (2026-11-14).

`app/api/validation_routes._ensure_table` ran `create_all` on the first request
to the validation queue. Startup's `create_all` has been gated by
`schema_guard.schema_mutation_allowed()` since the Area 1 ownership defect: in
production a table must arrive through an authorized Alembic run, because the
role that creates a table owns it. The request-time copy had no such gate, so
the first validation request on a production database that was behind the model
would have created every missing non-evidence table, owned by the runtime role.

These tests use a database of their own, created empty and dropped afterwards.
Nothing is created in, or dropped from, the suite's database.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import unquote, urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api import validation_routes as vr
from app.core import database, schema_guard


def _admin_parts():
    raw = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(raw)
    return {"host": parsed.hostname or "127.0.0.1", "port": parsed.port or 5432,
            "user": unquote(parsed.username or ""), "password": unquote(parsed.password or ""),
            "database": (parsed.path or "/postgres").lstrip("/")}


@pytest.fixture
async def empty_database(db_required, monkeypatch):
    """A new, empty database; `_ensure_table` is pointed at it and nowhere else."""
    import asyncpg

    parts = _admin_parts()
    name = f"gate_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(**parts)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = (f"postgresql+asyncpg://{parts['user']}:{parts['password']}@{parts['host']}:"
           f"{parts['port']}/{name}")
    engine = create_async_engine(url, poolclass=NullPool)
    monkeypatch.setattr(database, "_get_engine", lambda: engine)
    monkeypatch.setattr(vr, "_table_created", False)
    monkeypatch.setattr(vr, "_table_present", False, raising=False)

    async def tables():
        async with engine.connect() as conn:
            rows = await conn.execute(text(
                "select tablename from pg_tables where schemaname = 'public'"))
            return {r[0] for r in rows}

    try:
        yield tables
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(**parts)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()


def _production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv(schema_guard.STARTUP_SCHEMA_FLAG, raising=False)
    assert schema_guard.schema_mutation_allowed() is False


async def test_where_schema_mutation_is_not_allowed_a_request_creates_no_table(
        empty_database, monkeypatch):
    _production(monkeypatch)
    assert await empty_database() == set()
    with pytest.raises(HTTPException) as refused:
        await vr._ensure_table()
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "VALIDATION_QUEUE_NOT_APPLIED"
    assert await empty_database() == set(), "nothing was created by the request"
    # Asked again, it is the same answer and still nothing is created.
    with pytest.raises(HTTPException):
        await vr._ensure_table()
    assert await empty_database() == set()


async def test_the_internal_queue_call_declines_quietly_and_creates_nothing(
        empty_database, monkeypatch):
    """`auto_queue_output` is called from inside other routes; a missing queue
    table must not fail the request that produced the output."""
    _production(monkeypatch)
    out = await vr.auto_queue_output(
        None, "synthetic-user", "default", "doc", "synthetic.txt", "out", "summary",
        confidence=0.10, domain="healthcare", content_preview="synthetic")
    assert out is None
    assert await empty_database() == set()


async def test_an_unset_environment_on_a_deployed_host_is_treated_the_same(
        empty_database, monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv(schema_guard.STARTUP_SCHEMA_FLAG, raising=False)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "synthetic-site")
    with pytest.raises(HTTPException) as refused:
        await vr._ensure_table()
    assert refused.value.status_code == 409
    assert await empty_database() == set()


async def test_where_the_table_exists_the_gate_does_not_refuse(empty_database, monkeypatch):
    """A migrated production database: the table is there, nothing is created."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    await vr._ensure_table()                       # development: creates, as before
    before = await empty_database()
    assert "validation_queue" in before
    _production(monkeypatch)
    monkeypatch.setattr(vr, "_table_created", False)
    monkeypatch.setattr(vr, "_table_present", False, raising=False)
    await vr._ensure_table()                       # no refusal
    assert await empty_database() == before


async def test_where_schema_mutation_is_allowed_behaviour_is_unchanged(empty_database, monkeypatch):
    from app.tefca_registry.rce import traceability_models as tm

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv(schema_guard.STARTUP_SCHEMA_FLAG, raising=False)
    assert schema_guard.schema_mutation_allowed() is True
    await vr._ensure_table()
    created = await empty_database()
    assert "validation_queue" in created
    assert not (created & set(tm.MIGRATION_OWNED_TABLES))
    assert "rce_record_comments" not in created


# ── model-review follow-up, 2026-11-14 ───────────────────────────────────────

async def test_a_check_that_cannot_be_made_is_unavailable_not_not_applied(
        empty_database, monkeypatch):
    """A failed existence check says nothing about the table. It used to be
    reported as 409 "not applied", which tells an operator to run a migration."""
    _production(monkeypatch)

    def _broken():
        raise RuntimeError("synthetic: no engine")

    monkeypatch.setattr(database, "_get_engine", _broken)
    with pytest.raises(HTTPException) as refused:
        await vr._ensure_table()
    assert refused.value.status_code == 503
    assert refused.value.detail["code"] == "VALIDATION_QUEUE_UNAVAILABLE"
    assert vr._table_present is False


async def test_a_declined_queue_item_is_logged_with_what_was_not_queued(
        empty_database, monkeypatch, caplog):
    """The output was meant for human review and was not queued. The log line
    must say which document and output, and why, so it can be found and queued
    once the table exists. Identifiers only - never the content preview."""
    import logging

    _production(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=vr.logger.name):
        out = await vr.auto_queue_output(
            None, "synthetic-user", "default", "doc-SYN-1", "synthetic.txt", "out-SYN-9",
            "summary", confidence=0.10, domain="healthcare",
            content_preview="SYNTHETIC PREVIEW TEXT THAT MUST NOT BE LOGGED")
    assert out is None
    line = "\n".join(r.getMessage() for r in caplog.records)
    assert "doc-SYN-1" in line and "out-SYN-9" in line
    assert "VALIDATION_QUEUE_NOT_APPLIED" in line and "healthcare" in line
    assert "SYNTHETIC PREVIEW TEXT" not in line
    assert await empty_database() == set()
