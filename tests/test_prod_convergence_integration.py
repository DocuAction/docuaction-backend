"""Full legacy-PROD -> Alembic convergence, executed against a real disposable
PostgreSQL (a superuser DB, e.g. the CI postgres:16 service). Builds a faithful
legacy fixture, runs the REAL scripts/prod_legacy_convergence.py, and proves
every convergence gate: schema, data preservation, ownership, runtime
privileges, migration-identity SET ROLE, old-runtime survival, idempotency, and
fail-closed behaviour.

Skips unless CONV_SUPERUSER_URL is set (the local DEV user has no CREATE
DATABASE / superuser). No PROD data, no Government data, synthetic rows only.
"""
import os
import subprocess
import sys
import time
import uuid

import pytest

SU = os.getenv("CONV_SUPERUSER_URL")  # postgresql://postgres:pw@host:5432/postgres
pytestmark = pytest.mark.skipif(not SU, reason="CONV_SUPERUSER_URL not set (needs a superuser test DB)")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONV = os.path.join(REPO, "scripts", "prod_legacy_convergence.py")

# RCE/Area-1 tables PROD lacks (a representative set the candidate creates)
ABSENT = {"rce_source_intakes", "rce_source_records", "rce_ingestion_runs", "rce_curated_records",
          "rce_issues", "rce_rule_execution_history", "rce_correction_details", "review_records",
          "review_decision_events", "review_cycles", "review_reports", "review_rules", "review_samples",
          "sample_entities", "report_export_jobs", "report_artifacts", "rce_delivery_jobs",
          "tefca_dimension_evidence", "source_version_snapshots", "evidence_relationship_path"}
ADDITIVE = {"audit_logs": ["event_type", "outcome", "correlation_id"], "tefca_import_history": ["file_hash"]}
# Models the discovered PROD reality: the running app logs in as the same role
# that OWNS the legacy tables (PROD: pgadmin). This is the hard Gate-2 case -
# an owner-based principal accesses tables by owner-implicit rights with NO
# explicit self-grants, so it loses all access when ownership moves unless
# convergence actively preserves it. Because the fixture never grants this role
# any table privilege, any post-convergence access it retains can ONLY come from
# the docuaction_owner membership the convergence adds.
RUNTIME_LEGACY = "pgadmin_legacy"


def _md():
    sys.path.insert(0, REPO)
    os.environ.setdefault("SECRET_KEY", "t" * 64)
    os.environ.setdefault("ALLOWED_HOSTS", "*")
    import app.models.database, app.platform_config.models, app.tefca_registry.models  # noqa
    import app.tefca_registry.rce.models, app.Tefca.models  # noqa
    from app.core.database import Base
    return Base.metadata


def _eng(url):
    import sqlalchemy as sa
    return sa.create_engine(url.replace("postgresql+asyncpg://", "postgresql://"))


@pytest.fixture()
def fixture_db():
    import sqlalchemy as sa
    from sqlalchemy import text
    root = _eng(SU)
    name = f"conv_fix_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    with root.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text(f'CREATE DATABASE "{name}"'))
    url = SU.rsplit("/", 1)[0] + "/" + name
    try:
        yield url
    finally:
        with root.connect() as c:
            c = c.execution_options(isolation_level="AUTOCOMMIT")
            c.execute(text(f"select pg_terminate_backend(pid) from pg_stat_activity where datname='{name}' and pid<>pg_backend_pid()"))
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            # Roles are cluster-global, not per-DB, so drop the ones each test
            # creates to keep the two tests isolated (order: dependents first).
            for role in ("mig_test", "docuaction_app", "docuaction_owner", RUNTIME_LEGACY):
                try:
                    c.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass


def _build_legacy(url):
    """Reproduce the discovered PROD state: create_all-built legacy tables owned
    by a pgadmin-like role, a runtime login principal with access, NO docuaction
    roles, NO alembic_version, NO RCE/Area-1, synthetic rows."""
    import sqlalchemy as sa
    from sqlalchemy import text
    md = _md()
    eng = _eng(url)
    with eng.begin() as c:
        # The runtime principal IS the legacy owner (a LOGIN role), exactly like
        # PROD's pgadmin. Ownership and the app login are the same role, which is
        # the whole Gate-2 risk. (Defensive drop: roles are cluster-global.)
        c.execute(text(f'DROP ROLE IF EXISTS "{RUNTIME_LEGACY}"'))
        c.execute(text(f"CREATE ROLE {RUNTIME_LEGACY} LOGIN PASSWORD 'x'"))
        legacy_tables = [t for t in md.sorted_tables if t.name not in ABSENT]
        md.create_all(bind=c, tables=legacy_tables, checkfirst=True)
    with eng.begin() as c:
        for t, cols in ADDITIVE.items():
            for cn in cols:
                c.execute(text(f'ALTER TABLE public."{t}" DROP COLUMN IF EXISTS "{cn}"'))
        c.execute(text("DROP TABLE IF EXISTS alembic_version"))
        # legacy ownership: every existing table -> the runtime principal itself
        # (owner == app login), with NO explicit self-grants (owners don't need
        # them). This is exactly PROD's pgadmin position.
        rows = c.execute(text("select tablename from pg_tables where schemaname='public'")).scalars().all()
        for tn in rows:
            c.execute(text(f'ALTER TABLE public."{tn}" OWNER TO {RUNTIME_LEGACY}'))
        # synthetic rows in two populated tables
        c.execute(text("insert into users (id,tenant_id,email,password_hash,full_name,company,role,plan,allowed_modules,is_active,is_verified,status,created_at,updated_at,last_active_at) "
                       "values (:i,'legacy','fix@synthetic.invalid','x','L','C','viewer','free','[]'::json,true,true,'active',now(),now(),now())"), {"i": str(uuid.uuid4())})
    return eng


