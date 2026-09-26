"""GET /api/tefca/admin/pecos-retry-plan — the read-only dry-run endpoint.

Two layers, matching how the endpoint is built:

* Endpoint-contract tests (no database): authz, limit rejection, 404 shape,
  response sanitization, no-store caching — the planner is faked so these run
  everywhere, every time.
* Planner-integration tests (real PostgreSQL, run by the isolation-postgres CI
  job): seeded delivery + evidence rows prove bounded selection, invalid-NPI
  and existing-evidence exclusion, constant query count, zero writes,
  idempotency and cross-delivery isolation against the real schema.
"""
from __future__ import annotations

import uuid

import pytest

from support_delivery_api import (  # noqa: E402 — module handles its own DB skip
    _database_available,
    run,
    seed_delivery,
)

VALID_NPI = "1234567893"  # canonical CMS worked example, Luhn-valid


# ── endpoint contract (no database) ──────────────────────────────────────────

class _User:
    def __init__(self, role):
        self.id = str(uuid.uuid4()); self.email = f"{role}@test.local"; self.role = role
        self.is_active = True; self.status = "active"; self.tokens_revoked_at = None


class _Result:
    def __init__(self, user): self._user = user
    def scalar_one_or_none(self): return self._user
    def scalar(self): return None


class _Session:
    def __init__(self, user): self._user = user
    async def execute(self, *a, **k): return _Result(self._user)
    async def commit(self): return None
    async def rollback(self): return None
    async def close(self): return None
    def add(self, *a, **k): raise AssertionError("endpoint must never write")


@pytest.fixture
def as_role(client):
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app

    def make(role):
        user = _User(role)

        async def _override():
            yield _Session(user)

        app.dependency_overrides[get_db] = _override
        token = create_access_token({"sub": user.id, "role": role}, is_admin=(role == "admin"))
        return lambda path: client.get(path, headers={"Authorization": f"Bearer {token}"})

    yield make
    from app.main import app as _app
    from app.core.database import get_db as _gd
    _app.dependency_overrides.pop(_gd, None)


FAKE_PLAN = {
    "dry_run": True, "planner_version": "1.1.0", "intake_scope": "fake-intake",
    "executed_retry": False, "upstream_calls_made": 0, "database_writes_made": 0,
    "database_queries_made": 2,
    "target_sources": ["CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"],
    "excludes_legacy_pecos_proxy": True,
    "population_scope": "test", "identifier_note": "candidate_ref is pseudonymous; no name/address/full NPI/raw id",
    "candidates": [{"candidate_ref": "cand-abcdef123456", "npi_masked": "...7893",
                     "missing_sources": ["CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"],
                     "reason": "no current-generation evidence on file"}],
    "candidate_count": 1, "max_candidates": 25, "entities_scanned": 3,
    "excluded_invalid_npi": 1, "excluded_already_has_evidence": 1,
}


class _FakeJob:
    source_intake_id = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def fake_planner(monkeypatch):
    from app.Tefca import pecos_retry_planner as planner
    from app.tefca_registry.rce import delivery_jobs

    async def fake_plan(db, limit, *, intake_id=None):
        return dict(FAKE_PLAN, intake_scope=intake_id)

    async def fake_get_job(db, job_id):
        return _FakeJob() if str(job_id) == "known-job" else None

    monkeypatch.setattr(planner, "plan_pecos_retry", fake_plan)
    monkeypatch.setattr(delivery_jobs, "get_job", fake_get_job)


URL = "/api/tefca/admin/pecos-retry-plan"


def test_unauthenticated_is_rejected(client):
    r = client.get(f"{URL}?delivery_job_id=known-job")
    assert r.status_code in (401, 403)
    body = r.text
    assert "Traceback" not in body and "sqlalchemy" not in body


def test_viewer_is_rejected_with_sanitized_403(as_role, fake_planner):
    r = as_role("viewer")(f"{URL}?delivery_job_id=known-job")
    assert r.status_code == 403
    assert "Traceback" not in r.text and "SELECT" not in r.text


def test_admin_receives_a_bounded_sanitized_plan(as_role, fake_planner):
    r = as_role("admin")(f"{URL}?delivery_job_id=known-job&limit=25")
    assert r.status_code == 200, r.text
    body = r.json()
    # Required response fields (task item 15).
    for key in ("correlation_id", "delivery_job_id", "requested_limit", "scanned_count",
                "eligible_count", "selected_count", "excluded_invalid_npi_count",
                "excluded_existing_evidence_count", "target_connectors", "candidates",
                "planner_version", "build_sha"):
        assert key in body, key
    assert body["selected_count"] <= 25
    assert body["target_connectors"] == ["CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"]
    assert "pecos" not in [s.lower() for s in body["target_connectors"]]
    # Non-cacheable + correlation id surfaced as a header too.
    assert r.headers.get("Cache-Control") == "no-store"
    assert r.headers.get("X-Correlation-Id") == body["correlation_id"]


def test_candidates_carry_no_raw_identifier_or_npi(as_role, fake_planner):
    r = as_role("admin")(f"{URL}?delivery_job_id=known-job")
    cand = r.json()["candidates"][0]
    assert set(cand) == {"candidate_ref", "npi_masked", "missing_sources", "reason"}
    assert cand["candidate_ref"].startswith("cand-")
    assert VALID_NPI not in r.text  # full NPI never present anywhere in the response


