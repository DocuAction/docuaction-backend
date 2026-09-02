"""The controlled ONC/RCE delivery workflow: registration, jobs, QHIN, workspace.

WHAT THESE TESTS ARE FOR
────────────────────────
The asynchronous delivery path moved work off the request thread. That is a
change to WHEN things happen, and the risk in such a change is that a guarantee
which used to hold because everything ran inside one transaction quietly stops
holding. So these pin the guarantees, not the mechanics:

  * Area 1 gained no mutating route.
  * An analyst cannot establish official Government source data.
  * A delivery is not "ready for review" unless reconciliation actually passed.
  * A status the dashboard has never seen is surfaced, not folded into a bucket.
  * SOURCE_UNAVAILABLE is never converted into NO_MATCH.
  * A delivered URL cannot make the server dial its own network.

Most run with no database. The ones that need one are marked and skip cleanly,
matching the existing convention in this suite.
"""

from __future__ import annotations

import pytest

from app.core.security import role_level
from app.tefca_registry import website_evidence as web
from app.tefca_registry.rce import delivery_jobs as jobs
from app.tefca_registry.rce import delivery_dashboard as dash
from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob


# ── job identity ─────────────────────────────────────────────────────────────

class TestJobIdentity:
    """What makes two registrations the same registration."""

    def test_same_bytes_label_and_date_are_one_registration(self):
        from datetime import datetime

        when = datetime(2026, 9, 1)
        a = jobs.job_identity(sha256="a" * 64, delivery_label="September 2026",
                              received_date=when)
        b = jobs.job_identity(sha256="a" * 64, delivery_label="September 2026",
                              received_date=when)
        assert a == b

    def test_label_case_and_padding_do_not_split_a_registration(self):
        from datetime import datetime

        when = datetime(2026, 9, 1)
        a = jobs.job_identity(sha256="a" * 64, delivery_label="September 2026",
                              received_date=when)
        b = jobs.job_identity(sha256="a" * 64, delivery_label="  september 2026 ",
                              received_date=when)
        assert a == b, ("A double-click that differs only in whitespace or case "
                        "must not create a second Area 1 intake.")

    def test_the_same_file_received_twice_is_two_deliveries(self):
        """ONC resending in October what it sent in September is two arrivals.

        Recording them as one would lose the second arrival, which is exactly
        the record an auditor asks for.
        """
        from datetime import datetime

        a = jobs.job_identity(sha256="a" * 64, delivery_label="Monthly",
                              received_date=datetime(2026, 9, 1))
        b = jobs.job_identity(sha256="a" * 64, delivery_label="Monthly",
                              received_date=datetime(2026, 10, 1))
        assert a != b

    def test_different_bytes_are_different_registrations(self):
        a = jobs.job_identity(sha256="a" * 64, delivery_label="X",
                              received_date=None)
        b = jobs.job_identity(sha256="b" * 64, delivery_label="X",
                              received_date=None)
        assert a != b


# ── segregation of duties ────────────────────────────────────────────────────

class TestWhoMayRegisterADelivery:
    def test_registration_floor_excludes_the_analyst_role(self):
        """An analyst must not establish what the official source data IS.

        `analyst` aliases to `contributor`, so a contributor-gated registration
        route would be one an analyst can call. This pins the floor above it.
        """
        from app.tefca_registry.rce.delivery_routes import DATA_OPERATIONS_ROLE

        assert role_level("analyst") < role_level(DATA_OPERATIONS_ROLE)
        assert role_level("contributor") < role_level(DATA_OPERATIONS_ROLE)

    def test_registration_floor_excludes_the_CONTRACT_analyst_role(self):
        """The contract Analyst is `reviewer` (4), not the `analyst` alias (2).

        The first version of this test asserted the floor sat BELOW reviewer,
        reasoning that Data Operations is not a reviewer. Independent review
        found that `case_assignment.ROLE_ANALYST` is `reviewer` — the role that
        claims cases and records determinations — so a floor below it let the
        actual analyst establish official Government source data. The floor
        must exclude every review-side role.
        """
        from app.tefca_registry.case_assignment import ROLE_ANALYST, ROLE_SUPERVISOR
        from app.tefca_registry.rce.delivery_routes import DATA_OPERATIONS_ROLE

        assert role_level(ROLE_ANALYST) < role_level(DATA_OPERATIONS_ROLE)
        assert role_level(ROLE_SUPERVISOR) < role_level(DATA_OPERATIONS_ROLE)
        assert role_level("qalead") < role_level(DATA_OPERATIONS_ROLE)

    def test_verification_is_not_an_analyst_act(self, app_spec):
        """`/verify` mints review cases — it decides what enters the queue.

        At contributor the contract Analyst could choose their own review
        population outside the frozen sample. Raised to program_manager.
        """
        from app.tefca_registry.rce import routes as rce_routes

        floors = _role_floors(rce_routes.router, "/deliveries/{intake_id}/verify",
                              method="POST")
        assert floors and max(floors) >= role_level("program_manager")


