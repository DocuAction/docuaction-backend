"""
The fail-closed control on schema mutation at application startup.

WHY THIS EXISTS
`startup()` runs `Base.metadata.create_all()` and 27 `ALTER TABLE ... ADD COLUMN
IF NOT EXISTS` statements on every boot. Against a database the ORM already
matches that is a no-op, which is why it survived unnoticed. Against a database
that is BEHIND the model it silently creates whatever is missing.

Production is behind the model by fifteen tables, seven of them the `rce_*` Area
1 tables, and it connects as the server administrator. In PostgreSQL the role
that creates a table owns it, and an owner can always UPDATE and DELETE its own
rows regardless of grants — so an ungated container start would create the Area 1
immutability tables owned by an admin, making immutability inert from the moment
they existed, on the tables intended to hold Government data.

That is not a hypothetical failure mode. It already happened once on dev: the ACL
read correctly and UPDATE succeeded anyway because `pgadmin` owned the tables.
These tests exist so a container start cannot repeat it in production.

WHAT IS ASSERTED
Not just that a flag is read. That startup, with mutation denied, reaches no
`create_all` and executes no `ALTER TABLE` — and that the rest of boot still
runs, because a gate that also disabled the application would simply be turned
off again by whoever needed the app to start.
"""

from __future__ import annotations

import inspect

import pytest

from app.core import schema_guard

pytestmark = pytest.mark.regression

FLAG = schema_guard.STARTUP_SCHEMA_FLAG


def _code_only(fn) -> str:
    """Source with comments and the docstring stripped.

    These assertions compare where two things appear. Raw source makes them
    match explanatory prose instead — an earlier version of this file failed
    because the phrase "create_all" in a docstring counted as a call site.
    """
    import ast
    import inspect as _inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(_inspect.getsource(fn)))
    node = tree.body[0]
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]
    return ast.unparse(node)


def _env(monkeypatch, *, environment=None, flag=None):
    for name in ("ENVIRONMENT", "ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(FLAG, raising=False)
    if environment is not None:
        monkeypatch.setenv("ENVIRONMENT", environment)
    if flag is not None:
        monkeypatch.setenv(FLAG, flag)


# ── the flag itself ─────────────────────────────────────────────────────────

def test_production_denies_schema_mutation_by_default(monkeypatch):
    """Unset is the state a fresh deployment starts in."""
    _env(monkeypatch, environment="production")
    assert schema_guard.schema_mutation_allowed() is False


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "disabled", "False"])
def test_explicit_false_denies_anywhere(monkeypatch, value):
    _env(monkeypatch, environment="development", flag=value)
    assert schema_guard.schema_mutation_allowed() is False


def test_unrecognised_value_is_not_permission(monkeypatch):
    for junk in ("tru", "sure", "ENABLED!", "yes please"):
        _env(monkeypatch, environment="production", flag=junk)
        assert schema_guard.schema_mutation_allowed() is False, junk


def test_development_still_repairs_by_default(monkeypatch):
    """Dev drifts constantly; a missing column 500s every User query.

    Removing the repair would trade a production risk for a daily development
    obstacle, and someone would put it back.
    """
    _env(monkeypatch, environment="development")
    assert schema_guard.schema_mutation_allowed() is True
    _env(monkeypatch)
    assert schema_guard.schema_mutation_allowed() is True


def test_production_can_be_explicitly_enabled(monkeypatch):
    """For a deliberate, authorized maintenance boot."""
    _env(monkeypatch, environment="production", flag="true")
    assert schema_guard.schema_mutation_allowed() is True


def test_refusal_reason_names_the_flag_and_the_ownership_hazard(monkeypatch):
    _env(monkeypatch, environment="production")
    reason = schema_guard.schema_mutation_refusal_reason()
    assert FLAG in reason
    assert "production" in reason
    assert "owns" in reason, "the reason must say WHY creation is dangerous"


# ── the startup wiring ──────────────────────────────────────────────────────

def test_gate_precedes_create_all_in_startup():
    """ORDERING. A gate after create_all() would gate nothing."""
    import app.main as main

    src = _code_only(main.startup)
    gate = src.index("schema_mutation_allowed()")
    create = src.index("Base.metadata.create_all")
    alters = src.index("ALTER TABLE users ADD COLUMN")
    assert gate < create, "the gate must precede create_all"
    # The ALTER list is *defined* above the gate but only *executed* below it;
    # what matters is that execution happens inside the guarded branch.
    executed = src.index("await conn.execute(text(stmt))")
    assert gate < executed, "the ALTERs must only execute inside the guarded branch"
    assert alters < executed


def test_denied_startup_reaches_neither_create_all_nor_alter():
    """The skip path returns before any schema statement.

    Asserted on the source between the gate and its return: if create_all or an
    execute appeared there, the skip would still mutate.
    """
    import app.main as main

    src = _code_only(main.startup)
    start = src.index("if not schema_mutation_allowed():")
    end = src.index("_startup_after_schema()", start)
    between = src[start:end]
    for forbidden in ("create_all", "conn.execute", "ALTER TABLE"):
        assert forbidden not in between, f"skip path touches {forbidden}"


def test_the_rest_of_boot_still_runs_when_mutation_is_denied():
    """A gate that also disabled the application would be turned off again.

    The skipped branch hands control to the same coroutine the normal path ends
    with, so QA readiness, schedulers and router wiring are unaffected.
    """
    import app.main as main

    assert hasattr(main, "_startup_after_schema")
    src = _code_only(main.startup)
    assert src.count("_startup_after_schema()") >= 2, (
        "both the guarded and unguarded paths must continue into the rest of boot")


def test_qa_audit_table_creation_is_gated_too():
    """ensure_qa_table CREATEs a table, so it obeys the same rule."""
    import app.main as main

    src = _code_only(main._startup_after_schema)
    idx = src.index("ensure_qa_table")
    guard = src.rindex("schema_mutation_allowed()", 0, idx)
    assert guard < idx, "ensure_qa_table must sit inside the schema gate"


def test_no_other_startup_path_mutates_schema():
    """If a new create/alter site appears at startup, this fails.

    The point of the control is that schema changes arrive through an authorized
    Alembic run. A second ungated site would quietly reopen the hole.
    """
    import app.main as main

    src = _code_only(main._startup_after_schema)
    for forbidden in ("create_all", "ALTER TABLE", "CREATE TABLE"):
        if forbidden in src:
            idx = src.index(forbidden)
            assert "schema_mutation_allowed" in src[:idx], (
                f"{forbidden} appears at startup outside the schema gate")


def test_alembic_remains_the_authorized_path():
    """The control redirects schema change; it must not remove the means.

    Alembic's own env.py must be untouched by this flag, or an authorized
    migration would refuse to run for the same reason a restart does.
    """
    import pathlib

    env = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    text = env.read_text(encoding="utf-8", errors="ignore")
    assert schema_guard.STARTUP_SCHEMA_FLAG not in text
    assert "schema_mutation_allowed" not in text
