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


KNOWN_JOB = "22222222-2222-2222-2222-222222222222"
UNKNOWN_JOB = "33333333-3333-3333-3333-333333333333"


class _Session:
    """Auth lookups succeed; `get` serves the delivery-job lookup the endpoint
    now performs directly (db.get), or raises to simulate an infrastructure
    failure — which must surface as a sanitized 500, never a false 404."""
    def __init__(self, user, get_error=None):
        self._user = user; self._get_error = get_error
    async def execute(self, *a, **k): return _Result(self._user)
    async def get(self, model, pk):
        if self._get_error is not None:
            raise self._get_error
        return _FakeJob() if str(pk) == KNOWN_JOB else None
    async def commit(self): return None
    async def rollback(self): return None
    async def close(self): return None
    def add(self, *a, **k): raise AssertionError("endpoint must never write")


@pytest.fixture
def as_role(client):
    from app.core.database import get_db
    from app.core.security import create_access_token
    from app.main import app

    def make(role, get_error=None):
        user = _User(role)

        async def _override():
            yield _Session(user, get_error=get_error)

        app.dependency_overrides[get_db] = _override
        token = create_access_token({"sub": user.id, "role": role}, is_admin=(role == "admin"))
        return lambda path, extra=None: client.get(
            path, headers={"Authorization": f"Bearer {token}", **(extra or {})})

    yield make
    from app.main import app as _app
    from app.core.database import get_db as _gd
    _app.dependency_overrides.pop(_gd, None)