# ── Area 1 immutability ──────────────────────────────────────────────────────

class TestAreaOneStaysImmutable:
    def test_no_mutating_route_was_added_for_deliveries_or_records(self, app_spec):
        """The guarantee is enforced by ABSENCE, so absence is what is checked.

        `rce/routes.py` states that Area 1 has no PUT, PATCH or DELETE anywhere.
        The new delivery surface must not have quietly introduced one.
        """
        offenders = []
        for path, operations in app_spec["paths"].items():
            if "/rce/" not in path:
                continue
            if "issue" in path:
                continue  # Issues DO mutate — resolving one is the workflow.
            for method in ("put", "patch", "delete"):
                if method in operations:
                    offenders.append(f"{method.upper()} {path}")
        assert offenders == [], (
            f"A mutating route now exists over Area 1: {offenders}. Area 1 is "
            f"append-only and its immutability is enforced by the absence of "
            f"such a route.")

    def test_registration_is_accepted_asynchronously(self, app_spec):
        """202, because the response is a receipt and not an outcome."""
        operation = app_spec["paths"]["/api/tefca/rce/official-deliveries"]["post"]
        assert "202" in operation["responses"]

    def test_the_synchronous_upload_route_still_exists(self, app_spec):
        """The proven path is not removed. It ingested the delivered population."""
        assert "post" in app_spec["paths"]["/api/tefca/rce/deliveries"]


# ── the dashboard tells the truth ────────────────────────────────────────────

class TestDeliveryDashboard:
    def test_known_statuses_map_to_the_operational_words(self):
        counts, other = dash._classify(
            {"CLEAN": 21932, "CORRECTED": 1630, "HELD": 4})
        assert counts == {"ready": 21932, "warnings": 1630, "held": 4,
                          "excluded": 0}
        assert other == {}

    def test_an_unknown_status_is_surfaced_not_folded_away(self):
        """A status this code has never seen is the thing an operator must see.

        Folding it into a neighbouring bucket would make a new delivery
        condition invisible at exactly the moment it first appears.
        """
        counts, other = dash._classify({"CLEAN": 10, "QUARANTINED": 3})
        assert counts["ready"] == 10
        assert other == {"QUARANTINED": 3}
        assert sum(counts.values()) == 10, (
            "An unmapped status must not be counted into any operational word.")

    def test_ready_for_review_requires_reconciliation_to_have_passed(self):
        status = dash._status(None, {"passed": False})
        assert status["state"] == "RECONCILIATION_FAILED"
        assert "must not start" in status["detail"]

    def test_reconciliation_passing_is_what_makes_a_delivery_ready(self):
        assert dash._status(None, {"passed": True})["state"] == "READY_FOR_REVIEW"

    def test_a_delivery_with_no_reconciliation_is_not_ready(self):
        assert dash._status(None, None)["state"] == "NOT_RECONCILED"

    def test_create_review_cycle_is_refused_with_a_stated_reason(self):
        """A disabled button with no explanation generates a support call."""
        status = dash._status(None, {"passed": False})
        actions = dash._actions(status, {"passed": False}, {})
        create = actions[dash.ACTION_CREATE_REVIEW_CYCLE]
        assert create["available"] is False
        assert create["reason"]

    def test_exceptions_remain_viewable_after_a_failure(self):
        status = dash._status(None, {"passed": False})
        actions = dash._actions(status, {"passed": False}, {})
        assert actions[dash.ACTION_VIEW_EXCEPTIONS]["available"] is True

    def test_remaining_is_not_reported_as_zero_on_a_finished_delivery(self):
        """Zero remaining beside a failed run would read as success."""
        assert dash._remaining(25000, None, {"state": "FAILED"}) is None
        assert dash._remaining(25000, None, {"state": "READY_FOR_REVIEW"}) is None


