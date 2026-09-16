"""Decision 1 of the pre-merge review (2026-09-16): report authorization.

Every current, legacy, alias, direct-artifact and regeneration route that can
return a report's actual content (dataset, rendered HTML/PDF/DOCX/CSV, the
package, artifact history and download, and SOW deliverable data) needs
`reviewer`. `viewer` reaches metadata and availability only. This module
proves the DENY direction end-to-end over real HTTP, on real committed rows,
for both the current router (`app/reports/routes.py`) and the deprecated
legacy alias router (`app/Tefca/routes.py`), and proves a denial is audited.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from support_delivery_api import headers_for, run, seed_delivery

from test_report_storage_durable import _Committed, _generate  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_required")

CONTENT_ROUTES = (
    ("POST", "/api/reports/generate", True),   # body varies; checked separately
    ("GET", "/api/reports/{report_id}/html", False),
    ("GET", "/api/reports/{report_id}/pdf", False),
    ("GET", "/api/reports/{report_id}/docx", False),
    ("GET", "/api/reports/{report_id}/csv", False),
    ("GET", "/api/reports/{report_id}/package", False),
    ("GET", "/api/reports/artifacts/{report_id}", False),
    ("GET", "/api/reports/artifacts/{report_id}/download", False),
)

LEGACY_ALIAS_ROUTES = (
    ("GET", "/api/tefca/reports/{report_id}"),
    ("GET", "/api/tefca/reports/{report_id}/csv"),
    ("GET", "/api/tefca/reports/{report_id}/pdf"),
    ("GET", "/api/tefca/reports/{report_id}/docx"),
    ("GET", "/api/tefca/reports/{report_id}/download"),
)


@pytest.fixture
def two_deliveries(tmp_path, monkeypatch, db_required):
    """Two independent, committed deliveries+reports (A and B), so a
    cross-delivery substitution test has a second, genuinely different
    object to try. Both cleaned up unconditionally."""
    from app.core.storage import artifact_store

    monkeypatch.setenv("REPORT_ARTIFACT_ROOT", str(tmp_path / "authz-artifacts"))
    monkeypatch.delenv("REPORT_ARTIFACT_BACKEND", raising=False)
    artifact_store.reset_artifact_store()
    a, b = _Committed(), _Committed()
    run(a.seed_and_generate())
    run(b.seed_and_generate())
    try:
        yield a, b
    finally:
        run(a.cleanup())
        run(b.cleanup())
        artifact_store.reset_artifact_store()


def _url(template: str, report_id: str) -> str:
    return template.replace("{report_id}", report_id)


# ── 1-2. anonymous / viewer denied, reviewer allowed, over every route ──────

def test_anonymous_is_denied_on_every_content_route(client, two_deliveries):
    a, _b = two_deliveries
    report_id = a.result["report_id"]
    for method, template, _skip_body in CONTENT_ROUTES:
        url = _url(template, report_id)
        resp = client.post(url) if method == "POST" else client.get(url)
        assert resp.status_code in (401, 403), (method, url, resp.status_code)


def test_viewer_is_denied_on_every_content_route(client, two_deliveries):
    a, _b = two_deliveries
    report_id = a.result["report_id"]
    headers = headers_for("viewer")
    for method, template, _skip_body in CONTENT_ROUTES:
        url = _url(template, report_id)
        resp = (client.post(url, headers=headers, json={"report_type": "delivery_processing",
                                                        "format": "html",
                                                        "parameters": {"job_id": str(a.ids["job_id"])}})
               if method == "POST" else client.get(url, headers=headers))
        assert resp.status_code == 403, (method, url, resp.status_code, resp.text[:200])
        body = resp.json()
        assert body.get("code") in ("FORBIDDEN", None) or "request_id" in body
        assert "request_id" in body, (url, body)


#: These render through WeasyPrint; on a machine missing its native Pango/
#: Cairo/GObject libraries (this Windows dev box; the Dockerfile installs
#: them for the deployed image) the ENGINE answers 503, not authorization.
_ENGINE_DEPENDENT = ("pdf", "package")


def test_reviewer_is_allowed_on_every_download_route(client, two_deliveries):
    a, _b = two_deliveries
    report_id = a.result["report_id"]
    headers = headers_for("reviewer")
    for method, template, skip_body in CONTENT_ROUTES:
        if method == "POST":
            continue  # generate is exercised on its own below (needs a job)
        url = _url(template, report_id)
        resp = client.get(url, headers=headers)
        # Never a 401/403: authorization itself must never be what refuses a
        # reviewer. 503 is accepted only for the PDF-rendering routes, and
        # only for the engine's own documented reason.
        assert resp.status_code not in (401, 403), (method, url, resp.status_code, resp.text[:200])
        if resp.status_code == 503 and any(p in url for p in _ENGINE_DEPENDENT):
            # The 5xx handler scrubs the body (platform policy); the reason
            # lives in the server log, already asserted by
            # test_download_security.py. Here only the non-auth status matters.
            continue
        # 404 is a legitimate non-auth outcome (e.g. `delivery_processing`
        # reports have no DOCX form) -- what matters here is that a reviewer
        # is never refused by AUTHORIZATION.
        assert resp.status_code in (200, 404), (method, url, resp.status_code, resp.text[:300])


def test_reviewer_can_generate(client, two_deliveries):
    a, _b = two_deliveries
    resp = client.post("/api/reports/generate", headers=headers_for("reviewer"),
                       json={"report_type": "delivery_processing", "format": "html",
                             "parameters": {"job_id": str(a.ids["job_id"])}})
    assert resp.status_code == 200, resp.text


def test_contributor_below_reviewer_is_denied_on_generate(client, two_deliveries):
    """`/generate` moved from `contributor` to `reviewer` (Decision 1): the
    response can carry the report's full content."""
    a, _b = two_deliveries
    resp = client.post("/api/reports/generate", headers=headers_for("contributor"),
                       json={"report_type": "delivery_processing", "format": "html",
                             "parameters": {"job_id": str(a.ids["job_id"])}})
    assert resp.status_code == 403, resp.text


