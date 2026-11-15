"""Review Hub overview: what each role is told, what the readiness panel says
from stored evidence, and that reading it changes nothing.

Synthetic data only (`rce_traceability_support`), inside a transaction that is
rolled back. The first class needs no database.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import event

from app.core.security import ROLE_HIERARCHY, role_level
from app.tefca_registry import review_hub as hub
from app.tefca_registry.rce import reconciliation
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob
from rce_traceability_support import (SYN, make_rows, rolled_back_db,  # noqa: F401
                                      seed_intake)
from test_official_delivery_workflow import _role_floors, app_spec  # noqa: F401

ALL_ROLES = ["viewer", "contributor", "manager", "reviewer", "senior_analyst",
             "qalead", "program_manager", "admin"]


class _User:
    def __init__(self, role, email=None):
        self.role = role
        self.email = email or f"hub-{role}@synthetic-test.docuaction.invalid"
        self.id = None


def _licensed(monkeypatch, on: bool) -> None:
    from app.core.config import settings
    monkeypatch.setattr(settings, "ENABLE_IQVIA_SOURCES", on, raising=False)


async def _overview(db, role, **kw):
    return await hub.overview(Response(), job_id=kw.get("job_id"), db=db, user=_User(role))


def _states(readiness):
    return {item["key"]: item["state"] for item in readiness["items"]}


# ── no database ──────────────────────────────────────────────────────────────

class TestTheHubReportsTheFloorsTheRoutesEnforce:
    """The capability list must be the routes' answer, not a second one."""

    def test_every_listed_floor_equals_the_dependency_on_the_real_route(self):
        from app.tefca_registry import review_routes
        from app.tefca_registry.rce import delivery_routes, iqvia_routes

        routers = {"/api/tefca/rce/official-deliveries": delivery_routes.router,
                   "/api/tefca/rce/iqvia/": iqvia_routes.router,
                   "/api/tefca/arc/cases/import/": review_routes.router}
        checked = 0
        for cap in hub.capability_floors():
            method, path = cap["api"]
            router = next(r for prefix, r in routers.items() if path.startswith(prefix))
            suffix = path.split("/api/tefca/rce/iqvia")[-1] if "iqvia" in path else \
                "/" + path.split("/api/tefca/")[-1].split("/", 1)[1]
            floors = _role_floors(router, suffix, method=method)
            assert floors, f"{cap['key']}: no route {method} …{suffix}"
            assert max(floors) == ROLE_HIERARCHY[cap["required_role"]], (
                f"{cap['key']}: the hub says {cap['required_role']} but the route "
                f"enforces level {max(floors)}")
            checked += 1
        assert checked == 5

    def test_the_overview_is_a_get_and_nothing_else(self, app_spec):
        paths = {p: ops for p, ops in app_spec["paths"].items() if p.startswith("/api/tefca/hub")}
        assert set(paths) == {"/api/tefca/hub/overview"}
        assert set(paths["/api/tefca/hub/overview"]) == {"get"}

    def test_the_route_floor_is_viewer_so_the_page_never_403s_for_a_signed_in_user(self):
        assert _role_floors(hub.router, "/overview") == [ROLE_HIERARCHY["viewer"]]

    @pytest.mark.parametrize("role", ALL_ROLES)
    def test_capabilities_follow_the_role_ladder(self, role, monkeypatch):
        _licensed(monkeypatch, True)
        caps = {c["key"]: c for c in hub._capabilities(_User(role))}
        for cap in caps.values():
            expected = role_level(role) >= ROLE_HIERARCHY[cap["required_role"]]
            assert cap["allowed"] is expected, (role, cap["key"])
            assert cap["availability"] == ("available" if expected
                                           else f"requires_role:{cap['required_role']}")

    def test_an_unknown_role_is_allowed_nothing(self, monkeypatch):
        _licensed(monkeypatch, True)
        assert not any(c["allowed"] for c in hub._capabilities(_User("superuser-typo")))

    def test_licensed_acts_are_not_offered_when_the_source_flag_is_off(self, monkeypatch):
        _licensed(monkeypatch, False)
        caps = {c["key"]: c for c in hub._capabilities(_User("admin"))}
        assert caps["upload_reference_file"]["availability"] == "not_configured"
        assert caps["approve_reference_snapshot"]["allowed"] is False
        assert caps["register_official_delivery"]["allowed"] is True

    def test_ai_mode_is_read_from_configuration_and_claims_no_agreement(self, monkeypatch):
        monkeypatch.delenv("AI_ENTITY_RESOLUTION", raising=False)
        assert hub._mode()["ai_assistance"]["state"] == "DISABLED"
        monkeypatch.setenv("AI_ENTITY_RESOLUTION", "advisory")
        assert hub._mode()["ai_assistance"]["state"] == "ADVISORY"
        monkeypatch.setenv("AI_ENTITY_RESOLUTION", "definitely-not-a-mode")
        assert hub._mode()["ai_assistance"]["state"] == "DISABLED"
        text = json.dumps(hub._mode()).lower()
        assert hub._mode()["ai_assistance"]["provider_agreement_evidence"] == hub.UNVERIFIED
        for claim in ("hipaa compliant", "hipaa-compliant", "baa in place", "certified",
                      "covered by a baa"):
            assert claim not in text

    def test_the_name_and_text_make_no_security_claim(self):
        text = json.dumps([hub.capability_floors(), hub._mode(), hub.STATUS_LABEL]).lower()
        assert "secure" not in text and "compliant" not in text


