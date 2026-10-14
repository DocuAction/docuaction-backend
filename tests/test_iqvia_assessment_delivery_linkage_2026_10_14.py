"""Delivery linkage of IQVIA advisory assessments, against a REAL database (isolation-postgres job).

What this pins, end to end, with two independently seeded synthetic deliveries A and B:

  1. the request body the UI sends (the frontend unit test asserts the same keys) is accepted over HTTP by the
     real route, and the row is stored against delivery A (`delivery_intake_id`, FK to rce_source_intakes);
  2. delivery A's report CSV and report HTML (the source of the PDF) disclose that use; delivery B's do not;
  3. the server refuses a missing delivery (422 from the schema), an unknown delivery (DELIVERY_NOT_FOUND) and an
     entity that is not in the named delivery (ENTITY_NOT_IN_DELIVERY), and writes no row for any of them;
  4. switching consumption OFF afterwards does not erase the disclosure for A (it states the lookup is disabled and
     the records are historical), and B stays "not used";
  5. an environment whose assessment table is not applied says so; it does not claim "not used".

The lookup itself is stubbed (it has its own database-backed tests); what is real here is the HTTP route, the
validation, the stored row, and the report data service, CSV and HTML rendering. Synthetic data only. The PDF is
rendered from this same HTML by the "render"/"previews" CI jobs; WeasyPrint is not required here.
"""
from __future__ import annotations

import csv
import importlib
import io
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    artifact_root, rolled_back_db, seed_delivery)

pytestmark = pytest.mark.asyncio

NPI = "1043566623"  # Luhn-valid synthetic organisation NPI
SNAP_PREFIX = "aaaaaaaaaaaa"


@pytest.fixture
def routes(monkeypatch):
    """The IQVIA affiliation routes module with the consumption flag ON (routes exist only then)."""
    from app.core.config import settings
    from app.tefca_registry.rce import iqvia_affiliation_routes as mod

    monkeypatch.setattr(settings, "ENABLE_IQVIA_AFFILIATION_CONSUMPTION", True, raising=False)
    monkeypatch.setattr(settings, "ENABLE_IQVIA_SOURCES", True, raising=False)
    mod = importlib.reload(mod)
    yield mod
    monkeypatch.undo()
    importlib.reload(mod)  # leave the module as the default-OFF import it was


def _app(routes_mod, db, user) -> FastAPI:
    from app.core.database import get_db

    app = FastAPI()
    app.include_router(routes_mod.router)

    async def _db():
        yield db

    def _user():
        return user

    for route in routes_mod.router.routes:
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = _db if dep.call is get_db else _user
    return app


async def _snapshot(db):
    from app.tefca_registry.rce import snapshot_models as sm

    snap = sm.SourceSnapshot(
        id=uuid.uuid4(), source_system="IQVIA_AFFILIATION", snapshot_label="SYNTHETIC-IQVIA-LINKAGE",
        sha256="a" * 64, record_count=10, received_at=datetime.now(timezone.utc), status="APPROVED",
        approved_by="qa-lead@synthetic.invalid", approved_role="qalead", approved_at=datetime.now(timezone.utc), approval_ref="SYN-APPROVAL",
        created_by="synthetic@synthetic.invalid", correlation_id="linkage-test")
    db.add(snap)
    await db.flush()
    return snap


@pytest.fixture
def stub_lookup(monkeypatch):
    from app.tefca_registry.rce import iqvia_affiliation_consumer as ac

    def _install(snap):
        async def fake(db, **kw):
            return {
                "provenance": {"snapshot_id": str(snap.id), "source_system": "IQVIA_AFFILIATION",
                               "status": "APPROVED", "record_count": 10, "sha256_prefix": SNAP_PREFIX},
                "determination": ac.DETERMINATION, "requires_analyst_review": True, "affects_verification": False,
                "found_in_extract": True,
                "organisations": [{"hco_record_key": "SYN-O1", "relationship_rows": 3}],
                "organisations_omitted": 0, "conflicts": [], "notes": [],
                "registry_candidate": {"outcome": "CANDIDATE"}, "relationships": [], "total_relationships": 3,
                "returned": 0, "page_limit": 1, "truncated": True, "next_cursor": None}
        monkeypatch.setattr(ac, "organisation_relationships", fake)
    return _install


def _ui_body(snap, intake_id, **over):
    """Exactly the keys AdvisoryAssessment.js posts (frontend test: 'posts the chosen delivery')."""
    body = {"snapshot_id": str(snap.id), "org_npi": NPI, "decision": "IDENTITY_CORROBORATED_ADVISORY",
            "rationale": "Synthetic: organisation NPI matches the registry candidate.",
            "delivery_intake_id": str(intake_id)}
    body.update(over)
    return {k: v for k, v in body.items() if v is not None}


async def _post(app, body):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post("/api/tefca/rce/iqvia/affiliation/assessments", json=body)


async def _report(db, intake_id):
    from app.reports.generator import generate_report

    return await generate_report(db, report_type="delivery_processing", persist=False,
                                 query_parameters={"intake_id": str(intake_id)})


def _csv_rows(text_):
    return list(csv.reader(io.StringIO(text_)))


async def _row_count(db):
    from app.tefca_registry.rce import iqvia_advisory_assessment as aa

    return (await db.execute(select(func.count()).select_from(aa.IqviaAdvisoryAssessment))).scalar_one()


USER = SimpleNamespace(id=None, role="reviewer", email="iqvia-assessor@synthetic.invalid")


