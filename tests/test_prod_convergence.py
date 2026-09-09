"""Static safety properties of the three-step legacy-PROD convergence utility.

The utility mutates a production database only under an explicit per-step write
acknowledgement and only after a fail-closed guard, so its SHAPE is what these
assert - no live DB required, so they run in ordinary CI. Live behaviour is
validated separately against a throwaway fixture (see the integration test).
"""
import ast
import io
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONV = os.path.join(REPO, "scripts", "prod_legacy_convergence.py")


def _src():
    return io.open(CONV, encoding="utf-8").read()


def _code():
    """Executable source with all docstrings removed (SQL string literals kept)."""
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    return ast.unparse(tree)


def _func(code, name):
    marker = f"def {name}("
    assert marker in code, f"{name} not found in utility"
    return code.split(marker, 1)[1].split("\ndef ", 1)[0]


def test_utility_exists():
    assert os.path.exists(CONV)


def test_no_destructive_sql_in_executable_code():
    up = _code().upper()
    for banned in ("DROP TABLE", "TRUNCATE", "REASSIGN OWNED", "DROP DATABASE", "DELETE FROM"):
        assert banned not in up, f"convergence utility must never execute {banned}"


def test_three_steps_are_gated_and_dry_run_is_default():
    src = _src()
    for flag in ('"--bootstrap"', '"--i-understand-bootstrap-writes"',
                 '"--migrate"', '"--i-understand-migration-writes"',
                 '"--finalize"', '"--i-understand-finalize-writes"'):
        assert flag in src, f"missing gate flag {flag}"
    assert "if not args.i_understand_bootstrap_writes:" in src
    assert "if not args.i_understand_migration_writes:" in src
    assert "if not args.i_understand_finalize_writes:" in src
    assert "if not (args.bootstrap or args.migrate or args.finalize):" in src
    assert "DRY-RUN ONLY" in src


def test_steps_are_mutually_exclusive():
    src = _src()
    assert "> 1" in src and "choose exactly ONE" in src


def test_refuses_an_already_alembic_managed_database():
    code = _code()
    assert 'p[\'alembic_present\']' in code or 'p["alembic_present"]' in code
    assert "REFUSED" in code and "already Alembic-managed" in code


def test_roles_are_least_privilege():
    code = _code()
    for tok in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOBYPASSRLS"):
        assert tok in code, f"roles must be created {tok}"
    assert "SUPERUSER" not in code.replace("NOSUPERUSER", "")
    assert "BYPASSRLS" not in code.replace("NOBYPASSRLS", "")


def test_docuaction_app_is_the_login_application_identity():
    """docuaction_app is created as a LOGIN role with the application password
    (the DEV/baseline model); its access to non-Area-1 tables is by ownership."""
    code = _code()
    assert re.search(r'CREATE ROLE[^\n]*APP_ROLE[^\n]*LOGIN PASSWORD', code), \
        "docuaction_app must be created LOGIN with a password"
    # the password is passed in, never a literal in the source
    assert "app_password" in code and "CONV_APP_PASSWORD" in _src()


def test_password_is_never_logged():
    code = _code()
    for line in code.splitlines():
        if "print(" in line:
            assert "app_password" not in line and "_lit(" not in line, f"password must not be logged: {line}"


def test_ownership_is_explicit_alter_not_reassign_owned():
    code = _code()
    assert "tables_shared_reowned" in code
    assert "ALTER TABLE" in code and "OWNER TO" in code
    assert "REASSIGN OWNED" not in code.upper()


def test_finalize_gives_non_area1_to_app_and_keeps_area1_on_owner():
    code = _code()
    body = _func(code, "finalize_ownership")
    assert "AREA1_OWNER_TABLES" in body, "Finalize must reference the Area-1 owner set it keeps"
    assert "OWNER TO" in body and "APP_ROLE" in body, "Finalize reassigns to the app role"
    assert "keep" in body and "- keep" in body, "must exclude the kept (Area-1 + alembic_version) set"
    assert "command.upgrade" not in body and "create_all" not in body, "Finalize must run no Alembic"


def test_area1_owner_set_is_the_documented_five():
    src = _src()
    for t in ("rce_source_records", "rce_source_intakes", "rce_ingestion_runs",
              "rce_rule_execution_history", "rce_delivery_jobs"):
        assert f'"{t}"' in src


