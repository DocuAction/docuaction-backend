"""End-to-end Monday workflow with five real hospital NPIs.

TWO LAYERS, AND THE DISTINCTION IS DELIBERATE

Contract tests (always run). They assert the workflow is wired: every step's
endpoint is registered, correctly gated, and the demo runner targets the paths
that actually exist. These catch the failure mode that has bitten this codebase
twice — a fix or a caller pointing at a route nobody serves — and they need no
network and no database, so they hold in CI.

Live tests (opt-in). They call a real environment and are SKIPPED unless
DEMO_EMAIL and DEMO_PASSWORD are set. They are skipped rather than mocked
because a mocked NPPES lookup proves nothing about NPPES.

    DEMO_EMAIL=... DEMO_PASSWORD=... pytest -m e2e tests/

A skipped live test is never reported as a pass. The demo report produced by
scripts/run_full_demo.py makes the same distinction, for the same reason: the
report is shown to people as evidence.
"""

import os

import pytest

from app.core.security import ROLE_HIERARCHY
from app.main import app

pytestmark = [pytest.mark.e2e, pytest.mark.regression]

LIVE = bool(os.getenv("DEMO_EMAIL") and os.getenv("DEMO_PASSWORD"))
needs_live = pytest.mark.skipif(
    not LIVE, reason="live run needs DEMO_EMAIL and DEMO_PASSWORD")

# The same five NPIs the demo uses — the CORRECTED set. The originally supplied
# identifiers did not belong to the hospitals named (three absent from NPPES and
# failing the CMS check digit, two identifying other organisations); see
# scripts/run_full_demo.SUPERSEDED_NPIS.
DEMO_NPIS = ["1477978807", "1881018208", "1275791162", "1821141649", "1770626038"]


def _routes():
    found = []

    def walk(routes):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(getattr(original, "routes", []))
            elif hasattr(route, "path") and hasattr(route, "dependant"):
                found.append(route)

    walk(app.routes)
    return found


def _route(path, method):
    for route in _routes():
        if route.path == path and method.upper() in getattr(route, "methods", set()):
            return route
    return None


def _role(route):
    best, level = None, -1
    for dep in route.dependant.dependencies:
        role = getattr(dep.call, "minimum_role", None)
        if role and ROLE_HIERARCHY.get(role, 0) > level:
            best, level = role, ROLE_HIERARCHY[role]
    return best


def _render_empty_report(steps=None):
    """Render the report without touching the network.

    Asserting on the RENDERED markdown rather than on the source text is the
    point: the provenance sentence is built from adjacent string literals, so a
    source-text check passes or fails on where the line happens to wrap.
    """
    from datetime import datetime, timezone

    from scripts.run_full_demo import DemoRunner

    runner = DemoRunner.__new__(DemoRunner)
    runner.base_url = "https://example.invalid"
    runner.steps = steps or []
    runner.entity_results = []
    runner.page_results = []
    runner.started_at = datetime(2026, 8, 8, tzinfo=timezone.utc)
    return runner.report_markdown("Development")