async def test_saved_assessment_is_disclosed_on_its_own_delivery_only(routes, rolled_back_db, artifact_root,
                                                                       stub_lookup):
    from app.tefca_registry.rce import iqvia_advisory_assessment as aa

    db = rolled_back_db
    a, b = await seed_delivery(db, label="SYN-LINK-A"), await seed_delivery(db, label="SYN-LINK-B")
    snap = await _snapshot(db)
    stub_lookup(snap)

    resp = await _post(_app(routes, db, USER), _ui_body(snap, a["intake_id"]))
    assert resp.status_code == 201, resp.text
    assert resp.json()["delivery_intake_id"] == str(a["intake_id"])

    stored = (await db.execute(select(aa.IqviaAdvisoryAssessment))).scalars().all()
    assert len(stored) == 1 and stored[0].delivery_intake_id == a["intake_id"]
    assert stored[0].affects_verification is False

    ra, rb = await _report(db, a["intake_id"]), await _report(db, b["intake_id"])

    rows_a, rows_b = _csv_rows(ra["csv"]), _csv_rows(rb["csv"])
    assert ["State", "IQVIA_ADVISORY_USED"] in rows_a and ["Advisory assessments recorded", "1"] in rows_a
    assert ["Decision IDENTITY_CORROBORATED_ADVISORY", "1"] in rows_a
    assert ["Snapshot consulted", str(snap.id), f"sha256 prefix {SNAP_PREFIX}"] in rows_a
    assert ["State", "IQVIA_NOT_USED"] in rows_b and ["Advisory assessments recorded", "0"] in rows_b
    assert not any(r and r[0] == "Snapshot consulted" for r in rows_b)

    # HTML (the PDF's source): A discloses the use, B does not, and neither carries identifiers.
    assert "consulted in 1 analyst advisory assessment" in ra["html"] and str(snap.id) in ra["html"]
    assert "No IQVIA data was used for this delivery" in rb["html"] and str(snap.id) not in rb["html"]
    for out in (ra, rb):
        assert NPI not in out["csv"] and NPI not in out["html"] and "SYN-O1" not in out["csv"]
        assert "iqvia-assessor@synthetic.invalid" not in out["csv"]
    assert ra["dataset"]["iqvia_use"]["assessments"] == 1 and rb["dataset"]["iqvia_use"]["assessments"] == 0

    # The assessment changed no bucket, count or coverage figure on either delivery.
    assert ra["dataset"]["dispositions"]["counts"] == rb["dataset"]["dispositions"]["counts"]


async def test_server_refuses_a_missing_unknown_or_foreign_delivery_and_writes_nothing(
        routes, rolled_back_db, artifact_root, stub_lookup):
    db = rolled_back_db
    a, b = await seed_delivery(db, label="SYN-LINK-A"), await seed_delivery(db, label="SYN-LINK-B")
    snap = await _snapshot(db)
    stub_lookup(snap)
    app = _app(routes, db, USER)

    missing = await _post(app, _ui_body(snap, a["intake_id"], delivery_intake_id=None))
    assert missing.status_code == 422                                   # required by the request schema

    unknown = await _post(app, _ui_body(snap, uuid.uuid4()))
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "DELIVERY_NOT_FOUND"

    # An entity that is not present in the named delivery must not be linked to it.
    foreign = await _post(app, _ui_body(snap, a["intake_id"], entity_id=str(uuid.uuid4())))
    assert foreign.status_code == 422 and foreign.json()["detail"]["code"] == "ENTITY_NOT_IN_DELIVERY"

    assert await _row_count(db) == 0


async def test_disabling_consumption_keeps_the_disclosure_of_past_use(routes, rolled_back_db, artifact_root,
                                                                       stub_lookup, monkeypatch):
    from app.core.config import settings

    db = rolled_back_db
    a, b = await seed_delivery(db, label="SYN-LINK-A"), await seed_delivery(db, label="SYN-LINK-B")
    snap = await _snapshot(db)
    stub_lookup(snap)
    assert (await _post(_app(routes, db, USER), _ui_body(snap, a["intake_id"]))).status_code == 201

    monkeypatch.setattr(settings, "ENABLE_IQVIA_AFFILIATION_CONSUMPTION", False, raising=False)  # later disabled
    ra, rb = await _report(db, a["intake_id"]), await _report(db, b["intake_id"])

    use_a = ra["dataset"]["iqvia_use"]
    assert use_a["state"] == "IQVIA_ADVISORY_USED" and use_a["assessments"] == 1
    assert use_a["consumption_currently_enabled"] is False
    assert "currently disabled" in ra["html"] and "historical records of past use" in ra["html"]
    assert ["State", "IQVIA_ADVISORY_USED"] in _csv_rows(ra["csv"])
    assert ["State", "IQVIA_NOT_USED"] in _csv_rows(rb["csv"])          # still scoped to its own delivery


async def test_unapplied_assessment_store_is_stated_not_reported_as_not_used(rolled_back_db, artifact_root):
    db = rolled_back_db
    a = await seed_delivery(db, label="SYN-LINK-A")
    # DDL is transactional in PostgreSQL: the rename is undone with the test's outer rollback.
    await db.execute(text("ALTER TABLE iqvia_advisory_assessment RENAME TO iqvia_advisory_assessment_hidden"))
    ra = await _report(db, a["intake_id"])
    use = ra["dataset"]["iqvia_use"]
    assert use["state"] == "IQVIA_ASSESSMENT_STORE_NOT_APPLIED" and use["assessments"] == 0
    assert ["State", "IQVIA_ASSESSMENT_STORE_NOT_APPLIED"] in _csv_rows(ra["csv"])
    assert "not present in this environment" in ra["html"]
    assert "No IQVIA data was used for this delivery" not in ra["html"]
