"""`GET /api/tefca/rce/delivery-jobs/{id}/detail` against the contract
(2026-09-17, section 6), on the isolated database.

Pinned:
  * resolution: job id -> resolved_from=job_id; intake id with one job ->
    intake_id; an intake with two jobs -> 409 with candidates; unknown -> 404;
  * a job that FAILED before Area 1 renders Failed / Not Ready / guidance,
    with every block never_ran and no intake;
  * a viewer receives null evidence blocks with
    availability = requires_role:reviewer; a reviewer receives the blocks;
  * X-Request-ID is echoed and appears in `correlation.request_id`;
  * the `build` block carries git_sha, build_time, version, migration_revision;
  * the job list items carry processing_outcome / review_state.

Every test here needs the database and skips with the suite's standard reason
when none is reachable.
"""

from __future__ import annotations

import uuid

import pytest

from support_delivery_api import add_stage_events, headers_for, seed_delivery

pytestmark = pytest.mark.usefixtures("db_required")

BASE = "/api/tefca/rce"
BLOCKS = ("records", "exceptions", "lineage", "audit", "verification", "reports")


@pytest.fixture(scope="module")
def succeeded():
    d = seed_delivery(state="SUCCEEDED")
    add_stage_events(d["job_id"], d["intake_id"])
    return d


@pytest.fixture(scope="module")
def failed_without_intake():
    d = seed_delivery(state="FAILED", with_intake=False, stage="PARSING",
                      error_reason="delimiter_undecidable")
    add_stage_events(d["job_id"], None, failed_stage="PARSING")
    return d


@pytest.fixture(scope="module")
def ambiguous():
    return seed_delivery(state="SUCCEEDED", jobs_for_intake=2)


# -- resolution ------------------------------------------------------------------------

def test_detail_by_job_id(client, succeeded):
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['job_id']}/detail",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved_from"] == "job_id"
    assert body["job"]["job_id"] == succeeded["job_id"]
    assert body["job"]["intake_id"] == succeeded["intake_id"]
    for key in ("job", "status", "counts", "timeline", "reconciliation", "dispositions",
                "exceptions", "verification", "reports", "build", "correlation",
                "availability"):
        assert key in body, key


def test_detail_by_intake_id_falls_back_with_resolved_from(client, succeeded):
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['intake_id']}/detail",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved_from"] == "intake_id"
    assert body["job"]["job_id"] == succeeded["job_id"]


def test_ambiguous_intake_is_409_with_candidates(client, ambiguous):
    r = client.get(f"{BASE}/delivery-jobs/{ambiguous['intake_id']}/detail",
                   headers=headers_for("viewer"))
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "CONFLICT"
    ids = {c["job_id"] for c in body["candidates"]}
    assert ids == set(ambiguous["job_ids"]) and len(ids) == 2
    assert "request_id" in body


def test_unknown_and_malformed_ids_are_404(client):
    for ident in (str(uuid.uuid4()), "not-a-uuid"):
        r = client.get(f"{BASE}/delivery-jobs/{ident}/detail", headers=headers_for("viewer"))
        assert r.status_code == 404, (ident, r.status_code)
        assert r.json()["code"] == "NOT_FOUND"


# -- the failed job -------------------------------------------------------------------

def test_failed_job_without_intake_renders_failed_not_ready_and_guidance(client,
                                                                          failed_without_intake):
    r = client.get(f"{BASE}/delivery-jobs/{failed_without_intake['job_id']}/detail",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"]["intake_id"] is None
    assert body["job"]["failed_stage"] == "PARSING"
    assert "register it as a NEW delivery" in body["job"]["remediation_guidance"]
    assert "original bytes are preserved" in body["job"]["remediation_guidance"]
    assert body["status"]["processing_outcome"]["value"] == "Failed"
    assert body["status"]["processing_outcome"]["code"] == "FAILED"
    assert body["status"]["review_state"]["value"] == "Not Ready"
    assert body["status"]["review_state"]["code"] == "NOT_READY"
    assert body["dispositions"] is None
    assert body["reconciliation"] == {"snapshot": None, "history_count": 0}
    # nothing after Area 1 ever ran
    for block in ("verification", "reports"):
        assert body["availability"][block] == "never_ran", block
    # and the failure is visible on the timeline
    assert any(ev["stage"] == "PARSING" and ev["status"] == "FAILED" for ev in body["timeline"])


def test_guidance_text_is_keyed_by_failed_stage():
    from app.tefca_registry.rce import delivery_routes as dr

    assert "original bytes are preserved" in dr._guidance("PARSING")
    assert "Area 1 was preserved" in dr._guidance("CURATION")
    assert "Area 1 was preserved" in dr._guidance("PROMOTION")
    assert dr._guidance(None) == dr.GUIDANCE_UNKNOWN
    assert dr._guidance("SOMETHING_NEW") == dr.GUIDANCE_UNKNOWN


# -- field-level authorization ------------------------------------------------------

def test_viewer_gets_null_evidence_blocks_with_availability(client, succeeded):
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['job_id']}/detail",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    for block in ("records", "exceptions", "lineage", "audit"):
        assert body[block] is None, block
        assert body["availability"][block] == "requires_role:reviewer", block
    # the viewer still gets what carries no delivered value
    assert body["verification"] is not None
    assert body["availability"]["verification"] in (
        "available", "never_ran", "not_configured", "processing", "unavailable")
    assert body["availability"]["reports"] in ("available", "not_yet")
    assert body["dispositions"] is not None and "equation" in body["dispositions"]
    assert set(body["availability"]) == set(BLOCKS)
    # no raw delivered value anywhere in a viewer's response
    assert "Lane A Org" not in r.text
    assert "1881659506" not in r.text