# ── database ─────────────────────────────────────────────────────────────────

pytestmark_db = pytest.mark.regression


@pytest.fixture
async def seeded(rolled_back_db):  # noqa: F811
    db = rolled_back_db
    intake_id, job = await seed_intake(db, make_rows(3))
    return db, intake_id, job


@pytest.mark.regression
class TestOverview:
    @pytest.mark.parametrize("role", ALL_ROLES)
    async def test_every_role_gets_the_overview_and_the_same_status_label(
            self, seeded, role, monkeypatch):
        db, _intake, job = seeded
        _licensed(monkeypatch, True)
        out = await _overview(db, role)
        assert out["status_label"] == "DRAFT / NOT FINAL / INDEPENDENT QA PENDING"
        assert out["caller"]["role"] == role
        assert any(item["job_id"] == str(job.id) for item in out["deliveries"]["items"])
        assert len(out["deliveries"]["items"]) <= hub.MAX_DELIVERIES

    @pytest.mark.parametrize("role,expected", [
        ("viewer", "requires_role:reviewer"), ("manager", "requires_role:reviewer"),
        ("reviewer", "available"), ("admin", "available")])
    async def test_iqvia_inventory_keeps_the_reviewer_floor(self, seeded, role, expected,
                                                           monkeypatch):
        db, _intake, _job = seeded
        _licensed(monkeypatch, True)
        iqvia = (await _overview(db, role))["iqvia"]
        assert iqvia["availability"] == expected
        assert (iqvia["status"] is None) == (expected != "available")

    async def test_iqvia_inventory_is_withheld_from_everyone_when_the_flag_is_off(
            self, seeded, monkeypatch):
        db, _intake, _job = seeded
        _licensed(monkeypatch, False)
        iqvia = (await _overview(db, "admin"))["iqvia"]
        assert iqvia == {"availability": "not_configured", "status": None,
                         "page": "/tefca-arc/iqvia"}

    async def test_no_delivered_value_is_in_the_overview(self, seeded, monkeypatch):
        """Filename, hash, counts and stage names only - for every role."""
        db, _intake, _job = seeded
        _licensed(monkeypatch, True)
        rows = make_rows(3)
        delivered = {v for r in rows for k, v in r.items()
                     if k in ("OrganizationName", "NPI", "HCID", "TEFCAID", "id") and v}
        delivered |= {v for r in rows for v in r.values() if v and len(v) > 12}
        for role in ("viewer", "reviewer", "admin"):
            text = json.dumps(await _overview(db, role), default=str)
            leaked = sorted(v for v in delivered if v in text)
            assert not leaked, (role, leaked[:3])

    async def test_an_unknown_or_malformed_job_id_is_404(self, seeded):
        db, _intake, _job = seeded
        for bad in (str(uuid.uuid4()), "not-a-uuid", "1 OR 1=1"):
            with pytest.raises(HTTPException) as exc:
                await _overview(db, "viewer", job_id=bad)
            assert exc.value.status_code == 404


