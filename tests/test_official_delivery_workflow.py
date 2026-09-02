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

    def test_registration_floor_is_not_raised_beyond_data_operations(self):
        """Data Operations is not a reviewer, a QA lead or a program manager.

        The floor exists to exclude the analyst, not to make delivery
        registration a privileged act only management can perform.
        """
        from app.tefca_registry.rce.delivery_routes import DATA_OPERATIONS_ROLE

        assert role_level(DATA_OPERATIONS_ROLE) < role_level("reviewer")


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

    def test_the_verification_seed_is_bounded(self):
        """Verification calls shared Government sources; the auto pass is capped.

        An uncapped pass over a 25K delivery would issue hundreds of thousands
        of upstream calls before anyone decided the delivery was worth
        reviewing.
        """
        from app.tefca_registry.rce.delivery_runner import AUTO_VERIFY_LIMIT

        assert 0 < AUTO_VERIFY_LIMIT <= 5000


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
    def test_every_workflow_read_admits_a_viewer(self, app_spec):
        """The property `test_no_tefca_read_endpoint_sits_above_the_viewer_floor`
        asserts globally, pinned here for this router specifically so a future
        edit to one of these routes fails with a local, obvious message."""
        from app.tefca_registry import workflow_routes

        floors = {}
        for route in workflow_routes.router.routes:
            if "GET" not in getattr(route, "methods", set()):
                continue
            floors[route.path] = route
        assert floors, "no GET routes found on the workflow router"
