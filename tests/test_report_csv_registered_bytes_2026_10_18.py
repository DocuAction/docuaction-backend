"""The CSV `/api/reports/{id}/csv` serves is the CSV the artifact registry hashed (DEV, 2026-10-17, DA-ARC-2026-080).

WHY
---
Report DA-ARC-2026-080's registered CSV (sha256 a3e1db4d...) and the body returned by "Download CSV" differed by 48
bytes. Two causes, both covered here against a real PostgreSQL database:
  1. The route regenerated the CSV from `review_reports.report_data` on every call instead of serving the registered
     artifact, so any later change to the renderer changed what a historical report returned. It now serves the
     registered bytes first, exactly like /html, /pdf and the package (canonical-surface rule QA108-20260927-013).
  2. `report_data` is JSONB, which stores object keys shortest-first; a renderer that iterated a mapping in stored
     order printed the same rows in a different order from the rendering made at generation time. The regeneration
     path (legacy reports with no registered CSV) must therefore be byte-identical to the registered rendering.
Database-backed; the fixtures are the ones test_delivery_processing_report.py registers.
"""
from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_delivery_processing_report import (  # noqa: F401  (fixtures registered by import)
    artifact_root, rolled_back_db, seed_delivery)

USER = SimpleNamespace(email="qa@synthetic.invalid", id=None)
CSV = "text/csv"
# Keys the disclosure gained in PR #162; a dataset stored before it has none of them.
NEW_DISCLOSURE_KEYS = ("decision_counts", "entities_linked", "assessor_roles", "assessment_refs",
                       "lookup_availability", "lookup_availability_statement")


async def _persisted_delivery_report(db):
    from app.reports.generator import generate_report

    ids = await seed_delivery(db)
    result = await generate_report(db, report_type="delivery_processing", persist=True,
                                   query_parameters={"job_id": str(ids["job_id"])}, generated_by=USER.email)
    assert result["stored_id"], "the report must be stored for /csv to serve it"
    return ids, result


async def _registered_csv(db, report_id):
    from app.reports.data.artifact_registry import retrieve_artifact

    got = await retrieve_artifact(db, report_id, content_type=CSV)
    assert got["content"], "no CSV artifact was registered; nothing to compare"
    return got


async def _stored_row(db, report_id):
    from sqlalchemy import select

    from app.tefca_registry import models as reg

    return (await db.execute(select(reg.ReviewReport).where(reg.ReviewReport.report_id == report_id))).scalar_one()


def _without_new_keys(dataset: dict) -> dict:
    """The dataset shape a report stored before PR #162 carries (all of 071-080)."""
    iq = {k: v for k, v in (dataset.get("iqvia_use") or {}).items() if k not in NEW_DISCLOSURE_KEYS}
    iq["snapshots"] = [{k: v for k, v in sn.items() if k not in ("received_on", "source_effective_date")}
                       for sn in iq.get("snapshots") or []]
    return {**dataset, "iqvia_use": iq}


@pytest.mark.asyncio
async def test_csv_route_serves_the_registered_bytes_even_when_the_stored_dataset_drifts(rolled_back_db, artifact_root):
    """The registered artifact is the deliverable. If `report_data` later drifts (whatever wrote it), the route keeps
    serving the bytes the recipient can verify against the registry, never a fresh rendering of the drifted column."""
    from app.reports import routes

    db = rolled_back_db
    _ids, result = await _persisted_delivery_report(db)
    registered = await _registered_csv(db, result["report_id"])

    row = await _stored_row(db, result["report_id"])
    data = dict(row.report_data or {})
    drifted = dict(data.get("dataset") or {})
    drifted["iqvia_use"] = {**(drifted.get("iqvia_use") or {}), "statement": "synthetic drift: not what was issued"}
    row.report_data = {**data, "dataset": drifted}      # new object so the JSONB column is actually rewritten
    await db.flush()

    first = await routes.get_report_csv(result["report_id"], job_id=None, db=db, user=USER)
    second = await routes.get_report_csv(result["report_id"], job_id=None, db=db, user=USER)
    assert first.body == second.body == registered["content"]
    assert hashlib.sha256(first.body).hexdigest() == registered["artifact"]["rendered_sha256"]
    assert first.headers.get("X-Report-Source") == "artifact-registry"
    assert b"synthetic drift" not in first.body


