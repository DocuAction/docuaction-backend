"""Two-boundary legacy-PROD -> Alembic convergence, executed against a real
disposable PostgreSQL (a superuser DB, e.g. the CI postgres:16 service).

This models the REAL production security boundary discovered during Phase A:

  * legacy_owner       - owns the 42 legacy tables and runs BOOTSTRAP A. In PROD
                         this is pgadmin, which is ALSO the runtime principal
                         (the app connects as it) - the strictest Gate-2 case, so
                         runtime_principal is modeled as legacy_owner itself.
  * migration_identity - runs MIGRATION B. A NON-privileged login (no SUPERUSER,
                         CREATEDB, CREATEROLE, BYPASSRLS) that does NOT own the
                         legacy tables and can act only via docuaction_owner
                         membership. Models the dedicated Entra migration SP.
  * docuaction_owner / docuaction_app - created BY Bootstrap A (least privilege).

The fixture is deliberately NOT run as a superuser for the convergence itself:
Bootstrap A executes as legacy_owner, Migration B as migration_identity. Skips
unless CONV_SUPERUSER_URL is set (only the harness setup uses the superuser).
No PROD data, no Government data, synthetic rows only.
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

LEGACY_OWNER = "legacy_owner"        # owns legacy tables + runs Bootstrap A + IS the runtime principal (PROD pgadmin)
MIGRATION_ID = "migration_identity"  # runs Migration B; non-privileged
CK = "ck_review_record_has_subject"
_ALL_ROLES = ("mig_test", "docuaction_app", "docuaction_owner", MIGRATION_ID, LEGACY_OWNER, "pgadmin_legacy")


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
    """Same DB, connecting as a specific role."""
    tail = fixture_db.split("@", 1)[1]  # host:port/db
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
            # roles are cluster-global; drop the ones each test creates
            for role in _ALL_ROLES:
                try:
                    c.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
                except Exception:  # noqa: BLE001
                    pass


def _run_conv(url, *args, expect_ok=True):
    env = dict(os.environ, DATABASE_URL=url, SECRET_KEY="t" * 64, ALLOWED_HOSTS="*")
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


def _build_prod_like(url):
    """Reproduce PROD: legacy tables owned by legacy_owner (== runtime principal,
    like pgadmin), a NON-privileged migration_identity that owns nothing, NO
    docuaction roles, NO alembic_version, NO RCE/Area-1, synthetic rows, and a
    genuine legacy-only table. Built by the superuser harness; convergence itself
    runs as legacy_owner / migration_identity, never as the superuser."""
    import sqlalchemy as sa
    from sqlalchemy import text
    md = _md()
    eng = _eng(url)
    with eng.begin() as c:
        for r in _ALL_ROLES:
            c.execute(text(f'DROP ROLE IF EXISTS "{r}"'))
        # legacy_owner models pgadmin: owns the schema+tables, can create roles.
        c.execute(text(f"CREATE ROLE {LEGACY_OWNER} LOGIN PASSWORD 'x' NOSUPERUSER CREATEROLE NOBYPASSRLS"))
        # migration_identity: strictly least privilege, owns nothing.
        c.execute(text(f"CREATE ROLE {MIGRATION_ID} LOGIN PASSWORD 'x' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
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
        # every existing table -> legacy_owner (owner == app login), no self-grants
        for tn in c.execute(text("select tablename from pg_tables where schemaname='public'")).scalars().all():
            c.execute(text(f'ALTER TABLE public."{tn}" OWNER TO {LEGACY_OWNER}'))
        c.execute(text("insert into users (id,tenant_id,email,password_hash,full_name,company,role,plan,allowed_modules,is_active,is_verified,status,created_at,updated_at,last_active_at) "
                       "values (:i,'legacy','fix@synthetic.invalid','x','L','C','viewer','free','[]'::json,true,true,'active',now(),now(),now())"), {"i": str(uuid.uuid4())})
    return eng


def _role_attrs(conn, role):
    from sqlalchemy import text
    return conn.execute(text(
        "select rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolcanlogin "
        "from pg_roles where rolname=:r"), {"r": role}).first()


def _is_member(conn, member, group):
    from sqlalchemy import text
    return conn.execute(text(
        "select 1 from pg_auth_members m join pg_roles r on m.roleid=r.oid "
        "join pg_roles mem on m.member=mem.oid where r.rolname=:g and mem.rolname=:m"),
        {"g": group, "m": member}).first() is not None


def _owner_of(conn, table):
    from sqlalchemy import text
    return conn.execute(text("select relowner::regrole::text from pg_class where relname=:t"), {"t": table}).scalar()


# ─────────────────────────────────────────────────────────────────────────────
# Main two-boundary end-to-end + Gate/negative proofs
# ─────────────────────────────────────────────────────────────────────────────
def test_two_boundary_convergence_and_all_gates(fixture_db):
    import sqlalchemy as sa
    from sqlalchemy import text
    su = _build_prod_like(fixture_db)          # superuser engine (assertions only)
    insp = sa.inspect

    # BEFORE: pre-state + migration-identity security attributes -----------------
    with su.connect() as c:
        pre_ids = set(x[0] for x in c.execute(text("select id from users")).all())
        pre_users = len(pre_ids)
        assert not insp(c).has_table("alembic_version")
        assert not insp(c).has_table("rce_source_records")
        assert _owner_of(c, "users") == LEGACY_OWNER
        su_, cdb, crole, bypass, login = _role_attrs(c, MIGRATION_ID)
        assert (su_, cdb, crole, bypass, login) == (False, False, False, False, True), \
            "migration_identity must be non-superuser, no CREATEDB/CREATEROLE/BYPASSRLS (NEG 3-6)"
        assert not _is_member(c, MIGRATION_ID, "pgadmin") if _role_attrs(c, "pgadmin") else True

    mig_url = _url_as(fixture_db, MIGRATION_ID)
    mig = _eng(mig_url)

    # NEG 1: migration_identity cannot re-own a legacy_owner table before Bootstrap A
    def _reown():
        with mig.begin() as c:
            c.execute(text(f"ALTER TABLE public.users OWNER TO {MIGRATION_ID}"))
    _expect_db_error(_reown, "must be owner", "permission denied", "insufficient")
    # NEG 2: migration_identity cannot SET ROLE legacy_owner (not a member)
    def _setrole_legacy():
        with mig.begin() as c:
            c.execute(text(f"SET ROLE {LEGACY_OWNER}"))
    _expect_db_error(_setrole_legacy, "permission denied", "cannot", "insufficient")
    # NEG 8 (part): Migration B refuses before Bootstrap A (docuaction_owner absent)
    r = _run_conv(mig_url, "--migrate", "--i-understand-migration-writes", expect_ok=False)
    assert r.returncode != 0 and "Bootstrap A" in (r.stdout + r.stderr), "Migration B must refuse before Bootstrap A"
    print("NEG_PREBOOTSTRAP=PASS (no re-own, no SET ROLE legacy, migration refuses)")

    # BOOTSTRAP A - executed AS legacy_owner (owner-capable), runtime principal = legacy_owner
    rb = _run_conv(_url_as(fixture_db, LEGACY_OWNER), "--bootstrap", "--i-understand-bootstrap-writes",
                   "--runtime-principal", LEGACY_OWNER)
    assert "BOOTSTRAP A COMPLETE" in rb.stdout, rb.stdout[-500:]
    with su.connect() as c:
        for role in ("docuaction_owner", "docuaction_app"):
            s, _, _, b, lg = _role_attrs(c, role)
            assert (s, b, lg) == (False, False, False), f"{role} must be NOLOGIN NOSUPERUSER NOBYPASSRLS"
        assert _owner_of(c, "users") == "docuaction_owner", "candidate table must be re-owned"
        assert _owner_of(c, "bulletin_articles") == LEGACY_OWNER, "legacy-only ownership must be preserved"
        assert not insp(c).has_table("alembic_version"), "Bootstrap A must NOT run Alembic (NEG 8/9)"
        assert not insp(c).has_table("rce_source_records"), "Bootstrap A must NOT create tables"
        assert _is_member(c, LEGACY_OWNER, "docuaction_owner"), "runtime principal preserved via membership"
    # NEG 7 (pre-membership): migration_identity still cannot SET ROLE docuaction_owner yet
    def _setrole_owner_early():
        with mig.begin() as c:
            c.execute(text("SET ROLE docuaction_owner"))
    _expect_db_error(_setrole_owner_early, "permission denied", "cannot", "insufficient")
    print("BOOTSTRAP_A=PASS (roles created, ownership moved, legacy-only preserved, no Alembic)")

    # PROVISION the migration identity (Task 2 - admin maps + grants owner membership)
    with su.connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(text(f"GRANT docuaction_owner TO {MIGRATION_ID}"))
    # NEG 7 (post-membership): now it CAN SET ROLE docuaction_owner and current_user becomes owner
    with mig.begin() as c:
        c.execute(text("SET ROLE docuaction_owner"))
        assert c.execute(text("select current_user")).scalar() == "docuaction_owner"
    print("NEG_MEMBERSHIP=PASS (SET ROLE docuaction_owner only after approved membership)")

    # MIGRATION B - executed AS migration_identity
    t0 = time.time()
    rm = _run_conv(mig_url, "--migrate", "--i-understand-migration-writes")
    print(f"CONVERGENCE_ELAPSED_SECONDS={time.time()-t0:.1f}")
    assert "MIGRATION B COMPLETE" in rm.stdout, rm.stdout[-500:]
    # identity proof emitted by the utility
    assert f"session_user={MIGRATION_ID}" in rm.stdout and "current_user(after SET ROLE)=docuaction_owner" in rm.stdout, \
        "utility must prove session_user=migration_identity and current_user=docuaction_owner"

    # POST gates -----------------------------------------------------------------
    with su.connect() as c:
        ins = insp(c)
        # SCHEMA + equivalence
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == ["20260903_delivery_grants"]
        md = _md()
        actual = set(ins.get_table_names(schema="public"))
        model_tables = {t.name for t in md.sorted_tables}
        assert model_tables <= actual, f"missing model tables: {sorted(model_tables - actual)}"
        missing_cols = {}
        for t in md.sorted_tables:
            have = {col["name"] for col in ins.get_columns(t.name, schema="public")}
            want = {c2.name for c2 in t.columns}
            if want - have:
                missing_cols[t.name] = sorted(want - have)
        assert not missing_cols, f"model columns missing after convergence: {missing_cols}"
        for cn in ADDITIVE["audit_logs"]:
            assert cn in {col["name"] for col in ins.get_columns("audit_logs", schema="public")}
        assert "file_hash" in {col["name"] for col in ins.get_columns("tefca_import_history", schema="public")}
        # DATA preservation
        post_ids = set(x[0] for x in c.execute(text("select id from users")).all())
        assert post_ids == pre_ids and len(post_ids) == pre_users, "users PK set must be identical"
        assert c.execute(text("select count(*) from bulletin_articles")).scalar() == 1, "legacy-only row preserved"
        assert c.execute(text("select count(*) from rce_delivery_jobs")).scalar() == 0, "new table starts empty"
        # OWNERSHIP: candidate -> owner; legacy-only unchanged
        assert _owner_of(c, "users") == "docuaction_owner"
        assert _owner_of(c, "rce_delivery_jobs") == "docuaction_owner"
        assert _owner_of(c, "bulletin_articles") == LEGACY_OWNER
        # SECURITY: least-priv role attributes intact
        for role in ("docuaction_owner", "docuaction_app"):
            s, _, _, b, _ = _role_attrs(c, role)
            assert not s and not b
        # NEG 12: DELETE remains denied for the app role where the model requires it
        privs = {p: c.execute(text("select has_table_privilege('docuaction_app','rce_delivery_jobs',:p)"), {"p": p}).scalar()
                 for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}
        assert privs == {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False}, privs
        # migration_identity attributes still least-privilege (no escalation)
        assert _role_attrs(c, MIGRATION_ID)[:4] == (False, False, False, False), "no privilege escalation"

    # NEG 11: existing runtime principal (legacy_owner) still reads/writes re-owned tables
    with _eng(_url_as(fixture_db, LEGACY_OWNER)).begin() as c:
        assert c.execute(text("select current_user")).scalar() == LEGACY_OWNER
        assert c.execute(text("select count(*) from users")).scalar() == pre_users
        c.execute(text("update users set last_active_at=now()"))
        c.execute(text("insert into audit_logs (id,tenant_id,action,created_at) values (:i,'legacy','oldapp',now())"),
                  {"i": str(uuid.uuid4())})
        c.execute(text("select count(*) from rce_delivery_jobs"))
    print("RUNTIME_SURVIVAL=PASS (legacy_owner works via docuaction_owner membership; no DATABASE_URL change)")

    # IDEMPOTENCY: second Migration B refuses (alembic_version present)
    r2 = _run_conv(mig_url, "--migrate", "--i-understand-migration-writes", expect_ok=False)
    assert r2.returncode != 0 and "already present" in (r2.stdout + r2.stderr)
    # normal path: alembic upgrade head is a no-op
    from alembic import command
    cfg = _alembic_cfg(fixture_db)
    command.upgrade(cfg, "head")
    with su.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == ["20260903_delivery_grants"]
    print("TWO_BOUNDARY_CONVERGENCE=PASS")


def test_forced_failure_is_fail_closed(fixture_db):
    """Force a failure DURING Migration B's chain and prove fail-closed: a
    mid-chain error must not leave alembic_version at head (NEG 13)."""
    import sqlalchemy as sa
    from sqlalchemy import text
    _build_prod_like(fixture_db)
    _run_conv(_url_as(fixture_db, LEGACY_OWNER), "--bootstrap", "--i-understand-bootstrap-writes",
              "--runtime-principal", LEGACY_OWNER)
    with _eng(SU).connect() as c:
        c.execution_options(isolation_level="AUTOCOMMIT").execute(
            text(f"GRANT docuaction_owner TO {MIGRATION_ID}"))
    # decoy: a table the chain creates unguarded -> collision aborts the chain
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


# ─────────────────────────────────────────────────────────────────────────────
# Retained PR #34 regression: fresh Alembic build + 20260831 guard (Task 7)
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_roles(eng):
    """Create the two roles the chain references and prepare them the way DEV runs
    the chain (connecting user is a docuaction_owner member; owner can create in
    schema public). Roles are cluster-global; the fixture teardown drops them."""
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
        "select count(*) from pg_constraint c "
        "join pg_class t on t.oid = c.conrelid "
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
    import sqlalchemy as sa
    from sqlalchemy import text
    from alembic import command
    from alembic.script import ScriptDirectory
    eng = _eng(fixture_db)
    _ensure_roles(eng)
    cfg = _alembic_cfg(fixture_db)
    os.environ["DB_APP_ROLE"] = "docuaction_app"
    os.environ["DB_MIGRATION_ROLE"] = "docuaction_owner"
    command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["20260903_delivery_grants"], f"expected single head, got {heads}"
    with eng.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == ["20260903_delivery_grants"]
        assert _ck_count(c) == 1
    command.upgrade(cfg, "head")  # idempotent re-run
    with eng.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalars().all() == ["20260903_delivery_grants"]
        assert _ck_count(c) == 1
    print("FRESH_ALEMBIC_BUILD=PASS head=20260903_delivery_grants ck_count=1 rerun=no-op")


def test_20260831_skips_ck_when_already_present(fixture_db):
    from sqlalchemy import text
    from alembic import command
    eng = _eng(fixture_db)
    _ensure_roles(eng)
    cfg = _alembic_cfg(fixture_db)
    os.environ["DB_APP_ROLE"] = "docuaction_app"
    os.environ["DB_MIGRATION_ROLE"] = "docuaction_owner"
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
    os.environ["DB_APP_ROLE"] = "docuaction_app"
    os.environ["DB_MIGRATION_ROLE"] = "docuaction_owner"
    command.upgrade(cfg, "20260830_run_lifecycle")
    with eng.begin() as c:
        c.execute(text(f'ALTER TABLE public.review_records DROP CONSTRAINT "{CK}"'))
    with eng.connect() as c:
        assert _ck_count(c) == 0
    command.upgrade(cfg, "20260831_review_case")
    with eng.connect() as c:
        assert _ck_count(c) == 1
    print("REGRESSION_CREATE_WHEN_ABSENT=PASS")
