"""Static safety properties of the one-time legacy-PROD convergence utility.

The utility mutates a production database only under --apply and only after a
fail-closed guard, so its SHAPE is what these assert - no live DB required, so
they run in ordinary CI. Live apply behaviour is validated separately against a
throwaway fixture on a cluster that permits CREATE DATABASE (see PR notes); the
read-only --dry-run was exercised against the real PROD database and produced a
non-destructive plan.
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
    """Executable source with all docstrings removed (SQL string literals kept),
    so prose describing the safety rules is never mistaken for a violation."""
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
    return ast.unparse(tree)


def test_utility_exists():
    assert os.path.exists(CONV)


def test_no_destructive_sql_in_executable_code():
    up = _code().upper()
    for banned in ("DROP TABLE", "TRUNCATE", "REASSIGN OWNED", "DROP DATABASE", "DELETE FROM"):
        assert banned not in up, f"convergence utility must never execute {banned}"


def test_apply_is_gated_and_dry_run_is_default():
    src = _src()
    assert '"--apply"' in src and '"--i-understand-this-writes"' in src
    assert "if not args.apply:" in src
    assert "--apply requires --i-understand-this-writes" in src


def test_refuses_an_already_alembic_managed_database():
    code = _code()
    assert 'p[\'alembic_present\']' in code or 'p["alembic_present"]' in code
    assert "REFUSED" in code and "already Alembic-managed" in code


def test_roles_are_least_privilege_no_superuser_no_bypassrls():
    code = _code()
    assert "NOSUPERUSER NOBYPASSRLS" in code
    assert "SUPERUSER" not in code.replace("NOSUPERUSER", "")
    assert "BYPASSRLS" not in code.replace("NOBYPASSRLS", "")


def test_ownership_change_is_an_explicit_allowlist_not_reassign_owned():
    code = _code()
    assert "tables_shared_reowned" in code
    assert "ALTER TABLE" in code and "OWNER TO" in code
    assert "REASSIGN OWNED" not in code.upper()


def test_grants_come_from_the_reviewed_chain_not_reimplemented():
    """The schema and the Area-1 privilege model are not re-expressed here; the
    real alembic chain builds/grants them (and writes alembic_version - no false
    stamp). The grants this utility issues are role membership (GRANT owner TO
    CURRENT_USER / runtime principal) and ONE schema-level infra grant (CREATE,
    USAGE ON SCHEMA public TO the owner role) so the chain can create tables -
    never a table-privilege grant to the app role, and never a REVOKE."""
    code = _code()
    assert "command.upgrade(cfg" in code and "head" in code
    assert "DB_APP_ROLE" in code and "APP_ROLE" in code
    # every ON-grant present must be the schema-CREATE infra grant to the owner role
    for g in re.findall(r"GRANT[^\n]*\bON\b[^\n]*", code, re.I):
        assert "SCHEMA public" in g and "OWNER_ROLE" in g, f"unexpected ON grant: {g}"
    assert not re.search(r"\bREVOKE\b", code, re.I), "no invented REVOKE"


def test_bookkeeping_only_head_and_verified_no_stamp():
    code = _code()
    assert "assert cur == list(heads)" in code
    assert "assert len(heads) == 1" in code
    assert "stamp" not in code.lower(), "must not stamp - the chain writes alembic_version itself"


def test_schema_comes_from_the_chain_not_a_pre_chain_create_all():
    """Correct order: the chain builds the schema, THEN create_all(checkfirst)
    runs as a no-op safety net (as DEV does). apply() must not create_all before
    the chain, and equivalence to the model must be asserted after."""
    code = _code()
    assert "def apply(" in code
    apply_body = code.split("def apply(", 1)[1].split("\ndef ", 1)[0]
    assert "create_all" not in apply_body, "apply() must not create_all before the chain"
    assert "DB_MIGRATION_ROLE" in code, "chain must run as the owner role via DB_MIGRATION_ROLE"
    assert "missing_after" in code, "must assert schema equivalence to the model after convergence"


def test_runtime_principal_preservation_is_membership_only_and_opt_in():
    """The existing app login is preserved across the ownership move by granting
    it OWNER-ROLE MEMBERSHIP (no DATABASE_URL change), never by inventing a
    table-privilege grant, and only when explicitly named (default None)."""
    src = _src()
    code = _code()
    assert '"--runtime-principal"' in src
    # membership grant of the owner role to the named principal, no ON clause
    assert re.search(r'GRANT\s+"?\{?OWNER_ROLE\}?"?\s+TO\s+"?\{?runtime_principal\}?"?', code) \
        or ('GRANT' in code and 'OWNER_ROLE' in code and 'runtime_principal' in code)
    assert not re.search(r"runtime_principal.*\bON\b", code), "must not be a table-privilege grant"
    # opt-in: no preservation happens unless a principal is supplied
    assert "if runtime_principal:" in code
    # refuses a non-existent principal rather than silently skipping
    assert "does not exist" in code


def test_convergence_is_manual_opt_in_not_wired_to_auto_release():
    dev = io.open(os.path.join(REPO, ".github", "workflows", "dev-release.yml"), encoding="utf-8").read()
    assert "prod_legacy_convergence" not in dev
