"""20260917_delivery_traceability, executed against a throwaway database.

The static chain tests prove the revision is guarded and single-headed. This
file proves what it DOES: the chain builds from empty to the new head, the
revision downgrades cleanly while the evidence tables are empty and REFUSES
once they are not, the runtime role can append but never edit or erase, the
derived view returns the highest sequence, the equation CHECK rejects a
passing snapshot whose counts do not sum, and the grant matrix is exactly the
contract's (SELECT+INSERT everywhere, UPDATE on stage events only, DELETE
nowhere).

It needs a superuser on the isolated cluster (DATABASE_URL's user) to create
`mig_test`; it never touches the `test` database's tables, and it drops
`mig_test` afterwards. Skips without a reachable database.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The chain's actual head. 20260918 (Decision 2, added 2026-09-18) only adds
#: nullable columns to `rce_issues` and widens the snapshot trigger CHECK; it
#: does not touch the five evidence tables' grants this module tests, so
#: "upgrade to head" testing those grants stays valid with the head moved.
HEAD = "20260918_pp_verification"
PREVIOUS = "20260915_curated_text_columns"
MIG_DB = "mig_test"
OWNER, APP = "docuaction_owner", "docuaction_app"
TABLES = ("rce_delivery_stage_events", "rce_disposition_events", "rce_reconciliation_snapshots",
          "tefca_identifier_decision_events", "rce_delivery_report_links")
VIEW = "rce_current_dispositions"


def _sync(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _mig_url(url: str, driver: str = "postgresql+asyncpg://") -> str:
    base, _db = url.rsplit("/", 1)
    return _sync(base).replace("postgresql://", driver) + "/" + MIG_DB


def _alembic(url: str, *args, expect_ok=True):
    env = dict(os.environ, DATABASE_URL=_mig_url(url), DB_MIGRATION_ROLE=OWNER, DB_APP_ROLE=APP,
               SECRET_KEY=os.environ.get("SECRET_KEY", "t" * 64),
               ALLOWED_HOSTS=os.environ.get("ALLOWED_HOSTS", "*"))
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    if expect_ok:
        assert r.returncode == 0, f"alembic {' '.join(args)} failed:\n{r.stdout[-1500:]}\n{r.stderr[-2500:]}"
    return r


@pytest.fixture(scope="module")
def throwaway_db():
    """CREATE DATABASE mig_test OWNER docuaction_owner, grant schema public, drop after."""
    if os.environ.get("DATABASE_URL", "") == "":
        pytest.skip("DATABASE_URL not set")
    import sqlalchemy as sa
    from sqlalchemy import text

    url = os.environ["DATABASE_URL"]
    try:
        admin = sa.create_engine(_sync(url), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            if not c.execute(text("select rolsuper from pg_roles where rolname = current_user")).scalar():
                pytest.skip("DATABASE_URL user is not a superuser; cannot create the throwaway database")
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


def _version(conn):
    from sqlalchemy import text
    return conn.execute(text("select version_num from alembic_version")).scalars().all()


def _tables(conn):
    import sqlalchemy as sa
    insp = sa.inspect(conn)
    return set(insp.get_table_names()), set(insp.get_view_names())


def _privs(conn, role, rel):
    from sqlalchemy import text
    return {p: conn.execute(text("select has_table_privilege(:r, :t, :p)"),
                            {"r": role, "t": rel, "p": p}).scalar()
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")}


def _expect_error(fn, *needles):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        assert any(n.lower() in msg for n in needles), f"unexpected error: {exc}"
        return
    raise AssertionError("expected a database error")


@pytest.mark.usefixtures("db_required")
def test_traceability_migration_end_to_end(throwaway_db):
    import sqlalchemy as sa
    from sqlalchemy import text

    url, eng = throwaway_db

    # 1. empty -> head
    _alembic(url, "upgrade", "head")
    with eng.connect() as c:
        assert _version(c) == [HEAD]
        tables, views = _tables(c)
        assert set(TABLES) <= tables and VIEW in views
        for t in TABLES:
            assert c.execute(text("select relowner::regrole::text from pg_class where relname=:t"),
                             {"t": t}).scalar() == OWNER
    print("UPGRADE_HEAD=PASS")

    # 2. downgrade succeeds while the tables are empty, and upgrade rebuilds
    # Targets PREVIOUS by name, not "-1": HEAD may now be more than one
    # revision past 20260917 (20260918 stacks on top of it), and this test
    # is specifically about 20260917's own up/down behaviour.
    _alembic(url, "downgrade", PREVIOUS)
    with eng.connect() as c:
        assert _version(c) == [PREVIOUS]
        tables, views = _tables(c)
        assert not (set(TABLES) & tables) and VIEW not in views
    _alembic(url, "upgrade", "head")
    with eng.connect() as c:
        assert _version(c) == [HEAD]
    print("DOWNGRADE_ON_EMPTY=PASS")

    # 3. the grant matrix is the contract's
    with eng.connect() as c:
        for t in TABLES:
            p = _privs(c, APP, t)
            assert p["SELECT"] and p["INSERT"], (t, p)
            assert p["DELETE"] is False and p["TRUNCATE"] is False, (t, p)
            assert p["UPDATE"] is (t == "rce_delivery_stage_events"), (t, p)
        assert _privs(c, APP, VIEW)["SELECT"] is True
    print("GRANT_MATRIX=PASS")

    # 4. append as the runtime role; edit and erase are refused
    intake_id, record_id = uuid.uuid4(), uuid.uuid4()
    with eng.begin() as c:
        c.execute(text(
            "insert into rce_source_intakes (id, original_filename, storage_path, sha256, "
            "file_size_bytes, headers, schema_fingerprint, record_count, received_by, status) "
            "values (cast(:i as uuid), 'synthetic.psv', '(synthetic)', :sha, 10, '[\"id\"]'::jsonb, "
            ":fp, 1, 'SYNTHETIC-MIGRATION-TEST', 'PROCESSED')"),
            {"i": str(intake_id), "sha": "a" * 64, "fp": "b" * 64})
        c.execute(text(
            "insert into rce_source_records (id, source_intake_id, line_number, raw_line, "
            "record_sha256, field_count, parse_status, promotion_status) values "
            "(cast(:r as uuid), cast(:i as uuid), 2, '9.99.999.9.1', :sha, 1, 'ok', 'pending')"),
            {"r": str(record_id), "i": str(intake_id), "sha": "c" * 64})

    def _insert(c, seq, disposition, reason):
        c.execute(text(
            "insert into rce_disposition_events (id, intake_id, source_record_id, sequence, "
            "disposition, reason_code, actor, actor_type, correlation_id, build_sha) values "
            "(cast(:id as uuid), cast(:i as uuid), cast(:r as uuid), :s, :d, :rc, 'SYSTEM', "
            "'SYSTEM', 'mig-test', 'synthetic0')"),
            {"id": str(uuid.uuid4()), "i": str(intake_id), "r": str(record_id), "s": seq,
             "d": disposition, "rc": reason})

    with eng.begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{APP}"'))
        _insert(c, 1, "HELD", "HELD_QUALITY_ISSUE")
        assert c.execute(text("select count(*) from rce_disposition_events")).scalar() == 1
    app_url = _mig_url(url, "postgresql://")
    app_eng = sa.create_engine(app_url)

    def _as_app(sql):
        with app_eng.begin() as c:
            c.execute(text(f'SET LOCAL ROLE "{APP}"'))
            c.execute(text(sql))

    _expect_error(lambda: _as_app("update rce_disposition_events set reason_code='X'"),
                  "permission denied")
    _expect_error(lambda: _as_app("delete from rce_disposition_events"), "permission denied")
    _expect_error(lambda: _as_app("truncate rce_disposition_events"), "permission denied")
    for t in TABLES:
        if t != "rce_delivery_stage_events":
            _expect_error(lambda t=t: _as_app(f"delete from {t}"), "permission denied")
    app_eng.dispose()
    with eng.connect() as c:
        assert c.execute(text("select count(*) from rce_disposition_events")).scalar() == 1
    print("APPEND_ONLY_BY_GRANT=PASS")

    # 5. downgrade now refuses: evidence exists
    # Targets PREVIOUS (see the note above): the refusal must come from
    # 20260917's own downgrade, which the CLI reaches on the way to PREVIOUS
    # whether or not another revision now sits on top of it.
    r = _alembic(url, "downgrade", PREVIOUS, expect_ok=False)
    assert r.returncode != 0
    assert "DowngradeWouldDestroyEvidenceError" in (r.stdout + r.stderr)
    assert "rce_disposition_events (1 rows)" in (r.stdout + r.stderr)
    with eng.connect() as c:
        assert _version(c) == [HEAD]
        tables, views = _tables(c)
        assert set(TABLES) <= tables and VIEW in views
    print("DOWNGRADE_REFUSED_WITH_EVIDENCE=PASS")

    # 6. the view returns the highest sequence per record
    with eng.begin() as c:
        c.execute(text(f'SET LOCAL ROLE "{APP}"'))
        _insert(c, 2, "EXCLUDED", "ANALYST_DISPOSITION")
    with eng.connect() as c:
        rows = c.execute(text(f"select sequence, disposition from {VIEW} "
                              f"where source_record_id = cast(:r as uuid)"),
                         {"r": str(record_id)}).all()
        assert rows == [(2, "EXCLUDED")]
        assert c.execute(text("select count(*) from rce_disposition_events")).scalar() == 2
    print("CURRENT_VIEW_HIGHEST_SEQUENCE=PASS")

    # 7. the equation CHECK rejects a passing snapshot whose counts do not sum
    job_id = uuid.uuid4()
    with eng.begin() as c:
        c.execute(text(
            "insert into rce_delivery_jobs (id, identity, original_filename, storage_path, sha256, "
            "file_size_bytes, state, stage, registered_by, source_intake_id, created_at) values "
            "(cast(:j as uuid), :ident, 'synthetic.psv', '(synthetic)', :sha, 10, 'SUCCEEDED', "
            "'READY_FOR_REVIEW', 'SYNTHETIC-MIGRATION-TEST', cast(:i as uuid), now())"),
            {"j": str(job_id), "ident": "d" * 64, "sha": "a" * 64, "i": str(intake_id)})

    def _snapshot(passed, created):
        with eng.begin() as c:
            c.execute(text(f'SET LOCAL ROLE "{APP}"'))
            c.execute(text(
                "insert into rce_reconciliation_snapshots (id, job_id, intake_id, sequence, passed, "
                "received, created, updated, matched_unchanged, held, rejected, missing_key, excluded, "
                "actor, trigger, hash, correlation_id) values (cast(:id as uuid), cast(:j as uuid), "
                "cast(:i as uuid), (select coalesce(max(sequence),0)+1 from rce_reconciliation_snapshots "
                "where job_id = cast(:j as uuid)), :p, 1, :c, 0, 0, 0, 0, 0, 0, 'SYSTEM', 'PIPELINE', "
                ":h, 'mig-test')"),
                {"id": str(uuid.uuid4()), "j": str(job_id), "i": str(intake_id), "p": passed,
                 "c": created, "h": "e" * 64})

    _expect_error(lambda: _snapshot(True, 0), "ck_rce_snapshot_equation")
    _snapshot(False, 0)        # a FAILED snapshot may carry counts that do not sum
    _snapshot(True, 1)         # a passing one whose counts sum is accepted
    with eng.connect() as c:
        assert c.execute(text("select count(*) from rce_reconciliation_snapshots")).scalar() == 2
        assert c.execute(text("select count(*) from pg_constraint where conname='ck_rce_snapshot_equation'")).scalar() == 1
    print("EQUATION_CHECK=PASS")

    # 8. has_table_privilege matches the contract after the evidence writes too
    with eng.connect() as c:
        for t in TABLES:
            p = _privs(c, APP, t)
            assert (p["SELECT"], p["INSERT"], p["DELETE"]) == (True, True, False), (t, p)
        assert _privs(c, OWNER, "rce_disposition_events")["SELECT"] is True
    print("MIGRATION_TEST=PASS head=%s" % HEAD)