FAKE_PLAN = {
    "dry_run": True, "planner_version": "1.2.0", "intake_scope": "fake-intake",
    "executed_retry": False, "upstream_calls_made": 0, "database_writes_made": 0,
    "database_queries_made": 2,
    "target_sources": ["CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"],
    "excludes_legacy_pecos_proxy": True,
    # 1.2.0 diagnostics — the closed funnel: 3 scanned = 0 missing + 1 invalid
    # + 1 already-evidenced + 1 eligible.
    "scan_window": 500, "scan_rows_examined": 3, "scan_truncated": False,
    "eligible_count": 1, "returned_count": 1, "truncated_by_limit": False,
    "exclusion_counts": {"missing_npi": 0, "invalid_npi": 1,
                          "already_has_both_target_sources": 1},
    "missing_source_counts": {"CMS_PPEF_ENROLLMENT_only": 0,
                               "CMS_REVOCATION_only": 0, "both": 1},
    "sources_considered": ["NPPES", "CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"],
    "would_call_upstream": False, "would_write": False,
    "population_scope": "test", "identifier_note": "candidate_ref is pseudonymous; no name/address/full NPI/raw id",
    # The fake DELIBERATELY carries npi_masked, exactly as the real planner
    # does internally: the tests below prove the ENDPOINT strips it, so no
    # full or partial NPI digits ever reach the response.
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

    async def fake_plan(db, limit, *, intake_id=None):
        return dict(FAKE_PLAN, intake_scope=intake_id)

    monkeypatch.setattr(planner, "plan_pecos_retry", fake_plan)


URL = "/api/tefca/admin/pecos-retry-plan"


def test_unauthenticated_is_rejected(client):
    r = client.get(f"{URL}?delivery_job_id={KNOWN_JOB}")
    assert r.status_code in (401, 403)
    body = r.text
    assert "Traceback" not in body and "sqlalchemy" not in body


def test_viewer_is_rejected_with_sanitized_403(as_role, fake_planner):
    r = as_role("viewer")(f"{URL}?delivery_job_id={KNOWN_JOB}")
    assert r.status_code == 403
    assert "Traceback" not in r.text and "SELECT" not in r.text


def test_admin_receives_a_bounded_sanitized_plan(as_role, fake_planner):
    r = as_role("admin")(f"{URL}?delivery_job_id={KNOWN_JOB}&limit=25")
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


def test_candidates_carry_no_npi_masked_or_identifier(as_role, fake_planner):
    """The planner's npi_masked field must be STRIPPED by the endpoint: no full
    or partial NPI digits may appear anywhere in the response."""
    r = as_role("admin")(f"{URL}?delivery_job_id={KNOWN_JOB}")
    cand = r.json()["candidates"][0]
    assert set(cand) == {"candidate_ref", "missing_sources", "reason"}
    assert cand["candidate_ref"].startswith("cand-")
    assert "npi_masked" not in r.text            # the key itself never appears
    assert VALID_NPI not in r.text               # full NPI never present anywhere
    blob = str(r.json()["candidates"])
    assert "7893" not in blob                    # the masked fragment's digits neither


def test_eligible_count_subtracts_every_exclusion(as_role, fake_planner):
    """eligible = scanned − invalid-NPI − already-evidenced: 3 − 1 − 1 = 1."""
    body = as_role("admin")(f"{URL}?delivery_job_id={KNOWN_JOB}").json()
    assert body["scanned_count"] == 3
    assert body["excluded_invalid_npi_count"] == 1
    assert body["excluded_existing_evidence_count"] == 1
    assert body["eligible_count"] == 1
    assert body["eligible_count"] == (body["scanned_count"]
                                      - body["excluded_invalid_npi_count"]
                                      - body["excluded_existing_evidence_count"])


def test_limit_above_25_is_rejected_not_clamped(as_role, fake_planner):
    r = as_role("admin")(f"{URL}?delivery_job_id={KNOWN_JOB}&limit=26")
    assert r.status_code == 422
    assert "25" in r.text
    r = as_role("admin")(f"{URL}?delivery_job_id={KNOWN_JOB}&limit=200")
    assert r.status_code == 422


def test_unknown_valid_job_id_is_a_sanitized_404(as_role, fake_planner):
    r = as_role("admin")(f"{URL}?delivery_job_id={UNKNOWN_JOB}")
    assert r.status_code == 404
    assert UNKNOWN_JOB in r.text                 # only the caller's own input echoed
    assert "Traceback" not in r.text and "SELECT" not in r.text and "postgres" not in r.text


def test_malformed_job_id_is_a_sanitized_404(as_role, fake_planner):
    """Repo convention (delivery_jobs.get_job): a malformed identifier is a
    404-shaped fact, not a validation or server error. Validated before any
    database call, so nothing below can misclassify it."""
    r = as_role("admin")(f"{URL}?delivery_job_id=does-not-exist")
    assert r.status_code == 404
    assert "does-not-exist" in r.text
    assert "Traceback" not in r.text and "SELECT" not in r.text and "postgres" not in r.text


def test_db_failure_is_a_sanitized_500_never_a_false_404(as_role, fake_planner):
    """A database/infrastructure failure during job resolution must reach the
    global sanitized handler — 500 with a request id — and must NEVER be
    swallowed into a false 'no such job' 404."""
    boom = RuntimeError(
        "connection refused postgresql://app:sekretpw@db-host:5432/docuaction "
        "while running SELECT * FROM rce_delivery_jobs")
    r = as_role("admin", get_error=boom)(f"{URL}?delivery_job_id={KNOWN_JOB}")
    assert r.status_code in (500, 503)
    assert r.status_code != 404
    body = r.text
    # No raw SQL, stack trace, connection string, credential or driver detail.
    for forbidden in ("SELECT", "postgresql://", "sekretpw", "db-host",
                      "Traceback", "RuntimeError", "rce_delivery_jobs"):
        assert forbidden not in body, forbidden
    assert "request_id" in body                  # correlation is preserved


def test_missing_job_parameter_is_a_validation_error(as_role, fake_planner):
    r = as_role("admin")(URL)
    assert r.status_code == 422


def test_funnel_fields_pass_through_and_reconcile(as_role, fake_planner):
    """The 1.2.0 diagnostic funnel is closed: scanned = every exclusion +
    eligible, and the endpoint surfaces it without inventing numbers."""
    body = as_role("admin")(f"{URL}?delivery_job_id={KNOWN_JOB}").json()
    ec = body["exclusion_counts"]
    assert set(ec) == {"missing_npi", "invalid_npi", "already_has_both_target_sources"}
    assert body["scanned_count"] == (ec["missing_npi"] + ec["invalid_npi"]
                                     + ec["already_has_both_target_sources"]
                                     + body["eligible_count"])
    assert body["excluded_invalid_npi_count"] == ec["missing_npi"] + ec["invalid_npi"]
    assert body["excluded_existing_evidence_count"] == ec["already_has_both_target_sources"]
    assert set(body["missing_source_counts"]) == {"CMS_PPEF_ENROLLMENT_only",
                                                   "CMS_REVOCATION_only", "both"}
    assert body["truncated"] is False and body["scan_window"] == 500
    assert body["returned_count"] == body["selected_count"]
    assert body["would_call_upstream"] is False and body["would_write"] is False
    assert body["sources_considered"] == ["NPPES", "CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"]


def test_correlation_header_is_cors_exposed_and_matches_body(as_role, fake_planner):
    """A cross-origin browser could not read X-Correlation-Id (only CORS-
    safelisted headers like Cache-Control are visible without
    Access-Control-Expose-Headers). The header must be exposed BY NAME and
    equal the body's correlation_id — origins/methods/credentials unchanged."""
    r = as_role("admin")(f"{URL}?delivery_job_id={KNOWN_JOB}",
                         extra={"Origin": "http://localhost:3000"})
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
    expose = r.headers.get("access-control-expose-headers", "")
    assert "x-correlation-id" in expose.lower(), expose
    assert r.headers["X-Correlation-Id"] == r.json()["correlation_id"]


# ── planner unit tests (pure-Python fake session — run everywhere) ───────────

class _PlanRows:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _PlanDB:
    """Feeds the planner's two SELECTs from canned batches; any write is a
    failure by construction."""
    def __init__(self, batches): self._batches = list(batches)
    async def execute(self, stmt, *a, **k): return _PlanRows(self._batches.pop(0))
    def add(self, *a, **k): raise AssertionError("planner must never write")
    async def commit(self): raise AssertionError("planner must never commit")
    async def flush(self): raise AssertionError("planner must never flush")


async def test_scan_truncation_is_reported_and_limit_is_only_a_cap():
    """SCAN_WINDOW bounds ROWS; on a population larger than the window the
    plan must say truncated=True (the observed DEV case: 246 entities scanned
    of a 24,502-entity delivery). `limit` caps the RETURNED list only — it
    never shrinks the scan or the funnel counts."""
    from app.Tefca.pecos_retry_planner import SCAN_WINDOW, plan_pecos_retry

    rows = [(f"entity-{i:05d}", {"npi": VALID_NPI}) for i in range(SCAN_WINDOW + 1)]
    plan = await plan_pecos_retry(_PlanDB([rows, []]), 25)

    assert plan["scan_truncated"] is True
    assert plan["scan_rows_examined"] == SCAN_WINDOW
    assert plan["entities_scanned"] == SCAN_WINDOW          # the 501st row is NOT processed
    assert plan["eligible_count"] == SCAN_WINDOW            # none have target evidence
    assert plan["returned_count"] == plan["candidate_count"] == 25
    assert plan["truncated_by_limit"] is True
    assert plan["max_candidates"] == 25
    # Funnel closure holds even when truncated.
    ec = plan["exclusion_counts"]
    assert plan["entities_scanned"] == (ec["missing_npi"] + ec["invalid_npi"]
                                        + ec["already_has_both_target_sources"]
                                        + plan["eligible_count"])
    assert plan["missing_source_counts"]["both"] == SCAN_WINDOW
    assert plan["would_call_upstream"] is False and plan["would_write"] is False


async def test_small_population_is_not_truncated_and_funnel_splits_npi_reasons():
    """Three entities: valid-no-evidence (eligible), missing NPI, invalid NPI.
    The funnel distinguishes 'missing' from 'invalid' while the legacy
    combined counter keeps its 1.1.0 meaning."""
    from app.Tefca.pecos_retry_planner import plan_pecos_retry

    rows = [("entity-a", {"npi": VALID_NPI}),
            ("entity-b", {}),                       # no NPI on the identity row
            ("entity-c", {"npi": "1234567890"})]    # Luhn-invalid
    plan = await plan_pecos_retry(_PlanDB([rows, []]), 25)

    assert plan["scan_truncated"] is False
    assert plan["entities_scanned"] == 3
    assert plan["exclusion_counts"] == {"missing_npi": 1, "invalid_npi": 1,
                                         "already_has_both_target_sources": 0}
    assert plan["excluded_invalid_npi"] == 2                # combined, unchanged meaning
    assert plan["eligible_count"] == plan["returned_count"] == 1
    assert plan["truncated_by_limit"] is False
    blob = str(plan["candidates"])
    assert VALID_NPI not in blob and "entity-a" not in blob  # still sanitized


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

    # issues=3: seed_delivery creates one curated row per issue (default 2),
    # and this test must link THREE entities inside delivery A.
    a = seed_delivery(issues=3)
    b = seed_delivery()
    intake_a, intake_b = a["intake_id"], b["intake_id"]

    e_sel, e_evd, e_bad = (str(uuid.uuid4()) for _ in range(3))
    e_other = str(uuid.uuid4())

    async def _seed_and_plan():
        async with async_session_maker() as s:
            # Link three curated rows of delivery A (and one of B) to entities.
            # Explicit casts on every parameter: asyncpg refuses to guess a
            # parameter's type in contexts like jsonb_build_object or a UUID
            # comparison (IndeterminateDatatypeError otherwise).
            for intake, ent in ((intake_a, e_sel), (intake_a, e_evd), (intake_a, e_bad),
                                (intake_b, e_other)):
                linked = await s.execute(sql(
                    "UPDATE rce_curated_records SET canonical_entity_id = CAST(:ent AS uuid) "
                    "WHERE id = (SELECT id FROM rce_curated_records "
                    "            WHERE source_intake_id = CAST(:intake AS uuid) "
                    "              AND canonical_entity_id IS NULL "
                    "            LIMIT 1)"), {"ent": ent, "intake": str(intake)})
                # Fail HERE if the seed ran out of curated rows, not three
                # asserts downstream with a misleading exclusion count.
                assert linked.rowcount == 1, f"no free curated row in intake {intake}"
            # NPPES identity evidence for each entity.
            for ent, npi in ((e_sel, VALID_NPI), (e_evd, "1003879883"),
                             (e_bad, "1234567890"), (e_other, VALID_NPI)):
                await s.execute(sql(
                    "INSERT INTO tefca_dimension_evidence "
                    "(id, entity_id, evidence_dimension, source, disposition, original_values, generation_timestamp) "
                    "VALUES (gen_random_uuid(), CAST(:ent AS varchar), 'IDENTITY', 'NPPES', 'PASS', "
                    "        jsonb_build_object('npi', CAST(:npi AS text)), '2026-09-26T00:00:00')"),
                    {"ent": ent, "npi": npi})
            # Existing target evidence for e_evd only.
            for src in ("CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"):
                await s.execute(sql(
                    "INSERT INTO tefca_dimension_evidence "
                    "(id, entity_id, evidence_dimension, source, disposition, original_values, generation_timestamp) "
                    "VALUES (gen_random_uuid(), CAST(:ent AS varchar), 'MEDICARE_ENROLLMENT', "
                    "        CAST(:src AS varchar), 'PASS', '{}'::jsonb, "
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
    # 1.2.0 funnel against real data: closed, and untruncated at this size.
    ec = plan["exclusion_counts"]
    assert plan["entities_scanned"] == (ec["missing_npi"] + ec["invalid_npi"]
                                        + ec["already_has_both_target_sources"]
                                        + plan["eligible_count"])
    assert plan["scan_truncated"] is False
    assert plan["eligible_count"] >= plan["returned_count"] == plan["candidate_count"]
    # Sanitization against real data.
    blob = str(plan["candidates"])
    for forbidden in (VALID_NPI, e_sel, e_evd, e_bad):
        assert forbidden not in blob


@pytestmark_db
def test_zero_eligible_delivery_reconciles_and_unavailable_counts_as_coverage():
    """The observed DEV outcome in miniature: every valid-NPI entity already
    carries rows for both target sources, so eligible_count is 0 and the
    funnel still closes exactly.

    Includes the CHARACTERIZATION of current policy: an entity whose only
    target-source rows are disposition=UNAVAILABLE (an outage record, rule
    CMS_OUTAGE_IS_NOT_A_VERIFICATION_FAILURE) is ALSO excluded as
    already-evidenced, because the planner's coverage check is row-presence,
    not disposition. Whether an outage row should count as coverage for RETRY
    planning is a recorded open policy question (DEV holds exactly one such
    entity in the September delivery) — this test pins today's behaviour so
    any future policy change is deliberate, visible, and reviewed."""
    from sqlalchemy import text as sql
    from app.core.database import async_session_maker
    from app.Tefca.pecos_retry_planner import plan_pecos_retry

    a = seed_delivery(issues=3)
    intake_a = a["intake_id"]
    e_pass, e_unav, e_pass2 = (str(uuid.uuid4()) for _ in range(3))

    async def _seed_and_plan():
        async with async_session_maker() as s:
            for ent in (e_pass, e_unav, e_pass2):
                linked = await s.execute(sql(
                    "UPDATE rce_curated_records SET canonical_entity_id = CAST(:ent AS uuid) "
                    "WHERE id = (SELECT id FROM rce_curated_records "
                    "            WHERE source_intake_id = CAST(:intake AS uuid) "
                    "              AND canonical_entity_id IS NULL "
                    "            LIMIT 1)"), {"ent": ent, "intake": str(intake_a)})
                assert linked.rowcount == 1, f"no free curated row in intake {intake_a}"
            for ent in (e_pass, e_unav, e_pass2):
                await s.execute(sql(
                    "INSERT INTO tefca_dimension_evidence "
                    "(id, entity_id, evidence_dimension, source, disposition, original_values, generation_timestamp) "
                    "VALUES (gen_random_uuid(), CAST(:ent AS varchar), 'IDENTITY', 'NPPES', 'PASS', "
                    "        jsonb_build_object('npi', CAST(:npi AS text)), '2026-09-27T00:00:00')"),
                    {"ent": ent, "npi": VALID_NPI})
            # e_pass / e_pass2: completed determinations for both sources.
            # e_unav: OUTAGE rows only for both sources.
            for ent, disp in ((e_pass, "PASS"), (e_pass2, "CORROBORATED"),
                              (e_unav, "UNAVAILABLE")):
                for src in ("CMS_PPEF_ENROLLMENT", "CMS_REVOCATION"):
                    await s.execute(sql(
                        "INSERT INTO tefca_dimension_evidence "
                        "(id, entity_id, evidence_dimension, source, disposition, original_values, generation_timestamp) "
                        "VALUES (gen_random_uuid(), CAST(:ent AS varchar), 'MEDICARE_ENROLLMENT', "
                        "        CAST(:src AS varchar), CAST(:disp AS varchar), '{}'::jsonb, "
                        "        '2026-09-27T00:00:00')"), {"ent": ent, "src": src, "disp": disp})
            await s.commit()
        async with async_session_maker() as s:
            first = await plan_pecos_retry(s, 25, intake_id=str(intake_a))
            second = await plan_pecos_retry(s, 25, intake_id=str(intake_a))
        return first, second

    plan, plan_again = run(_seed_and_plan())

    assert plan["entities_scanned"] == 3
    assert plan["eligible_count"] == 0 and plan["candidates"] == []
    assert plan["returned_count"] == 0 and plan["truncated_by_limit"] is False
    # Current policy: the outage-only entity counts as covered too.
    assert plan["exclusion_counts"] == {"missing_npi": 0, "invalid_npi": 0,
                                         "already_has_both_target_sources": 3}
    assert plan["missing_source_counts"] == {"CMS_PPEF_ENROLLMENT_only": 0,
                                              "CMS_REVOCATION_only": 0, "both": 0}
    # Closed funnel and determinism on the zero-candidate path.
    assert plan["entities_scanned"] == sum(plan["exclusion_counts"].values()) + plan["eligible_count"]
    assert plan == plan_again