@pytest.mark.regression
class TestReadinessRestatesStoredEvidence:
    async def test_a_running_job_with_no_snapshot_is_in_progress_and_unknown(self, seeded):
        db, _intake, job = seeded
        readiness = (await _overview(db, "viewer", job_id=str(job.id)))["readiness"]
        states = _states(readiness)
        assert states["original_preserved"] == hub.MET
        assert states["processing"] == hub.IN_PROGRESS
        assert states["reconciliation"] == hub.UNKNOWN           # missing evidence, not a pass
        assert states["report_generated"] == hub.NOT_MET
        assert states["human_review"] != hub.MET
        assert states["release"] == hub.UNVERIFIED

    async def test_a_passing_snapshot_is_met_with_the_snapshots_own_time(self, seeded,
                                                                        monkeypatch):
        """The database refuses a `passed` snapshot whose counts do not reconcile
        (ck_rce_snapshot_equation), so a passing row is supplied to the reader
        here rather than forged in the table."""
        from types import SimpleNamespace
        from datetime import timezone

        db, _intake, job = seeded
        when = datetime(2026, 11, 13, 9, 30, tzinfo=timezone.utc)

        async def passing(_db, job_id):
            assert job_id == job.id
            return SimpleNamespace(passed=True, sequence=4, received=3, created_at=when,
                                   reconstructed=False, failure_reason=None)

        monkeypatch.setattr(reconciliation, "latest_snapshot", passing)
        item = next(i for i in (await _overview(db, "viewer", job_id=str(job.id)))
                    ["readiness"]["items"] if i["key"] == "reconciliation")
        assert item["state"] == hub.MET and "3" in item["detail"]
        assert datetime.fromisoformat(item["as_of"]) == when

    async def test_a_reconstructed_snapshot_is_unverified_even_when_it_passed(
            self, seeded, monkeypatch):
        from types import SimpleNamespace

        db, _intake, job = seeded

        async def reconstructed(_db, _job_id):
            return SimpleNamespace(passed=True, sequence=2, received=3,
                                   created_at=datetime(2026, 11, 13), reconstructed=True,
                                   failure_reason=None)

        monkeypatch.setattr(reconciliation, "latest_snapshot", reconstructed)
        item = next(i for i in (await _overview(db, "viewer", job_id=str(job.id)))
                    ["readiness"]["items"] if i["key"] == "reconciliation")
        assert item["state"] == hub.UNVERIFIED

    async def test_the_database_refuses_a_passed_snapshot_that_does_not_reconcile(self, seeded):
        """Why the test above does not write one: the claim is enforced below
        the application."""
        from sqlalchemy.exc import IntegrityError

        db, intake_id, job = seeded
        result = await reconciliation.reconcile_delivery(db, intake_id)
        assert result["passed"] is False
        result["passed"] = True
        with pytest.raises(IntegrityError):
            await reconciliation.persist_snapshot(
                db, intake_id, result, job_id=job.id, actor=SYN, trigger="MANUAL")

    async def test_a_failing_snapshot_is_not_met_and_gives_its_reason(self, seeded):
        db, intake_id, job = seeded
        result = await reconciliation.reconcile_delivery(db, intake_id)
        result["passed"] = False
        result["failure_reason"] = "synthetic: deliberately failing"
        await reconciliation.persist_snapshot(
            db, intake_id, result, job_id=job.id, actor=SYN, trigger="MANUAL")
        item = next(i for i in (await _overview(db, "viewer", job_id=str(job.id)))
                    ["readiness"]["items"] if i["key"] == "reconciliation")
        assert item["state"] == hub.NOT_MET

    async def test_a_failed_job_is_not_met_and_names_the_stage(self, seeded):
        db, _intake, job = seeded
        job.state = RceDeliveryJob.STATE_FAILED
        job.active_marker = None
        job.failed_at = datetime.utcnow()
        job.error_reason = "synthetic processing failure"
        await db.commit()
        item = next(i for i in (await _overview(db, "viewer", job_id=str(job.id)))
                    ["readiness"]["items"] if i["key"] == "processing")
        assert item["state"] == hub.NOT_MET
        assert "QUALITY" in item["detail"] and "synthetic processing failure" in item["detail"]
        assert item["as_of"] is not None

    async def test_release_is_never_reported_as_met(self, seeded):
        """Whatever the evidence, this panel does not evaluate the release gates."""
        db, _intake_id, job = seeded
        job.state = RceDeliveryJob.STATE_SUCCEEDED
        job.active_marker = None
        job.completed_at = datetime.utcnow()
        await db.commit()
        readiness = (await _overview(db, "admin", job_id=str(job.id)))["readiness"]
        assert _states(readiness)["release"] == hub.UNVERIFIED
        assert "not an approval" in readiness["statement"]

    async def test_every_item_has_a_state_a_basis_and_a_time_or_none(self, seeded):
        db, _intake, job = seeded
        readiness = (await _overview(db, "viewer", job_id=str(job.id)))["readiness"]
        assert [i["key"] for i in readiness["items"]] == [
            "original_preserved", "processing", "reconciliation", "exceptions",
            "human_review", "report_generated", "release"]
        for item in readiness["items"]:
            assert item["state"] in (hub.MET, hub.NOT_MET, hub.IN_PROGRESS, hub.UNKNOWN,
                                     hub.UNVERIFIED)
            assert item["basis"] and item["detail"]
            assert item["as_of"] is None or datetime.fromisoformat(item["as_of"])