def test_limit_above_25_is_rejected_not_clamped(as_role, fake_planner):
    r = as_role("admin")(f"{URL}?delivery_job_id=known-job&limit=26")
    assert r.status_code == 422
    assert "25" in r.text
    r = as_role("admin")(f"{URL}?delivery_job_id=known-job&limit=200")
    assert r.status_code == 422


def test_unknown_job_is_a_sanitized_404(as_role, fake_planner):
    r = as_role("admin")(f"{URL}?delivery_job_id=does-not-exist")
    assert r.status_code == 404
    assert "does-not-exist" in r.text            # only the caller's own input echoed
    assert "Traceback" not in r.text and "SELECT" not in r.text and "postgres" not in r.text


def test_missing_job_parameter_is_a_validation_error(as_role, fake_planner):
    r = as_role("admin")(URL)
    assert r.status_code == 422


# ── planner integration (real PostgreSQL — isolation-postgres CI job) ────────

pytestmark_db = pytest.mark.skipif(not _database_available(),
                                   reason="No database reachable at DATABASE_URL. This test "
                                          "exercises a database-backed path; skipping rather "
                                          "than reporting a false failure.")


@pytestmark_db
def test_seeded_delivery_plan_is_bounded_isolated_and_write_free():
    """One seeded delivery with three entities: valid-NPI-no-evidence (selected),
    valid-NPI-with-evidence (excluded), invalid-NPI (excluded). A second seeded
    delivery proves cross-delivery isolation. Query count and write-freedom are
    asserted on a counting session wrapper around the real one."""
    from sqlalchemy import text as sql
    from app.core.database import async_session_maker
    from app.Tefca.pecos_retry_planner import plan_pecos_retry

    a = seed_delivery()
    b = seed_delivery()
    intake_a, intake_b = a["intake_id"], b["intake_id"]

    e_sel, e_evd, e_bad = (str(uuid.uuid4()) for _ in range(3))
    e_other = str(uuid.uuid4())

    async def _seed_and_plan():
        async with async_session_maker() as s:
            # Link three curated rows of delivery A (and one of B) to entities.
            for intake, ent in ((intake_a, e_sel), (intake_a, e_evd), (intake_a, e_bad),
                                (intake_b, e_other)):
                await s.execute(sql(
                    "UPDATE rce_curated_records SET canonical_entity_id = :ent "
                    "WHERE id = (SELECT id FROM rce_curated_records "
                    "            WHERE source_intake_id = :intake AND canonical_entity_id IS NULL "
                    "            LIMIT 1)"), {"ent": ent, "intake": str(intake)})
            # NPPES identity evidence for each entity.
            for ent, npi in ((e_sel, VALID_NPI), (e_evd, "1003879883"),
                             (e_bad, "1234567890"), (e_other, VALID_NPI)):
                await s.execute(sql(
                    "INSERT INTO tefca_dimension_evidence "
                    "(id, entity_id, evidence_dimension, source, disposition, original_values, generation_timestamp) "
                    "VALUES (gen_random_uuid(), :ent, 'IDENTITY', 'NPPES', 'PASS', "
                    "        jsonb_build_object('npi', :npi), '2026-09-26T00:00:00')"),
                    {"ent": ent, "npi": npi})
            # Existing target evidence for e_evd only.
            for src in ("CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"):
                await s.execute(sql(
                    "INSERT INTO tefca_dimension_evidence "
                    "(id, entity_id, evidence_dimension, source, disposition, original_values, generation_timestamp) "
                    "VALUES (gen_random_uuid(), :ent, 'MEDICARE_ENROLLMENT', :src, 'PASS', '{}'::jsonb, "
                    "        '2026-09-26T00:00:00')"), {"ent": e_evd, "src": src})
            await s.commit()

        class CountingSession:
            def __init__(self, inner): self._inner = inner; self.selects = 0
            async def execute(self, stmt, *a, **k):
                self.selects += 1
                return await self._inner.execute(stmt, *a, **k)
            def add(self, *a, **k): raise AssertionError("planner must never write")
            async def commit(self): raise AssertionError("planner must never commit")
            async def flush(self): raise AssertionError("planner must never flush")

        async with async_session_maker() as s:
            cs = CountingSession(s)
            plan1 = await plan_pecos_retry(cs, 25, intake_id=str(intake_a))
            q1 = cs.selects
            cs2 = CountingSession(s)
            plan2 = await plan_pecos_retry(cs2, 25, intake_id=str(intake_a))
        return plan1, q1, plan2

    plan, queries, plan_again = run(_seed_and_plan())

    refs = [c["candidate_ref"] for c in plan["candidates"]]
    from app.Tefca.pecos_retry_planner import _opaque_ref
    assert _opaque_ref(e_sel) in refs                     # valid NPI, no evidence → selected
    assert _opaque_ref(e_evd) not in refs                 # existing evidence → excluded
    assert _opaque_ref(e_bad) not in refs                 # invalid NPI → excluded
    assert _opaque_ref(e_other) not in refs               # OTHER DELIVERY → never enters the plan
    assert plan["excluded_invalid_npi"] >= 1
    assert plan["excluded_already_has_evidence"] >= 1
    assert plan["candidate_count"] <= 25
    assert queries == 2                                   # constant, not N+1, with scope applied
    assert plan["candidates"] == plan_again["candidates"]  # idempotent on unchanged data
    # Sanitization against real data.
    blob = str(plan["candidates"])
    for forbidden in (VALID_NPI, e_sel, e_evd, e_bad):
        assert forbidden not in blob
