"""scripts/dev_repairs/report_export_jobs_ownership_repair.sql, executed for real.

DEV's diagnosed state (run 37337241352) is rebuilt on a throwaway database:
the chain at 20260930_alembic_version_read, `report_export_jobs` owned by the
RUNTIME role, an admin role that owns the table only through its membership in
`docuaction_app` and holds nothing but ADMIN OPTION on `docuaction_owner`.

The repair is then run by that NON-SUPERUSER admin through psql, exactly as an
operator would, and the outcome is read back independently. Afterwards the
blocked revision is applied, and the application's own job functions are
exercised as `docuaction_app` -- not as a superuser -- to prove a report job
can still be created, read, claimed and updated, and can no longer be deleted.

Needs a superuser DATABASE_URL (to build and drop the throwaway databases and
test roles), the docuaction_owner / docuaction_app roles, and psql. Skips
otherwise.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL = os.path.join(REPO, "scripts", "dev_repairs", "report_export_jobs_ownership_repair.sql")
DIAGNOSED_REV = "20260930_alembic_version_read"
OWNER, APP = "docuaction_owner", "docuaction_app"
ADMIN, NOAUTH, EXTRA = "repair_admin_test", "repair_noauth_test", "repair_extra_test"
TEMPLATE = "repair_tpl_test"
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _sync(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _with_db(url: str, db: str, driver: str = "postgresql://") -> str:
    base, _ = _sync(url).rsplit("/", 1)
    return base.replace("postgresql://", driver) + "/" + db


def _alembic(url: str, db: str, *args):
    env = dict(os.environ, DATABASE_URL=_with_db(url, db, "postgresql+asyncpg://"),
               DB_MIGRATION_ROLE=OWNER, DB_APP_ROLE=APP,
               SECRET_KEY=os.environ.get("SECRET_KEY", "t" * 64),
               ALLOWED_HOSTS=os.environ.get("ALLOWED_HOSTS", "*"))
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"alembic {' '.join(args)} failed:\n{r.stdout[-1500:]}\n{r.stderr[-2500:]}"


def _psql(url: str, db: str, user: str, *extra):
    """Run the repair script the way an operator does: psql, as a login role."""
    u = urlsplit(_sync(url))
    env = dict(os.environ, PGPASSWORD="x", PGCONNECT_TIMEOUT="15")
    r = subprocess.run(
        [shutil.which("psql"), "-h", u.hostname, "-p", str(u.port or 5432), "-U", user, "-d", db,
         "-X", "-v", "ON_ERROR_STOP=1", *extra, "-f", SQL],
        env=env, capture_output=True, text=True, timeout=180)
    return r.returncode, r.stdout + r.stderr


@pytest.fixture(scope="module")
def cluster():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    if not shutil.which("psql"):
        pytest.skip("psql is not installed; the repair is a psql script")
    import sqlalchemy as sa
    from sqlalchemy import text

    url = os.environ["DATABASE_URL"]
    created: list[str] = []
    try:
        admin = sa.create_engine(_sync(url), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            if not c.execute(text("select rolsuper from pg_roles where rolname = current_user")).scalar():
                pytest.skip("DATABASE_URL user is not a superuser; cannot build the throwaway databases")
            for role in (OWNER, APP):
                if not c.execute(text("select 1 from pg_roles where rolname=:r"), {"r": role}).first():
                    pytest.skip(f"role {role} is absent on this cluster")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cluster not usable for the ownership-repair test: {exc}")

    def su(db, sql, **params):
        eng = sa.create_engine(_with_db(url, db), isolation_level="AUTOCOMMIT")
        try:
            with eng.connect() as c:
                res = c.execute(text(sql), params)
                return res.fetchall() if res.returns_rows else None
        finally:
            eng.dispose()

    def drop_db(name):
        with admin.connect() as c:
            c.execute(text("select pg_terminate_backend(pid) from pg_stat_activity "
                           "where datname=:d and pid<>pg_backend_pid()"), {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))

    def new_db(name):
        drop_db(name)
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}" OWNER "{OWNER}"'))
        created.append(name)
        return name

    def cleanup_roles():
        with admin.connect() as c:
            if c.execute(text("select 1 from pg_roles where rolname=:r"), {"r": EXTRA}).first():
                c.execute(text(f'REVOKE "{EXTRA}" FROM "{APP}"'))
            for role in (ADMIN, NOAUTH, EXTRA):
                c.execute(text(f'DROP ROLE IF EXISTS "{role}"'))

    for name in (TEMPLATE,):
        drop_db(name)
    cleanup_roles()
    with admin.connect() as c:
        # The diagnosed DEV shape: an admin that owns the table only through its
        # membership in the runtime role, and has ADMIN OPTION (nothing else) on the owner role.
        c.execute(text(f"CREATE ROLE \"{ADMIN}\" LOGIN PASSWORD 'x' NOSUPERUSER"))
        c.execute(text(f'GRANT "{APP}" TO "{ADMIN}" WITH INHERIT TRUE, SET TRUE'))
        c.execute(text(f'GRANT "{OWNER}" TO "{ADMIN}" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE'))
        c.execute(text(f"CREATE ROLE \"{NOAUTH}\" LOGIN PASSWORD 'x' NOSUPERUSER"))
        c.execute(text(f'CREATE ROLE "{EXTRA}" NOLOGIN'))
        c.execute(text(f'CREATE DATABASE "{TEMPLATE}" OWNER "{OWNER}"'))
    created.append(TEMPLATE)
    su(TEMPLATE, f'GRANT USAGE, CREATE ON SCHEMA public TO "{OWNER}", "{APP}"')
    _alembic(url, TEMPLATE, "upgrade", DIAGNOSED_REV)
    su(TEMPLATE, f'ALTER TABLE public.report_export_jobs OWNER TO "{APP}"')   # what DEV actually has

    def snap(db):
        owner = su(db, "select tableowner from pg_tables where tablename='report_export_jobs'")[0][0]
        eff = sorted(p for p in PRIVS if su(
            db, "select has_table_privilege(:r, 'public.report_export_jobs', :p)", r=APP, p=p)[0][0])
        members = sorted(str(tuple(r)) for r in su(
            db, "select m.roleid::regrole::text, m.member::regrole::text, m.grantor::regrole::text, "
                "(to_jsonb(m) - 'oid' - 'roleid' - 'member' - 'grantor')::text from pg_auth_members m "
                "where m.roleid::regrole::text in (:o, :a) or m.member::regrole::text in (:o, :a)", o=OWNER, a=APP))
        acl = sorted(str(tuple(r)) for r in su(
            db, "select gor.rolname, coalesce(gee.rolname,'PUBLIC'), a.privilege_type, a.is_grantable "
                "from pg_class k cross join lateral aclexplode(coalesce(k.relacl, acldefault('r', k.relowner))) a "
                "join pg_roles gor on gor.oid=a.grantor left join pg_roles gee on gee.oid=a.grantee "
                "where k.oid='public.report_export_jobs'::regclass"))
        return {"owner": owner, "app_effective": eff, "members": members, "acl": acl,
                "alembic": su(db, "select version_num from alembic_version")[0][0]}

    try:
        yield {"url": url, "su": su, "new_db": new_db, "snap": snap}
    finally:
        for name in reversed(created):
            drop_db(name)
        cleanup_roles()
        admin.dispose()


def test_dry_run_changes_nothing(cluster):
    db = cluster["new_db"]("repair_dry_test")
    before = cluster["snap"](db)
    assert before["owner"] == APP and before["alembic"] == DIAGNOSED_REV
    rc, out = _psql(cluster["url"], db, ADMIN)
    assert rc == 0, out[-2500:]
    assert "DRY RUN COMPLETE" in out and "validation passed" in out
    assert cluster["snap"](db) == before, "a dry run must leave owner, grants and memberships exactly as found"


def test_apply_transfers_ownership_and_keeps_runtime_access(cluster):
    db = cluster["new_db"]("repair_apply_test")
    before = cluster["snap"](db)
    rc, out = _psql(cluster["url"], db, ADMIN, "-v", "apply=1")
    assert rc == 0, out[-2500:]
    assert "APPLIED AND COMMITTED" in out and "DELETE correctly refused" in out
    after = cluster["snap"](db)
    assert after["owner"] == OWNER
    assert after["app_effective"] == ["INSERT", "SELECT", "UPDATE"]
    assert after["members"] == before["members"], "the temporary SET grant must not survive the transaction"
    assert after["alembic"] == DIAGNOSED_REV

    # A second run finds nothing to do and refuses.
    rc2, out2 = _psql(cluster["url"], db, ADMIN, "-v", "apply=1")
    assert rc2 != 0 and "already owned by docuaction_owner" in out2
    assert cluster["snap"](db) == after

    # The revision that failed on DEV now applies, and the chain reaches its head.
    _alembic(cluster["url"], db, "upgrade", "head")
    assert cluster["su"](db, "select count(*) from information_schema.columns where table_name="
                             "'report_export_jobs' and column_name in ('report_type','request_parameters')")[0][0] == 2
    assert cluster["snap"](db)["app_effective"] == ["INSERT", "SELECT", "UPDATE"]


async def test_runtime_role_can_create_read_and_update_a_report_job(cluster):
    """The application's own job functions, as docuaction_app -- not as a superuser."""
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.reports.data import export_jobs
    from app.reports.data.export_job_model import ReportExportJob

    db = cluster["new_db"]("repair_runtime_test")
    rc, out = _psql(cluster["url"], db, ADMIN, "-v", "apply=1")
    assert rc == 0, out[-2500:]
    _alembic(cluster["url"], db, "upgrade", "head")

    engine = create_async_engine(_with_db(cluster["url"], db, "postgresql+asyncpg://"),
                                 connect_args={"server_settings": {"role": APP}})
    try:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            who, is_super = (await s.execute(text(
                "select current_user, current_setting('is_superuser')"))).one()
            assert (who, is_super) == (APP, "off"), "this test must run with the runtime role's privileges"
            intake = uuid.uuid4()
            job = await export_jobs.request_job(
                s, identity=uuid.uuid4().hex * 2, export_type="report:delivery_processing", intake_id=intake,
                classification="DEVELOPMENT", generator_version="repair-test", requested_by="synthetic@test.invalid",
                report_type="delivery_processing", request_parameters={"intake_id": str(intake)})
            job_id = job.id if hasattr(job, "id") else job["job"].id            # created
            claimed = await export_jobs.claim_next_queued(s)                    # SELECT ... FOR UPDATE + UPDATE
            assert claimed is not None and claimed.id == job_id
            await export_jobs.heartbeat(s, job_id, phase="rendering")           # UPDATE
            await export_jobs.finish_succeeded(s, job_id, report_id="DA-TEST-0001", artifact_id="a" * 32,
                                               artifact_version=1, rendered_sha256="b" * 64, size_bytes=10)
            read_back = await export_jobs.get_job(s, job_id)                    # SELECT
            assert read_back.state == ReportExportJob.STATE_SUCCEEDED and read_back.report_id == "DA-TEST-0001"
            for forbidden in ("DELETE FROM public.report_export_jobs", "TRUNCATE public.report_export_jobs"):
                with pytest.raises(ProgrammingError) as denied:
                    await s.execute(text(forbidden))
                assert "permission denied" in str(denied.value)
                await s.rollback()
    finally:
        await engine.dispose()