def test_reviewer_gets_the_evidence_blocks(client, succeeded):
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['job_id']}/detail",
                   headers=headers_for("reviewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    for block in ("records", "exceptions", "lineage", "audit"):
        assert body[block] is not None, block
        assert body["availability"][block] != "requires_role:reviewer", block
    assert body["records"]["source_records"] == 3
    assert body["exceptions"]["total"] == 2
    assert body["exceptions"]["by_code"] == {"NPI-003": 1, "NPI-008": 1}
    assert body["exceptions"]["by_severity"] == {"HIGH": 2}
    assert body["exceptions"]["open"] == 2 and body["exceptions"]["resolved"] == 0
    assert "unresolved_identifier_conflicts" in body["lineage"]
    assert "registry_audit_rows" in body["audit"]


# -- correlation and build ----------------------------------------------------------------

def test_request_id_header_is_echoed_and_in_the_body(client, succeeded):
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['job_id']}/detail",
                   headers=headers_for("viewer", {"X-Request-ID": "lane-a-detail-0007"}))
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == "lane-a-detail-0007"
    assert r.json()["correlation"]["request_id"] == "lane-a-detail-0007"
    assert r.json()["correlation"]["job_id"] == succeeded["job_id"]


def test_build_block_is_present_with_migration_revision(client, succeeded, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "deadbeefcafe")
    monkeypatch.setenv("BUILD_TIME", "2026-09-17T00:00:00Z")
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['job_id']}/detail",
                   headers=headers_for("viewer"))
    build = r.json()["build"]
    assert build["git_sha"] == "deadbeefcafe"
    assert build["build_time"] == "2026-09-17T00:00:00Z"
    assert build["version"]
    assert build["migration_revision"] == "20260917_delivery_traceability"


# -- status derivation ------------------------------------------------------------------

def test_succeeded_job_without_snapshot_is_partially_processed(client, succeeded):
    """SUCCEEDED means the pipeline ran; without a persisted reconciliation
    snapshot the contract does not let it claim completion."""
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['job_id']}/detail",
                   headers=headers_for("viewer"))
    status = r.json()["status"]
    assert status["processing_outcome"]["code"] == "PARTIALLY_PROCESSED"
    assert status["review_state"]["code"] == "NOT_READY"
    assert status["derived_by"]


def test_timeline_endpoint_and_registration_events(client, succeeded):
    r = client.get(f"{BASE}/delivery-jobs/{succeeded['job_id']}/timeline",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    stages = [e["stage"] for e in body["events"]]
    assert stages[:3] == ["REGISTERED", "RECEIPT_PRESERVED", "SHA256"]
    assert body["summary"]["failed_stage"] is None
    assert body["resolved_from"] == "job_id"


def test_job_list_items_carry_the_two_axis_status(client, succeeded, failed_without_intake):
    r = client.get(f"{BASE}/delivery-jobs?limit=200", headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    assert r.json()["total"] >= r.json()["count"] >= 2
    by_id = {item["job_id"]: item for item in r.json()["items"]}
    assert by_id[succeeded["job_id"]]["processing_outcome"]["code"] == "PARTIALLY_PROCESSED"
    assert by_id[failed_without_intake["job_id"]]["processing_outcome"]["code"] == "FAILED"
    assert by_id[failed_without_intake["job_id"]]["review_state"]["code"] == "NOT_READY"


def test_verification_coverage_is_viewer_and_counts_only(client, succeeded):
    r = client.get(f"{BASE}/deliveries/{succeeded['intake_id']}/verification-coverage",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["sources"]) == {"nppes", "pecos", "leie", "sam"}
    for source in body["sources"].values():
        assert set(source) >= {"state", "configured", "eligible", "attempted", "verified",
                               "not_found", "deactivated", "failed", "unavailable"}
    assert body["eligible"] == 0  # nothing promoted in the seed
    assert body["state"] in ("Not Run", "Not Configured")


def test_dashboard_carries_two_axis_status_dispositions_and_snapshot(client, succeeded):
    r = client.get(f"{BASE}/deliveries/{succeeded['intake_id']}/dashboard",
                   headers=headers_for("viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["state"]  # legacy word kept
    assert body["status"]["processing_outcome"]["code"] == "PARTIALLY_PROCESSED"
    assert body["status"]["review_state"]["code"] == "NOT_READY"
    assert body["dispositions"]["equation"]["received"] == 3
    assert body["snapshot"] is None
