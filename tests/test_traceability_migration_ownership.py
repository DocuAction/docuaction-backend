"""F2 (independent review, 2026-09-16): the traceability migration refuses to
adopt an evidence table it does not own.

`_table_exists` skips creation of a table that is already there. If something
else created it (a runtime create_all, manual DDL) the GRANTs would be issued
by a non-owner and silently do nothing. The migration must stop and name the
table instead. Runs against the same throwaway database recipe as
`test_traceability_migration`, in its own module so it starts empty.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from test_traceability_migration import (  # noqa: F401 - fixture import
    APP, PREVIOUS, _alembic, _version, throwaway_db,
)


@pytest.mark.usefixtures("db_required")
def test_upgrade_refuses_an_evidence_table_owned_by_the_runtime_role(throwaway_db):
    url, eng = throwaway_db
    _alembic(url, "upgrade", PREVIOUS)
    with eng.begin() as c:
        assert _version(c) == [PREVIOUS]
        c.execute(text("CREATE TABLE rce_disposition_events (id uuid primary key)"))
        c.execute(text(f'ALTER TABLE rce_disposition_events OWNER TO "{APP}"'))

    r = _alembic(url, "upgrade", "head", expect_ok=False)

    assert r.returncode != 0, "upgrade must fail while a foreign-owned evidence table exists"
    combined = (r.stdout + r.stderr)
    assert "TraceabilityOwnershipError" in combined
    assert "rce_disposition_events" in combined and APP in combined
    with eng.begin() as c:
        assert _version(c) == [PREVIOUS], "nothing applied"
        owner = c.execute(text("select tableowner from pg_tables where tablename = "
                               "'rce_disposition_events'")).scalar()
        assert owner == APP, "the foreign table was left exactly as found"
        assert c.execute(text("select count(*) from pg_views where viewname = "
                              "'rce_current_dispositions'")).scalar() == 0