@pytest.mark.asyncio
async def test_registered_downloads_expose_the_artifact_hash_as_a_header_not_only_in_the_body(
        rolled_back_db, artifact_root):
    """DEF-2026-10-26-06: the body of a served HTML report can be altered by something OUTSIDE this
    application (confirmed live: the Azure App Service platform injects a per-request script tag into
    every text/html response from this App Service, including unrelated ones like /docs -- not this
    app's own code). A caller who re-hashes the HTML body can never reproduce the registered hash
    because of that, through no fault of the artifact system.

    X-Artifact-SHA256 carries the hash of the REGISTERED bytes -- re-verified server-side by
    _registered_bytes/_verified before this header is ever set -- so a caller has something to check
    that a body-level transformation cannot touch. CSV and PDF aren't subject to the same platform
    behaviour (confirmed: their downloads are byte-identical across repeats), but they get the same
    header for the same reason: the header is the artifact's identity; the body is one way to deliver it.
    """
    from app.reports import routes
    from app.reports.engine.pdf_engine import pdf_available

    db = rolled_back_db
    _ids, result = await _persisted_delivery_report(db)
    registered_csv = await _registered_csv(db, result["report_id"])

    html_first = await routes.get_report_html(result["report_id"], job_id=None, db=db, user=USER)
    html_second = await routes.get_report_html(result["report_id"], job_id=None, db=db, user=USER)
    for resp in (html_first, html_second):
        assert resp.headers.get("X-Artifact-Verified") == "true"
        assert resp.headers.get("X-Artifact-SHA256") == hashlib.sha256(resp.body).hexdigest()
    assert html_first.headers["X-Artifact-SHA256"] == html_second.headers["X-Artifact-SHA256"], \
        "same report, same registered artifact -- the header must not vary call to call"

    csv_resp = await routes.get_report_csv(result["report_id"], job_id=None, db=db, user=USER)
    assert csv_resp.headers.get("X-Artifact-SHA256") == registered_csv["artifact"]["rendered_sha256"]
    assert csv_resp.headers.get("X-Artifact-Verified") == "true"

    if pdf_available():
        pdf_resp = await routes.get_report_pdf(result["report_id"], job_id=None, db=db, user=USER)
        assert pdf_resp.headers.get("X-Artifact-SHA256") == hashlib.sha256(pdf_resp.body).hexdigest()
        assert pdf_resp.headers.get("X-Artifact-Verified") == "true"


@pytest.mark.asyncio
async def test_csv_route_refuses_a_mismatched_delivery_before_any_registry_access(rolled_back_db, artifact_root, monkeypatch):
    """Scope first. A caller naming another delivery's job_id is refused with 409 REPORT_DELIVERY_MISMATCH before the
    registry is consulted, so the refusal can trigger neither the registry's integrity error nor its audit rows."""
    from app.reports import routes

    db = rolled_back_db
    _ids, result = await _persisted_delivery_report(db)
    calls = []
    real = routes._registered_bytes

    async def spy(db_, report_id, content_type):
        calls.append(content_type)
        return await real(db_, report_id, content_type)

    monkeypatch.setattr(routes, "_registered_bytes", spy)
    with pytest.raises(HTTPException) as exc:
        await routes.get_report_csv(result["report_id"], job_id=str(uuid.uuid4()), db=db, user=USER)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "REPORT_DELIVERY_MISMATCH"
    assert calls == [], "the registry was consulted for a request that was refused"

    # the matching delivery is still served from the registry
    ok = await routes.get_report_csv(result["report_id"], job_id=str(_ids["job_id"]), db=db, user=USER)
    assert ok.headers.get("X-Report-Source") == "artifact-registry" and calls == [CSV]


