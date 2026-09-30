"""QA108-20260927 — the reports listing projects columns, and changes nothing.

The listing used to SELECT full ReviewReport entities, dragging every row's
frozen dataset (megabytes for a 24,589-record delivery report) and rendered
HTML out of Postgres to serve a page of metadata — 8–13 s for 50 rows on DEV.
The fix projects exactly the returned columns plus the three small JSONB
subtrees the listing reads. This module proves, against a REAL report stored
in a REAL PostgreSQL, that every field of the new response equals what the
old full-entity code computed for the same row.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from support_delivery_api import _database_available, headers_for, run

pytestmark = pytest.mark.skipif(
    not _database_available(),
    reason="No database reachable at DATABASE_URL. This test exercises a "
           "database-backed path; skipping rather than reporting a false failure.")


@pytest.fixture
def committed_report(tmp_path, monkeypatch):
    from app.core.storage import artifact_store
    from test_report_storage_durable import _Committed

    monkeypatch.setenv("REPORT_ARTIFACT_ROOT", str(tmp_path / "listing-artifacts"))
    monkeypatch.delenv("REPORT_ARTIFACT_BACKEND", raising=False)
    artifact_store.reset_artifact_store()
    c = _Committed()
    run(c.seed_and_generate())
    try:
        yield c
    finally:
        run(c.cleanup())
        artifact_store.reset_artifact_store()


def _old_shape(row):
    """The listing item EXACTLY as the pre-projection code computed it, from
    the full entity — the oracle the new response must match."""
    from app.reports.routes import _deliverable_meta, _listing_extras

    return {
        "report_id": row.report_id,
        "report_type": row.report_type,
        "generated_at": row.generated_at,
        "generated_by": str(row.generated_by) if row.generated_by else None,
        "snapshot": (row.report_data or {}).get("snapshot", {}),
        **_listing_extras(row),
    }


def test_listing_item_equals_the_full_entity_computation(client, committed_report):
    from app.core.database import async_session_maker
    from app.tefca_registry import models as reg

    report_id = committed_report.report_ids[0]

    async def _row():
        async with async_session_maker() as db:
            return (await db.execute(
                select(reg.ReviewReport).where(reg.ReviewReport.report_id == report_id)
            )).scalar_one()

    row = run(_row())
    expected = _old_shape(row)

    r = client.get("/api/reports?limit=500", headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    match = [i for i in r.json()["items"] if i["report_id"] == report_id]
    assert match, "the committed report is missing from the listing"
    item = match[0]

    # Identical KEYS (no field lost, none invented)…
    assert set(item) == set(_json_roundtrip(expected)), (
        set(item) ^ set(_json_roundtrip(expected)))
    # …and identical VALUES, field by field, through the same JSON encoding
    # the API applies.
    assert item == _json_roundtrip(expected)


def _json_roundtrip(value):
    """Encode exactly as FastAPI would, so datetimes/UUIDs compare equal."""
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(value)


def test_listing_answers_without_touching_dataset_or_html(client, committed_report):
    """The projected query names its columns: neither `report_html` nor the
    `dataset` subtree may appear in the compiled listing statement."""
    import inspect

    from app.reports import routes as report_routes

    src = inspect.getsource(report_routes.list_reports)
    # Strip the docstring (it narrates the OLD defect by name); the CODE must
    # never touch the rendered-HTML column or load the dataset wholesale.
    code = src.split('"""')[-1]
    assert "report_html" not in code
    # The only dataset touches are path extracts of small subtrees: the two
    # contract-number paths and, since the ONC demo implementation
    # (2026-09-30), the `delivery` and `scope` identifier blocks that name a
    # scoped report's population. Never the dataset itself.
    assert code.count('["dataset"]') == 4
    assert 'R.report_data["dataset"].label' not in code
    r = client.get("/api/reports?limit=5", headers=headers_for("viewer"))
    assert r.status_code == 200


def test_report_type_filter_still_applies(client, committed_report):
    r = client.get("/api/reports?limit=500&report_type=definitely-not-a-type",
                   headers=headers_for("viewer"))
    assert r.status_code == 200
    assert all(i["report_type"] == "definitely-not-a-type" for i in r.json()["items"])
    assert committed_report.report_ids[0] not in [i["report_id"] for i in r.json()["items"]]