# ── the runner records honestly ──────────────────────────────────────────────

class TestDeliveryRunner:
    def test_a_stage_failure_names_the_stage_and_the_error_type(self):
        from app.tefca_registry.rce.delivery_runner import _reason

        reason = _reason(RceDeliveryJob.STAGE_QUALITY, ValueError("bad rule"))
        assert "QUALITY" in reason and "ValueError" in reason

    def test_promotion_declining_is_held_not_failed(self):
        """Refusing to promote a drifted schema is the pipeline working.

        Promoting an unreconciled schema would mis-assign values into the
        canonical registry, so declining is correct behaviour and must not be
        reported as an error.
        """
        import asyncio

        from app.tefca_registry.rce import delivery_runner

        async def refuse(db, intake_id, actor=None):
            raise ValueError("schema drift; the map must be reconciled first")

        import app.tefca_registry.rce.promotion as promotion
        original = promotion.promote_delivery
        promotion.promote_delivery = refuse
        try:
            out = asyncio.run(delivery_runner._stage_promotion(None, "x", "me"))
        finally:
            promotion.promote_delivery = original

        assert out["held"] is True
        assert out["completed"] is False
        assert "declined, not failed" in out["note"]

    def test_the_delivery_job_creates_no_review_cases(self):
        """Verification follows the SAMPLE, not the delivery.

        The first runner verified a 250-entity "seed" inside the job.
        `verify_and_classify` mints a new ReviewRecord on every call, so the
        seed doubled the review population on any re-run, and its cases were
        never members of the approved sample. The stage now records connector
        readiness and nothing else.
        """
        import inspect

        from app.tefca_registry.rce import delivery_runner

        source = inspect.getsource(delivery_runner._stage_verification)
        assert "verify_and_classify" not in source
        assert "AUTO_VERIFY_LIMIT" not in inspect.getsource(delivery_runner)
        assert "readiness" in source

    def test_a_reaped_job_cannot_be_resurrected_by_a_late_worker(self):
        """The reaper's verdict stands; a slow worker does not overwrite it.

        Once the reaper has failed a job and released its identity, a second
        registration may already be running. A late `finish_succeeded` that
        flipped the row back would leave two workers on one delivery.
        """
        import asyncio

        from app.tefca_registry.rce import delivery_jobs as jobs
        from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

        class Row:
            state = RceDeliveryJob.STATE_FAILED
            error_reason = jobs.REAPED_REASON
            stage = "QUALITY"
            active_marker = None
            source_intake_id = None
            stage_detail = {}

        row = Row()

        class Db:
            async def get(self, model, key):
                return row

            async def commit(self):
                raise AssertionError("a settled job must not be written to")

        asyncio.run(jobs.finish_succeeded(Db(), "job", reconciliation_passed=True))
        asyncio.run(jobs.heartbeat(Db(), "job", stage="CURATION"))
        asyncio.run(jobs.bind_intake(Db(), "job", "intake", records_received=1))
        assert row.state == RceDeliveryJob.STATE_FAILED
        assert row.active_marker is None

    def test_a_running_job_is_still_written(self):
        import asyncio

        from app.tefca_registry.rce import delivery_jobs as jobs
        from app.tefca_registry.rce.delivery_job_model import RceDeliveryJob

        class Row:
            state = RceDeliveryJob.STATE_RUNNING
            stage = "QUALITY"
            heartbeat_at = None
            stage_detail = {}
            records_received = None
            records_processed = None

        row = Row()
        commits = []

        class Db:
            async def get(self, model, key):
                return row

            async def commit(self):
                commits.append(True)

        asyncio.run(jobs.heartbeat(Db(), "job", stage="CURATION"))
        assert row.stage == "CURATION" and commits

    def test_the_heartbeat_uses_its_own_session(self):
        """Heartbeating on the stage's session would commit half an Area 1.

        `ingest_delivery` holds one transaction across every batch so a crash
        rolls the whole intake back. A mid-stage commit on that session — which
        is what a same-session heartbeat is — would destroy that contract.
        """
        import inspect

        from app.tefca_registry.rce import delivery_runner

        source = inspect.getsource(delivery_runner._Heartbeat)
        assert "async_session_maker" in source

    def test_a_failed_stage_settles_the_session_before_writing_the_job(self):
        """PendingRollbackError would otherwise swallow the failure reason."""
        import inspect

        from app.tefca_registry.rce import delivery_runner

        source = inspect.getsource(delivery_runner._run_stages)
        assert source.count("await _settle(db)") >= 3