@pytest.mark.asyncio
async def test_csv_route_regenerates_a_legacy_report_with_no_artifact_and_says_so(rolled_back_db, artifact_root):
    """A report from before artifact registration has only the stored dataset; the route regenerates it, exactly as
    before, and labels the source so a reader can tell the two cases apart."""
    from app.reports import routes
    from app.tefca_registry import models as reg

    db = rolled_back_db
    legacy = reg.ReviewReport(
        id=uuid.uuid4(), report_id="DA-SYN-LEGACY-CSV-001", report_type="delivery_processing",
        report_data={"dataset": {"iqvia_use": {"state": "IQVIA_USED", "assessments": 2, "reviews_linked": 1,
                                               "by_decision": {"NOT_FOUND_IN_EXTRACT": 1, "INSUFFICIENT_EVIDENCE": 1},
                                               "snapshots": [{"snapshot_id": str(uuid.uuid4()), "sha256_prefix": "dbc52f5269a3"}],
                                               "statement": "Advisory IQVIA assessments were recorded for this delivery."}},
                     "snapshot": {"generation_timestamp": "2026-10-17T22:44:00Z"}},
        report_html="<html><body>legacy</body></html>")
    db.add(legacy)
    await db.flush()

    response = await routes.get_report_csv("DA-SYN-LEGACY-CSV-001", job_id=None, db=db, user=USER)
    assert response.headers.get("X-Report-Source") == "regenerated-from-stored-dataset"
    text = response.body.decode("utf-8-sig")
    assert "## IQVIA use disclosure" in text
    # the pre-#162 shape regenerates with honest gaps, not a missing-field error
    assert "Assessments linked to a registry entity,not recorded" in text
    assert "Decision INSUFFICIENT_EVIDENCE,1" in text and "Decision NOT_FOUND_IN_EXTRACT,1" in text
    assert text.index("Decision INSUFFICIENT_EVIDENCE") < text.index("Decision NOT_FOUND_IN_EXTRACT")   # sorted, not JSONB order


@pytest.mark.asyncio
async def test_regenerating_from_the_stored_jsonb_dataset_gives_the_registered_bytes(rolled_back_db, artifact_root, monkeypatch):
    """The DA-ARC-2026-080 root cause, against a real JSONB round trip: when no artifact can be served (forced here),
    the regeneration from `report_data` is byte-identical to the rendering registered at generation time."""
    from app.reports import routes

    db = rolled_back_db
    _ids, result = await _persisted_delivery_report(db)
    registered = await _registered_csv(db, result["report_id"])
    await db.refresh(await _stored_row(db, result["report_id"]))          # read the dataset back from JSONB, not the cache

    async def none_registered(db_, report_id, content_type):
        return None

    monkeypatch.setattr(routes, "_registered_bytes", none_registered)
    response = await routes.get_report_csv(result["report_id"], job_id=None, db=db, user=USER)
    assert response.headers.get("X-Report-Source") == "regenerated-from-stored-dataset"
    assert response.body == registered["content"], "regeneration from the stored dataset differs from the registered CSV"


@pytest.mark.asyncio
async def test_a_dataset_stored_before_pr_162_regenerates_and_renders_without_missing_field_errors(rolled_back_db, artifact_root, monkeypatch):
    """Reports 071-080 were stored without the new disclosure keys. The CSV regeneration path must take that shape
    from a real JSONB row (the strict-undefined HTML template is covered over the real 060 fixture in
    test_iqvia_disclosure_traceability_2026_10_17.py)."""
    from app.reports import routes

    db = rolled_back_db
    _ids, result = await _persisted_delivery_report(db)
    row = await _stored_row(db, result["report_id"])
    data = dict(row.report_data or {})
    old_shape = _without_new_keys(dict(data.get("dataset") or {}))
    assert not any(k in old_shape["iqvia_use"] for k in NEW_DISCLOSURE_KEYS)
    row.report_data = {**data, "dataset": old_shape}
    await db.flush()
    await db.refresh(row)

    async def none_registered(db_, report_id, content_type):
        return None

    monkeypatch.setattr(routes, "_registered_bytes", none_registered)
    response = await routes.get_report_csv(result["report_id"], job_id=None, db=db, user=USER)
    assert response.status_code == 200 and b"## IQVIA use disclosure" in response.body
    assert b"not recorded" in response.body            # the gaps are stated, not raised
