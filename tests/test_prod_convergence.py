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
    for g in re.findall(r"GRANT[^\n]*\bON\b[^\n]*", code, re.I):
        assert ("ON SCHEMA" in g) or ("ON DATABASE" in g), f"only DB/SCHEMA infra ON-grants allowed: {g}"
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


def test_convergence_is_manual_opt_in_not_wired_to_auto_release():
    dev = io.open(os.path.join(REPO, ".github", "workflows", "dev-release.yml"), encoding="utf-8").read()
    assert "prod_legacy_convergence" not in dev