# ── website evidence: the SSRF guard ─────────────────────────────────────────

class TestWebsiteUrlSafety:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
    ])
    def test_non_public_addresses_are_refused_before_connecting(self, url):
        ok, reason = web.check_url(web.normalize_url(url) or url)
        assert ok is False
        assert reason

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
    ])
    def test_only_http_and_https_are_dialled(self, url):
        assert web.normalize_url(url) is None

    def test_unusual_ports_are_refused(self):
        ok, reason = web.check_url("http://example.com:22/")
        assert ok is False
        assert "port" in reason

    def test_a_bare_domain_is_assumed_https(self):
        assert web.normalize_url("example.org").startswith("https://")

    def test_query_and_fragment_are_dropped(self):
        assert web.normalize_url("https://example.org/a?b=c#d") == \
            "https://example.org/a"

    def test_a_relative_redirect_resolves_against_its_host(self):
        assert web.resolve_redirect("https://example.org/a/b", "/c") == \
            "https://example.org/c"

    def test_a_redirect_to_link_local_is_refused(self):
        """The textbook SSRF: the first URL is fine, the redirect is not."""
        nxt = web.resolve_redirect("https://example.org/",
                                   "http://169.254.169.254/")
        ok, _ = web.check_url(nxt)
        assert ok is False


class TestWebsiteExtraction:
    def test_it_reads_what_the_page_publishes(self):
        html = """
        <html><head><title>Riverside Community Health Network</title></head>
        <body><p>100 North Main Street, Suite 4</p>
        <p>Call (512) 555-0142</p><p>info@riverside.example</p></body></html>
        """
        out = web.extract(html)
        assert out["organization_name_observed"] == \
            "Riverside Community Health Network"
        assert out["phone_observed"] == "(512) 555-0142"
        assert out["contact_observed"] == "info@riverside.example"
        assert "100 North Main Street" in out["address_observed"]

    def test_script_and_style_content_is_not_read_as_text(self):
        html = ("<html><body><script>var p='(999) 999-9999';</script>"
                "<p>Call (512) 555-0142</p></body></html>")
        assert web.extract(html)["phone_observed"] == "(512) 555-0142"

    def test_an_empty_page_observes_nothing_rather_than_guessing(self):
        assert web.extract("<html><body></body></html>") == {
            "organization_name_observed": None, "phone_observed": None,
            "contact_observed": None, "address_observed": None}

    def test_observed_values_are_length_capped(self):
        html = f"<html><head><title>{'x' * 5000}</title></head></html>"
        assert len(web.extract(html)["organization_name_observed"]) <= 200

    def test_body_is_capped_in_bytes(self):
        class Response:
            content = b"<html>" + b"x" * (web.MAX_BYTES * 2)
            encoding = "utf-8"

        assert len(web.body_text(Response())) <= web.MAX_BYTES

    def test_a_response_without_raw_bytes_still_reads(self):
        """Test doubles and some clients expose only `.text`."""
        class Response:
            text = "<html><title>Acme</title></html>"

        assert "Acme" in web.body_text(Response())


class TestWebsiteIsNeverAuthoritative:
    def test_it_claims_authority_over_nothing(self):
        assert web.AUTHORITATIVE_FOR == ()

    def test_every_observation_says_so(self):
        fields = web.observation_fields("https://example.org/",
                                        "<html><title>X</title></html>",
                                        reachable=True)
        assert fields["authoritative"] is False
        assert fields["authoritative_for"] == []

    def test_the_health_report_names_the_boundary(self):
        note = web.health()["note"].lower()
        for authority in ("npi", "enrolment", "exclusion", "registration",
                          "tefca"):
            assert authority in note


