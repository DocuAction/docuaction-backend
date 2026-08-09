"""Import → registry bridge (Task 1).

The Entity Import page writes `tefca_entities`; verification reads
`tefca_reg_entities`. They were disjoint, so importing five hospitals and then
verifying them touched two unrelated sets of rows — and every step still
reported success, which is what made it survive so long. The end-to-end demo
exposed it: step 3 matched registry rows by NAME, and address comparison
reported `not_compared` because those rows carried no address.

These tests cover the bridge logic and its wiring. The DB-backed behaviour is
exercised end-to-end by scripts/run_full_demo.py against a real environment;
here the concern is that the mapping is right and that a bridge failure can
never discard an import that already succeeded.
"""

import asyncio
import inspect
import uuid

import pytest

from app.tefca_registry import import_bridge
from app.tefca_registry.import_bridge import (CREATED, FAILED, UPDATED,
                                              bridge_entity, bridge_many,
                                              one_line_address)

pytestmark = [pytest.mark.regression, pytest.mark.qa_defect]


class FakeSession:
    """Minimal async session: records adds, resolves one optional lookup."""

    def __init__(self, existing=None, identifier_entity_id=None, explode=False):
        self.added = []
        self.flushed = 0
        self._existing = existing
        self._identifier_entity_id = identifier_entity_id
        self._explode = explode

    def add(self, obj):
        if self._explode:
            raise RuntimeError("simulated write failure")
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1

    async def execute(self, _stmt):
        entity_id = self._identifier_entity_id

        class R:
            def scalar_one_or_none(self_inner):
                return entity_id

        return R()

    async def get(self, _model, _pk):
        return self._existing

    def begin_nested(self):
        """bridge_many wraps each row in a savepoint so one bad row cannot roll
        back the rows already written. The fake mirrors that shape."""
        session = self

        class Savepoint:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, *exc):
                return False

        return Savepoint()


class FakeRegEntity:
    def __init__(self):
        self.id = uuid.uuid4()
        self.name = "OLD NAME"
        self.display_name = "OLD NAME"
        self.address = None
        self.city = None
        self.state = None
        self.zip = None
        self.updated_at = None


ROW = {
    "npi": "1477978807",
    "name": "Johns Hopkins Hospital",
    "address": "1800 Orleans St",
    "city": "Baltimore",
    "state": "MD",
    "zip_code": "21287",
    "entity_type": "PARTICIPANT",
}


# ── creation ─────────────────────────────────────────────────────────────────

def test_import_creates_registry_entity():
    session = FakeSession(identifier_entity_id=None)
    out = asyncio.run(bridge_entity(session, **ROW))
    assert out["status"] == CREATED
    assert out["entity_id"]
    kinds = [type(o).__name__ for o in session.added]
    assert "TefcaRegEntity" in kinds
    assert "TefcaEntityIdentifier" in kinds, "the NPI must be recorded as an identifier"


def test_registry_entity_has_name():
    session = FakeSession(identifier_entity_id=None)
    asyncio.run(bridge_entity(session, **ROW))
    entity = next(o for o in session.added if type(o).__name__ == "TefcaRegEntity")
    assert entity.name == "Johns Hopkins Hospital"
    assert entity.display_name == "Johns Hopkins Hospital"


def test_registry_entity_has_address():
    """The whole point of the bridge: without an address on the registry row,
    address comparison has nothing to compare and reports not_compared."""
    session = FakeSession(identifier_entity_id=None)
    asyncio.run(bridge_entity(session, **ROW))
    entity = next(o for o in session.added if type(o).__name__ == "TefcaRegEntity")
    assert entity.address == "1800 Orleans St Baltimore MD 21287"
    assert entity.city == "Baltimore"
    assert entity.state == "MD"
    assert entity.zip == "21287"


def test_a_new_registry_entity_is_not_marked_verified():
    """Importing a row says where it came from, not that anything confirmed it."""
    session = FakeSession(identifier_entity_id=None)
    asyncio.run(bridge_entity(session, **ROW))
    entity = next(o for o in session.added if type(o).__name__ == "TefcaRegEntity")
    assert entity.verification_status == "not_verified"


# ── update / dedup ───────────────────────────────────────────────────────────

def test_import_updates_existing_registry_entity():
    existing = FakeRegEntity()
    session = FakeSession(existing=existing,
                          identifier_entity_id=existing.id)
    out = asyncio.run(bridge_entity(session, **ROW))
    assert out["status"] == UPDATED
    assert existing.name == "Johns Hopkins Hospital"
    assert existing.address == "1800 Orleans St Baltimore MD 21287"


