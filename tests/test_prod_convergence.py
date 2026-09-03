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


def _func(code, name):
    """Body of one top-level function in the docstring-stripped source."""
    marker = f"def {name}("
    assert marker in code, f"{name} not found in utility"
    return code.split(marker, 1)[1].split("\ndef ", 1)[0]


def test_utility_exists():
    assert os.path.exists(CONV)


def test_no_destructive_sql_in_executable_code():
    up = _code().upper()
    for banned in ("DROP TABLE", "TRUNCATE", "REASSIGN OWNED", "DROP DATABASE", "DELETE FROM"):
        assert banned not in up, f"convergence utility must never execute {banned}"


def test_writes_are_gated_per_boundary_and_dry_run_is_default():
    """Each boundary is a separate mode with its OWN write acknowledgement, and
    the default (no write flag) is a read-only plan."""
    src = _src()
    for flag in ('"--bootstrap"', '"--i-understand-bootstrap-writes"',
                 '"--migrate"', '"--i-understand-migration-writes"'):
        assert flag in src, f"missing gate flag {flag}"
    assert "if not args.i_understand_bootstrap_writes:" in src
    assert "if not args.i_understand_migration_writes:" in src
    # default path (neither boundary requested) is a dry-run plan
    assert "if not args.bootstrap and not args.migrate:" in src
    assert "DRY-RUN ONLY" in src


def test_two_boundaries_are_mutually_exclusive():
    src = _src()
    assert "if args.bootstrap and args.migrate:" in src
    assert "choose exactly ONE" in src


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
    runs as a no-op safety net (as DEV does). Neither boundary runs create_all
    before the chain, and equivalence to the model is asserted after."""
    code = _code()
    for fn in ("bootstrap_apply", "migration_apply"):
        assert "create_all" not in _func(code, fn), f"{fn} must not create_all before the chain"
    assert "DB_MIGRATION_ROLE" in code, "chain must run as the owner role via DB_MIGRATION_ROLE"
    assert "missing_after" in code, "must assert schema equivalence to the model after convergence"


def test_bootstrap_a_runs_no_alembic_and_no_schema_build():
    """BOOTSTRAP A is owner-only prep: it must not run Alembic, create tables,
    create_all, or add columns - those belong to Migration B."""
    body = _func(_code(), "bootstrap_apply")
    # operational constructs only (the word "Alembic" legitimately appears in the
    # already-managed refusal message and the informational print).
    for banned in ("command.upgrade", "create_all", "ADD COLUMN", "op.create", "run_chain"):
        assert banned.lower() not in body.lower(), f"Bootstrap A must not contain {banned!r}"


def test_migration_b_has_no_pgadmin_ownership_transfer():
    """MIGRATION B never transfers ownership of the legacy tables (Bootstrap A
    did that) and never runs as pgadmin: no 'OWNER TO' in its execution path."""
    code = _code()
    for fn in ("migration_apply", "run_chain_and_verify"):
        assert "OWNER TO" not in _func(code, fn), f"{fn} must not transfer table ownership"
    # and it proves the migration-identity boundary
    assert "session_user" in code and "current_user" in code
    assert '_prove_migration_identity' in code


def test_migration_b_requires_bootstrap_first():
    """Migration B refuses unless the owner role exists and the candidate tables
    are already owned by it (i.e. Bootstrap A has run)."""
    body = _func(_code(), "migration_apply")
    assert "_tables_not_owned_by_owner" in body
    assert "Bootstrap A" in body and "REFUSED" in body


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