class TestWebsiteConnectorIsNotDuplicated:
    def test_there_is_exactly_one_website_connector(self):
        """The connector is `evidence_service.website_corroboration`.

        This module is its safety and extraction library, not a second
        connector: two definitions of "what the site said" would diverge the
        first time one was fixed.
        """
        assert not hasattr(web, "observe_website")
        from app.Tefca.evidence_service import website_corroboration

        assert callable(website_corroboration)

    def test_an_unreachable_site_is_still_never_a_finding(self):
        """The pinned contract, re-asserted after the guard was added."""
        import asyncio

        from app.Tefca.evidence_service import (WEBSITE_UNAVAILABLE,
                                                website_corroboration)

        entity = {"name": "Acme",
                  "telecom": [{"system": "url", "value": "http://127.0.0.1/"}]}
        out = asyncio.run(website_corroboration(entity))
        assert out["result"] == WEBSITE_UNAVAILABLE
        assert out["affects_determination"] is False

    def test_a_delivered_metadata_url_is_refused_not_fetched(self):
        """An SSRF attempt through the delivery file reaches no socket."""
        import asyncio

        from app.Tefca.evidence_service import (WEBSITE_UNAVAILABLE,
                                                website_corroboration)

        entity = {"name": "Acme", "telecom": [
            {"system": "url", "value": "http://169.254.169.254/latest/"}]}
        out = asyncio.run(website_corroboration(entity))
        assert out["result"] == WEBSITE_UNAVAILABLE
        assert "Refused before connecting" in out["note"]


# ── workload distribution decides nothing ────────────────────────────────────

class TestDistributionMakesNoComplianceDecision:
    def test_the_planner_takes_no_classification_or_finding(self):
        """Signature-level guarantee, checked rather than trusted.

        A planner that accepted a bucket or a severity could route by it, and
        routing by a finding is the system forming a view about an entity before
        an analyst has.
        """
        import inspect

        from app.tefca_registry.qhin_workload import plan_distribution

        parameters = set(inspect.signature(plan_distribution).parameters)
        assert parameters == {"db", "review_ids", "analyst_ids"}

    def test_distribution_defaults_to_preview(self):
        from app.tefca_registry.workflow_routes import DistributionRequest

        assert DistributionRequest(review_ids=[], analyst_user_ids=[]).preview \
            is True

    def test_distribute_floor_equals_the_per_case_assign_floor(self):
        """A route floor below `assign()`'s would refuse every case it touched."""
        from app.tefca_registry import workflow_routes
        from app.tefca_registry.case_assignment import ROLE_SUPERVISOR

        floors = _role_floors(workflow_routes.router, "/distribute", method="POST")
        assert floors and max(floors) == role_level(ROLE_SUPERVISOR)


# ── the workspace keeps the layers apart ─────────────────────────────────────

class TestWorkspaceSeparation:
    def test_it_declares_the_determination_vocabulary_it_reuses(self):
        """No parallel decision codes are invented.

        Two vocabularies for one decision is how two reports of the same case
        end up disagreeing.
        """
        import inspect

        from app.tefca_registry import workspace

        source = inspect.getsource(workspace._section_recommendation)
        assert '"CONFIRM", "RECLASSIFY"' in source
        assert '"B1", "B2", "B3", "B4"' in source

    def test_source_unavailable_is_never_mapped_to_no_match(self):
        import inspect

        from app.tefca_registry import workspace

        source = inspect.getsource(workspace)
        assert "SOURCE_UNAVAILABLE" in source
        # The only relationship stated between the two is that one is NOT the
        # other. Nothing assigns NO_MATCH from an unavailable source.
        assert "= NO_MATCH" not in source
        assert 'or "NO_MATCH"' not in source

    def test_usps_is_evidence_and_never_rewrites_the_source(self):
        import inspect

        from app.tefca_registry import workspace

        source = inspect.getsource(workspace._section_usps)
        assert '"source_modified": False' in source
        assert "legacy_web_tools_used" in source

    def test_curated_values_always_travel_with_their_source_value(self):
        import inspect

        from app.tefca_registry import workspace

        source = inspect.getsource(workspace._section_curated)
        assert '"source_value"' in source and '"curated_value"' in source


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_spec():
    """The live OpenAPI document, so route shape is checked against reality."""
    from app.main import app

    return app.openapi()


# ── the workspace masks rather than denies ───────────────────────────────────

