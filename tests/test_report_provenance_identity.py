"""
A stored report must record who generated it.

WHAT WAS WRONG
`report_artifacts.generated_by` correctly carried the authenticated principal
while `review_reports.generated_by` was NULL for every report ever stored. The
cause was a type mismatch rather than a missing lookup: the column is UUID-typed,
but the whole provenance chain carries the principal as an EMAIL — the snapshot,
the artifact row and the audit entry all use the address. The value at hand could
not be assigned to the column, so it was quietly dropped.

That left the two halves of the same provenance record disagreeing about
authorship, which is the specific failure a deliverable's chain of custody cannot
have: the artifact says who issued the document, the report row says nobody did.

WHAT IS ASSERTED
That the id reaches the row, that it comes from the AUTHENTICATED context rather
than the request body, and that an unauthenticated run stores NULL instead of
inventing an identity.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

pytestmark = pytest.mark.regression


class _FakeSession:
    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class _Snapshot:
    report_id = "DA-ARC-2026-999"
    report_type = "verification"
    b1_b4_rule_version = "2"

    def to_dict(self):
        return {"report_id": self.report_id, "generated_by": "someone@example.invalid"}


@pytest.mark.asyncio
async def test_stored_report_records_the_authenticated_principal():
    """The defect, directly: the id must land on the row."""
    from app.reports.data.report_snapshot import store_report

    db = _FakeSession()
    principal = uuid.uuid4()
    row_id = await store_report(db, _Snapshot(), {"a": 1}, "<html></html>",
                                generated_by_id=principal)

    assert row_id is not None and db.commits == 1
    assert len(db.added) == 1
    assert db.added[0].generated_by == principal, (
        "review_reports.generated_by must carry the authenticated principal")


@pytest.mark.asyncio
async def test_unauthenticated_generation_stores_null_not_a_placeholder():
    """Absence of an identity must not become a fabricated one.

    A synthesised value here would be worse than NULL: it would look like
    provenance while asserting something nobody verified.
    """
    from app.reports.data.report_snapshot import store_report

    db = _FakeSession()
    await store_report(db, _Snapshot(), {"a": 1}, "<html></html>")
    assert db.added[0].generated_by is None


def test_identity_comes_from_the_authenticated_context_not_the_request_body():
    """A caller must not be able to claim authorship of a report.

    The route reads the id off the authenticated `user`, never off `request`.
    """
    import app.reports.routes as routes

    src = inspect.getsource(routes)
    idx = src.index("generated_by_id=")
    line = src[idx:src.index("\n", idx)]
    assert 'getattr(user, "id"' in line, f"identity must come from `user`: {line}"
    assert "request." not in line, f"identity must not come from the request body: {line}"


def test_generator_threads_the_id_through_to_storage():
    """The plumbing between route and row, pinned.

    The bug survived because the value existed at both ends and was lost in the
    middle.
    """
    from app.reports.generator import generate_report

    assert "generated_by_id" in inspect.signature(generate_report).parameters
    src = inspect.getsource(generate_report)
    assert "generated_by_id=generated_by_id" in src, (
        "generate_report must pass the principal id to store_report")


def test_email_and_id_describe_the_same_principal():
    """The two tables record different representations, deliberately.

    report_artifacts.generated_by is the email; review_reports.generated_by is
    the UUID, because that is what each column is typed for. Both must be fed
    from the same authenticated user object, or the chain of custody forks.
    """
    import app.reports.routes as routes

    src = inspect.getsource(routes)
    i = src.index("generated_by=")
    block = src[i:i + 220]
    assert 'getattr(user, "email"' in block
    assert 'getattr(user, "id"' in block