def test_app_and_owner_are_not_members_of_each_other():
    code = _code()
    assert 'GRANT "{OWNER_ROLE}" TO "{APP_ROLE}"' not in code
    assert 'GRANT "{APP_ROLE}" TO "{OWNER_ROLE}"' not in code
    # the only role-membership grants target the operator (CURRENT_USER)
    for m in re.findall(r'GRANT "\{(?:OWNER_ROLE|APP_ROLE)\}" TO [^\n\']+', code):
        assert "CURRENT_USER" in m, f"unexpected role-membership grant: {m}"


def test_grants_come_from_the_reviewed_chain_not_reimplemented():
    """The Area-1 table-privilege model comes from the reviewed chain. The utility
    itself only issues CONNECT/schema (infra) grants and operator role-membership
    grants - never a table-privilege grant to the app role, never a REVOKE."""
    code = _code()
    assert "command.upgrade(cfg" in code and "head" in code
    assert "DB_APP_ROLE" in code and "APP_ROLE" in code
    prep = _func(code, "managed_prepare")
    for g in re.findall(r"GRANT[^\n]*\bON\b[^\n]*", code.replace(prep, ""), re.I):
        assert ("ON SCHEMA" in g) or ("ON DATABASE" in g), f"only DB/SCHEMA infra ON-grants allowed: {g}"
    # The ONE exception, scoped and pinned: managed PREPARE temporarily re-owns the
    # single non-Area-1 table a pending revision ALTERs (MANAGED_CHAIN_ALTERS) and
    # keeps the application's former owner-level access on it for the window.
    # Area-1 privileges still come only from the chain (asserted at import).
    for g in re.findall(r"GRANT[^\n]*\bON\b[^\n]*", prep, re.I):
        assert ("ON SCHEMA" in g) or ('ON public."{t}" TO "{APP_ROLE}"' in g), f"unexpected grant in PREPARE: {g}"
    assert "for t in MANAGED_CHAIN_ALTERS:" in prep
    assert "assert not set(MANAGED_CHAIN_ALTERS) & AREA1_OWNER_TABLES" in code
    assert not re.search(r"\bREVOKE\b", code, re.I), "no invented REVOKE"


def test_bookkeeping_only_head_and_verified_no_stamp():
    code = _code()
    assert "assert cur == list(heads)" in code
    assert "assert len(heads) == 1" in code
    assert "stamp" not in code.lower(), "must not stamp - the chain writes alembic_version itself"


def test_schema_comes_from_the_chain_not_a_pre_chain_create_all():
    code = _code()
    for fn in ("bootstrap_apply", "migration_apply", "finalize_ownership"):
        assert "create_all" not in _func(code, fn), f"{fn} must not create_all before the chain"
    assert "DB_MIGRATION_ROLE" in code, "chain must run as the owner role via DB_MIGRATION_ROLE"
    assert "missing_after" in code, "must assert schema equivalence after convergence"


def test_bootstrap_a_runs_no_alembic_and_no_schema_build():
    body = _func(_code(), "bootstrap_apply")
    for banned in ("command.upgrade", "create_all", "ADD COLUMN", "op.create", "run_chain"):
        assert banned.lower() not in body.lower(), f"Bootstrap A must not contain {banned!r}"


def test_migration_b_has_no_ownership_transfer_and_proves_identity():
    code = _code()
    for fn in ("migration_apply", "run_chain_and_verify"):
        assert "OWNER TO" not in _func(code, fn), f"{fn} must not transfer table ownership"
    assert "session_user" in code and "current_user" in code
    assert "_prove_migration_identity" in code


def test_migration_b_requires_bootstrap_first():
    body = _func(_code(), "migration_apply")
    assert "_tables_not_owned_by" in body
    assert "Bootstrap A" in body and "REFUSED" in body


def test_bootstrap_can_run_via_setrole_secretless_admin():
    """--bootstrap-as-role lets a secretless admin that can SET ROLE to the legacy
    owner run Bootstrap A / Finalize without the owner's password (session-local
    SET ROLE, not a new grant), and proves the role switch actually took effect."""
    src = _src(); code = _code()
    assert '"--bootstrap-as-role"' in src
    assert "become_role" in code
    body = _func(code, "bootstrap_apply")
    assert "SET ROLE" in body and "become_role" in body
    assert "current_user" in body and "did not take effect" in body
    # it is a session-local SET ROLE, not a permanent GRANT of the owner role
    assert "GRANT" not in body.split("SET ROLE", 1)[1].split("\n")[0]


