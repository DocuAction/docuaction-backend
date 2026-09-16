"""Report generation is itself evidence: audit row first, then the link that
points at it, then the job's REPORT_GENERATION stage event; every download is
an audit row too.

Uses the synthetic delivery from test_delivery_processing_report and the same
rolled-back session, so nothing here touches Government data or persists.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    SYN, artifact_root, rolled_back_db, seed_delivery)


async def _generate(db, report_type="delivery_processing", **params):
    from app.reports.generator import generate_report

    return await generate_report(db, report_type=report_type, persist=True,
                                 query_parameters=params,
                                 generated_by="analyst@synthetic.invalid")


@pytest.mark.asyncio
async def test_generation_writes_audit_then_link_then_stage_event(rolled_back_db, artifact_root):
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import links_for_job, links_for_report
    from app.reports.engine.template_engine import TEMPLATE_VERSION
    from app.tefca_registry.rce import traceability_models as tm

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, job_id=str(ids["job_id"]))
    report_id = result["report_id"]
    link_summary = result["delivery_link"]
    assert link_summary["written"] is True, link_summary
    assert link_summary["audit_id"] and link_summary["link_id"] and link_summary["stage_event_id"]

    # 1. the audit row of record
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "report_generated", AuditLog.resource_id == report_id))).scalar_one()
    assert audit.event_type == "reporting" and audit.resource_type == "report"
    assert audit.outcome == "success" and audit.correlation_id
    details = audit.details
    assert details["job_id"] == str(ids["job_id"])
    assert details["intake_id"] == str(ids["intake_id"])
    assert details["snapshot_id"] == str(ids["snapshot_ids"][-1])
    assert details["template_version"] == TEMPLATE_VERSION
    assert "build_sha" in details and details["actor"] == "analyst@synthetic.invalid"

    # the audit row names every durable rendering and the backend it went to
    assert {a["content_type"] for a in details["artifacts"]} >= {"text/html", "text/csv"}
    assert details["storage_backend"] == "local" and details["durable"] is False

    # 2. ONE link per stored artifact (HTML, CSV, and PDF when the engine is
    #    present), every one pointing at that audit row and the pinned snapshot
    from app.reports.engine.pdf_engine import pdf_available

    links = await links_for_report(db, report_id)
    expected_types = {"text/html", "text/csv"} | ({"application/pdf"} if pdf_available() else set())
    assert {l["content_type"] for l in links} == expected_types
    assert len(links) == len(expected_types) == len(link_summary["link_ids"])
    for link in links:
        assert link["job_id"] == str(ids["job_id"]) and link["intake_id"] == str(ids["intake_id"])
        assert link["snapshot_id"] == str(ids["snapshot_ids"][-1]) == result["snapshot_id"]
        assert link["report_type"] == "delivery_processing"
        assert link["template_version"] == TEMPLATE_VERSION
        assert link["generation_audit_id"] == str(audit.id) == link_summary["audit_id"]
        assert link["generated_by"] == "analyst@synthetic.invalid"
        assert link["correlation_id"] == audit.correlation_id
        assert link["build_sha"]
        # the registry facts the model has no column for come from the join
        assert link["artifact_id"] and link["artifact_id"] in link_summary["artifact_ids"]
        assert link["file_sha256"] and len(link["file_sha256"]) == 64
        assert link["size_bytes"] > 0 and link["storage_backend"] == "local"
        assert link["durable"] is False and "tests" in link["storage_note"]
        assert link["download_url"].startswith(f"/api/reports/artifacts/{report_id}/download?")
    # deterministic order inside one generation: the HTML link first
    assert links[0]["content_type"] == "text/html"
    assert link_summary["link_id"] in {l["id"] for l in links}
    if not pdf_available():
        assert link_summary["pdf_unavailable_reason"]
    by_job = await links_for_job(db, ids["job_id"])
    assert sorted(l["id"] for l in by_job) == sorted(l["id"] for l in links)

    # 3. the job's own timeline records the generation
    ev = (await db.execute(select(tm.RceDeliveryStageEvent).where(
        tm.RceDeliveryStageEvent.job_id == ids["job_id"],
        tm.RceDeliveryStageEvent.stage == "REPORT_GENERATION"))).scalar_one()
    assert ev.status == "COMPLETED" and ev.completed_at is not None
    assert ev.detail["report_id"] == report_id
    assert ev.detail["snapshot_id"] == links[0]["snapshot_id"]
    assert ev.detail["audit_id"] == str(audit.id)
    assert sorted(ev.detail["artifact_ids"]) == sorted(l["artifact_id"] for l in links)
    assert str(ev.intake_id) == str(ids["intake_id"])


@pytest.mark.asyncio
async def test_regeneration_links_the_snapshot_it_was_rendered_from(rolled_back_db, artifact_root):
    from app.reports.data.delivery_report_links import links_for_job

    db = rolled_back_db
    ids = await seed_delivery(db)
    first, latest = (str(s) for s in ids["snapshot_ids"])
    a = await _generate(db, job_id=str(ids["job_id"]))
    b = await _generate(db, job_id=str(ids["job_id"]), snapshot_id=first)
    assert a["snapshot_id"] == latest and b["snapshot_id"] == first
    links = {l["report_id"]: l for l in await links_for_job(db, ids["job_id"])}
    assert links[a["report_id"]]["snapshot_id"] == latest
    assert links[b["report_id"]]["snapshot_id"] == first


@pytest.mark.asyncio
async def test_data_quality_report_for_a_named_delivery_is_linked_too(rolled_back_db, artifact_root):
    from app.reports.data.delivery_report_links import links_for_report

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, report_type="data_quality", intake_id=str(ids["intake_id"]))
    links = await links_for_report(db, result["report_id"])
    # one link per durable rendering, HTML and CSV at least
    assert {l["content_type"] for l in links} >= {"text/html", "text/csv"}
    for link in links:
        assert link["report_type"] == "data_quality"
        assert link["job_id"] == str(ids["job_id"])
        assert link["snapshot_id"] == str(ids["snapshot_ids"][-1])
        assert link["file_sha256"] and link["artifact_id"]
    # the durable copies name the delivery file they describe
    assert all(a["source_artifact_sha256"] == ids["sha256"]
               for a in result["artifacts"]["artifacts"])


@pytest.mark.asyncio
async def test_without_a_snapshot_the_audit_row_stands_alone(rolled_back_db, artifact_root):
    """The link table requires a snapshot (NOT NULL FK). A delivery that never
    reconciled gets the audit row and an explicit reason, never a fabricated
    snapshot id."""
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import links_for_report

    db = rolled_back_db
    ids = await seed_delivery(db, with_snapshot=False)
    result = await _generate(db, job_id=str(ids["job_id"]))
    summary = result["delivery_link"]
    assert summary["written"] is True and summary["link_id"] is None
    assert "snapshot_id" in summary["reason"]
    assert await links_for_report(db, result["report_id"]) == []
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "report_generated",
        AuditLog.resource_id == result["report_id"]))).scalar_one()
    assert audit.details["snapshot_id"] is None


@pytest.mark.asyncio
async def test_downloads_are_audited(rolled_back_db):
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import record_report_download

    db = rolled_back_db
    report_id = f"DA-ARC-TEST-{uuid.uuid4().hex[:6]}"
    audit_id = await record_report_download(
        db, report_id=report_id, report_type="delivery_processing", fmt="csv",
        actor="viewer@synthetic.invalid", extra={"note": "test"})
    assert audit_id
    row = (await db.execute(select(AuditLog).where(AuditLog.id == uuid.UUID(audit_id)))).scalar_one()
    assert row.action == "report_downloaded" and row.event_type == "reporting"
    assert row.resource_type == "report" and row.resource_id == report_id
    assert row.details["format"] == "csv" and row.details["actor"] == "viewer@synthetic.invalid"
    assert row.details["note"] == "test" and row.correlation_id


def test_every_download_route_writes_a_download_audit():
    """Static pin: each served format calls the audit helper."""
    import inspect

    from app.reports import routes

    for fn, fmt in ((routes.get_report_html, '"html"'), (routes.get_report_pdf, '"pdf"'),
                    (routes.get_report_csv, '"csv"'), (routes.get_report_docx, '"docx"'),
                    (routes.get_package, '"package"')):
        source = inspect.getsource(fn)
        assert "_audit_download" in source and fmt in source, fn.__name__
    assert "record_report_download" in inspect.getsource(routes.artifact_download)


def test_by_delivery_and_detail_routes_are_registered():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/reports/by-delivery/{job_id}" in paths
    assert "/api/reports/{report_id}" in paths


def test_by_delivery_route_requires_authentication():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get(f"/api/reports/by-delivery/{uuid.uuid4()}")
    assert response.status_code in (401, 403)
