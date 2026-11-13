"""Three API defects found 2026-11-13 by probing every mounted GET route in-process
(2,222 requests; 45 of them answered 500 from application code).

  D1  A path id that is not a UUID reached the database driver and came back as
      500 INTERNAL_ERROR on eleven read routes. It is now the 404 those routes
      already use for "no such thing".
  D2  GET /api/tefca/rce/deliveries/{id}/reconciliation for a delivery that does
      not exist raised ValueError("No intake ...") -> 500. Now 404.
  D3  Three numeric query parameters had no bounds: margin=0 (ZeroDivisionError),
      sample_size=-1 (ValueError in random.sample), limit=-1 (PostgreSQL refuses a
      negative LIMIT). Each was a 500; each is now 422.

WHAT IS REAL
    The real routers, their own role gates (real JWTs for users seeded in the
    transaction - no auth override) and the platform's exception handlers, on a
    disposable database inside a rolled-back transaction. Only `get_db` is
    overridden. ONE seam is stubbed, and only in the D3 tests that check a VALID
    value still reaches the engine: `qa_engine.validate_sampling` /
    `internal_consistency_check` are replaced by a recorder, because the real
    functions write to `tefca_qa_audit`, a table the application creates at
    startup rather than by migration (it is absent from a migrated test
    database). No external service is involved in any of these routes.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI

from rce_traceability_support import make_rows, rolled_back_db, seed_intake  # noqa: F401

NOT_UUIDS = ["not-a-uuid", "0", "99999999999999999999", "x" * 200, "80f31aab-3d46-4645-b76a"]
UNKNOWN = "80f31aab-3d46-4645-b76a-c9028c4cf8a3"          # well-formed, matches nothing

#: (url template, what the 404 names, role that may call it, what a well-formed
#: unknown id answers - unchanged by this change, except /reconciliation, which
#: is D2: it was a 500 and is now the 404 of GET /deliveries/{id})
ID_ROUTES = [
    ("/api/tefca/rce/deliveries/{id}", "delivery", "viewer", 404),
    ("/api/tefca/rce/deliveries/{id}/records", "delivery", "reviewer", 200),
    ("/api/tefca/rce/deliveries/{id}/integrity", "delivery", "viewer", 200),
    ("/api/tefca/rce/deliveries/{id}/runs", "delivery", "viewer", 200),
    ("/api/tefca/rce/deliveries/{id}/issues", "delivery", "reviewer", 200),
    ("/api/tefca/rce/deliveries/{id}/curated", "delivery", "reviewer", 200),
    ("/api/tefca/rce/curated/{id}/lineage", "curated record", "reviewer", 404),
    ("/api/tefca/rce/deliveries/{id}/reconciliation", "delivery", "viewer", 404),
    ("/api/tefca/workflow/deliveries/{id}/qhins", "delivery", "viewer", None),
    ("/api/tefca/workflow/deliveries/{id}/qhins/" + UNKNOWN, "delivery", "viewer", None),
    ("/api/admin/users/{id}/activity", "user", "admin", 200),
]


def _app(db) -> FastAPI:
    from app.api.admin_users import router as admin_router
    from app.core.database import get_db
    from app.core.error_handler import register_exception_handlers
    from app.Tefca.routes import tefca_dashboard_router, tefca_router
    from app.tefca_registry.rce.routes import router as rce_router
    from app.tefca_registry.workflow_routes import router as workflow_router

    app = FastAPI()
    register_exception_handlers(app)
    for router in (rce_router, workflow_router, admin_router, tefca_router, tefca_dashboard_router):
        app.include_router(router)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db           # the ONLY override
    return app


def _client(db):
    # raise_app_exceptions=False: an unhandled error is reported as the 500 the
    # caller would see, instead of being re-raised into the test.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(db), raise_app_exceptions=False),
                             base_url="http://t")


async def _headers(db, role: str):
    from app.core.security import create_access_token, hash_password
    from app.models.database import User

    user = User(id=uuid.uuid4(), tenant_id="synthetic-api-contract",
                email=f"{role}-{uuid.uuid4().hex[:10]}@synthetic-test.docuaction.invalid",
                password_hash=hash_password(uuid.uuid4().hex), full_name=f"SYNTHETIC {role}",
                role=role, is_active=True, is_verified=True, status="active", allowed_modules=[])
    db.add(user)
    await db.flush()
    token = create_access_token({"sub": str(user.id), "role": role}, is_admin=(role == "admin"))
    return {"Authorization": f"Bearer {token}"}


# ── D1: an id that cannot exist is "not found", not an internal error ─────────

@pytest.mark.parametrize("template,what,role,_unknown", ID_ROUTES)
async def test_a_path_id_that_is_not_a_uuid_is_404_not_500(rolled_back_db, template, what, role, _unknown):
    db = rolled_back_db
    headers = await _headers(db, role)
    async with _client(db) as client:
        for bad in NOT_UUIDS:
            r = await client.get(template.format(id=bad), headers=headers)
            assert r.status_code == 404, (template, bad[:30], r.status_code, r.text[:200])
            body = r.json()
            assert body["code"] == "NOT_FOUND" and body["error"].startswith(f"No {what} ")
            assert len(body["error"]) < 120                  # a very long value is not echoed whole


@pytest.mark.parametrize("template,what,role,unknown_status", [r for r in ID_ROUTES if r[3] is not None])
async def test_a_well_formed_unknown_id_answers_as_it_did_before(rolled_back_db, template, what, role,
                                                                unknown_status):
    # (the two QHIN routes are left out: what they answer for an unknown delivery is not pinned here)
    db = rolled_back_db
    headers = await _headers(db, role)
    async with _client(db) as client:
        r = await client.get(template.format(id=UNKNOWN), headers=headers)
    assert r.status_code == unknown_status, (template, r.status_code, r.text[:200])
    if unknown_status == 200 and "/admin/" not in template and "/integrity" not in template:
        assert r.json().get("items") == []


async def test_an_existing_delivery_is_served_unchanged_in_any_spelling_of_its_id(rolled_back_db):
    db = rolled_back_db
    headers = await _headers(db, "reviewer")
    intake_id, _job = await seed_intake(db, make_rows(3, arc="9.99.782.1"), with_job=False)
    async with _client(db) as client:
        plain = await client.get(f"/api/tefca/rce/deliveries/{intake_id}/records", headers=headers)
        upper = await client.get(f"/api/tefca/rce/deliveries/{str(intake_id).upper()}/records", headers=headers)
        detail = await client.get(f"/api/tefca/rce/deliveries/{intake_id}", headers=headers)
    assert plain.status_code == upper.status_code == detail.status_code == 200
    assert plain.json()["count"] == 3 and plain.json() == upper.json()
    assert [i["line_number"] for i in plain.json()["items"]] == [2, 3, 4]
    assert detail.json()["id"] == str(intake_id) and detail.json()["record_count"] == 3


async def test_the_id_check_does_not_come_before_authorization(rolled_back_db):
    """A caller who may not use a route learns nothing from the id check: the
    role gate answers first, exactly as before."""
    db = rolled_back_db
    contributor = await _headers(db, "contributor")
    viewer = await _headers(db, "viewer")
    async with _client(db) as client:
        assert (await client.get("/api/tefca/rce/deliveries/not-a-uuid/records")).status_code in (401, 403)
        assert (await client.get("/api/tefca/rce/deliveries/not-a-uuid/records",
                                 headers=viewer)).status_code == 403          # reviewer floor
        assert (await client.get("/api/admin/users/not-a-uuid/activity",
                                 headers=contributor)).status_code == 403     # admin only
        assert (await client.get(f"/api/tefca/rce/deliveries/{UNKNOWN}/reconciliation?recompute=true",
                                 headers=viewer)).status_code == 403          # qalead gate first (see below)


# ── D2: reconciliation of a delivery that does not exist ──────────────────────

async def test_reconciliation_of_an_unknown_delivery_is_404_and_a_real_one_still_answers(rolled_back_db):
    db = rolled_back_db
    headers = await _headers(db, "viewer")
    intake_id, _job = await seed_intake(db, make_rows(2, arc="9.99.782.2"), with_job=False)
    async with _client(db) as client:
        missing = await client.get(f"/api/tefca/rce/deliveries/{UNKNOWN}/reconciliation", headers=headers)
        real = await client.get(f"/api/tefca/rce/deliveries/{intake_id}/reconciliation", headers=headers)
    assert missing.status_code == 404
    assert missing.json() == {"error": f"No delivery {UNKNOWN}", "code": "NOT_FOUND",
                              "request_id": missing.json()["request_id"]}
    assert real.status_code == 200, real.text[:300]
    assert real.json()["intake_id"] == str(intake_id) and "passed" in real.json()


# ── recompute=true: the qalead requirement answers BEFORE anything about the id ──

RECOMPUTE_403 = ("Live reconciliation recomputation requires the qalead role; "
                 "the persisted snapshot is served without this flag.")


@pytest.mark.parametrize("role", ["viewer", "reviewer"])
async def test_recompute_below_qalead_is_403_whatever_the_id_is(rolled_back_db, role):
    """A caller who may not recompute gets the same 403 for an existing, an unknown
    and a malformed id. Answering 404 first would tell them which deliveries exist
    (and, before this change, was how the id check slipped in front of the gate)."""
    db = rolled_back_db
    headers = await _headers(db, role)
    intake_id, _job = await seed_intake(db, make_rows(2, arc="9.99.782.3"), with_job=False)
    async with _client(db) as client:
        for label, value in (("existing", str(intake_id)), ("unknown", UNKNOWN), ("malformed", "not-a-uuid")):
            r = await client.get(f"/api/tefca/rce/deliveries/{value}/reconciliation?recompute=true", headers=headers)
            assert r.status_code == 403, (role, label, r.status_code, r.text[:200])
            assert r.json()["error"] == RECOMPUTE_403, (role, label, r.json())
        # the ordinary read is unchanged for the same caller
        assert (await client.get(f"/api/tefca/rce/deliveries/{intake_id}/reconciliation",
                                 headers=headers)).status_code == 200
        assert (await client.get(f"/api/tefca/rce/deliveries/{UNKNOWN}/reconciliation",
                                 headers=headers)).status_code == 404
        assert (await client.get("/api/tefca/rce/deliveries/not-a-uuid/reconciliation",
                                 headers=headers)).status_code == 404


@pytest.mark.parametrize("role", ["qalead", "program_manager"])
async def test_recompute_at_or_above_qalead_recomputes_or_says_not_found(rolled_back_db, role):
    db = rolled_back_db
    headers = await _headers(db, role)
    intake_id, _job = await seed_intake(db, make_rows(2, arc="9.99.782.4"), with_job=False)
    async with _client(db) as client:
        live = await client.get(f"/api/tefca/rce/deliveries/{intake_id}/reconciliation?recompute=true", headers=headers)
        unknown = await client.get(f"/api/tefca/rce/deliveries/{UNKNOWN}/reconciliation?recompute=true", headers=headers)
        malformed = await client.get("/api/tefca/rce/deliveries/not-a-uuid/reconciliation?recompute=true", headers=headers)
    assert live.status_code == 200, live.text[:300]
    assert live.json()["source"] == "live_recompute" and live.json()["intake_id"] == str(intake_id)
    assert unknown.status_code == 404 and unknown.json()["error"] == f"No delivery {UNKNOWN}"
    assert malformed.status_code == 404 and malformed.json()["error"] == "No delivery not-a-uuid"


async def test_recompute_without_a_session_is_refused_before_anything_else(rolled_back_db):
    db = rolled_back_db
    async with _client(db) as client:
        for value in (UNKNOWN, "not-a-uuid"):
            r = await client.get(f"/api/tefca/rce/deliveries/{value}/reconciliation?recompute=true")
            assert r.status_code in (401, 403) and r.json().get("error") != RECOMPUTE_403


# ── D3: numeric query parameters ──────────────────────────────────────────────

class _Recorder:
    """Stands in for a qa_engine function (see the module docstring)."""

    def __init__(self):
        self.calls = []

    async def __call__(self, db, *args, **kwargs):
        self.calls.append(args)
        return {"stub": True}


async def test_sampling_validation_refuses_a_zero_or_negative_margin(rolled_back_db, monkeypatch):
    from app.Tefca import qa_engine

    recorder = _Recorder()
    monkeypatch.setattr(qa_engine, "validate_sampling", recorder)
    db = rolled_back_db
    headers = await _headers(db, "viewer")
    async with _client(db) as client:
        for bad in ("0", "-0.05", "0.0"):
            r = await client.get(f"/api/tefca/qa/sampling-validation?margin={bad}", headers=headers)
            assert r.status_code == 422, (bad, r.status_code, r.text[:200])
            assert r.json()["code"] == "VALIDATION_ERROR" and "margin" in r.json()["error"]
        assert recorder.calls == []
        assert (await client.get("/api/tefca/qa/sampling-validation", headers=headers)).status_code == 200
        assert (await client.get("/api/tefca/qa/sampling-validation?population=500&confidence=0.99&margin=0.1",
                                 headers=headers)).status_code == 200
    assert recorder.calls == [(94231, 0.95, 0.05), (500, 0.99, 0.1)]      # valid values are untouched


@pytest.mark.parametrize("path", ["/api/tefca/qa/internal-consistency", "/api/tefca/qa/inter-rater"])
async def test_consistency_checks_refuse_a_negative_sample_size(rolled_back_db, monkeypatch, path):
    from app.Tefca import qa_engine

    recorder = _Recorder()
    monkeypatch.setattr(qa_engine, "internal_consistency_check", recorder)
    db = rolled_back_db
    headers = await _headers(db, "viewer")
    async with _client(db) as client:
        r = await client.get(f"{path}?sample_size=-1", headers=headers)
        assert r.status_code == 422 and "sample_size" in r.json()["error"], r.text[:200]
        assert recorder.calls == []
        for query in ("", "?sample_size=0", "?sample_size=500&seed=7"):
            assert (await client.get(path + query, headers=headers)).status_code == 200
    assert recorder.calls == [(20, 42), (0, 42), (500, 7)]


@pytest.mark.parametrize("path,role", [("/api/v1/tefca/queue/tier2", "viewer"),
                                       ("/api/v1/tefca/queue/tier3", "senior_analyst")])
async def test_queues_refuse_a_negative_limit_and_serve_the_rest(rolled_back_db, path, role):
    db = rolled_back_db
    headers = await _headers(db, role)
    async with _client(db) as client:
        r = await client.get(f"{path}?limit=-1", headers=headers)
        assert r.status_code == 422 and "limit" in r.json()["error"], r.text[:200]
        for query in ("", "?limit=0", "?limit=5000"):                     # no new upper bound
            ok = await client.get(path + query, headers=headers)
            assert ok.status_code == 200, (query, ok.status_code, ok.text[:200])
            assert set(ok.json()) == {"total_pending", "queue"}
        if role == "senior_analyst":                                      # the tier-3 floor still holds
            viewer = await _headers(db, "viewer")
            assert (await client.get(path, headers=viewer)).status_code == 403


def test_known_uuid_accepts_every_spelling_of_a_uuid_and_nothing_else():
    from fastapi import HTTPException

    from app.core.path_ids import known_uuid

    canonical = "80f31aab-3d46-4645-b76a-c9028c4cf8a3"
    for spelling in (canonical, canonical.upper(), canonical.replace("-", ""), uuid.UUID(canonical)):
        assert known_uuid(spelling, "delivery") == canonical
    for bad in ("", "not-a-uuid", None, 0, canonical[:-1], canonical + "0", "DA-ARC-2026-001"):
        with pytest.raises(HTTPException) as exc:
            known_uuid(bad, "delivery")
        assert exc.value.status_code == 404 and exc.value.detail.startswith("No delivery ")