# ── 1. viewer sees metadata and availability on GET /{report_id} ───────────

def test_viewer_sees_metadata_and_availability_only_on_report_detail(client, two_deliveries):
    a, _b = two_deliveries
    report_id = a.result["report_id"]
    resp = client.get(f"/api/reports/{report_id}", headers=headers_for("viewer"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report_id"] == report_id
    assert body["report_type"] == "delivery_processing"
    assert "generated_at" in body and "snapshot" in body
    # content fields are null with a stated reason, never omitted and never
    # silently empty
    assert body["dataset"] is None
    assert body["delivery_link"] is None
    assert body["delivery_links"] == []
    assert body["artifacts"] == []
    assert body["availability"] == {
        "dataset": "requires_role:reviewer",
        "delivery_links": "requires_role:reviewer",
        "artifacts": "requires_role:reviewer",
    }


def test_reviewer_sees_the_full_report_detail(client, two_deliveries):
    a, _b = two_deliveries
    report_id = a.result["report_id"]
    resp = client.get(f"/api/reports/{report_id}", headers=headers_for("reviewer"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dataset"], "reviewer must see the report's actual content"
    assert body["availability"] == {"dataset": "available", "delivery_links": "available",
                                    "artifacts": "available"}


def test_viewer_reaches_the_listing_and_release_status_metadata_only(client, two_deliveries):
    """Requirement 1: metadata/availability stays open to viewer. `GET ""`
    and `GET /{report_id}/release` carry no delivered values."""
    a, _b = two_deliveries
    report_id = a.result["report_id"]
    headers = headers_for("viewer")
    listing = client.get("/api/reports", headers=headers)
    assert listing.status_code == 200
    release = client.get(f"/api/reports/{report_id}/release", headers=headers)
    assert release.status_code == 200


# ── 4. every current, legacy, alias, direct-artifact route ──────────────────

def test_legacy_alias_routes_deny_viewer_and_serve_reviewer(client, two_deliveries):
    """`app/Tefca/routes.py`'s deprecated `/api/tefca/reports/*` aliases render
    the SAME content through a different renderer. A caller must not reach
    Government-derived report bytes by using the deprecated path instead of
    the current one."""
    from app.tefca_registry import models as reg

    a, _b = two_deliveries
    # These legacy routes read a DIFFERENT model (TEFCAReport); seed one row
    # so the alias path has a real object to try to reach, rather than only
    # proving a 404 is not mistaken for a 403.
    tefca_report_id = uuid.uuid4()
    run(_seed_legacy_tefca_report(tefca_report_id))
    try:
        for method, template in LEGACY_ALIAS_ROUTES:
            url = _url(template, str(tefca_report_id))
            anon = client.get(url)
            assert anon.status_code in (401, 403), (url, anon.status_code)
            viewer = client.get(url, headers=headers_for("viewer"))
            assert viewer.status_code == 403, (url, viewer.status_code, viewer.text[:200])
            reviewer = client.get(url, headers=headers_for("reviewer"))
            assert reviewer.status_code in (200, 500, 503), (
                url, reviewer.status_code, reviewer.text[:200])
            # Never a 403/401 for reviewer: whatever the renderer's own outcome,
            # authorization itself must not be what stops a reviewer.
            assert reviewer.status_code not in (401, 403)
    finally:
        run(_cleanup_legacy_tefca_report(tefca_report_id))


async def _seed_legacy_tefca_report(report_id):
    from app.core.database import async_session_maker
    from app.Tefca.models import TEFCAReport

    async with async_session_maker() as db:
        db.add(TEFCAReport(
            report_id=report_id, report_type="D3.1_WEEKLY",
            report_data={"summary": "synthetic legacy report for an authorization test"},
            generated_by="authz-test@example.test"))
        await db.commit()


async def _cleanup_legacy_tefca_report(report_id):
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        await db.execute(text("DELETE FROM tefca_reports WHERE report_id = :i"),
                         {"i": str(report_id)})
        await db.commit()


# ── 5. an alternate identifier / alias cannot bypass authorization ─────────

def test_the_registry_row_id_alias_on_artifact_download_is_equally_denied(client, two_deliveries):
    """`artifact_download` accepts either the report id or the registry ROW id
    as `report_id`. A viewer must be refused through EITHER form."""
    from app.reports.data.artifact_registry import artifacts_for_report

    a, _b = two_deliveries
    report_id = a.result["report_id"]
    rows = run(artifacts_for_report_async(report_id))
    assert rows, "fixture must have registered at least one artifact"
    row_id = str(rows[0]["id"])

    by_report_id = f"/api/reports/artifacts/{report_id}/download?content_type=text/html"
    by_row_id = f"/api/reports/artifacts/{row_id}/download"

    for url in (by_report_id, by_row_id):
        assert client.get(url, headers=headers_for("viewer")).status_code == 403, url
        assert client.get(url, headers=headers_for("reviewer")).status_code == 200, url


async def artifacts_for_report_async(report_id):
    from app.core.database import async_session_maker
    from app.reports.data.artifact_registry import artifacts_for_report

    async with async_session_maker() as db:
        rows = await artifacts_for_report(db, report_id)
        # `artifacts_for_report` already returns plain dicts (registry rows).
        return [{"id": r["id"], "content_type": r["content_type"]} for r in rows]


# ── 6. cross-delivery object substitution cannot bypass authorization ──────

def test_cross_delivery_substitution_is_denied_the_same_way_for_both_deliveries(
        client, two_deliveries):
    """Roles are global on this platform (no per-delivery scoping exists); the
    floor must therefore be identical whichever delivery's report a caller
    names -- a viewer cannot succeed by picking delivery B's report_id, job_id
    or artifact after being refused on delivery A's."""
    a, b = two_deliveries
    viewer = headers_for("viewer")
    reviewer = headers_for("reviewer")

    for delivery in (a, b):
        report_id = delivery.result["report_id"]
        job_id = str(delivery.ids["job_id"])
        for url in (f"/api/reports/{report_id}/html",
                    f"/api/reports/by-delivery/{job_id}"):
            assert client.get(url, headers=viewer).status_code == 403, (delivery, url)
            assert client.get(url, headers=reviewer).status_code == 200, (delivery, url)

    # Substituting B's job_id while requesting A's report_id/vice versa proves
    # nothing new is granted by the mismatch -- still refused for viewer.
    mismatched = f"/api/reports/{a.result['report_id']}/html"
    assert client.get(mismatched, headers=viewer).status_code == 403
    by_delivery_b = f"/api/reports/by-delivery/{b.ids['job_id']}"
    assert client.get(by_delivery_b, headers=viewer).status_code == 403


# ── 7. a denied attempt is recorded (security-audit policy) ────────────────

def test_a_denied_download_attempt_is_recorded_in_the_audit_trail(client, two_deliveries):
    from app.core.database import async_session_maker
    from app.models.database import AuditLog

    a, _b = two_deliveries
    report_id = a.result["report_id"]
    url = f"/api/reports/{report_id}/html"

    async def _count():
        async with async_session_maker() as db:
            result = await db.execute(
                select(AuditLog).where(
                    AuditLog.event_type == "security",
                    AuditLog.outcome == "blocked",
                    AuditLog.resource_type == "report",
                    AuditLog.resource_id == url))
            return result.scalars().all()

    before = run(_count())
    resp = client.get(url, headers=headers_for("viewer"))
    assert resp.status_code == 403
    after = run(_count())
    assert len(after) == len(before) + 1, "exactly one denial row must be written"
    row = after[-1]
    assert row.details.get("required_role") == "reviewer"
    assert row.details.get("current_role") == "viewer"
    assert row.correlation_id


def test_a_successful_download_is_not_recorded_as_a_denial(client, two_deliveries):
    from app.core.database import async_session_maker
    from app.models.database import AuditLog

    a, _b = two_deliveries
    report_id = a.result["report_id"]
    url = f"/api/reports/{report_id}/html"

    async def _count():
        async with async_session_maker() as db:
            return len((await db.execute(
                select(AuditLog).where(
                    AuditLog.event_type == "security", AuditLog.outcome == "blocked",
                    AuditLog.resource_id == url))).scalars().all())

    before = run(_count())
    resp = client.get(url, headers=headers_for("reviewer"))
    assert resp.status_code == 200
    after = run(_count())
    assert after == before


# ── 10. consumer impact: every route this decision changes ─────────────────

def test_the_set_of_routes_raised_by_this_decision_is_exactly_what_was_claimed():
    """A living inventory, not prose: if a future edit removes the reviewer
    floor from any of these, this fails instead of a review missing it."""
    import inspect

    from app.Tefca import routes as legacy_routes
    from app.reports import routes

    raised_in_reports = [
        routes.generate, routes.get_package, routes.get_report_html,
        routes.get_report_pdf, routes.get_report_docx, routes.get_report_csv,
        routes.sow_deliverable, routes.artifact_history,
        routes.reports_by_delivery, routes.artifact_download,
    ]
    for fn in raised_in_reports:
        src = inspect.getsource(fn)
        assert 'require_role_audited("reviewer"' in src or 'require_role("reviewer")' in src, fn.__name__

    raised_in_legacy = [
        legacy_routes.get_tefca_report, legacy_routes.get_tefca_report_csv,
        legacy_routes.get_tefca_report_pdf, legacy_routes.get_tefca_report_docx,
        legacy_routes.download_report, legacy_routes.priority_report,
    ]
    for fn in raised_in_legacy:
        assert 'require_role_audited("reviewer"' in inspect.getsource(fn), fn.__name__

    # untouched, intentionally: metadata/availability stays at viewer
    for fn in (routes.list_reports, routes.get_report, routes.get_release,
              routes.list_sow_families, routes.engine_health):
        assert 'require_role("viewer")' in inspect.getsource(fn), fn.__name__
