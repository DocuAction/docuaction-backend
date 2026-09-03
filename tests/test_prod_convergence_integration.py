"""Three-step legacy-PROD -> Alembic convergence against a real disposable
PostgreSQL (a superuser DB, e.g. the CI postgres:16 service).

Models the certified DEV runtime security boundary:

  * legacy_owner       - owns the legacy tables, runs BOOTSTRAP A and FINALIZE
                         (PROD: pgadmin). Owner-capable operator.
  * migration_identity - runs MIGRATION B only. Non-privileged login, member of
                         docuaction_owner only. Models the dedicated Entra SP.
  * docuaction_app     - the application LOGIN identity, created by Bootstrap A
                         with the app password; OWNS non-Area-1 tables (its
                         access is by ownership); least-priv grants on Area-1.
  * docuaction_owner   - NOLOGIN; owns ONLY the Area-1 tables + alembic_version.

Neither docuaction_app nor docuaction_owner is a member of the other. Skips
unless CONV_SUPERUSER_URL is set. No PROD/Government data; synthetic rows only.
"""
import os
import subprocess
import sys
import time
import uuid

import pytest

SU = os.getenv("CONV_SUPERUSER_URL")
pytestmark = pytest.mark.skipif(not SU, reason="CONV_SUPERUSER_URL not set (needs a superuser test DB)")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONV = os.path.join(REPO, "scripts", "prod_legacy_convergence.py")

ABSENT = {"rce_source_intakes", "rce_source_records", "rce_ingestion_runs", "rce_curated_records",
          "rce_issues", "rce_rule_execution_history", "rce_correction_details", "review_records",
          "review_decision_events", "review_cycles", "review_reports", "review_rules", "review_samples",
          "sample_entities", "report_export_jobs", "report_artifacts", "rce_delivery_jobs",
          "tefca_dimension_evidence", "source_version_snapshots", "evidence_relationship_path"}
ADDITIVE = {"audit_logs": ["event_type", "outcome", "correlation_id"], "tefca_import_history": ["file_hash"]}
AREA1 = {"rce_source_records", "rce_source_intakes", "rce_ingestion_runs",
         "rce_rule_execution_history", "rce_delivery_jobs"}

LEGACY_OWNER = "legacy_owner"
MIGRATION_ID = "migration_identity"
APP_PW = "app_pw_synthetic_9x!"        # synthetic test password for docuaction_app LOGIN
CK = "ck_review_record_has_subject"
_ALL_ROLES = ("mig_test", "docuaction_app", "docuaction_owner", MIGRATION_ID, LEGACY_OWNER)


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


def _url_as(fixture_db, role, pw="x"):
    tail = fixture_db.split("@", 1)[1]
    return f"postgresql://{role}:{pw}@{tail}"


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
            for role in _ALL_ROLES:
                try:
                    c.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
                except Exception:  # noqa: BLE001
                    pass


def _run_conv(url, *args, expect_ok=True, extra_env=None):
    env = dict(os.environ, DATABASE_URL=url, SECRET_KEY="t" * 64, ALLOWED_HOSTS="*")
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, CONV, *args], cwd=REPO, env=env, capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, f"convergence step failed:\n{r.stdout[-1800:]}\n{r.stderr[-1800:]}"
    return r


def _expect_db_error(fn, *needles):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if needles:
            assert any(n.lower() in msg for n in needles), f"unexpected error: {e}"
        return str(e)
    raise AssertionError("expected a database error, but none was raised")


def _run_sql_as(url, sql, pw="x"):
    """Run one statement in its own transaction as `url`'s role; errors propagate
    and the transaction rolls back cleanly."""
    from sqlalchemy import text
    with _eng(url).begin() as c:
        c.execute(text(sql))


