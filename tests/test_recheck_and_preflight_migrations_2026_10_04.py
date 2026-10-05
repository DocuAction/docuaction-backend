"""The three migrations this round added past PR #110's own head
(ea92ea5), executed against a throwaway database -- not read as source
text, run.

`test_traceability_migration.py` already proves, at the current chain
head, that the five EVIDENCE tables' ownership and grants survive an
upgrade to head unaffected by these three revisions. It does not round-
trip these three revisions' OWN upgrade/downgrade behaviour, which is
what this file adds -- found to be genuinely untested while correcting
this round's own prior claim that "no new migration was added."

Three revisions, in chain order:
  20261004_preflight_exec_held      widens a CHECK (adds 'held')
  20261004_stage_event_preflight    widens a CHECK (adds 'PREFLIGHT')
  20261004_recheck_jobs             creates rce_recheck_job/_item, grants

Proven here, against a real database, not assumed from reading the
file:
  - each upgrades cleanly from the prior revision
  - each downgrades cleanly while no dependent data exists
  - recheck_jobs' downgrade EXPLICITLY REFUSES once a job row exists
    (coded precondition, not a raw constraint violation)
  - the two CHECK-widening migrations have NO such explicit refusal:
    downgrading them while a dependent value ('held' / 'PREFLIGHT')
    exists in the data fails with a raw CheckViolation from Postgres
    itself, not a clean, named refusal -- documented here as a real,
    asymmetric gap between the three, not claimed safe because all
    three "look additive" in their upgrade direction.
  - recheck_jobs' GRANT is exactly SELECT/INSERT/UPDATE, never DELETE,
    for the app role.

Needs a superuser on the isolated cluster (DATABASE_URL's user) to
create a throwaway database; skips without one. Never touches the
`test` database directly; drops its own throwaway database afterwards.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid

import pytest

REPO = os.path.dirname(os.path.abspath(__file__)).rsplit(os.sep, 1)[0]
HEAD = "20261004_recheck_jobs"
PREVIOUS = "20261003_preflight_shadow"
REV_EXEC_HELD = "20261004_preflight_exec_held"
REV_STAGE_PREFLIGHT = "20261004_stage_event_preflight"
MIG_DB = "mig_test_recheck"
OWNER, APP = "docuaction_owner", "docuaction_app"


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


def _check_def(conn, constraint_name: str) -> str:
    from sqlalchemy import text
    return conn.execute(text(
        "select pg_get_constraintdef(oid) from pg_constraint where conname = :n"),
        {"n": constraint_name}).scalar()


@pytest.mark.usefixtures("db_required")
def test_preflight_check_widening_migrations_round_trip(throwaway_db):
    """The two CHECK-widening migrations, upgraded and downgraded against
    a real database, with the downgrade's real (unguarded) failure mode
    demonstrated rather than assumed."""
    from sqlalchemy import text

    url, eng = throwaway_db
    _alembic(url, "upgrade", PREVIOUS)
    with eng.connect() as c:
        assert _version(c) == [PREVIOUS]

    # 1. exec_held: upgrade widens the CHECK, downgrade narrows it back
    #    cleanly while no row uses 'held'.
    _alembic(url, "upgrade", REV_EXEC_HELD)
    with eng.connect() as c:
        assert _version(c) == [REV_EXEC_HELD]
        assert "'held'" in _check_def(c, "ck_rce_preflight_finding_execution")
    _alembic(url, "downgrade", PREVIOUS)
    with eng.connect() as c:
        assert _version(c) == [PREVIOUS]
        assert "'held'" not in _check_def(c, "ck_rce_preflight_finding_execution")
    _alembic(url, "upgrade", REV_EXEC_HELD)
    print("EXEC_HELD_ROUND_TRIP_EMPTY=PASS")

    # 2. stage_event_preflight: same shape, one revision further.
    _alembic(url, "upgrade", REV_STAGE_PREFLIGHT)
    with eng.connect() as c:
        assert _version(c) == [REV_STAGE_PREFLIGHT]
        assert "'PREFLIGHT'" in _check_def(c, "ck_rce_stage_event_stage")
    _alembic(url, "downgrade", REV_EXEC_HELD)
    with eng.connect() as c:
        assert _version(c) == [REV_EXEC_HELD]
        assert "'PREFLIGHT'" not in _check_def(c, "ck_rce_stage_event_stage")
    _alembic(url, "upgrade", REV_STAGE_PREFLIGHT)
    print("STAGE_EVENT_PREFLIGHT_ROUND_TRIP_EMPTY=PASS")

    # 3a. The real, demonstrated gap for stage_event_preflight: its
    # downgrade does NOT refuse when a dependent value exists -- it reaches
    # a raw Postgres CheckViolation instead of a clean, named precondition
    # error (contrast with recheck_jobs' explicit refusal, proven in the
    # other test below). A synthetic job is needed to satisfy
    # rce_delivery_stage_events' own foreign key first.
    job_id = uuid.uuid4()
    with eng.begin() as c:
        c.execute(text(
            "insert into rce_delivery_jobs (id, identity, original_filename, storage_path, "
            "sha256, file_size_bytes, registered_by, state, stage) values "
            "(cast(:j as uuid), :ident, 'synthetic.psv', '(synthetic)', :sha, 10, "
            "'SYNTHETIC-MIGRATION-TEST', 'QUEUED', 'ACCEPTED')"),
            {"j": str(job_id), "ident": "g" * 64, "sha": "g" * 64})
        c.execute(text(
            "insert into rce_delivery_stage_events (id, job_id, stage, status, started_at, "
            "correlation_id) values (gen_random_uuid(), cast(:j as uuid), 'PREFLIGHT', "
            "'COMPLETED', now(), 'mig-test-stage-preflight')"),
            {"j": str(job_id)})
    with eng.connect() as c:
        assert c.execute(text(
            "select stage from rce_delivery_stage_events where job_id = cast(:j as uuid)"),
            {"j": str(job_id)}).scalar() == "PREFLIGHT"

    r = _alembic(url, "downgrade", REV_EXEC_HELD, expect_ok=False)
    assert r.returncode != 0
    combined = (r.stdout + r.stderr)
    assert "CheckViolation" in combined or "check constraint" in combined.lower(), (
        f"expected a raw CHECK-violation failure (demonstrating the undocumented gap), got:\n{combined[-1500:]}")
    print("STAGE_EVENT_PREFLIGHT_DOWNGRADE_WITH_DEPENDENT_DATA_FAILS_RAW=PASS "
          "(no named precondition -- a real, asymmetric gap versus recheck_jobs)")
    with eng.connect() as c:
        assert _version(c) == [REV_STAGE_PREFLIGHT], "a failed downgrade must leave the chain where it was"
    with eng.begin() as c:
        c.execute(text("delete from rce_delivery_stage_events where job_id = cast(:j as uuid)"),
                 {"j": str(job_id)})
        c.execute(text("delete from rce_delivery_jobs where id = cast(:j as uuid)"), {"j": str(job_id)})

    # 3b. The same gap, one revision deeper: exec_held's own downgrade does
    # not refuse when a 'held' finding exists either -- target PREVIOUS so
    # exec_held's own downgrade (not stage_event_preflight's) actually runs.
    intake_id, record_id, run_id, finding_id = (uuid.uuid4() for _ in range(4))
    with eng.begin() as c:
        c.execute(text(
            "insert into rce_source_intakes (id, original_filename, storage_path, sha256, "
            "file_size_bytes, headers, schema_fingerprint, record_count, received_by, status) "
            "values (cast(:i as uuid), 'synthetic.psv', '(synthetic)', :sha, 10, '[\"id\"]'::jsonb, "
            ":fp, 1, 'SYNTHETIC-MIGRATION-TEST', 'PROCESSED')"),
            {"i": str(intake_id), "sha": "e" * 64, "fp": "f" * 64})
        c.execute(text(
            "insert into rce_source_records (id, source_intake_id, line_number, raw_line, "
            "record_sha256, field_count, parse_status, promotion_status) values "
            "(cast(:r as uuid), cast(:i as uuid), 2, '9.99.999.9.2', :sha, 1, 'ok', 'pending')"),
            {"r": str(record_id), "i": str(intake_id), "sha": "a1" + "a" * 62})
        c.execute(text(
            "insert into rce_preflight_run (id, source_intake_id, field_map_version, "
            "rule_set_version, preflight_version, status, classification_gate, "
            "records_evaluated, actor, correlation_id) values "
            "(cast(:run as uuid), cast(:i as uuid), 'v1', 'v1', 'v1', 'COMPLETE', 'CLEAR', 1, "
            "'SYNTHETIC-MIGRATION-TEST', 'mig-test-exec-held')"),
            {"run": str(run_id), "i": str(intake_id)})
        c.execute(text(
            "insert into rce_preflight_finding (id, run_id, source_record_id, line_number, "
            "sequence, category, code, applicability, execution, disposition, description) "
            "values (cast(:f as uuid), cast(:run as uuid), cast(:r as uuid), 2, 1, 'IDENTIFIER', "
            "'TEST-001', 'applies', 'held', 'open', 'synthetic migration test finding')"),
            {"f": str(finding_id), "run": str(run_id), "r": str(record_id)})
    with eng.connect() as c:
        assert c.execute(text("select execution from rce_preflight_finding where id = cast(:f as uuid)"),
                         {"f": str(finding_id)}).scalar() == "held"

    r = _alembic(url, "downgrade", PREVIOUS, expect_ok=False)
    assert r.returncode != 0
    combined = (r.stdout + r.stderr)
    assert "CheckViolation" in combined or "check constraint" in combined.lower(), (
        f"expected a raw CHECK-violation failure (demonstrating the undocumented gap), got:\n{combined[-1500:]}")
    print("EXEC_HELD_DOWNGRADE_WITH_DEPENDENT_DATA_FAILS_RAW=PASS "
          "(no named precondition -- a real, asymmetric gap versus recheck_jobs)")
    with eng.connect() as c:
        assert _version(c) == [REV_STAGE_PREFLIGHT], "a failed downgrade must leave the chain where it was"
    with eng.begin() as c:
        c.execute(text("delete from rce_preflight_finding where id = cast(:f as uuid)"), {"f": str(finding_id)})

    # Clean up the dependent row so later revisions can still be reached,
    # then restore to HEAD for the next test in this module.
    with eng.begin() as c:
        c.execute(text("delete from rce_preflight_finding where id = cast(:f as uuid)"), {"f": str(finding_id)})
    _alembic(url, "upgrade", HEAD)
    with eng.connect() as c:
        assert _version(c) == [HEAD]
    print("RESTORED_TO_HEAD=PASS")


@pytest.mark.usefixtures("db_required")
def test_recheck_jobs_migration_round_trip_and_explicit_refusal(throwaway_db):
    """recheck_jobs: upgrade creates both tables and the documented grant
    shape; downgrade succeeds while empty and EXPLICITLY REFUSES (a named
    precondition, not a raw constraint failure) once a job row exists."""
    from sqlalchemy import text

    url, eng = throwaway_db
    with eng.connect() as c:
        assert _version(c) == [HEAD], "expected the prior test to leave this database at HEAD"

    # 1. downgrade succeeds while both tables are empty (idempotent DROP),
    #    re-upgrade rebuilds cleanly.
    _alembic(url, "downgrade", REV_STAGE_PREFLIGHT)
    with eng.connect() as c:
        assert _version(c) == [REV_STAGE_PREFLIGHT]
        import sqlalchemy as sa
        names = sa.inspect(c).get_table_names()
        assert "rce_recheck_job" not in names and "rce_recheck_item" not in names
    _alembic(url, "upgrade", HEAD)
    with eng.connect() as c:
        assert _version(c) == [HEAD]
    print("RECHECK_JOBS_ROUND_TRIP_EMPTY=PASS")

    # 2. the grant shape: SELECT/INSERT/UPDATE, never DELETE, for the app role.
    with eng.connect() as c:
        for table in ("rce_recheck_job", "rce_recheck_item"):
            privs = {p: c.execute(text("select has_table_privilege(:r, :t, :p)"),
                                  {"r": APP, "t": table, "p": p}).scalar()
                    for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")}
            assert privs["SELECT"] and privs["INSERT"] and privs["UPDATE"], (table, privs)
            assert privs["DELETE"] is False and privs["TRUNCATE"] is False, (table, privs)
    print("RECHECK_JOBS_GRANT_SHAPE=PASS")

    # 3. downgrade EXPLICITLY REFUSES once a job row exists -- a named
    # precondition (RecheckJobsPreconditionError), the opposite failure
    # mode from the two CHECK-widening migrations proven above.
    intake_id, job_id = uuid.uuid4(), uuid.uuid4()
    with eng.begin() as c:
        c.execute(text(
            "insert into rce_source_intakes (id, original_filename, storage_path, sha256, "
            "file_size_bytes, headers, schema_fingerprint, record_count, received_by, status) "
            "values (cast(:i as uuid), 'synthetic.psv', '(synthetic)', :sha, 10, '[\"id\"]'::jsonb, "
            ":fp, 1, 'SYNTHETIC-MIGRATION-TEST', 'PROCESSED')"),
            {"i": str(intake_id), "sha": "b" * 64, "fp": "c" * 64})
        c.execute(text(
            "insert into rce_recheck_job (id, intake_id, trigger_kind, source_id, trigger_ref, "
            "idempotency_key, requested_by, rationale, baseline_hash) values "
            "(cast(:j as uuid), cast(:i as uuid), 'SOURCE_RECOVERY', 'SAM_GOV', "
            "'MIGRATION-TEST-REF', 'migration-test-key', 'synthetic-migration-test', "
            "'synthetic migration test row', 'deadbeef')"),
            {"j": str(job_id), "i": str(intake_id)})

    r = _alembic(url, "downgrade", REV_STAGE_PREFLIGHT, expect_ok=False)
    assert r.returncode != 0
    combined = (r.stdout + r.stderr)
    assert "RecheckJobsPreconditionError" in combined, (
        f"expected the named precondition refusal, got:\n{combined[-1500:]}")
    assert "holds 1 row" in combined
    with eng.connect() as c:
        assert _version(c) == [HEAD], "a refused downgrade must leave the chain exactly where it was"
        import sqlalchemy as sa
        assert "rce_recheck_job" in sa.inspect(c).get_table_names()
    print("RECHECK_JOBS_DOWNGRADE_REFUSED_WITH_DATA=PASS")
