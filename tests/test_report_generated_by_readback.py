"""Regression: the stored report principal was written but never readable.

WHAT BROKE
`review_reports.generated_by` is populated correctly — the DEV ARC rehearsal
report DA-ARC-2026-009 carries the authenticated analyst's user UUID. But
neither GET /api/reports nor GET /api/reports/{report_id} put that column in the
response, so an auditor asking the API who generated a report got nothing back.

That is worse than a missing feature. The audit requirement is "no NULL
generated_by where authentication exists", and the API answered as though the
principal were absent while the database held it. A provenance check run through
the API would have reported a populated row as anonymous.

The fix is additive: surface the stored column. The snapshot copy inside
report_data is NOT a substitute — it is a JSON blob written at generation time,
while the column is the row the application actually wrote and the one an
integrity check would query.
"""
from __future__ import annotations

import inspect
import re

import pytest

from app.reports import routes as report_routes


def _handler_source(func) -> str:
    return inspect.getsource(func)


@pytest.mark.regression
def test_report_detail_returns_the_stored_principal():
    source = _handler_source(report_routes.get_report)
    assert "generated_by" in source, (
        "GET /api/reports/{report_id} must return the stored generated_by; "
        "without it a populated audit column reads back as anonymous")
    assert re.search(r'"generated_by"\s*:\s*str\(row\.generated_by\)', source), (
        "generated_by must come from the stored column, not from the snapshot "
        "copy inside report_data")


@pytest.mark.regression
def test_report_list_returns_the_stored_principal():
    source = _handler_source(report_routes.list_reports)
    assert re.search(r'"generated_by"\s*:\s*str\(r\.generated_by\)', source), (
        "GET /api/reports must return the stored generated_by for each row")


@pytest.mark.regression
def test_a_null_principal_serialises_as_null_not_the_string_none():
    """Reports predating the principal fix have a NULL column.

    str(None) is "None", which is a truthy JSON string and would make an
    unauthenticated legacy report look attributed. The guard is the conditional.
    """
    for func in (report_routes.get_report, report_routes.list_reports):
        source = _handler_source(func)
        assert re.search(r'str\((?:row|r)\.generated_by\)\s+if\s+(?:row|r)\.generated_by\s+else\s+None',
                         source), (
            "%s must emit None for an absent principal, never the string 'None'"
            % func.__name__)


@pytest.mark.regression
def test_generated_by_column_is_still_a_uuid_column():
    """Pin the shape: the column holds the actor's id, not a display name.

    If this ever becomes a string column, the read-back above starts returning
    an email and any integrity check joining it to users.id breaks silently.
    """
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    from app.tefca_registry import models as reg

    column = reg.ReviewReport.__table__.c.generated_by
    assert isinstance(column.type, PG_UUID), (
        "review_reports.generated_by must remain a uuid column so the recorded "
        "principal joins to users.id")