def _build_prod_like(url):
    import sqlalchemy as sa
    from sqlalchemy import text
    md = _md()
    eng = _eng(url)
    with eng.begin() as c:
        for r in _ALL_ROLES:
            c.execute(text(f'DROP ROLE IF EXISTS "{r}"'))
        c.execute(text(f"CREATE ROLE {LEGACY_OWNER} LOGIN PASSWORD 'x' NOSUPERUSER CREATEROLE NOBYPASSRLS"))
        c.execute(text(f"CREATE ROLE {MIGRATION_ID} LOGIN PASSWORD 'x' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
        c.execute(text(f"ALTER SCHEMA public OWNER TO {LEGACY_OWNER}"))
        legacy_tables = [t for t in md.sorted_tables if t.name not in ABSENT]
        md.create_all(bind=c, tables=legacy_tables, checkfirst=True)
        c.execute(text("CREATE TABLE bulletin_articles (id uuid primary key, title text)"))
        c.execute(text("INSERT INTO bulletin_articles (id,title) VALUES (:i,'legacy-only')"), {"i": str(uuid.uuid4())})
    with eng.begin() as c:
        for t, cols in ADDITIVE.items():
            for cn in cols:
                c.execute(text(f'ALTER TABLE public."{t}" DROP COLUMN IF EXISTS "{cn}"'))
        c.execute(text("DROP TABLE IF EXISTS alembic_version"))
        for tn in c.execute(text("select tablename from pg_tables where schemaname='public'")).scalars().all():
            c.execute(text(f'ALTER TABLE public."{tn}" OWNER TO {LEGACY_OWNER}'))
        c.execute(text("insert into users (id,tenant_id,email,password_hash,full_name,company,role,plan,allowed_modules,is_active,is_verified,status,created_at,updated_at,last_active_at) "
                       "values (:i,'legacy','fix@synthetic.invalid','x','L','C','viewer','free','[]'::json,true,true,'active',now(),now(),now())"), {"i": str(uuid.uuid4())})
    return eng


def _attrs(conn, role):
    from sqlalchemy import text
    return conn.execute(text(
        "select rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin from pg_roles where rolname=:r"),
        {"r": role}).first()


def _member(conn, m, g):
    from sqlalchemy import text
    return conn.execute(text("select pg_has_role(:m,:g,'MEMBER')"), {"m": m, "g": g}).scalar()


def _owner(conn, t):
    from sqlalchemy import text
    return conn.execute(text("select relowner::regrole::text from pg_class where relname=:t"), {"t": t}).scalar()


# ─────────────────────────────────────────────────────────────────────────────
def test_three_step_convergence_and_all_gates(fixture_db):
    import sqlalchemy as sa
    from sqlalchemy import text
    su = _build_prod_like(fixture_db)
    insp = sa.inspect

    with su.connect() as c:
        pre_ids = set(x[0] for x in c.execute(text("select id from users")).all())
        pre_users = len(pre_ids)
        assert _owner(c, "users") == LEGACY_OWNER
        assert _attrs(c, MIGRATION_ID)[:4] == (False, False, False, False), "migration_identity must be least privilege"

    mig_url = _url_as(fixture_db, MIGRATION_ID)
    mig = _eng(mig_url)
    # NEG: before Bootstrap A, migration_identity cannot re-own or SET ROLE legacy_owner, and Migration B refuses
    _expect_db_error(lambda: _run_sql_as(mig_url, f"ALTER TABLE public.users OWNER TO {MIGRATION_ID}"),
                     "must be owner", "permission denied", "insufficient")
    _expect_db_error(lambda: _run_sql_as(mig_url, f"SET ROLE {LEGACY_OWNER}"),
                     "permission denied", "cannot", "insufficient")
    r = _run_conv(mig_url, "--migrate", "--i-understand-migration-writes", expect_ok=False)
    assert r.returncode != 0 and "Bootstrap A" in (r.stdout + r.stderr)
    print("NEG_PREBOOTSTRAP=PASS")

    # BOOTSTRAP A (legacy_owner) - creates docuaction_app LOGIN with the app password
    rb = _run_conv(_url_as(fixture_db, LEGACY_OWNER), "--bootstrap", "--i-understand-bootstrap-writes",
                   extra_env={"CONV_APP_PASSWORD": APP_PW})
    assert "BOOTSTRAP A COMPLETE" in rb.stdout, rb.stdout[-400:]
    with su.connect() as c:
        s, cdb, crole, byp, login = _attrs(c, "docuaction_app")
        assert (s, cdb, crole, byp, login) == (False, False, False, False, True), "docuaction_app must be LOGIN least-priv"
        s2, _, _, byp2, login2 = _attrs(c, "docuaction_owner")
        assert (s2, byp2, login2) == (False, False, False), "docuaction_owner must be NOLOGIN least-priv"
        assert not _member(c, "docuaction_app", "docuaction_owner"), "app must NOT be a member of owner"
        assert not _member(c, "docuaction_owner", "docuaction_app"), "owner must NOT be a member of app"
        assert _owner(c, "users") == "docuaction_owner", "candidate tables temporarily owned by owner for the chain"
        assert _owner(c, "bulletin_articles") == LEGACY_OWNER, "legacy-only ownership preserved"
        assert not insp(c).has_table("alembic_version"), "Bootstrap A runs no Alembic"
    print("BOOTSTRAP_A=PASS")

    # provision migration identity (admin grants owner membership only)
    with su.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text(f"GRANT docuaction_owner TO {MIGRATION_ID}"))
    with mig.begin() as c:  # NEG7: SET ROLE owner works only after membership
        c.execute(text("SET ROLE docuaction_owner"))
        assert c.execute(text("select current_user")).scalar() == "docuaction_owner"

    # MIGRATION B (migration_identity)
    t0 = time.time()
    rm = _run_conv(mig_url, "--migrate", "--i-understand-migration-writes")
    print(f"CONVERGENCE_ELAPSED_SECONDS={time.time()-t0:.1f}")
    assert "MIGRATION B COMPLETE" in rm.stdout
    assert f"session_user={MIGRATION_ID}" in rm.stdout and "current_user(after SET ROLE)=docuaction_owner" in rm.stdout
    with su.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == ["20260903_delivery_grants"]
    print("MIGRATION_B=PASS")

    # FINALIZE (legacy_owner) - reassign to DEV ownership model
    rf = _run_conv(_url_as(fixture_db, LEGACY_OWNER), "--finalize", "--i-understand-finalize-writes")
    assert "FINALIZE COMPLETE" in rf.stdout

    # ── GATES ──
    with su.connect() as c:
        ins = insp(c)
        # OWNERSHIP matches DEV: non-Area-1 -> docuaction_app; Area-1 + alembic_version -> docuaction_owner
        for t in ("users", "documents", "audit_logs", "review_records", "report_artifacts", "tefca_import_history"):
            assert _owner(c, t) == "docuaction_app", f"{t} must be owned by docuaction_app"
        for t in AREA1:
            assert _owner(c, t) == "docuaction_owner", f"{t} (Area-1) must stay docuaction_owner"
        assert _owner(c, "alembic_version") == "docuaction_owner"
        assert _owner(c, "bulletin_articles") == LEGACY_OWNER, "legacy-only untouched"
        # schema + column equivalence
        md = _md()
        model_tables = {t.name for t in md.sorted_tables}
        assert model_tables <= set(ins.get_table_names(schema="public"))
        for cn in ADDITIVE["audit_logs"]:
            assert cn in {col["name"] for col in ins.get_columns("audit_logs", schema="public")}
        # DATA preserved
        assert set(x[0] for x in c.execute(text("select id from users")).all()) == pre_ids
        assert c.execute(text("select count(*) from bulletin_articles")).scalar() == 1
        # DELETE denied where required (docuaction_app on rce_delivery_jobs)
        privs = {p: c.execute(text("select has_table_privilege('docuaction_app','rce_delivery_jobs',:p)"), {"p": p}).scalar()
                 for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
        assert privs == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}, privs
        # least-priv attrs unchanged
        assert _attrs(c, "docuaction_app")[:4] == (False, False, False, False)
    print("OWNERSHIP_DEV_MODEL=PASS")

    # APP connects DIRECTLY as docuaction_app and works
    app = _eng(_url_as(fixture_db, "docuaction_app", pw=APP_PW))
    with app.begin() as c:
        su_, cu_ = c.execute(text("select session_user, current_user")).first()
        assert su_ == "docuaction_app" and cu_ == "docuaction_app", (su_, cu_)
        assert c.execute(text("select count(*) from users")).scalar() == pre_users     # SELECT owned
        c.execute(text("update users set last_active_at=now()"))                        # UPDATE owned
        c.execute(text("insert into audit_logs (id,tenant_id,action,created_at) values (:i,'legacy','app',now())"),
                  {"i": str(uuid.uuid4())})                                              # INSERT owned
        c.execute(text("select count(*) from rce_source_records"))                      # SELECT Area-1 (grant)
    print("APP_RUNTIME=PASS (connects as docuaction_app; owns non-Area-1; Area-1 by grant)")

    # NEG: app cannot SET ROLE docuaction_owner, cannot alter Area-1 ownership (no migration/ownership power)
    app_url = _url_as(fixture_db, "docuaction_app", pw=APP_PW)
    _expect_db_error(lambda: _run_sql_as(app_url, "SET ROLE docuaction_owner"),
                     "permission denied", "cannot", "insufficient")
    _expect_db_error(lambda: _run_sql_as(app_url, "ALTER TABLE public.rce_source_records OWNER TO docuaction_app"),
                     "must be owner", "permission denied", "insufficient")
    print("NEG_APP_CANNOT_ESCALATE=PASS")

    # IDEMPOTENCY: second Migration B refuses (alembic present)
    r2 = _run_conv(mig_url, "--migrate", "--i-understand-migration-writes", expect_ok=False)
    assert r2.returncode != 0 and "already present" in (r2.stdout + r2.stderr)
    print("THREE_STEP_CONVERGENCE=PASS")


def test_forced_failure_is_fail_closed(fixture_db):
    import sqlalchemy as sa
    from sqlalchemy import text
    _build_prod_like(fixture_db)
    _run_conv(_url_as(fixture_db, LEGACY_OWNER), "--bootstrap", "--i-understand-bootstrap-writes",
              extra_env={"CONV_APP_PASSWORD": APP_PW})
    with _eng(SU).connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text(f"GRANT docuaction_owner TO {MIGRATION_ID}"))
    with _eng(_url_as(fixture_db, MIGRATION_ID)).begin() as c:
        c.execute(text("SET ROLE docuaction_owner"))
        c.execute(text("CREATE TABLE rce_delivery_jobs (id integer primary key)"))
    r = _run_conv(_url_as(fixture_db, MIGRATION_ID), "--migrate", "--i-understand-migration-writes", expect_ok=False)
    assert r.returncode != 0, f"expected the chain to fail on the decoy:\n{r.stdout[-600:]}"
    with _eng(SU).connect() as c:
        has = sa.inspect(c).has_table("alembic_version")
        rev = c.execute(text("select version_num from alembic_version")).scalars().all() if has else None
    assert rev != ["20260903_delivery_grants"], "must not report head after a failed chain"
    print(f"FORCED_FAILURE=FAIL_CLOSED rev={rev} recovery=EXPLICIT_REPAIR_OR_PITR")