@pytest.mark.e2e
@pytest.mark.regression
class TestMondayWorkflow:
    """
    End-to-end Monday workflow.
    Runs the complete TEFCA verification cycle
    with 5 real hospital NPIs.
    """

    # ── step contracts ───────────────────────────────────────────────────────

    def test_step1_login(self):
        assert _route("/api/auth/login", "POST") is not None, \
            "the login endpoint the demo posts to does not exist"

    def test_step2_import_csv(self):
        route = _route("/api/tefca/entities/upload", "POST")
        assert route is not None, "the Entity Import page's endpoint is missing"
        role = _role(route)
        assert ROLE_HIERARCHY[role] <= ROLE_HIERARCHY["contributor"], \
            f"import requires {role}; an analyst runs this step"

    def test_step3_verify_entities(self):
        route = _route("/api/tefca/registry/entities/{entity_id}/verify", "POST")
        assert route is not None
        assert ROLE_HIERARCHY[_role(route)] <= ROLE_HIERARCHY["contributor"]

    def test_step4_registry_stats(self):
        assert _route("/api/tefca/registry/stats", "GET") is not None

    def test_step5_draw_sample(self):
        assert _route("/api/tefca/arc/samples", "POST") is not None

    def test_step6_generate_report(self):
        assert _route("/api/tefca/arc/reports/generate", "POST") is not None

    def test_step7_create_cycle(self):
        route = _route("/api/tefca/arc/cycles", "POST")
        assert route is not None
        assert _role(route) == "admin", "cycle creation is admin-only"
        # The legacy path the deployed frontend uses must keep working.
        assert _route("/api/v1/tefca/cycles", "POST") is not None

    def test_step8_priority_review(self):
        route = _route("/api/tefca/arc/priority-review", "POST")
        assert route is not None
        assert _role(route) == "admin"

    def test_step9_audit_trail(self):
        assert _route("/api/tefca/import/history", "GET") is not None
        assert _route("/api/tefca/registry/import/history", "GET") is not None

    # ── the demo runner must target routes that exist ────────────────────────

    def test_every_page_check_targets_a_real_route(self):
        """The demo report lists a row per frontend page. A row for a route that
        does not exist would render as a failure nobody can act on."""
        from scripts.run_full_demo import PAGE_CHECKS

        for name, method, path, _expected in PAGE_CHECKS:
            assert _route(path, method) is not None, \
                f"page check '{name}' targets missing route {method} {path}"

    def test_the_demo_uses_the_five_specified_npis(self):
        from scripts.run_full_demo import ENTITIES

        assert [e["npi"] for e in ENTITIES] == DEMO_NPIS

    def test_the_demo_npis_pass_the_cms_check_digit(self):
        """Real NPIs, not plausible-looking ones.

        This test caught the original demo list: three of those five failed the
        check digit and do not exist in NPPES, so the import would have rejected
        them outright, and the two that did import identified other companies.
        """
        from app.services.npi_validator import validate_npi

        for npi in DEMO_NPIS:
            ok, message = validate_npi(npi)
            assert ok, f"{npi} fails the CMS check digit: {message}"

    def test_the_superseded_npis_are_not_reinstated(self):
        """The originally supplied identifiers must not come back. Two of them
        are real NPIs belonging to other organisations, so reinstating one would
        put the wrong company in a customer-facing report rather than fail."""
        from scripts.run_full_demo import ENTITIES, SUPERSEDED_NPIS

        in_use = {e["npi"] for e in ENTITIES}
        assert not (in_use & set(SUPERSEDED_NPIS)), \
            f"superseded NPI back in the demo set: {in_use & set(SUPERSEDED_NPIS)}"

    def test_the_demo_import_would_survive_the_npi_gate(self):
        """The CSV import rejects a bad NPI at parse time (QA-1.2/1.3). Every
        demo row must clear that gate or step 2 imports fewer than five."""
        from app.tefca_registry.csv_import import _validate_npi_cell
        from scripts.run_full_demo import ENTITIES

        for entity in ENTITIES:
            _validate_npi_cell(entity["npi"])   # raises ValueError if rejected

    def test_the_demo_csv_matches_the_import_format(self):
        """Column names must be the ones the endpoint reads, not the ones in the
        brief. The first demo run had all five rows rejected for an empty
        entity_name: the import was correct and the file was wrong."""
        from scripts.run_full_demo import DemoRunner

        header = DemoRunner.build_csv(
            DemoRunner.__new__(DemoRunner)).decode().splitlines()[0].split(",")
        # Exactly what app/Tefca/routes.upload_entities requires per row.
        for required in ("entity_name", "npi", "qhin"):
            assert required in header, f"import requires a {required} column"

    def test_the_demo_csv_rows_satisfy_the_import_validators(self):
        """Every row must clear entity_name, NPI and qhin, or step 2 imports
        fewer than five and step 3 is left verifying pre-existing records."""
        import csv
        import io as _io

        from app.Tefca.routes import _valid_npi
        from scripts.run_full_demo import DemoRunner

        text = DemoRunner.build_csv(DemoRunner.__new__(DemoRunner)).decode()
        rows = list(csv.DictReader(_io.StringIO(text)))
        assert len(rows) == 5
        for row in rows:
            assert row["entity_name"].strip()
            assert row["qhin"].strip()
            assert _valid_npi(row["npi"]), f"{row['npi']} fails the import NPI check"

    def test_the_qhin_placeholder_is_obviously_a_placeholder(self):
        """The QHIN each hospital exchanges under comes from the ONC-provided
        dataset. Putting a real QHIN name here would assert a TEFCA relationship
        we have not been told, in a record that then reads as fact."""
        from scripts.run_full_demo import DemoRunner

        assert "placeholder" in DemoRunner.DEMO_QHIN.lower()
        assert "ONC" in DemoRunner.DEMO_QHIN

    # ── report integrity ─────────────────────────────────────────────────────

    def test_a_blocked_step_is_never_counted_as_a_pass(self):
        """The property that makes the demo report worth showing anyone."""
        from scripts.run_full_demo import BLOCKED, DemoRunner, PASS, Step

        runner = DemoRunner.__new__(DemoRunner)
        runner.steps = [Step(1, "Login").record(BLOCKED, "no credential"),
                        Step(2, "Import").record(PASS, "ok")]
        counts = runner.counts()
        assert counts[PASS] == 1 and counts[BLOCKED] == 1
        assert "NOT a pass" in runner.verdict()
        assert "INCOMPLETE" in runner.verdict()

    def test_a_fully_passing_run_reports_complete(self):
        from scripts.run_full_demo import DemoRunner, PASS, Step

        runner = DemoRunner.__new__(DemoRunner)
        runner.steps = [Step(i, f"s{i}").record(PASS, "ok") for i in range(1, 10)]
        assert runner.verdict().startswith("COMPLETE")

    def test_the_runner_exits_non_zero_when_a_step_is_blocked(self):
        """So CI cannot treat an incomplete demo as a green build."""
        import inspect

        from scripts import run_full_demo

        src = inspect.getsource(run_full_demo.main)
        assert "counts[FAIL] == 0 and counts[BLOCKED] == 0" in src

    def test_the_report_never_invents_a_value(self):
        import inspect

        from scripts import run_full_demo

        src = inspect.getsource(run_full_demo.DemoRunner)
        assert '"not returned"' in src, \
            "missing values must be labelled, not filled in"

    def test_the_demo_states_onc_provenance(self):
        """Entity population data comes from ONC, and the generated report has
        to say so. The absence check for vendor names lives in
        test_data_provenance.py, which scans scripts/ along with the rest of the
        tree — duplicating the forbidden strings here would make this file its
        own violation."""
        report = _render_empty_report()
        assert "provided by ONC per contract direction" in report
        assert "AGT does not independently source entity population data" in report

    # ── live run (opt-in) ────────────────────────────────────────────────────

    @needs_live
    def test_live_workflow_runs_end_to_end(self):
        """Executes all nine steps against the configured environment."""
        from scripts.run_full_demo import BLOCKED, DemoRunner, FAIL

        runner = DemoRunner(
            os.getenv("DEMO_BASE_URL", "https://docuaction-dev.azurewebsites.net"),
            os.environ["DEMO_EMAIL"], os.environ["DEMO_PASSWORD"], 120.0)
        runner.run()
        counts = runner.counts()
        failures = [f"step {s.number} {s.description}: {s.status} — {s.detail}"
                    for s in runner.steps if s.status in (FAIL, BLOCKED)]
        assert not failures, "\n".join(failures)
        assert counts[FAIL] == 0