class TestWorkspacePiiMasking:
    """A viewer must see the case without seeing the identifiers.

    Gating the workspace above viewer would hide it from a role the product can
    assign; handing a viewer raw NPIs would break the rule the viewer role
    exists to enforce. Masking satisfies both, and it reuses the masking the
    rest of the module already uses rather than inventing a second one.
    """

    @staticmethod
    def _workspace():
        return {
            "source": {
                "identifiers": {"npi": "1999000101", "tefcaid": "TEFCA-778812",
                                "hcid": None},
                "delivered_values": {"name": "Riverside Community Health",
                                     "NPI": "1999000101",
                                     "address": "100 North Main Street"},
                "raw_line": "1999000101|Riverside Community Health|100 North Main Street",
            },
            "curated": {
                "curated_values": {"name": "Riverside Community Health",
                                   "npi": "1999000101"},
                "corrections": [{"column": "npi", "source_value": "1999000101",
                                 "curated_value": "1999000102"}],
            },
        }

    class _User:
        def __init__(self, role):
            self.role = role

    def test_a_viewer_sees_masked_identifiers(self):
        from app.tefca_registry.workflow_routes import _mask_workspace

        out = _mask_workspace(self._workspace(), self._User("viewer"))
        assert out["pii_masked"] is True
        assert out["source"]["identifiers"]["npi"] == "••••••0101"
        assert out["source"]["delivered_values"]["NPI"] == "••••••0101"
        assert out["curated"]["curated_values"]["npi"] == "••••••0101"

    def test_masking_does_not_touch_non_identifier_fields(self):
        """The name and address still have to be readable, or the case is not
        reviewable at all."""
        from app.tefca_registry.workflow_routes import _mask_workspace

        out = _mask_workspace(self._workspace(), self._User("viewer"))
        values = out["source"]["delivered_values"]
        assert values["name"] == "Riverside Community Health"
        assert values["address"] == "100 North Main Street"

    def test_the_raw_line_is_withheld_not_doctored(self):
        """A partially masked copy of Area 1 evidence would be worse than none.

        The delivered line cannot be masked without corrupting it, so it is
        withheld with a stated reason rather than handed over altered.
        """
        from app.tefca_registry.workflow_routes import _mask_workspace

        out = _mask_workspace(self._workspace(), self._User("viewer"))
        assert out["source"]["raw_line"] is None
        assert "Reviewer and above" in out["source"]["raw_line_withheld"]

    def test_a_reviewer_sees_the_identifiers(self):
        from app.tefca_registry.workflow_routes import _mask_workspace

        out = _mask_workspace(self._workspace(), self._User("reviewer"))
        assert out["pii_masked"] is False
        assert out["source"]["identifiers"]["npi"] == "1999000101"

    def test_pii_masked_is_always_stated(self):
        """A caller must be able to tell "redacted for your role" from "absent"."""
        from app.tefca_registry.workflow_routes import _mask_workspace

        for role in ("viewer", "reviewer", "admin"):
            out = _mask_workspace(self._workspace(), self._User(role))
            assert isinstance(out.get("pii_masked"), bool)

    def test_masking_reuses_the_existing_helpers(self):
        """One definition of what a masked value looks like, not two."""
        import inspect

        from app.tefca_registry import workflow_routes

        source = inspect.getsource(workflow_routes._mask_workspace)
        assert "from app.Tefca.routes import _can_see_pii, _mask_identifier" in source


class TestReadsStayAtTheViewerFloor:
    def test_every_workflow_read_admits_a_viewer(self):
        """Pinned locally so a future edit fails with an obvious message."""
        from app.tefca_registry import workflow_routes

        checked = 0
        for route in workflow_routes.router.routes:
            if "GET" not in getattr(route, "methods", set()):
                continue
            floors = _role_floors(workflow_routes.router, route.path)
            assert floors and max(floors) == role_level("viewer"), route.path
            checked += 1
        assert checked >= 5


# ── the review cycle is the gate, server-side ────────────────────────────────

