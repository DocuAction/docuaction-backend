"""Import must not do one database round trip per row.

A 1,000-row upload timed out with the request still pending. The cause was not
the file: `upload_entities` ran a `SELECT ... WHERE rce_organization_id = ?` for
every accepted row, sequentially, inside the HTTP request. A thousand rows meant
a thousand latencies before the registry bridge spent a thousand more. Raising a
timeout could not fix that, because the cost grows with the file.

These tests pin the shape of the fix rather than a wall-clock number, which
would be flaky: the number of lookup round trips must grow with the number of
CHUNKS, not with the number of rows.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.Tefca import routes as tefca_routes


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Records every execute() so the test can count round trips."""

    def __init__(self):
        self.executes = 0
        self.added = []

    async def execute(self, *_a, **_k):
        self.executes += 1
        return _Result([])            # nothing pre-exists

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class _FakeUpload:
    def __init__(self, payload, filename="entities.csv"):
        self._payload = payload
        self.filename = filename

    async def read(self):
        return self._payload


class _User:
    email = "importer@docuaction.io"


def _csv(rows):
    head = "entity_name,npi,qhin\n"
    return (head + "".join(f"Org {i},{npi},QHIN-A\n" for i, npi in rows)).encode()


def _run(monkeypatch, payload):
    db = _FakeDB()

    async def _scan_ok(*_a, **_k):
        return "0" * 64                       # scanner passes; returns a sha256

    async def _no_bridge(_session, rows, **_k):
        return {"registry_created": 0, "registry_updated": 0,
                "registry_failed": 0, "registry_details": []}

    async def _no_audit(*_a, **_k):
        return None

    import app.api.routes as api_routes
    import app.tefca_registry.import_bridge as bridge
    monkeypatch.setattr(api_routes, "_scan_upload_or_reject", _scan_ok)
    monkeypatch.setattr(bridge, "bridge_many", _no_bridge)
    monkeypatch.setattr(tefca_routes, "log_tefca_event", _no_audit)

    result = asyncio.run(tefca_routes.upload_entities(
        request=None, file=_FakeUpload(payload), db=db, user=_User()))
    return db, result


def test_lookup_round_trips_do_not_grow_with_row_count(monkeypatch):
    """300 rows and 30 rows must cost the SAME number of lookups: both fit in
    one 1,000-row chunk. Under the old per-row SELECT this would have been 300
    against 30."""
    db_small, _ = _run(monkeypatch, _csv([(i, f"19990{i:05d}") for i in range(30)]))
    db_large, _ = _run(monkeypatch, _csv([(i, f"19990{i:05d}") for i in range(300)]))
    assert db_large.executes == db_small.executes, (
        f"lookup round trips grew with row count: {db_small.executes} for 30 rows "
        f"vs {db_large.executes} for 300 — the per-row SELECT is back")
    assert db_large.executes <= 2, (
        f"{db_large.executes} round trips for a single chunk of rows")


def test_the_same_npi_twice_in_one_file_does_not_insert_twice(monkeypatch):
    """rce_organization_id is unique. The per-row SELECT this replaced saw an
    earlier row in the same file via autoflush; a lookup built before the loop
    cannot, so without care the second occurrence would insert a duplicate and
    the whole import would fail on the constraint."""
    payload = _csv([(1, "1999000001"), (2, "1999000001"), (3, "1999000002")])
    db, result = _run(monkeypatch, payload)
    ids = [getattr(e, "rce_organization_id", None) for e in db.added]
    entity_ids = [i for i in ids if i and i.startswith("import-")]
    assert len(entity_ids) == len(set(entity_ids)), (
        f"the same rce_organization_id was added twice: {entity_ids}")
    assert "import-1999000001" in entity_ids and "import-1999000002" in entity_ids
    assert result["skipped"] == 1, (
        f"the repeated NPI should be counted as a duplicate, got skipped="
        f"{result['skipped']}")
