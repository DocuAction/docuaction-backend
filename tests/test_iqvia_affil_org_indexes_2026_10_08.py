"""20261009_iqvia_affil_org_indexes, executed against a throwaway database.

Proves, on a real PostgreSQL cluster: the migration builds both PARTIAL expression indexes
(valid, owned by the table owner, predicate present), the downgrade/upgrade round trip works,
and the statements the organisation lookup actually emits (captured from SQLAlchemy, not
re-typed) are planned on those indexes.

ROBUSTNESS NOTE: the planner's choice depends on table size and statistics. This test loads
only ~60k synthetic rows, where a sequential scan can legitimately win on cost, so the EXPLAIN
assertion runs with `SET enable_seqscan = off`. That proves the planner CAN use the index for the
emitted expression (i.e. the expression text matches the index); the natural-cost choice at
scale is shown by the measured EXPLAIN output in docs/architecture/iqvia_org_first_design.md.

Needs a superuser DATABASE_URL on an isolated cluster; creates and drops `mig_a8_idx`.
Synthetic data only. Never touches any shared database.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD = "20261009_iqvia_affil_org_indexes"
PARENT = "20261008_record_check_results"
MIG_DB = "mig_a8_idx"
OWNER, APP = "docuaction_owner", "docuaction_app"
NPI_IDX, CCN_IDX = "ix_iqvia_affil_snapshot_org_npi", "ix_iqvia_affil_snapshot_org_ccn"
SID = uuid.UUID("a8a8a8a8-0000-4000-8000-0000000000aa")
NPI_VALUE = "1234567893"        # Luhn-valid synthetic (80840 + 123456789 -> check digit 3)
CCN_VALUE = "010001"

pytestmark = pytest.mark.regression


def _sync(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _mig_url(url: str, driver: str = "postgresql+asyncpg://") -> str:
    base, _db = url.rsplit("/", 1)
    return _sync(base).replace("postgresql://", driver) + "/" + MIG_DB


def _alembic(url: str, *args):
    env = dict(os.environ, DATABASE_URL=_mig_url(url), DB_MIGRATION_ROLE=OWNER, DB_APP_ROLE=APP,
               SECRET_KEY=os.environ.get("SECRET_KEY", "t" * 64),
               ALLOWED_HOSTS=os.environ.get("ALLOWED_HOSTS", "*"))
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"alembic {' '.join(args)} failed:\n{r.stdout[-1500:]}\n{r.stderr[-2500:]}"
    return r


@pytest.fixture(scope="module")
def throwaway_db():
    if os.environ.get("DATABASE_URL", "") == "":
        pytest.skip("DATABASE_URL not set")
    import sqlalchemy as sa
    from sqlalchemy import text

    url = os.environ["DATABASE_URL"]
    try:
        admin = sa.create_engine(_sync(url), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            if not c.execute(text("select rolsuper from pg_roles where rolname = current_user")).scalar():
                pytest.skip("DATABASE_URL user is not a superuser")
            for role in (OWNER, APP):
                if not c.execute(text("select 1 from pg_roles where rolname=:r"), {"r": role}).first():
                    pytest.skip(f"role {role} is absent on this cluster")
            c.execute(text(f"select pg_terminate_backend(pid) from pg_stat_activity "
                           f"where datname='{MIG_DB}' and pid<>pg_backend_pid()"))
            c.execute(text(f'DROP DATABASE IF EXISTS "{MIG_DB}"'))
            c.execute(text(f'CREATE DATABASE "{MIG_DB}" OWNER "{OWNER}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no database reachable for the migration test: {exc}")
    eng = sa.create_engine(_mig_url(url, "postgresql://"))
    with eng.begin() as c:
        c.execute(text(f'GRANT USAGE, CREATE ON SCHEMA public TO "{OWNER}", "{APP}"'))
    try:
        yield url, eng
    finally:
        eng.dispose()
        with admin.connect() as c:
            c.execute(text(f"select pg_terminate_backend(pid) from pg_stat_activity "
                           f"where datname='{MIG_DB}' and pid<>pg_backend_pid()"))
            c.execute(text(f'DROP DATABASE IF EXISTS "{MIG_DB}"'))
        admin.dispose()


def _index_rows(eng):
    from sqlalchemy import text
    with eng.connect() as c:
        return {r[0]: r for r in c.execute(text(
            "select c.relname, pg_get_userbyid(c.relowner), i.indisvalid, i.indpred is not null, "
            "pg_get_indexdef(c.oid) from pg_class c join pg_index i on i.indexrelid = c.oid "
            "where c.relname in (:a, :b)"), {"a": NPI_IDX, "b": CCN_IDX})}


async def _explain_emitted_statements(sync_url: str):
    """Run the real lookup, capture the SQL it emits, EXPLAIN each statement with seqscan off."""
    import asyncpg
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.tefca_registry.rce import iqvia_affiliation_consumer as ac

    eng = create_async_engine(sync_url.replace("postgresql://", "postgresql+asyncpg://"))
    captured = []

    @event.listens_for(eng.sync_engine, "before_cursor_execute")
    def _cap(conn, cur, stmt, params, ctx, many):  # noqa: ARG001
        if "iqvia_affiliation_observation" in stmt:
            captured.append((stmt, params))

    async with AsyncSession(eng) as db:
        res = await ac.organisation_relationships(db, snapshot_id=SID, org_npi=NPI_VALUE, org_ccn=CCN_VALUE,
                                                  diagnostic_allow_pending=True)
    await eng.dispose()
    assert res["found_in_extract"] is True

    conn = await asyncpg.connect(sync_url)
    plans = {}
    try:
        await conn.execute("set enable_seqscan = off")
        for stmt, params in captured:
            rows = await conn.fetch("EXPLAIN " + stmt, *params)
            plans[stmt] = "\n".join(r[0] for r in rows)
    finally:
        await conn.close()
    return plans


@pytest.mark.usefixtures("db_required")
def test_org_indexes_exist_are_partial_owned_and_used(throwaway_db):
    from sqlalchemy import text

    url, eng = throwaway_db
    _alembic(url, "upgrade", PARENT)
    assert _index_rows(eng) == {}, "indexes must not exist before this revision"
    _alembic(url, "upgrade", "head")
    with eng.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == [HEAD]

    idx = _index_rows(eng)
    assert set(idx) == {NPI_IDX, CCN_IDX}
    for name, key in ((NPI_IDX, "ORG_NPI"), (CCN_IDX, "ORG_CCN_ID")):
        _n, owner, valid, partial, ddl = idx[name]
        assert owner == OWNER, f"{name} must be owned by the table owner, not the migrating principal"
        assert valid is True and partial is True
        assert "source_snapshot_id" in ddl and f"'{key}'" in ddl and "IS NOT NULL" in ddl

    # round trip: downgrade drops both, upgrade rebuilds both
    _alembic(url, "downgrade", PARENT)
    assert _index_rows(eng) == {}
    _alembic(url, "upgrade", "head")
    assert set(_index_rows(eng)) == {NPI_IDX, CCN_IDX}
    # idempotent: re-running the revision over existing indexes is a no-op, never an error
    with eng.begin() as c:
        c.execute(text("update alembic_version set version_num = :p"), {"p": PARENT})
    _alembic(url, "upgrade", "head")
    assert all(r[2] for r in _index_rows(eng).values())

    # synthetic data: 60k rows; one organisation carries the looked-up identifiers
    with eng.begin() as c:
        c.execute(text("insert into source_snapshot (id, source_system, snapshot_label, sha256, record_count, "
                       "received_at, status, created_by, correlation_id) values (:i, 'IQVIA_AFFILIATION', "
                       "'SYN-a8-test', repeat('a',64), 60000, now(), 'PENDING', 'SYN-a8', 'SYN-a8')"),
                  {"i": SID})
        c.execute(text(
            "insert into iqvia_affiliation_observation (id, source_snapshot_id, hcp_record_key, hco_record_key, "
            "affiliation_type, record_sha256, payload, correlation_id) "
            "select gen_random_uuid(), :s, 'SYN-H'||g, 'SYN-O'||(g%5000), 'A'||(g%3), md5(g::text)||md5(g::text), "
            "jsonb_build_object("
            "'ORG_NPI', case when g%5000=7 then cast(:npi as text) when g%4<3 then '1'||lpad((g%5000)::text,9,'0') else '' end, "
            "'ORG_CCN_ID', case when g%5000=7 then cast(:ccn as text) when g%2=0 then lpad((200000+g%5000)::text,6,'0') else '' end), "
            "'SYN-a8' from generate_series(1,60000) g"),
            {"s": SID, "npi": NPI_VALUE, "ccn": CCN_VALUE})
        c.execute(text("analyze iqvia_affiliation_observation"))

    plans = asyncio.run(_explain_emitted_statements(_mig_url(url, "postgresql://")))
    joined = "\n".join(plans.values())
    assert NPI_IDX in joined, f"ORG_NPI lookup does not use {NPI_IDX}:\n{joined}"
    assert CCN_IDX in joined, f"ORG_CCN_ID lookup does not use {CCN_IDX}:\n{joined}"
    for s, p in plans.items():
        if "hit" in s and "MATERIALIZED" in s:
            assert "Seq Scan on iqvia_affiliation_observation" not in p, p


@pytest.mark.usefixtures("db_required")
def test_interrupted_build_leaves_an_invalid_index_that_is_detected_and_repaired(throwaway_db):
    """A CONCURRENTLY build that fails part-way leaves an INVALID index under the same name. Simulate a real
    failed build (a unique concurrent build over duplicate keys), prove the detection query sees it, then prove the
    same governed upgrade drops the leftover and rebuilds the CORRECT partial index, with the revision recorded
    only afterwards."""
    from sqlalchemy import text

    url, eng = throwaway_db
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mig_20261009", os.path.join(REPO, "alembic", "versions", "20261009_iqvia_affil_org_indexes.py"))
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    _alembic(url, "downgrade", PARENT)                       # both indexes dropped, version = parent
    assert _index_rows(eng) == {}
    admin = __import__("sqlalchemy").create_engine(_mig_url(url, "postgresql://"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text("SET ROLE docuaction_owner"))
            with pytest.raises(Exception):                   # duplicate (snapshot) keys: the build FAILS
                c.execute(text(f"CREATE UNIQUE INDEX CONCURRENTLY {NPI_IDX} ON iqvia_affiliation_observation "
                               "(source_snapshot_id)"))
        with eng.connect() as c:
            invalid = c.execute(text(mig.INVALID_INDEX_SQL)).scalars().all()
        assert invalid == [NPI_IDX], "detection query must report the leftover INVALID index"
        assert _index_rows(eng)[NPI_IDX][2] is False

        # IF NOT EXISTS alone would skip this name and leave the broken index in place; the upgrade must not.
        _alembic(url, "upgrade", "head")
        rows = _index_rows(eng)
        assert set(rows) == {NPI_IDX, CCN_IDX}
        for name, key in ((NPI_IDX, "ORG_NPI"), (CCN_IDX, "ORG_CCN_ID")):
            assert rows[name][2] is True and rows[name][3] is True, f"{name} must be valid and partial"
            assert f"'{key}'" in rows[name][4] and "UNIQUE" not in rows[name][4]
        with eng.connect() as c:
            assert c.execute(text(mig.INVALID_INDEX_SQL)).scalars().all() == []
            assert c.execute(text("select version_num from alembic_version")).scalars().all() == [HEAD]
    finally:
        admin.dispose()