class TestReviewCycleGate:
    def test_the_official_sampler_now_has_a_route(self, app_spec):
        """`finalize_plan` was reachable from nowhere. Now it is, and only via
        a program-manager route that gates on reconciliation."""
        from app.tefca_registry import workflow_routes

        path = "/api/tefca/workflow/deliveries/{intake_id}/review-cycle"
        assert "post" in app_spec["paths"][path]
        floors = _role_floors(workflow_routes.router,
                              "/deliveries/{intake_id}/review-cycle", method="POST")
        assert floors and max(floors) == role_level("program_manager")

    def test_an_unreconciled_delivery_is_refused_before_any_sampling(self, monkeypatch):
        """No plan is drawn, nothing is verified, and the refusal is audited."""
        import asyncio

        from app.tefca_registry import review_cycle
        from app.tefca_registry.rce import reconciliation

        drawn = []

        async def not_passed(db, intake_id):
            return {"passed": False,
                    "checks": [{"check": "D == A", "passed": False}]}

        async def must_not_run(*a, **k):
            drawn.append(True)

        monkeypatch.setattr(reconciliation, "reconcile_delivery", not_passed)
        import app.tefca_registry.qhin_sampling as qs
        monkeypatch.setattr(qs, "finalize_plan", must_not_run)

        audited = []

        class Db:
            async def get(self, model, key):
                return object()  # an intake exists

            async def commit(self):
                pass

            def add(self, row):
                audited.append(row)

        class User:
            id = None
            email = "pm@example.test"

        with pytest.raises(review_cycle.CycleRefused) as refused:
            asyncio.run(review_cycle.create_review_cycle(Db(), "intake", user=User()))
        assert "not passed reconciliation" in str(refused.value)
        assert drawn == [], "sampling must not run on an unreconciled delivery"
        assert any(getattr(r, "action", "") == "review_cycle_refused" for r in audited)

    def test_the_cycle_route_uses_the_approved_sampler_only(self):
        """No Cochran call, no draw_sample, no formula — `finalize_plan` only."""
        import inspect

        from app.tefca_registry import review_cycle

        source = inspect.getsource(review_cycle)
        assert "finalize_plan" in source
        assert "draw_sample" not in source
        assert "CochranSampler" not in source

    def test_every_cycle_review_case_is_scoped_to_its_delivery(self):
        """The key `case_assignment._queue` and `qhin_workload` filter on."""
        import inspect

        from app.tefca_registry import review_cycle

        source = inspect.getsource(review_cycle.create_review_cycle)
        for key in ('"source_intake_id": str(intake_id)',
                    '"queue_source": QUEUE_SOURCE',
                    "record.sample_id = sample_id",
                    "member.review_id = review_id"):
            assert key in source, key


class TestQhinRollupScoping:
    def test_reviews_are_scoped_by_delivery_not_by_entity(self):
        """An entity delivered twice is one entity; its reviews are not."""
        from app.tefca_registry import qhin_workload

        stmt = _sql(qhin_workload._reviews_for("abc"))
        assert "source_intake_id" in stmt
        assert "sample_id" not in stmt  # only when asked for

    def test_a_sample_narrows_the_review_columns(self):
        from app.tefca_registry import qhin_workload

        stmt = _sql(qhin_workload._reviews_for("abc", sample_id="s"))
        assert "sample_id" in stmt

    def test_no_python_list_of_entity_ids_reaches_the_driver(self):
        """asyncpg refuses more than 32,767 bind parameters; 100K is expected."""
        import inspect

        from app.tefca_registry import qhin_sampling, qhin_workload

        assert "in_(promoted)" in inspect.getsource(qhin_sampling.resolve_qhin_strata)
        assert "_promoted_entities(intake_id)" in inspect.getsource(
            qhin_workload._entity_levels)
        assert "_chunks(" in inspect.getsource(qhin_workload.case_states)


# ── SSRF: the gaps independent review found ──────────────────────────────────