def test_managed_prod_mode_shape():
    """The already-managed PROD path (docuaction-db-geo, measured 2026-09-08):
    pinned expected revision, fail-closed lineage/fingerprint gate, owner-run
    PREPARE that touches only the measured model-only columns + alembic_version
    ownership, migration-identity MIGRATE with a full identity proof and a
    proven no-op re-run. Never stamps; never creates roles; never re-owns broadly."""
    src = _src(); code = _code()
    assert 'EXPECTED_MANAGED_REVISION = "20260829_report_artifacts"' in src
    for flag in ('"--managed-prepare"', '"--i-understand-prepare-writes"', '"--managed-migrate"',
                 '"--expected-revision"', '"--admin-role"'):
        assert flag in src, f"missing managed flag {flag}"
    assert "if not args.i_understand_prepare_writes:" in src
    assert "command.stamp" not in code and ".stamp(" not in code, "the managed path must never stamp"
    gate = _func(code, "managed_gate")
    for needle in ("expected exactly one", "certified expected revision", "unknown to this repository",
                   "already at head", "AHEAD of the expected revision", "not an ancestor of head",
                   "owned by neither", "already present before the chain",
                   "legacy-only table set differs", "unexpected model-only drift", "prohibited cross-membership"):
        assert needle in gate, f"managed gate must refuse on: {needle}"
    for banned in ("CREATE ROLE", "OWNER TO", "GRANT ", "ALTER TABLE", "command.upgrade", "create_all"):
        assert banned not in gate, f"managed_gate must be read-only ({banned!r})"
    prep = _func(code, "managed_prepare")
    assert "CREATE ROLE" not in prep and "CONV_APP_PASSWORD" not in prep and "REASSIGN" not in prep
    assert prep.count("OWNER TO") == 2 and 'alembic_version" OWNER TO' in prep and "MANAGED_CHAIN_ALTERS" in prep, \
        "PREPARE may re-own alembic_version and the single-table MANAGED_CHAIN_ALTERS set only"
    assert "MANAGED_CHAIN_ALTERS = ('review_records',)" in code, "only review_records is ALTERed by a pending revision"
    assert "ADD COLUMN IF NOT EXISTS" in prep and "MANAGED_PREPARE_TABLES" in prep
    assert "command.upgrade" not in prep and "create_all" not in prep
    assert "MANAGED_PREPARE_TABLES = ('decisions',)" in code, "review_records columns belong to 20260831_review_case"
    mig = _func(code, "managed_migrate")
    assert "OWNER TO" not in mig and "CREATE ROLE" not in mig and "ADD COLUMN" not in mig
    assert "after_prepare=True" in mig and "_prove_managed_migration_identity" in mig
    assert "run_chain_and_verify" in mig and "NO-OP" in mig
    proof = _func(code, "_prove_managed_migration_identity")
    assert "direct != {OWNER_ROLE}" in proof and "boundary broken" in proof
    assert "for forbidden in (admin_role, APP_ROLE)" in proof
    # legacy guards untouched
    assert "REFUSED: alembic_version already present" in _func(code, "bootstrap_apply")
    assert "REFUSED: alembic_version already present" in _func(code, "migration_apply")


def test_prod_migration_workflow_targets_the_authoritative_server():
    """PROD Step B must target docuaction-db-geo (the authoritative PROD write DB)
    with the managed path, and no executable migration path may still name the
    stale server docuaction-db."""
    wf_dir = os.path.join(REPO, ".github", "workflows")
    prod = io.open(os.path.join(wf_dir, "prod-migration.yml"), encoding="utf-8").read()
    assert "PGHOST: docuaction-db-geo.postgres.database.azure.com" in prod
    assert "-s docuaction-db-geo" in prod
    assert "--managed-migrate --i-understand-migration-writes" in prod
    assert "--expected-revision 20260829_report_artifacts" in prod
    assert "docuaction-db.postgres" not in prod and "-s docuaction-db " not in prod
    for name in os.listdir(wf_dir):
        body = io.open(os.path.join(wf_dir, name), encoding="utf-8").read()
        for line in body.splitlines():
            if "docuaction-db.postgres" in line or re.search(r"(-s|-n|--name|--server-name)\s+docuaction-db\b(?!-)", line):
                raise AssertionError(f"{name} still targets the stale PROD server: {line.strip()[:120]}")


