"""Final-convergence regression tests (2026-09-30) — one test group per root cause.

RC-01  MQA-2026-014  alembic_version is readable (and only readable) by the app role
RC-02  MQA-2026-011  covered in test_def005_disposition_inflight_guard (machine code)
RC-03  MQA-2026-103  /api/reports/generate refuses an unscoped draft (422 REPORT_SCOPE_REQUIRED)
RC-04  QA108-004     every report type registers its PDF at generation; legacy reports
                     get one through an idempotent, qalead-gated backfill
RC-08  MQA-2026-013  verification coverage names the PECOS backing (nppes_proxy)
Envelope            a {"error","code"} HTTPException detail reaches the client unwrapped

Database-backed tests run in the no-skip PostgreSQL CI job. Every value is
synthetic (SYN-* / 9.99.777.* / DocuAction test accounts). Nothing here touches
a Government delivery.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import re

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.reports import routes as report_routes


# ═══ RC-03: explicit scope rule (pure) ═══════════════════════════════════════

@pytest.mark.parametrize("params", [
    {"review_cycle_id": "rc-1"},
    {"job_id": "j-1"},
    {"intake_id": "i-1"},
    {"period_start": "2026-09-01", "period_end": "2026-09-30"},
])
def test_scope_rule_accepts_every_named_scope(params):
    report_routes.require_explicit_scope("retrospective_weekly", params)


@pytest.mark.parametrize("params", [
    {},
    {"period_start": "2026-09-01"},
    {"period_end": "2026-09-30"},
    {"suggested_changes": "x"},
    {"job_id": "", "review_cycle_id": None},
])
def test_scope_rule_refuses_an_unscoped_draft_with_a_machine_code(params):
    with pytest.raises(HTTPException) as refused:
        report_routes.require_explicit_scope("retrospective_weekly", params)
    assert refused.value.status_code == 422
    assert refused.value.detail["code"] == report_routes.REPORT_SCOPE_REQUIRED == "REPORT_SCOPE_REQUIRED"
    assert "Nothing was generated" in refused.value.detail["error"]


def test_generate_route_enforces_the_scope_rule_before_any_work():
    source = inspect.getsource(report_routes.generate)
    assert "require_explicit_scope(request.report_type, parameters)" in source
    # ...and before the idempotency replay, so an unscoped request can never
    # be answered from a previous report either.
    assert source.index("require_explicit_scope") < source.index("_replay_for_key")


# ═══ Envelope: dict-shaped details are unwrapped, named codes are honoured ═══

def _envelope_app() -> TestClient:
    from app.core.error_handler import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/dict-detail")
    async def dict_detail():
        raise HTTPException(422, detail={"error": "named in the body", "code": "MY_CODE"})

    @app.get("/attr-code")
    async def attr_code():
        exc = HTTPException(409, "named on the exception")
        exc.code = "ATTR_CODE"
        raise exc

    @app.get("/plain")
    async def plain():
        raise HTTPException(404, "just a message")

    return TestClient(app)


def test_dict_detail_reaches_the_client_as_error_plus_code_not_a_stringified_dict():
    body = _envelope_app().get("/dict-detail").json()
    assert body["error"] == "named in the body"
    assert body["code"] == "MY_CODE"
    assert "{" not in body["error"]


def test_an_exception_attribute_names_the_machine_code():
    body = _envelope_app().get("/attr-code").json()
    assert body == {**body, "error": "named on the exception", "code": "ATTR_CODE"}


def test_a_plain_detail_keeps_the_status_map_code():
    body = _envelope_app().get("/plain").json()
    assert body["code"] == "NOT_FOUND" and body["error"] == "just a message"


# ═══ RC-08: PECOS backing is named on the coverage payload (pure) ════════════

def test_coverage_stamps_the_pecos_backing_from_the_single_constant():
    from app.Tefca.connectors import PECOS_BACKING, PECOS_UI_LABEL
    from app.tefca_registry.rce import verification_coverage as vc

    sources = {"pecos": {"state": "partial"}, "nppes": {"state": "complete"}}
    vc.stamp_pecos_backing(sources)
    assert sources["pecos"]["pecos_backing"] == PECOS_BACKING == "nppes_proxy"
    assert sources["pecos"]["pecos_label"] == PECOS_UI_LABEL
    assert "pecos_backing" not in sources["nppes"]

    empty = vc.empty_coverage(reason="no intake yet")
    assert empty["sources"]["pecos"]["pecos_backing"] == "nppes_proxy"

    # The live path stamps it too — pinned at the source so the route test
    # in test_job_detail_contract is not the only guard.
    assert "stamp_pecos_backing(sources)" in inspect.getsource(vc.coverage_for_intake)


def test_pecos_backing_never_claims_direct_pecos():
    from app.Tefca.connectors import PECOS_BACKING
    assert "proxy" in PECOS_BACKING.lower()


# ═══ RC-04 (pure): the generator registers renderings for EVERY report type ══

def test_generator_registers_renderings_for_every_report_type_not_only_rce():
    from app.reports import generator

    source = inspect.getsource(generator.generate_report)
    call = source[source.index("artifacts = await finalize_report_renderings"):]
    # Not guarded by the RCE-only condition any more: PDFs for the SOW /
    # global families are what QA downloads (QA108-20260927-004).
    guard = source[:source.index("artifacts = await finalize_report_renderings")].rstrip().splitlines()[-1]
    assert "if report_type in RCE_TYPES" not in guard
    assert "include_csv=bool(report_type in RCE_TYPES" in call


def test_backfill_route_is_qalead_gated_idempotent_and_budgeted():
    source = inspect.getsource(report_routes.backfill_pdf_artifact)
    assert 'require_role_audited("qalead"' in source
    # Idempotent: an existing registered PDF short-circuits before any render.
    assert source.index("_registered_bytes(db, report_id, PDF)") < source.index("pdf_available()")
    assert "timeout=PDF_RENDER_BUDGET_SECONDS" in source
    assert "record_report_download(" in source and "record_report_download_failure(" in source


# ═══ Database-backed: RC-01 and RC-04 ════════════════════════════════════════

def _app_role_url():
    """DATABASE_URL re-pointed at DB_APP_ROLE. CI connects as the superuser with
    the roles created at password 'x'; a local trust-auth instance ignores the
    password. Either way this is a REAL connection as the runtime role."""
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    role = os.environ.get("DB_APP_ROLE", "").strip()
    assert role, "DB_APP_ROLE must name the runtime role for this test to mean anything"
    # The CI job creates both roles with password 'x' (pr-tests.yml); the
    # superuser's own password on DATABASE_URL is not the app role's. A local
    # trust-auth instance ignores the value. DB_APP_ROLE_PASSWORD overrides.
    return url.set(username=role, password=os.environ.get("DB_APP_ROLE_PASSWORD", "x"))


@pytest.mark.asyncio
async def test_app_role_can_read_but_not_write_alembic_version(db_required):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import _normalize_url

    engine = create_async_engine(_normalize_url(str(_app_role_url())), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            value = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            assert value and value != "unknown"
            assert re.fullmatch(r"[0-9]{8}_[a-z0-9_]+", value), value
            with pytest.raises(Exception) as denied:
                await conn.execute(text("UPDATE alembic_version SET version_num = version_num"))
            assert "permission denied" in str(denied.value)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_revision_helper_reports_the_real_revision_as_the_app_role(db_required):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.api.admin_health import migration_revision
    from app.core.database import _normalize_url

    engine = create_async_engine(_normalize_url(str(_app_role_url())), poolclass=NullPool)
    try:
        async with AsyncSession(engine) as db:
            value = await migration_revision(db)
        assert value != "unknown", "the app role must be able to name the deployed revision"
        assert value >= "20260930_alembic_version_read"
    finally:
        await engine.dispose()


def test_the_grant_migration_fails_closed_and_grants_select_only():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "alembic", "versions", "20260930_alembic_version_read_grant.py")
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    assert 'down_revision = "20260921_september_snapshot"' in src
    assert "DB_APP_ROLE is not set" in src, "must fail closed without a named role"
    assert "OWNS" in src, "must refuse to grant the owner"
    assert 'GRANT SELECT ON alembic_version TO' in src.replace("{TABLE}", "alembic_version")
    for verb in ("GRANT INSERT", "GRANT UPDATE", "GRANT DELETE", "GRANT ALL"):
        assert verb not in src


# ── RC-04 end to end: generate → registered PDF; legacy → backfill once ──────

@pytest.fixture
def report_pack(db_required, tmp_path, monkeypatch):
    """A committed synthetic delivery with one delivery-scoped report generated
    WITHOUT a PDF (the pre-fix, legacy shape) and one SOW report generated with
    the fix in place. Local artifact root on a tmp dir; everything removed."""
    from support_delivery_api import run

    from app.core.storage import artifact_store

    monkeypatch.setenv("REPORT_ARTIFACT_ROOT", str(tmp_path / "durable-artifacts"))
    monkeypatch.delenv("REPORT_ARTIFACT_BACKEND", raising=False)
    artifact_store.reset_artifact_store()
    from test_report_storage_durable import _Committed  # the proven seed/cleanup harness

    fixture = _Committed()
    run(fixture.seed_and_generate())
    try:
        yield fixture
    finally:
        run(fixture.cleanup())
        artifact_store.reset_artifact_store()


@pytest.fixture
def synthetic_pdf_engine(monkeypatch):
    """A deterministic stand-in for WeasyPrint, so the REGISTRATION, idempotency
    and byte-serving logic is proven on every runner — the CI job does not
    install the engine's native libraries, and a test that skips there proves
    nothing. The rendered bytes are a function of the HTML, so two renders of
    the same document register once (content-addressed) and the served bytes
    can be checked against the registered hash. The real engine is exercised
    by tests/test_reports.py where it is present."""
    from app.reports.engine import pdf_engine

    calls = []

    def render(html, *, title=None, variant=None):
        calls.append(title)
        return b"%PDF-1.7 synthetic " + hashlib.sha256(html.encode("utf-8")).hexdigest().encode()

    monkeypatch.setattr(pdf_engine, "pdf_available", lambda: True)
    monkeypatch.setattr(pdf_engine, "render_pdf", render)
    return calls


def test_pdf_backfill_registers_once_then_answers_from_the_registry(report_pack, client, synthetic_pdf_engine):
    """A report that has HTML but no registered PDF (every pre-fix SOW report on
    DEV) gets exactly one PDF artifact from the backfill; a repeat call renders
    nothing and returns the same artifact."""
    from support_delivery_api import headers_for, run

    report_id = report_pack.result["report_id"]
    from app.reports.data import artifact_registry
    from sqlalchemy import delete

    # Legacy shape: strip the PDF the fixed generator registered, keep HTML.
    async def _strip_pdf():
        from app.core.database import async_session_maker
        async with async_session_maker() as db:
            await db.execute(delete(artifact_registry.ReportArtifact).where(
                artifact_registry.ReportArtifact.report_id == report_id,
                artifact_registry.ReportArtifact.content_type == "application/pdf"))
            await db.commit()
    run(_strip_pdf())

    url = f"/api/reports/{report_id}/artifacts/backfill"
    assert client.post(url, headers=headers_for("reviewer")).status_code == 403
    assert client.post(url).status_code in (401, 403)

    first = client.post(url, headers=headers_for("qalead"))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["backfilled"] is True
    assert body["artifact"]["content_type"] == "application/pdf"
    assert re.fullmatch(r"[0-9a-f]{64}", body["artifact"]["rendered_sha256"])

    renders_after_first = len(synthetic_pdf_engine)
    second = client.post(url, headers=headers_for("qalead"))
    assert second.status_code == 200, second.text
    assert second.json()["backfilled"] is False
    assert second.json()["artifact"]["rendered_sha256"] == body["artifact"]["rendered_sha256"]
    assert len(synthetic_pdf_engine) == renders_after_first, "a repeat backfill must not render again"

    # The download path now serves the REGISTERED bytes, hash-verified.
    served = client.get(f"/api/reports/{report_id}/pdf", headers=headers_for("reviewer"))
    assert served.status_code == 200
    assert served.headers.get("X-Report-Source") == "artifact-registry"
    assert hashlib.sha256(served.content).hexdigest() == body["artifact"]["rendered_sha256"]
    assert served.content[:5] == b"%PDF-"


def test_a_report_type_outside_the_rce_family_registers_its_pdf_at_generation(report_pack, synthetic_pdf_engine):
    """The generator fix itself: a global-scope technical report (not
    delivery_processing) ends generation with a registered PDF."""
    from support_delivery_api import run

    async def _generate_sow():
        from app.core.database import async_session_maker
        from app.reports.data.artifact_registry import artifacts_for_report
        from app.reports.generator import generate_report

        async with async_session_maker() as db:
            result = await generate_report(
                db, report_type="verification", persist=True,
                query_parameters={"period_start": "2026-09-01", "period_end": "2026-09-30"},
                generated_by="qalead@docuaction.io")
            rows = await artifacts_for_report(db, result["report_id"])
            return result["report_id"], sorted(r["content_type"] for r in rows)
    report_id, types = run(_generate_sow())
    try:
        assert "text/html" in types and "application/pdf" in types, types
        assert "text/csv" not in types  # CSV stays delivery-scoped
    finally:
        async def _cleanup():
            from sqlalchemy import delete

            from app.core.database import async_session_maker
            from app.reports.data.artifact_registry import ReportArtifact
            from app.tefca_registry import models as reg
            async with async_session_maker() as db:
                await db.execute(delete(ReportArtifact).where(ReportArtifact.report_id == report_id))
                await db.execute(delete(reg.ReviewReport).where(reg.ReviewReport.report_id == report_id))
                await db.commit()
        run(_cleanup())