# ── Retained PR #34/#35 regression: fresh Alembic build + 20260831 guard ──
def _ensure_roles(eng):
    from sqlalchemy import text
    with eng.begin() as c:
        for r in ("docuaction_owner", "docuaction_app"):
            c.execute(text(f'DROP ROLE IF EXISTS "{r}"'))
            c.execute(text(f'CREATE ROLE "{r}" WITH NOLOGIN NOSUPERUSER NOBYPASSRLS'))
        c.execute(text('GRANT "docuaction_owner" TO CURRENT_USER'))
        c.execute(text('GRANT CREATE, USAGE ON SCHEMA public TO "docuaction_owner"'))


def _ck_count(conn):
    from sqlalchemy import text
    return conn.execute(text(
        "select count(*) from pg_constraint c join pg_class t on t.oid = c.conrelid "
        "join pg_namespace n on n.oid = t.relnamespace "
        "where c.conname = :n and t.relname = 'review_records' and n.nspname = 'public'"),
        {"n": CK}).scalar()


def _alembic_cfg(url):
    from alembic.config import Config
    cfg = Config(os.path.join(REPO, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("postgresql+asyncpg://", "postgresql://").replace("%", "%%"))
    os.environ["DATABASE_URL"] = url.replace("postgresql+asyncpg://", "postgresql://")
    return cfg


def test_fresh_alembic_upgrade_head_from_empty(fixture_db):
    from sqlalchemy import text
    from alembic import command
    from alembic.script import ScriptDirectory
    eng = _eng(fixture_db)
    _ensure_roles(eng)
    cfg = _alembic_cfg(fixture_db)
    os.environ["DB_APP_ROLE"] = "docuaction_app"
    os.environ["DB_MIGRATION_ROLE"] = "docuaction_owner"
    command.upgrade(cfg, "head")
    assert ScriptDirectory.from_config(cfg).get_heads() == ["20260903_delivery_grants"]
    with eng.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == ["20260903_delivery_grants"]
        assert _ck_count(c) == 1
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        assert _ck_count(c) == 1
    print("FRESH_ALEMBIC_BUILD=PASS head=20260903_delivery_grants ck_count=1 rerun=no-op")


def test_20260831_skips_ck_when_already_present(fixture_db):
    from alembic import command
    eng = _eng(fixture_db)
    _ensure_roles(eng)
    cfg = _alembic_cfg(fixture_db)
    os.environ["DB_APP_ROLE"] = "docuaction_app"; os.environ["DB_MIGRATION_ROLE"] = "docuaction_owner"
    command.upgrade(cfg, "20260830_run_lifecycle")
    with eng.connect() as c:
        assert _ck_count(c) == 1
    command.upgrade(cfg, "20260831_review_case")
    with eng.connect() as c:
        assert _ck_count(c) == 1
    print("REGRESSION_SKIP_WHEN_PRESENT=PASS")


def test_20260831_creates_ck_when_absent(fixture_db):
    from sqlalchemy import text
    from alembic import command
    eng = _eng(fixture_db)
    _ensure_roles(eng)
    cfg = _alembic_cfg(fixture_db)
    os.environ["DB_APP_ROLE"] = "docuaction_app"; os.environ["DB_MIGRATION_ROLE"] = "docuaction_owner"
    command.upgrade(cfg, "20260830_run_lifecycle")
    with eng.begin() as c:
        c.execute(text(f'ALTER TABLE public.review_records DROP CONSTRAINT "{CK}"'))
    with eng.connect() as c:
        assert _ck_count(c) == 0
    command.upgrade(cfg, "20260831_review_case")
    with eng.connect() as c:
        assert _ck_count(c) == 1
    print("REGRESSION_CREATE_WHEN_ABSENT=PASS")