def _conv_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("conv_under_test", CONV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_alembic_url_renames_only_the_tls_key():
    """psycopg2 keeps `sslmode=`; the Alembic/asyncpg URL gets `ssl=` with the same
    mode. Everything else is preserved: scheme, encoded credentials, host, port,
    database, unrelated parameters, and their order."""
    conv = _conv_module()
    psycopg = "postgresql://imran%40agtbi.com:eyJ0eXAi.Oi-JKV1Q_%2B%3D@docuaction-db-geo.postgres.database.azure.com:5432/postgres?sslmode=require"
    out = conv._alembic_url(psycopg)
    assert out == "postgresql://imran%40agtbi.com:eyJ0eXAi.Oi-JKV1Q_%2B%3D@docuaction-db-geo.postgres.database.azure.com:5432/postgres?ssl=require"
    assert "sslmode" not in out and psycopg.split("?")[0] == out.split("?")[0]
    # other parameters and order preserved; only the key is renamed
    assert conv._alembic_url("postgresql://u:p@h/db?application_name=x&sslmode=verify-full&connect_timeout=10") == \
        "postgresql://u:p@h/db?application_name=x&ssl=verify-full&connect_timeout=10"
    # the psycopg URL object itself is never mutated by the helper (pure function)
    assert psycopg.endswith("sslmode=require")


def test_alembic_url_without_tls_parameter_is_unchanged_and_weakening_is_refused():
    conv = _conv_module()
    for u in ("postgresql://u:p@127.0.0.1:5432/conv_fix", "postgresql://u:p@h/db?application_name=x", "postgresql://u:p@h/db?"):
        assert conv._alembic_url(u) == u, "no TLS parameter -> deterministic, unchanged (fixture / local database)"
    import pytest
    for weak in ("disable", "allow"):
        with pytest.raises(SystemExit):
            conv._alembic_url(f"postgresql://u:p@h/db?sslmode={weak}")
    assert "sslmode=disable" not in _code()


def test_alembic_url_is_accepted_by_the_real_asyncpg_dialect():
    """Regression for PROD run 34307375621: build the actual asyncpg connect
    arguments through SQLAlchemy's dialect (the path alembic/env.py takes) and
    check them against the installed asyncpg's connect() signature. The raw
    psycopg URL must be REJECTED by that check and the translated one ACCEPTED."""
    import inspect
    import asyncpg
    from sqlalchemy.engine import make_url
    from sqlalchemy.dialects.postgresql.asyncpg import dialect as AsyncpgDialect
    conv = _conv_module()
    accepted = set(inspect.signature(asyncpg.connect).parameters)
    assert "ssl" in accepted and "sslmode" not in accepted

    def connect_kwargs(url):
        u = make_url(url.replace("postgresql://", "postgresql+asyncpg://", 1))   # exactly what env.py does
        _, cparams = AsyncpgDialect().create_connect_args(u)
        return cparams

    raw = "postgresql://sp:tok@docuaction-db-geo.postgres.database.azure.com:5432/postgres?sslmode=require"
    bad = connect_kwargs(raw)
    assert "sslmode" in bad and set(bad) - accepted, "the untranslated URL must be caught by this test"
    good = connect_kwargs(conv._alembic_url(raw))
    assert good.get("ssl") == "require" and "sslmode" not in good
    assert not (set(good) - accepted), f"asyncpg would reject: {set(good) - accepted}"


def test_both_chain_paths_use_the_helper_and_scope_the_environment():
    code = _code()
    for fn in ("run_chain_and_verify", "managed_migrate"):
        body = _func(code, fn)
        assert "set_main_option('sqlalchemy.url', _alembic_url(" in body, f"{fn} must hand Alembic the translated URL"
        assert "with _alembic_environment(" in body, f"{fn} must scope DATABASE_URL to the Alembic call"
        assert "os.environ['DATABASE_URL'] =" not in body, f"{fn} must not set DATABASE_URL outside the scoped context"
    env_body = _func(code, "_alembic_environment")
    assert "previous = os.environ.get('DATABASE_URL')" in env_body and "finally:" in env_body
    # the psycopg engines still receive the caller's URL untouched
    assert "sa.create_engine(db_url)" in _func(code, "run_chain_and_verify")
    assert "sa.create_engine(sync_url)" in _func(code, "managed_migrate")
    # never logged
    for line in code.splitlines():
        if "print(" in line:
            assert "_alembic_url(" not in line and "DATABASE_URL" not in line, f"URL must not be logged: {line}"


def test_convergence_is_manual_opt_in_not_wired_to_auto_release():
    dev = io.open(os.path.join(REPO, ".github", "workflows", "dev-release.yml"), encoding="utf-8").read()
    assert "prod_legacy_convergence" not in dev