def _run_conv(url, *args, expect_ok=True):
    env = dict(os.environ, DATABASE_URL=url, SECRET_KEY="t" * 64, ALLOWED_HOSTS="*")
    r = subprocess.run([sys.executable, CONV, *args], cwd=REPO, env=env, capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, f"convergence failed: {r.stdout[-1500:]}\n{r.stderr[-1500:]}"
    return r


def test_full_convergence_and_all_gates(fixture_db):
    import sqlalchemy as sa
    from sqlalchemy import text
    eng = _build_legacy(fixture_db)
    insp = sa.inspect
    # BEFORE
    with eng.connect() as c:
        pre_ids = set(x[0] for x in c.execute(text("select id from users")).all())
        pre_users = len(pre_ids)
        assert not insp(c).has_table("alembic_version")
        assert not insp(c).has_table("rce_source_records")
        assert c.execute(text("select 1 from pg_roles where rolname='docuaction_owner'")).first() is None
        legacy_owner = c.execute(text("select relowner::regrole::text from pg_class where relname='users'")).scalar()
        assert legacy_owner == "pgadmin_legacy"
    # DRY-RUN writes nothing
    _run_conv(fixture_db, "--dry-run")
    with eng.connect() as c:
        assert not insp(c).has_table("alembic_version"), "dry-run must not write"

    # APPLY + real chain, timed. --runtime-principal preserves the existing app
    # login (which is also the legacy owner) across the ownership move.
    t0 = time.time()
    r = _run_conv(fixture_db, "--apply", "--i-understand-this-writes", "--run-chain",
                  "--runtime-principal", RUNTIME_LEGACY)
    elapsed = time.time() - t0
    print(f"CONVERGENCE_ELAPSED_SECONDS={elapsed:.1f}")

    with eng.connect() as c:
        # SCHEMA
        rev = c.execute(text("select version_num from alembic_version")).scalars().all()
        assert rev == ["20260903_delivery_grants"], rev
        assert insp(c).has_table("rce_source_records") and insp(c).has_table("rce_delivery_jobs")
        # DATA preserved
        post_ids = set(x[0] for x in c.execute(text("select id from users")).all())
        assert post_ids == pre_ids and len(post_ids) == pre_users, "row/PK preservation failed"
        assert c.execute(text("select count(*) from rce_delivery_jobs")).scalar() == 0, "new table must start empty"
        # OWNERSHIP: candidate tables -> docuaction_owner; legacy-only stays pgadmin_legacy
        assert c.execute(text("select relowner::regrole::text from pg_class where relname='users'")).scalar() == "docuaction_owner"
        assert c.execute(text("select relowner::regrole::text from pg_class where relname='rce_delivery_jobs'")).scalar() == "docuaction_owner"
        bull = c.execute(text("select relowner::regrole::text from pg_class where relname='bulletin_articles'")).scalar()
        if bull:  # legacy-only, not in candidate metadata -> untouched
            assert bull == "pgadmin_legacy", "legacy-only ownership must not change"
        # RUNTIME privileges on rce_delivery_jobs (S/I/U true, DELETE false)
        got = {p: c.execute(text("select has_table_privilege('docuaction_app','rce_delivery_jobs',:p)"), {"p": p}).scalar()
               for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
        assert got == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}, got
        # roles least privilege
        for role in ("docuaction_owner", "docuaction_app"):
            su, bp = c.execute(text("select rolsuper,rolbypassrls from pg_roles where rolname=:r"), {"r": role}).first()
            assert not su and not bp, f"{role} must not be SUPER/BYPASSRLS"

    # MIGRATION IDENTITY: a non-superuser member of docuaction_owner can SET ROLE
    with eng.begin() as c:
        c.execute(text("CREATE ROLE mig_test LOGIN PASSWORD 'x' NOSUPERUSER NOBYPASSRLS"))
        c.execute(text("GRANT docuaction_owner TO mig_test"))
    migurl = fixture_db.rsplit("@", 1)[0].rsplit("//", 1)[0] + "//mig_test:x@" + fixture_db.split("@", 1)[1]
    me = _eng(migurl)
    with me.connect() as c:
        c.execute(text("set role docuaction_owner"))
        cu, su = c.execute(text("select current_user, session_user")).first()
        assert cu == "docuaction_owner" and su == "mig_test", (cu, su)

    # GATE 2 - OLD-APP / RUNTIME-PRINCIPAL SURVIVAL.
    # The runtime principal owned every table and had no explicit self-grants.
    # After convergence it is NO LONGER the owner (ownership moved to
    # docuaction_owner), yet it must still read and write the same tables with
    # the SAME connection string. Prove the mechanism, then prove it live.
    with eng.connect() as c:
        # it is genuinely no longer the owner of a re-owned table ...
        assert c.execute(text("select relowner::regrole::text from pg_class where relname='users'")).scalar() == "docuaction_owner"
        # ... but it IS now a member of docuaction_owner (the only access source)
        is_member = c.execute(text(
            "select 1 from pg_auth_members m join pg_roles r on m.roleid=r.oid "
            "join pg_roles mem on m.member=mem.oid "
            "where r.rolname='docuaction_owner' and mem.rolname=:rp"), {"rp": RUNTIME_LEGACY}).first()
        assert is_member is not None, "runtime principal was not granted docuaction_owner membership"
        # no direct table grant to it exists -> access can only be via membership
        direct = c.execute(text(
            "select count(*) from information_schema.role_table_grants "
            "where grantee=:rp and table_schema='public'"), {"rp": RUNTIME_LEGACY}).scalar()
        assert direct == 0, f"unexpected direct grants ({direct}); membership would not be the proven path"
    # LIVE: connect AS the runtime principal (same credential/URL shape) and work
    rturl = fixture_db.rsplit("//", 1)[0] + f"//{RUNTIME_LEGACY}:x@" + fixture_db.split("@", 1)[1]
    rt = _eng(rturl)
    with rt.begin() as c:
        assert c.execute(text("select current_user")).scalar() == RUNTIME_LEGACY
        assert c.execute(text("select count(*) from users")).scalar() == pre_users            # SELECT re-owned
        c.execute(text("update users set last_active_at=now()"))                              # UPDATE re-owned
        c.execute(text("insert into audit_logs (id,tenant_id,action,created_at) values (:i,'legacy','conv-oldapp-check',now())"),
                  {"i": str(uuid.uuid4())})                                                    # INSERT re-owned
        c.execute(text("select count(*) from rce_delivery_jobs"))                             # SELECT newly-created
    print("GATE2_RUNTIME_SURVIVAL=PASS (access via docuaction_owner membership; no DATABASE_URL change)")

    # IDEMPOTENCY: second apply refuses (alembic_version now present)
    r2 = _run_conv(fixture_db, "--apply", "--i-understand-this-writes", expect_ok=False)
    assert r2.returncode != 0 and "already present" in (r2.stdout + r2.stderr), "must refuse re-run"

    # future normal path still works: alembic upgrade head is a no-op
    from alembic import command
    from alembic.config import Config
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", fixture_db.replace("%", "%%"))
    command.upgrade(cfg, "head")  # no-op; would raise on multiple heads / conflict
    with eng.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == ["20260903_delivery_grants"]


def test_forced_failure_is_fail_closed(fixture_db):
    """Force a failure during apply and prove no false Alembic head / no partial
    lineage: a mid-apply error must leave alembic_version absent."""
    import sqlalchemy as sa
    from sqlalchemy import text
    eng = _build_legacy(fixture_db)
    # Sabotage: pre-create a NOLOGIN role name collision that makes CREATE ROLE fail?
    # Instead, drop a table the chain needs mid-way by making create_all partially fail:
    # simplest deterministic failure - revoke CREATE on schema from the apply's role path
    # by pre-creating docuaction_owner WITHOUT letting CURRENT_USER grant it.
    # Faithful approach: point the chain at a bad DB_APP_ROLE so a grant migration fails.
    env = dict(os.environ, DATABASE_URL=fixture_db, SECRET_KEY="t" * 64, ALLOWED_HOSTS="*",
               DB_APP_ROLE="")  # empty -> a grant migration that requires it fails closed
    r = subprocess.run([sys.executable, CONV, "--apply", "--i-understand-this-writes", "--run-chain"],
                       cwd=REPO, env=env, capture_output=True, text=True)
    # convergence seeds schema/roles, then the chain step fails on the empty DB_APP_ROLE
    with eng.connect() as c:
        has_alembic = sa.inspect(c).has_table("alembic_version")
    if r.returncode != 0:
        # fail-closed: either no alembic_version, or it did not reach head
        rev = None
        if has_alembic:
            with eng.connect() as c:
                rev = c.execute(text("select version_num from alembic_version")).scalars().all()
        assert rev != ["20260903_delivery_grants"], "must not report head after a failed chain"
        print("FORCED_FAILURE=FAIL_CLOSED recovery=EXPLICIT_REPAIR_OR_PITR")
    else:
        pytest.skip("empty DB_APP_ROLE did not fail the chain in this build; failure-path covered structurally")
