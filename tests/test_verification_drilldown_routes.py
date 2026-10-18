"""HTTP-layer tests for the verification drill-down routes (item 2 of the
reporting-architecture task): GET .../verification-coverage/{source}/{outcome}
and its .csv sibling. Data-layer correctness (reconciliation, pagination,
sort, the 'failed' refusal) is proven in test_verification_drilldown.py;
this file proves the ROUTE wiring -- role floor, 404 for a missing delivery,
422/409 surfaced correctly, response shape, and a basic timing sanity check.
"""
from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text

from support_delivery_api import cleanup, headers_for, run, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"


@pytest.fixture(scope="module")
def delivery():
    # issues=1, not 0: support_delivery_api._seed_delivery only creates a
    # curated record alongside an issue row -- issues=0 seeds source records
    # but zero curated records, which this fixture needs one of.
    d = seed_delivery(state="SUCCEEDED", issues=1)

    async def _seed_verification_row():
        from app.core.database import async_session_maker

        # seed_delivery() does Area 1 (intake/quality/curation) only -- no
        # record is promoted, so canonical_entity_id is NULL on every curated
        # row it creates. This fixture needs ONE promoted entity: a
        # tefca_reg_entities row (tefca_verifications.entity_id's FK target)
        # plus the curated row's canonical_entity_id pointed at it, same
        # pattern test_coverage_sql_aggregation.py's own fixture uses.
        ent_id = uuid.uuid4()
        async with async_session_maker() as db:
            source_record_id = (await db.execute(text(
                "SELECT id FROM rce_curated_records "
                "WHERE source_intake_id = CAST(:i AS uuid) LIMIT 1"),
                {"i": d["intake_id"]})).scalar()
            assert source_record_id is not None, "fixture delivery seeded no curated record"
            await db.execute(text(
                "INSERT INTO tefca_reg_entities (id, name, entity_level, entity_type) "
                "VALUES (:id, 'DRILLDOWN ROUTE TEST ENTITY', 'participant', 'provider')"),
                {"id": ent_id})
            await db.execute(text(
                "UPDATE rce_curated_records SET canonical_entity_id = :e WHERE id = :id"),
                {"e": ent_id, "id": source_record_id})
            await db.execute(text(
                "INSERT INTO tefca_verifications (id, source, verification_status, entity_id) "
                "VALUES (:id, 'nppes', 'verified', :e)"),
                {"id": uuid.uuid4(), "e": ent_id})
            await db.commit()
            return str(ent_id)
    d["verified_entity_id"] = run(_seed_verification_row())
    yield d

    async def _cleanup_verification_rows():
        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            await db.execute(text(
                "DELETE FROM tefca_verifications WHERE entity_id::text = :e"),
                {"e": d.get("verified_entity_id") or ""})
            await db.execute(text(
                "UPDATE rce_curated_records SET canonical_entity_id = NULL "
                "WHERE canonical_entity_id::text = :e"),
                {"e": d.get("verified_entity_id") or ""})
            await db.execute(text("DELETE FROM tefca_reg_entities WHERE id::text = :e"),
                             {"e": d.get("verified_entity_id") or ""})
            await db.commit()
    run(_cleanup_verification_rows())
    cleanup()


def test_viewer_is_denied_entity_level_drilldown(client, delivery):
    """Decision 1 floor: the coverage COUNTS route is viewer-reachable, but
    this route returns entity name/NPI, so it needs reviewer, same as
    dispositions/exceptions."""
    resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/verified",
                      headers=headers_for("viewer"))
    assert resp.status_code == 403


def test_reviewer_sees_the_seeded_verified_entity(client, delivery):
    resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/verified",
                      headers=headers_for("reviewer"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "nppes" and body["outcome"] == "verified"
    assert body["total"] >= 1
    assert any(i["entity_id"] == delivery["verified_entity_id"] for i in body["items"])


def test_failed_outcome_is_refused_with_409_not_an_empty_list(client, delivery):
    resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/failed",
                      headers=headers_for("reviewer"))
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "OUTCOME_RECONCILIATION_PENDING"


def test_unknown_source_is_422(client, delivery):
    resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/not-a-source/verified",
                      headers=headers_for("reviewer"))
    assert resp.status_code == 422


def test_unknown_delivery_is_404(client):
    resp = client.get(f"{BASE}/deliveries/{uuid.uuid4()}/verification-coverage/nppes/verified",
                      headers=headers_for("reviewer"))
    assert resp.status_code == 404


def test_csv_export_matches_the_json_total(client, delivery):
    json_resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/verified",
                           headers=headers_for("reviewer"))
    csv_resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/verified/csv",
                          headers=headers_for("reviewer"))
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.headers["X-Returned-Rows"] == str(json_resp.json()["total"])
    assert "entity_id,source_record_id,line_number,entity_name,npi" in csv_resp.text


def test_csv_export_of_failed_is_also_refused(client, delivery):
    resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/failed/csv",
                      headers=headers_for("reviewer"))
    assert resp.status_code == 409
    assert resp.json()["code"] == "OUTCOME_RECONCILIATION_PENDING"


def test_metadata_get_style_response_time_under_gate(client, delivery):
    """Not the 24,589-row scale (that is the EXPLAIN ANALYZE evidence file's
    job), but a basic route-level sanity check: a small-delivery drill-down
    page answers well under the 3 s 'first verification page' gate."""
    t0 = time.perf_counter()
    resp = client.get(f"{BASE}/deliveries/{delivery['intake_id']}/verification-coverage/nppes/verified",
                      headers=headers_for("reviewer"))
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200
    assert elapsed < 3.0, f"took {elapsed:.3f}s"