class TestSsrfHardening:
    def test_carrier_grade_nat_is_refused(self):
        """Python's `is_private` excludes 100.64.0.0/10; Azure VNet uses it."""
        ok, reason = web.check_url("http://100.64.0.1/")
        assert ok is False and reason

    @pytest.mark.parametrize("url", [
        "http://198.18.0.1/",       # benchmarking
        "http://192.0.0.9/",        # IETF protocol assignments
        "http://[64:ff9b::7f00:1]/",  # NAT64 loopback
        "http://[::ffff:169.254.169.254]/",  # IPv4-mapped metadata
    ])
    def test_the_other_non_public_ranges_are_refused(self, url):
        ok, _ = web.check_url(url)
        assert ok is False

    def test_credentials_in_a_delivered_url_are_never_sent(self):
        """A delivery file is not a credential store."""
        assert "user" not in (web.normalize_url("https://user:pw@example.org/") or "")
        ok, reason = web.check_url("https://user:pw@example.org/")
        assert ok is False and "credentials" in reason

    def test_the_connection_is_pinned_to_the_address_that_passed(self):
        """DNS rebinding: the name is resolved once and never again."""
        target = web.PinnedTarget("https://example.org/path", "example.org",
                                  "93.184.216.34")
        assert target.url == "https://93.184.216.34/path"
        assert target.headers == {"Host": "example.org"}
        assert target.extensions == {"sni_hostname": "example.org"}

    def test_an_ipv6_pin_is_bracketed(self):
        target = web.PinnedTarget("http://example.org/", "example.org",
                                  "2606:2800:220:1:248:1893:25c8:1946")
        assert target.url.startswith("http://[2606:2800:220:1:248:1893:25c8:1946]/")

    def test_the_connector_dials_the_pinned_target(self):
        """The guard is only a guard if the connection uses what it returned."""
        import inspect

        from app.Tefca import evidence_service

        source = inspect.getsource(evidence_service.website_corroboration)
        assert "pinned_target" in source
        assert "target.url" in source and "target.headers" in source
        assert "check_url_async" not in source, "the unpinned path must be gone"

    def test_the_byte_cap_stops_reading_not_just_trims(self):
        """The first version buffered the whole body and THEN sliced it."""
        import asyncio

        chunks_served = []

        class Stream:
            async def aiter_bytes(self):
                for i in range(100):
                    chunk = b"x" * 1024 * 1024  # 1 MiB
                    chunks_served.append(i)
                    yield chunk

        body, truncated = asyncio.run(web.read_capped(Stream(), limit=3 * 1024 * 1024))
        assert len(body) == 3 * 1024 * 1024
        assert truncated is True
        assert len(chunks_served) <= 4, "reading must stop at the cap"

    def test_the_connector_streams(self):
        import inspect

        from app.Tefca import evidence_service

        source = inspect.getsource(evidence_service.website_corroboration)
        assert "client.stream(" in source and "read_capped" in source


# ── the workspace reads the delivered address by its real name ───────────────

class TestWorkspaceAddressFields:
    def test_the_delivered_address_fields_are_read(self):
        from app.tefca_registry import workspace

        class Source:
            parsed = {"address_line": "100 North Main Street",
                      "address_city": "Austin", "address_state": "TX",
                      "address_postalCode": "78701", "address_text": "Primary"}

        out = workspace._address_of(Source(), None)
        assert out["street"] == "100 North Main Street"
        assert out["city"] == "Austin" and out["state"] == "TX"
        assert out["zip"] == "78701"

    def test_address_text_is_never_read_as_the_address(self):
        """It is the literal label "Primary" on 75% of delivered rows."""
        from app.tefca_registry import workspace

        class Source:
            parsed = {"address_text": "Primary", "address_line": ""}

        out = workspace._address_of(Source(), None)
        assert out["street"] is None
        assert "Primary" not in str(out.values())


# ── helpers ──────────────────────────────────────────────────────────────────

def _role_floors(router, path: str, method: str = "GET"):
    """Every `require_role` level on a route, router-level floors included.

    The checker `require_role` returns exposes `minimum_role` as an attribute —
    the same hook `test_no_tefca_read_endpoint_sits_above_the_viewer_floor`
    reads. Router routes carry the prefix, so the path is matched by suffix.
    """
    from app.core.security import ROLE_HIERARCHY

    levels = []
    for route in router.routes:
        if not route.path.endswith(path):
            continue
        if method not in (getattr(route, "methods", None) or set()):
            continue
        for fn in _deps(route.dependant):
            minimum = getattr(fn, "minimum_role", None)
            if minimum in ROLE_HIERARCHY:
                levels.append(ROLE_HIERARCHY[minimum])
    return levels


def _sql(stmt) -> str:
    """The WHERE clause with literals inlined, so a JSON key is visible."""
    from sqlalchemy.dialects import postgresql

    return str(stmt.whereclause.compile(dialect=postgresql.dialect(),
                                        compile_kwargs={"literal_binds": True}))


def _deps(dependant, seen=None):
    seen = seen if seen is not None else set()
    if id(dependant) in seen:
        return
    seen.add(id(dependant))
    if dependant.call is not None:
        yield dependant.call
    for sub in dependant.dependencies:
        yield from _deps(sub, seen)
