"""Regression: the P12 reconciliation gate bound its intake id as VARCHAR.

WHAT BROKE
GET /api/tefca/rce/deliveries/{intake_id}/reconciliation returned 500 against
the real August 21 ONC delivery in dev:

    asyncpg.exceptions.UndefinedFunctionError:
    operator does not exist: uuid = character varying
    [SQL: ... WHERE c.source_intake_id = $1::VARCHAR ...]

reconcile_delivery() mixes ORM queries with seven raw text() statements. The ORM
ones carry column type information, so SQLAlchemy coerces the incoming str to
uuid. The raw ones do not: asyncpg uses the extended query protocol and declares
the parameter VARCHAR, and PostgreSQL has no uuid = varchar operator. psycopg2
never showed it, because it sends the parameter untyped and PostgreSQL resolves
`unknown` to uuid — so the same statement passes by hand and fails in the app.

WHY THE EXISTING TEST DID NOT CATCH IT
TestReconciliationContract asserts on inspect.getsource(...) substrings. It reads
the function; it never executes a statement or compiles one against a dialect.
Source-text assertions cannot see a parameter type error, so the gate shipped
unable to run at all.

The check below compiles each statement against the real PostgreSQL asyncpg
dialect and asserts no intake-id comparison is left untyped — which is the thing
that actually failed, and it needs no database to run.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.tefca_registry.rce import reconciliation

# Every uuid column an intake id is compared against in this module.
UUID_COLUMNS = ("source_intake_id",)


def _raw_sql_statements() -> list[str]:
    """The SQL string literals passed to text() inside reconcile_delivery."""
    source = inspect.getsource(reconciliation.reconcile_delivery)
    # text( "..." "..." ) — adjacent implicitly-concatenated literals.
    blocks = re.findall(r"text\(\s*((?:\s*\"[^\"]*\"\s*)+)", source)
    return ["".join(re.findall(r"\"([^\"]*)\"", b)) for b in blocks]


def test_reconcile_delivery_still_uses_raw_sql():
    """Guard the guard: if the raw SQL is gone, this file must be revisited."""
    statements = _raw_sql_statements()
    assert statements, "expected raw text() statements in reconcile_delivery"


@pytest.mark.regression
def test_every_uuid_comparison_casts_its_bind_parameter():
    """A bare `uuid_column = :param` is the defect. Require an explicit cast."""
    offenders = []
    for sql in _raw_sql_statements():
        for column in UUID_COLUMNS:
            # `col = :i`  -> broken.   `col = CAST(:i AS uuid)` -> fine.
            for match in re.finditer(re.escape(column) + r"\s*=\s*([^\s]+)", sql):
                bound = match.group(1)
                if bound.startswith(":"):
                    offenders.append((column, sql[:110]))
    assert not offenders, (
        "raw SQL compares a uuid column to an uncast bind parameter; under "
        "asyncpg this raises 'operator does not exist: uuid = character "
        "varying':\n" + "\n".join("  %s in: %s..." % o for o in offenders))


@pytest.mark.regression
def test_statements_compile_for_asyncpg_without_a_varchar_intake_bind():
    """Compile against the real dialect and assert no ::VARCHAR intake cast.

    This is the exact rendering that failed in dev: the driver stamped the
    parameter VARCHAR before PostgreSQL ever saw it.
    """
    from sqlalchemy import text
    from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg

    dialect = pg_asyncpg.dialect()
    intake_id = "95d78cf6-e5a2-465c-acdc-6e451e05b672"

    for sql in _raw_sql_statements():
        if ":i" not in sql:
            continue
        compiled = str(text(sql).bindparams(i=intake_id).compile(dialect=dialect))
        normalised = compiled.upper().replace(" ", "")
        assert "SOURCE_INTAKE_ID=$1::VARCHAR" not in normalised, (
            "intake id still compiles to a VARCHAR bind against a uuid column:\n"
            + compiled[:200])


@pytest.mark.regression
def test_the_fix_is_a_uuid_cast_not_a_string_column():
    """The correct fix casts the parameter. It does NOT widen the column.

    Changing source_intake_id to text would make the join work and destroy
    referential typing across Area 1 and Area 2, so pin the shape of the fix.
    """
    from app.tefca_registry.rce import models as m
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    column = m.RceCuratedRecord.__table__.c.source_intake_id
    assert isinstance(column.type, PG_UUID), (
        "source_intake_id must remain a uuid column; the reconciliation fix "
        "belongs in the bind parameter, not in the schema")

    for sql in _raw_sql_statements():
        if "source_intake_id" in sql and ":i" in sql:
            assert "CAST(:i AS uuid)" in sql, (
                "expected an explicit uuid cast on the intake bind: " + sql[:120])
