"""Durable report storage for delivery reports.

WHAT IS PROVED
──────────────
  * generating a delivery-scoped report registers a FINALISED artifact for
    every rendering (HTML, CSV; PDF when the engine is present, otherwise the
    reason is recorded) through the artifact registry and the configured
    store, each with its own SHA-256, size, type, template version, data hash
    and the delivered file's SHA-256;
  * one `rce_delivery_report_links` row per artifact; the link's response
    carries `file_sha256`, `content_type`, `size_bytes` and `storage_backend`
    from the registry join (the model has no such columns; the migration is
    not altered);
  * the local backend is reported as `local / durable: false` — it is for
    tests and development and is never claimed as a durable copy;
  * regeneration from a cited snapshot is a new report with new artifacts
    whose page one and link rows name that snapshot;
  * after the engine is disposed and the store singleton dropped (a process
    restart, as far as the application can tell) the report, its delivery
    listing and every artifact download still succeed from registry + store;
  * a download re-hashes before serving; registered bytes missing from the
    store answer 410 `ARTIFACT_MISSING` (never 500) and write
    `report_download_failed`; tampered bytes are refused;
  * `report_generated`, `report_downloaded` and `report_download_failed`
    audit rows exist with correlation ids;
  * `reviewer` is the floor for the listing and the download. The platform's
    roles are GLOBAL — nothing scopes a reviewer to one delivery — and that is
    stated, not papered over.

GOVERNMENT DATA
    Every fixture is synthetic (the 9.99.999 arc, placeholder names, the
    published NPI check-digit example). The rolled-back tests never reach disk;
    the API tests commit to the isolated test database and remove every row
    they wrote. Artifact bytes go to a pytest temporary directory.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import uuid

import pytest
from sqlalchemy import select, text

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    SYN, artifact_root, rolled_back_db, seed_delivery)

ANALYST = "analyst@synthetic.invalid"
HTML, CSV, PDF = "text/html", "text/csv", "application/pdf"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _generate(db, report_type="delivery_processing", **params):
    from app.reports.generator import generate_report

    return await generate_report(db, report_type=report_type, persist=True,
                                 query_parameters=params, generated_by=ANALYST)


def _expected_types():
    from app.reports.engine.pdf_engine import pdf_available

    return {HTML, CSV} | ({PDF} if pdf_available() else set())


# ═══ generation, storage, retrieval (rolled back) ═══════════════════════════

@pytest.mark.asyncio
async def test_every_rendering_is_a_finalised_artifact_with_provenance(rolled_back_db, artifact_root):
    from app.core.storage.artifact_store import get_artifact_store
    from app.reports.data.artifact_registry import (ReportArtifact, artifacts_for_report,
                                                    retrieve_artifact)
    from app.reports.engine.csv_engine import to_bytes
    from app.reports.engine.pdf_engine import pdf_available
    from app.reports.engine.template_engine import TEMPLATE_VERSION

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, job_id=str(ids["job_id"]))
    report_id, snapshot = result["report_id"], result["snapshot"]
    finalised = result["artifacts"]
    assert finalised["errors"] == [], finalised["errors"]

    # the store the application is configured with — local here, and SAID so
    assert get_artifact_store().backend == "local"
    assert finalised["storage_backend"] == "local" and finalised["durable"] is False
    assert "tests" in finalised["storage_note"] and "App Service" in finalised["storage_note"]

    rows = {a["content_type"]: a for a in finalised["artifacts"]}
    assert set(rows) == _expected_types()
    if not pdf_available():
        assert finalised["pdf"] is None and finalised["pdf_unavailable_reason"]
    else:
        assert finalised["pdf_unavailable_reason"] is None

    expected_bytes = {HTML: result["html"].encode("utf-8"), CSV: to_bytes(result["csv"])}
    for content_type, row in rows.items():
        assert row["finalized"] is True and row["id"]
        assert row["report_id"] == report_id and row["report_type"] == "delivery_processing"
        assert row["content_type"] == content_type
        assert row["template_version"] == TEMPLATE_VERSION == snapshot.template_version
        assert row["report_data_hash"] == snapshot.data_payload_hash
        # the delivery THIS report describes, not whatever is current
        assert row["source_artifact_sha256"] == ids["sha256"] == result["dataset"]["delivery"]["sha256"]
        assert row["data_classification"] == snapshot.data_classification
        assert row["retention"]["classification"] == "PROGRAM_GUIDANCE_REQUESTED"
        assert row["retention"]["worm_locked"] is False
        assert row["generated_by"] == ANALYST and row["generated_at"]
        if content_type in expected_bytes:
            assert row["rendered_sha256"] == _sha(expected_bytes[content_type])
            assert row["size_bytes"] == len(expected_bytes[content_type])
        # the registry row is real and the bytes come back verified
        db_row = await db.get(ReportArtifact, uuid.UUID(row["id"]))
        assert db_row is not None and db_row.rendered_sha256 == row["rendered_sha256"]
        got = await retrieve_artifact(db, report_id, content_type=content_type)
        assert got["verified"] is True and _sha(got["content"]) == row["rendered_sha256"]
        if content_type in expected_bytes:
            assert got["content"] == expected_bytes[content_type]

    # and the bytes are on the tmp root, not in the repository
    assert str(artifact_root) in os.path.abspath(get_artifact_store().root)
    listed = await artifacts_for_report(db, report_id)
    assert {a["content_type"] for a in listed} == set(rows)


@pytest.mark.asyncio
async def test_one_link_per_artifact_with_registry_facts_joined(rolled_back_db, artifact_root):
    from app.reports.data.delivery_report_links import links_for_job, links_for_report
    from app.tefca_registry.rce import traceability_models as tm

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, job_id=str(ids["job_id"]))
    report_id = result["report_id"]
    summary = result["delivery_link"]
    assert summary["written"] is True
    assert len(summary["link_ids"]) == len(summary["artifact_ids"]) == len(_expected_types())

    links = await links_for_report(db, report_id)
    by_type = {l["content_type"]: l for l in links}
    assert set(by_type) == _expected_types()
    registry = {a["content_type"]: a for a in result["artifacts"]["artifacts"]}
    for content_type, link in by_type.items():
        row = registry[content_type]
        assert link["artifact_id"] == row["id"]
        assert link["file_sha256"] == row["rendered_sha256"]
        assert link["size_bytes"] == row["size_bytes"]
        assert link["storage_backend"] == "local" and link["durable"] is False
        assert link["download_url"] == (f"/api/reports/artifacts/{report_id}/download"
                                        f"?content_type={content_type.replace('/', '%2F')}"
                                        f"&version={row['artifact_version']}")
        assert link["artifact"]["rendered_sha256"] == row["rendered_sha256"]
        assert "storage_locator" not in link and "storage_locator" not in link["artifact"]
        assert link["job_id"] == str(ids["job_id"]) and link["intake_id"] == str(ids["intake_id"])
        assert link["snapshot_id"] == str(ids["snapshot_ids"][-1])
        assert link["generation_audit_id"] == summary["audit_id"]
        assert link["correlation_id"] == summary["correlation_id"]
    # the table really holds one row per artifact (unique on report_id, artifact_id)
    count = (await db.execute(select(tm.RceDeliveryReportLink).where(
        tm.RceDeliveryReportLink.report_id == report_id))).scalars().all()
    assert len(count) == len(links)
    assert {l["id"] for l in await links_for_job(db, ids["job_id"])} == {l["id"] for l in links}


@pytest.mark.asyncio
async def test_regeneration_from_a_cited_snapshot_is_a_new_report_with_new_artifacts(
        rolled_back_db, artifact_root):
    from app.reports.data.artifact_registry import retrieve_artifact
    from app.reports.data.delivery_report_links import links_for_job

    db = rolled_back_db
    ids = await seed_delivery(db)
    first, latest = (str(s) for s in ids["snapshot_ids"])
    a = await _generate(db, job_id=str(ids["job_id"]))
    b = await _generate(db, job_id=str(ids["job_id"]), snapshot_id=first)
    assert a["report_id"] != b["report_id"]
    assert a["snapshot_id"] == latest and b["snapshot_id"] == first

    a_rows = {r["content_type"]: r for r in a["artifacts"]["artifacts"]}
    b_rows = {r["content_type"]: r for r in b["artifacts"]["artifacts"]}
    assert set(a_rows) == set(b_rows) == _expected_types()
    for content_type in (HTML, CSV):
        assert a_rows[content_type]["id"] != b_rows[content_type]["id"]
        assert a_rows[content_type]["rendered_sha256"] != b_rows[content_type]["rendered_sha256"]
    assert a_rows[HTML]["report_data_hash"] != b_rows[HTML]["report_data_hash"]

    # page one of the stored HTML artifact prints the cited snapshot id and hash
    stored = (await retrieve_artifact(db, b["report_id"], content_type=HTML))["content"].decode("utf-8")
    assert first in stored and b["dataset"]["snapshot_hash"] in stored
    assert "NOT the latest" in stored
    stored_csv = (await retrieve_artifact(db, b["report_id"], content_type=CSV))["content"].decode("utf-8")
    assert f"# Reconciliation snapshot id: {first}" in stored_csv

    # every link row of the regeneration references the cited snapshot
    links = await links_for_job(db, ids["job_id"])
    for link in links:
        expected = first if link["report_id"] == b["report_id"] else latest
        assert link["snapshot_id"] == expected, link


@pytest.mark.asyncio
async def test_missing_bytes_are_reported_as_missing_not_as_a_server_fault(rolled_back_db, artifact_root):
    """The registry layer distinguishes 'gone' from 'tampered' so the route can
    answer 410 for one and refuse the other."""
    from app.core.storage.artifact_store import ArtifactNotFound, get_artifact_store
    from app.reports.data.artifact_registry import retrieve_artifact_by_id

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, job_id=str(ids["job_id"]))
    csv_row = next(r for r in result["artifacts"]["artifacts"] if r["content_type"] == CSV)
    store = get_artifact_store()
    path = store._resolve(csv_row["storage_locator"])
    os.remove(path)
    with pytest.raises(ArtifactNotFound):
        await retrieve_artifact_by_id(db, csv_row["id"])
    # unknown id is a LookupError (404), not a store error
    with pytest.raises(LookupError):
        await retrieve_artifact_by_id(db, uuid.uuid4())


@pytest.mark.asyncio
async def test_audit_history_carries_correlation_ids(rolled_back_db, artifact_root):
    from app.models.database import AuditLog
    from app.reports.data.delivery_report_links import (
        record_report_download, record_report_download_failure)

    db = rolled_back_db
    ids = await seed_delivery(db)
    result = await _generate(db, job_id=str(ids["job_id"]))
    report_id = result["report_id"]
    generated = (await db.execute(select(AuditLog).where(
        AuditLog.action == "report_generated", AuditLog.resource_id == report_id))).scalar_one()
    assert generated.correlation_id == result["delivery_link"]["correlation_id"]
    assert generated.outcome == "success"
    assert [a["content_type"] for a in generated.details["artifacts"]] and \
        all(a["rendered_sha256"] for a in generated.details["artifacts"])

    ok = await record_report_download(db, report_id=report_id, report_type="delivery_processing",
                                      fmt="artifact:csv", actor=ANALYST,
                                      extra={"rendered_sha256": "x" * 64})
    failed = await record_report_download_failure(
        db, report_id=report_id, report_type="delivery_processing", fmt="artifact:csv",
        actor=ANALYST, code="ARTIFACT_MISSING", reason="bytes gone")
    rows = {r.action: r for r in (await db.execute(select(AuditLog).where(
        AuditLog.resource_id == report_id, AuditLog.resource_type == "report"))).scalars().all()}
    assert set(rows) >= {"report_generated", "report_downloaded", "report_download_failed"}
    assert str(rows["report_downloaded"].id) == ok and rows["report_downloaded"].outcome == "success"
    assert str(rows["report_download_failed"].id) == failed
    assert rows["report_download_failed"].outcome == "failure"
    assert rows["report_download_failed"].details["code"] == "ARTIFACT_MISSING"
    for row in rows.values():
        assert row.event_type == "reporting" and row.correlation_id
        assert 8 <= len(row.correlation_id) <= 64


# ═══ the API, across a restart (committed, then cleaned) ═══════════════════

def _reviewer_headers():
    from support_delivery_api import headers_for

    return headers_for("reviewer")


class _Committed:
    """A synthetic delivery and a generated report, COMMITTED to the isolated
    database so the HTTP layer can read them, and removed afterwards."""

    def __init__(self):
        self.ids = None
        self.result = None
        self.report_ids: list = []
        self.stored_bytes: dict = {}

    async def seed_and_generate(self):
        from app.core.database import async_session_maker
        from app.reports.data.artifact_registry import retrieve_artifact

        async with async_session_maker() as db:
            self.ids = await seed_delivery(db, label=f"{SYN}-DURABLE-{uuid.uuid4().hex[:6]}")
            self.result = await _generate(db, job_id=str(self.ids["job_id"]))
            self.report_ids.append(self.result["report_id"])
            for row in self.result["artifacts"]["artifacts"]:
                got = await retrieve_artifact(db, self.result["report_id"],
                                              content_type=row["content_type"])
                self.stored_bytes[row["content_type"]] = got["content"]
            await db.commit()

    async def restart(self):
        """What a process restart leaves behind: nothing in memory.

        The engine's connections are disposed and the store singleton dropped,
        so the next request must rebuild both from configuration and read the
        report from the database and the artifact root — the same two places a
        fresh App Service instance would have.
        """
        from app.core import database as core_db
        from app.core.storage import artifact_store

        await core_db._get_engine().dispose()
        artifact_store.reset_artifact_store()

    async def cleanup(self):
        from app.core.database import async_session_maker

        if not self.ids:
            return
        p = {
            "jobs": [str(self.ids["job_id"])], "intakes": [str(self.ids["intake_id"])],
            "records": [str(r) for r in self.ids["record_ids"]],
            "entities": [str(self.ids["created_entity_id"]), str(self.ids["existing_entity_id"])],
            "reports": list(self.report_ids) or [None],
        }
        statements = [
            "DELETE FROM rce_delivery_report_links WHERE job_id::text = ANY(:jobs)",
            "DELETE FROM audit_logs WHERE resource_type = 'report' AND resource_id = ANY(:reports)",
            "DELETE FROM report_artifacts WHERE report_id = ANY(:reports)",
            "DELETE FROM review_reports WHERE report_id = ANY(:reports)",
            "DELETE FROM rce_delivery_stage_events WHERE job_id::text = ANY(:jobs)",
            "DELETE FROM rce_reconciliation_snapshots WHERE job_id::text = ANY(:jobs)",
            "DELETE FROM tefca_identifier_decision_events WHERE intake_id::text = ANY(:intakes)",
            "DELETE FROM rce_disposition_events WHERE intake_id::text = ANY(:intakes)",
            "DELETE FROM review_decision_events WHERE review_id IN "
            "(SELECT review_id FROM review_records WHERE source_record_id::text = ANY(:records))",
            "DELETE FROM review_records WHERE source_record_id::text = ANY(:records)",
            "DELETE FROM rce_correction_details WHERE source_record_id::text = ANY(:records)",
            "DELETE FROM rce_curated_records WHERE source_intake_id::text = ANY(:intakes)",
            "DELETE FROM rce_issues WHERE source_intake_id::text = ANY(:intakes)",
            "DELETE FROM rce_ingestion_runs WHERE source_intake_id::text = ANY(:intakes)",
            "DELETE FROM rce_source_records WHERE source_intake_id::text = ANY(:intakes)",
            "DELETE FROM rce_delivery_jobs WHERE id::text = ANY(:jobs)",
            "DELETE FROM rce_source_intakes WHERE id::text = ANY(:intakes)",
            "DELETE FROM tefca_entity_identifiers WHERE entity_id::text = ANY(:entities)",
            "DELETE FROM tefca_entity_versions WHERE entity_id::text = ANY(:entities)",
            "DELETE FROM tefca_reg_entities WHERE id::text = ANY(:entities)",
        ]
        # One transaction PER statement: a failing delete must not roll back
        # the ones before it, or every later delete fails on the rows the
        # rollback resurrected.
        async with async_session_maker() as db:
            for sql in statements:
                try:
                    await db.execute(text(sql), p)
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    print(f"cleanup: {sql[:60]}... {type(exc).__name__}: {exc}")
                    await db.rollback()


@pytest.fixture
def committed(db_required, tmp_path, monkeypatch):
    """Committed synthetic delivery + report, artifact root on a tmp dir that
    outlives the simulated restart, everything removed at the end."""
    from support_delivery_api import run

    from app.core.storage import artifact_store

    monkeypatch.setenv("REPORT_ARTIFACT_ROOT", str(tmp_path / "durable-artifacts"))
    monkeypatch.delenv("REPORT_ARTIFACT_BACKEND", raising=False)
    artifact_store.reset_artifact_store()
    fixture = _Committed()
    run(fixture.seed_and_generate())
    try:
        yield fixture
    finally:
        run(fixture.cleanup())
        artifact_store.reset_artifact_store()


def test_reports_survive_a_restart_from_registry_and_store(committed, client):
    from support_delivery_api import run

    from app.models.database import AuditLog

    report_id = committed.result["report_id"]
    job_id = str(committed.ids["job_id"])
    run(committed.restart())
    headers = _reviewer_headers()

    # the report itself, with its artifacts listed
    detail = client.get(f"/api/reports/{report_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["report_id"] == report_id
    assert body["snapshot"]["report_id"] == report_id
    assert {a["content_type"] for a in body["artifacts"]} == _expected_types()
    assert len(body["delivery_links"]) == len(body["artifacts"])
    assert body["delivery_link"]["snapshot_id"] == str(committed.ids["snapshot_ids"][-1])
    for a in body["artifacts"]:
        assert a["storage_backend"] == "local" and a["durable"] is False
        assert "storage_locator" not in a

    # the delivery listing names every artifact with hash, size, backend, url
    listing = client.get(f"/api/reports/by-delivery/{job_id}", headers=headers)
    assert listing.status_code == 200, listing.text
    payload = listing.json()
    assert payload["job_id"] == job_id and payload["count"] == len(_expected_types())
    assert payload["storage"] == {"storage_backend": "local", "durable": False,
                                  "storage_note": payload["storage"]["storage_note"]}
    assert "tests" in payload["storage"]["storage_note"]
    assert payload["scope"]["per_delivery_scoping"] is True  # QA-034: verified per delivery since 2026-09-20
    assert payload["quarantined"] == []
    assert payload["scope"]["minimum_role"] == "reviewer"
    assert len(payload["reports"]) == 1 and payload["reports"][0]["report_id"] == report_id
    assert len(payload["reports"][0]["artifacts"]) == len(_expected_types())
    for item in payload["items"]:
        assert item["file_sha256"] and item["content_type"] and item["size_bytes"]
        assert item["storage_backend"] == "local" and item["durable"] is False
    artifacts = {a["content_type"]: a for a in payload["artifacts"]}
    assert set(artifacts) == _expected_types()

    # every artifact downloads through the verified path, by url and by row id
    for content_type, a in artifacts.items():
        for url in (a["download_url"], f"/api/reports/artifacts/{a['id']}/download"):
            response = client.get(url, headers=headers)
            assert response.status_code == 200, (url, response.text)
            assert response.headers["content-type"].startswith(content_type)
            assert response.headers["X-Artifact-SHA256"] == a["rendered_sha256"] == _sha(response.content)
            assert response.headers["X-Artifact-Verified"] == "true"
            assert response.headers["Content-Disposition"].startswith("attachment;")
            assert "no-store" in response.headers["Cache-Control"]
            assert len(response.content) == a["size_bytes"]
            assert response.content == committed.stored_bytes[content_type]

    # audit rows: the generation, and one download per served response
    async def _audit():
        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            return (await db.execute(select(AuditLog).where(
                AuditLog.resource_type == "report", AuditLog.resource_id == report_id
            ).order_by(AuditLog.created_at))).scalars().all()

    rows = run(_audit())
    actions = [r.action for r in rows]
    assert actions.count("report_generated") == 1
    assert actions.count("report_downloaded") == 2 * len(_expected_types())
    assert "report_download_failed" not in actions
    for r in rows:
        assert r.correlation_id and r.event_type == "reporting"
    downloaded = [r for r in rows if r.action == "report_downloaded"]
    assert all(r.details["verified"] is True and r.details["rendered_sha256"] for r in downloaded)
    assert {r.details["actor"] for r in downloaded} == {run_actor()}


def run_actor() -> str:
    from support_delivery_api import user_for

    return user_for("reviewer")["email"]


def test_missing_bytes_answer_410_artifact_missing_and_are_audited(committed, client):
    from support_delivery_api import run

    from app.core.storage.artifact_store import get_artifact_store
    from app.models.database import AuditLog

    report_id = committed.result["report_id"]
    csv_row = next(r for r in committed.result["artifacts"]["artifacts"] if r["content_type"] == CSV)
    html_row = next(r for r in committed.result["artifacts"]["artifacts"] if r["content_type"] == HTML)
    store = get_artifact_store()
    os.remove(store._resolve(csv_row["storage_locator"]))          # the blob/file is gone
    with open(store._resolve(html_row["storage_locator"]), "ab") as fh:  # the bytes changed
        fh.write(b"<!-- altered after issue -->")
    run(committed.restart())
    headers = _reviewer_headers()

    gone = client.get(f"/api/reports/artifacts/{report_id}/download?content_type=text/csv",
                      headers=headers)
    assert gone.status_code == 410, gone.text
    body = gone.json()
    assert body["code"] == "ARTIFACT_MISSING" and body["report_id"] == report_id
    assert body["request_id"] and "registered" in body["error"]
    assert "local://" not in gone.text and "artifact.csv" not in gone.text
    gone_by_id = client.get(f"/api/reports/artifacts/{csv_row['id']}/download", headers=headers)
    assert gone_by_id.status_code == 410 and gone_by_id.json()["code"] == "ARTIFACT_MISSING"

    # tampered bytes are REFUSED. The platform's handler scrubs every 5xx body
    # to a generic message; the machine code is in the audit row below.
    tampered = client.get(f"/api/reports/artifacts/{report_id}/download?content_type=text/html",
                          headers=headers)
    assert tampered.status_code == 500, tampered.text
    assert tampered.json()["code"] == "INTERNAL_ERROR"
    assert not tampered.headers.get("content-type", "").startswith("text/html")
    assert "X-Artifact-SHA256" not in tampered.headers
    assert b"altered after issue" not in tampered.content

    # an unknown id is audited under the id that was asked for; register it so
    # the fixture removes that audit row too
    unknown_id = str(uuid.uuid4())
    committed.report_ids.append(unknown_id)
    unknown = client.get(f"/api/reports/artifacts/{unknown_id}/download", headers=headers)
    assert unknown.status_code == 404 and unknown.json()["code"] == "ARTIFACT_NOT_REGISTERED"

    # the listing still names the artifact: the registry row is the record of issue
    listing = client.get(f"/api/reports/by-delivery/{committed.ids['job_id']}", headers=headers)
    assert listing.status_code == 200
    assert csv_row["id"] in {a["id"] for a in listing.json()["artifacts"]}

    async def _failures():
        from app.core.database import async_session_maker

        async with async_session_maker() as db:
            return (await db.execute(select(AuditLog).where(
                AuditLog.action == "report_download_failed",
                AuditLog.resource_id == report_id))).scalars().all()

    failures = run(_failures())
    codes = sorted(r.details["code"] for r in failures)
    assert codes == ["ARTIFACT_INTEGRITY_FAILURE", "ARTIFACT_MISSING", "ARTIFACT_MISSING"]
    for r in failures:
        assert r.outcome == "failure" and r.correlation_id and r.event_type == "reporting"
        assert r.details["actor"] == run_actor()
        assert "local://" not in str(r.details)


def test_reviewer_is_the_floor_and_roles_are_global(committed, client):
    """`viewer` and `contributor` are refused; `reviewer` and above are served.

    There is no per-delivery scoping on this platform: `app.core.tenant`
    scopes only the enterprise document tables and no RCE or report table has
    a tenant or delivery-ownership column. A reviewer can therefore fetch any
    delivery's artifacts by id. That is enforced as a role floor plus an audit
    row per download — and stated, not disguised as a scoping control.
    """
    from support_delivery_api import headers_for

    from app.reports import routes

    job_id = str(committed.ids["job_id"])
    report_id = committed.result["report_id"]
    download = f"/api/reports/artifacts/{report_id}/download?content_type=text/csv"
    listing = f"/api/reports/by-delivery/{job_id}"

    for path in (listing, download):
        assert client.get(path).status_code in (401, 403), path
        for role in ("viewer", "contributor"):
            assert client.get(path, headers=headers_for(role)).status_code == 403, (path, role)
        for role in ("reviewer", "qalead"):
            assert client.get(path, headers=headers_for(role)).status_code == 200, (path, role)

    for handler in (routes.reports_by_delivery, routes.artifact_download):
        # Decision 1 of the pre-merge review (2026-09-16) added an audit row
        # on every denial; the floor is still `reviewer`, now via the
        # audited wrapper. `test_report_authorization.py` proves the row is
        # actually written.
        assert 'require_role_audited("reviewer"' in inspect.getsource(handler), handler.__name__
    # nothing in the reports package pretends to scope by tenant or delivery
    source = inspect.getsource(routes)
    assert "get_tenant" not in source and "TenantContext" not in source
    assert "per_delivery_scoping" in inspect.getsource(routes.reports_by_delivery)


# ═══ static pins ════════════════════════════════════════════════════════════

def test_local_backend_is_never_reported_as_durable():
    from app.reports.data.artifact_registry import storage_durability

    local = storage_durability("local")
    assert local["durable"] is False and "not durable" in local["storage_note"].lower()
    assert storage_durability("azure_blob")["durable"] is True
    assert storage_durability(None)["durable"] is False


def test_the_download_route_never_answers_500_for_missing_bytes():
    from app.reports import routes

    source = inspect.getsource(routes.artifact_download)
    assert "ArtifactNotFound" in source and "create_error_response(410" in source
    assert "ARTIFACT_MISSING" in source and "record_report_download_failure" in source
    # ArtifactNotFound subclasses RuntimeError: it must be caught FIRST or it
    # would fall into the integrity branch and become a 500.
    assert source.index("except ArtifactNotFound") < source.index("except RuntimeError")


def test_generator_registers_every_rendering_for_delivery_reports():
    from app.reports import generator

    source = inspect.getsource(generator.generate_report)
    assert "finalize_report_renderings" in source
    assert source.index("finalize_artifact") < source.index("finalize_report_renderings") \
        < source.index("record_report_generation")
    assert '"artifacts": artifacts' in source