def test_duplicate_npi_updates_not_duplicates():
    """Re-importing the same roster must not create a second registry row for
    one provider — matched on NPI, which is what the provider is known by."""
    existing = FakeRegEntity()
    session = FakeSession(existing=existing, identifier_entity_id=existing.id)
    asyncio.run(bridge_entity(session, **ROW))
    assert not [o for o in session.added if type(o).__name__ == "TefcaRegEntity"]


def test_an_import_without_an_address_does_not_blank_an_existing_one():
    """A CSV lacking an address column must not erase an address the registry
    already holds from another source."""
    existing = FakeRegEntity()
    existing.address = "55 Fruit St Boston MA 02114"
    session = FakeSession(existing=existing, identifier_entity_id=existing.id)
    row = {**ROW, "address": None, "city": None, "state": None, "zip_code": None}
    asyncio.run(bridge_entity(session, **row))
    assert existing.address == "55 Fruit St Boston MA 02114"


# ── failure containment ──────────────────────────────────────────────────────

def test_a_bridge_failure_never_raises():
    """The entity is already in tefca_entities. Losing a completed import
    because a secondary write failed would discard the operator's work."""
    session = FakeSession(identifier_entity_id=None, explode=True)
    out = asyncio.run(bridge_entity(session, **ROW))
    assert out["status"] == FAILED
    assert "reason" in out


@pytest.mark.parametrize("bad", [
    {"npi": "", "name": "Acme"},
    {"npi": "1477978807", "name": ""},
])
def test_a_row_without_npi_or_name_cannot_be_bridged(bad):
    session = FakeSession(identifier_entity_id=None)
    out = asyncio.run(bridge_entity(session, **bad))
    assert out["status"] == FAILED
    assert "required" in out["reason"]


# ── address formatting ───────────────────────────────────────────────────────

def test_zip_is_truncated_to_five_digits():
    """NPPES pads ZIP to nine digits with no hyphen; 212870010 matches nothing."""
    assert one_line_address("1800 Orleans St", "Baltimore", "MD",
                            "212870010").endswith("21287")


def test_one_line_address_skips_missing_parts():
    assert one_line_address("55 Fruit St", None, "MA", None) == "55 Fruit St MA"
    assert one_line_address(None, None, None, None) == ""


def test_entity_level_maps_from_the_legacy_type():
    from app.tefca_registry.import_bridge import DEFAULT_LEVEL, LEVEL_BY_LEGACY_TYPE

    assert LEVEL_BY_LEGACY_TYPE["QHIN"] == "qhin"
    assert LEVEL_BY_LEGACY_TYPE["SUBPARTICIPANT"] == "sub_participant"
    assert DEFAULT_LEVEL == "participant"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_verify_uses_imported_data():
    """The import endpoint must actually call the bridge — the logic being
    correct is worth nothing if nothing invokes it."""
    from app.Tefca import routes as legacy

    src = inspect.getsource(legacy.upload_entities)
    assert "bridge_many" in src
    assert '"city": a.get("city")' in src, "city must reach the registry"
    assert '"zip_code": a.get("zip")' in src


def test_the_bridge_runs_after_the_legacy_write():
    """Ordering matters: bridging first would create registry rows for entities
    the legacy write might then reject."""
    from app.Tefca import routes as legacy

    src = inspect.getsource(legacy.upload_entities)
    assert src.index("db.add(TEFCAEntity(") < src.index("bridge_many(")


def test_the_response_reports_registry_outcomes_separately():
    """"imported: 5" alone would not tell a caller that verification still
    cannot see those entities."""
    from app.Tefca import routes as legacy

    src = inspect.getsource(legacy.upload_entities)
    for field in ('"registry_created"', '"registry_updated"', '"registry_failed"'):
        assert field in src


def test_address_comparison_runs():
    """review_service reads entity.address; the bridge is what puts one there."""
    from app.tefca_registry import review_service

    src = inspect.getsource(review_service._compare_addresses)
    assert 'getattr(entity, "address", "")' in src


def test_name_comparison_runs():
    """Name resolution needs an authoritative record, which needs the source
    payload carried through probe_sources."""
    from app.tefca_registry import review_service

    src = inspect.getsource(review_service.probe_sources)
    assert 'out[key]["data"]' in src
    assert "organization_name" in src


def test_bridge_many_counts_each_outcome():
    session = FakeSession(identifier_entity_id=None)
    out = asyncio.run(bridge_many(session, [ROW, {**ROW, "npi": ""}]))
    assert out["registry_created"] == 1
    assert out["registry_failed"] == 1
    assert len(out["registry_details"]) == 2