def test_wrong_revision_stops_before_the_table_is_touched(cluster):
    db = cluster["new_db"]("repair_rev_test")
    before = cluster["snap"](db)
    rc, out = _psql(cluster["url"], db, ADMIN, "-v", "apply=1", "-v", "expected_rev=20260921_september_snapshot")
    assert rc != 0 and "expected 20260921_september_snapshot - nothing changed" in out
    assert cluster["snap"](db) == before


def test_a_role_without_authority_is_refused(cluster):
    db = cluster["new_db"]("repair_noauth_db_test")
    before = cluster["snap"](db)
    rc, out = _psql(cluster["url"], db, NOAUTH, "-v", "apply=1")
    assert rc != 0 and "does not hold docuaction_app's privileges" in out
    assert cluster["snap"](db) == before


def test_a_failed_validation_rolls_back_ownership_grants_and_memberships(cluster):
    """Make the post-transfer check fail for real: the runtime role inherits DELETE
    from another role, so 'exactly SELECT, INSERT, UPDATE' is false after the transfer."""
    db = cluster["new_db"]("repair_rollback_test")
    cluster["su"](db, f'GRANT DELETE ON public.report_export_jobs TO "{EXTRA}"')
    cluster["su"](db, f'GRANT "{EXTRA}" TO "{APP}"')
    try:
        before = cluster["snap"](db)
        rc, out = _psql(cluster["url"], db, ADMIN, "-v", "apply=1")
        assert rc != 0 and "VALIDATION FAILED: docuaction_app effective privileges" in out
        assert "APPLIED AND COMMITTED" not in out
        after = cluster["snap"](db)
        assert after == before, "ownership, grants and role memberships must all be rolled back"
        assert after["owner"] == APP
    finally:
        cluster["su"](db, f'REVOKE "{EXTRA}" FROM "{APP}"')