@pytest.mark.regression
class TestOpeningTheHubChangesNothing:
    async def test_the_overview_issues_no_write_and_starts_no_work(self, seeded, monkeypatch):
        db, _intake, job = seeded
        _licensed(monkeypatch, True)
        before = (job.state, job.stage, job.attempt_count, job.heartbeat_at)
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.strip().split(None, 1)[0].upper())

        sync_conn = (await db.connection()).sync_connection
        event.listen(sync_conn, "before_cursor_execute", record)
        try:
            for role in ("viewer", "reviewer", "admin"):
                await _overview(db, role)
                await _overview(db, role, job_id=str(job.id))
        finally:
            event.remove(sync_conn, "before_cursor_execute", record)
        writes = [s for s in statements if s in ("INSERT", "UPDATE", "DELETE", "TRUNCATE",
                                                 "ALTER", "CREATE", "DROP")]
        assert statements, "the read did query the database"
        assert writes == [], writes
        await db.refresh(job)
        assert (job.state, job.stage, job.attempt_count, job.heartbeat_at) == before


# -- review findings on #220 (CodeRabbit, 2026-11-13) --

@pytest.mark.regression
class TestAFailedOptionalReadCostsOnlyItself:
    async def test_a_status_query_that_fails_in_the_database_leaves_the_rows_usable(
            self, seeded, monkeypatch):
        """Before: the fallback rolled the whole session back, which expired the
        job rows; serialising them then raised instead of returning the list."""
        from sqlalchemy import text
        from app.tefca_registry.rce import delivery_jobs

        db, _intake, job = seeded
        _licensed(monkeypatch, True)

        async def broken(db_, rows):
            await db_.execute(text("SELECT 1 / 0"))        # a real database error

        monkeypatch.setattr(delivery_jobs, "status_for_jobs", broken)
        out = await _overview(db, "reviewer", job_id=str(job.id))
        item = next(i for i in out["deliveries"]["items"] if i["job_id"] == str(job.id))
        assert item["status_available"] is False and item["processing_outcome"] is None
        assert item["state"] == "RUNNING" and item["sha256"]
        states = _states(out["readiness"])
        assert states["exceptions"] == hub.UNKNOWN and states["human_review"] == hub.UNKNOWN
        assert states["original_preserved"] == hub.MET            # later reads still worked
        assert out["iqvia"]["availability"] == "available"

    async def test_a_report_link_read_that_fails_is_unknown_and_nothing_else_is_lost(
            self, seeded, monkeypatch):
        from sqlalchemy import text
        from app.reports.data import delivery_report_links

        db, _intake, job = seeded

        async def broken(db_, job_id):
            await db_.execute(text("SELECT 1 / 0"))

        monkeypatch.setattr(delivery_report_links, "links_for_job", broken)
        readiness = (await _overview(db, "viewer", job_id=str(job.id)))["readiness"]
        assert _states(readiness)["report_generated"] == hub.UNKNOWN
        assert _states(readiness)["release"] == hub.UNVERIFIED
        assert readiness["job_id"] == str(job.id)

    async def test_a_stored_driver_error_is_masked_in_the_outcome_detail_too(
            self, seeded, monkeypatch):
        """`to_dict()` masks `error_reason`; the derived outcome embeds the raw
        reason, so the hub masks its copy as well - for every role."""
        from app.core.logging_config import sanitize_stored_error

        db, _intake, job = seeded
        raw = ("PROMOTION: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class "
               "'asyncpg.exceptions.UniqueViolationError'>: duplicate key\n[SQL: INSERT INTO "
               "tefca_entities (name) VALUES ($1)]\n[parameters: ('SYNTHETIC SECRET VALUE 4711',)]")
        assert "4711" not in sanitize_stored_error(raw), "fixture must be something the sanitizer masks"
        job.state = RceDeliveryJob.STATE_FAILED
        job.active_marker = None
        job.failed_at = datetime.utcnow()
        job.error_reason = raw
        await db.commit()
        for role in ("viewer", "reviewer", "admin"):
            text_out = json.dumps(await _overview(db, role, job_id=str(job.id)), default=str)
            assert "SYNTHETIC SECRET VALUE 4711" not in text_out, role
            assert "[parameters" not in text_out, role
