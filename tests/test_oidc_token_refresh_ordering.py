"""OIDC token freshness across every bounded firewall wait in the release.

Run 35391488253 (pre-deploy): the handshake wait (bounded ~20 min) and the
Postgres connection both succeeded, but "Token, connection and role
assumption" failed with AADSTS700024 - the first azure/login@v2's federated
client assertion (valid ~5 min) had expired by the time the wait finished.
PR #79 fixed that in migration-preflight.yml's `preflight` job.

Run 35395945838 (post-deploy): the identical defect resurfaced in
dev-release.yml's `gov-verify` job, which has its own independent bounded
wait (a second runner, a second temporary /32) and never had the refresh
applied. Deployment itself succeeded; only the Government-data comparison
never ran, because token acquisition failed before it.

These tests pin both jobs' ordering so neither wait can regress on its own.
"""
import io
import os

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(REPO, ".github", "workflows")
MIGRATION = os.path.join(WF, "migration-preflight.yml")
DEV_RELEASE = os.path.join(WF, "dev-release.yml")

# (workflow path, job name, id of the step that first acquires a Postgres token)
JOBS = [(MIGRATION, "preflight"), (DEV_RELEASE, "gov-verify")]

pytestmark = pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")


def _doc(path):
    return yaml.safe_load(io.open(path, encoding="utf-8").read())


def _steps(path, job):
    return _doc(path)["jobs"][job].get("steps", [])


def _index(steps, pred, what):
    i = next((i for i, s in enumerate(steps) if pred(s)), None)
    assert i is not None, f"could not locate step: {what}"
    return i


def _is_login(s):
    return str(s.get("uses", "")).startswith("azure/login@")


def _token_step_index(steps):
    """The first step (after the wait) that mints a Postgres access token,
    identified by content rather than a job-specific id/name, since the two
    jobs structure this differently (a dedicated step vs. inline in the
    comparison step)."""
    return _index(steps, lambda s: "az account get-access-token" in (s.get("run") or ""),
                  "Postgres token acquisition")


@pytest.mark.parametrize("path,job", JOBS)
def test_firewall_wait_precedes_the_fresh_login(path, job):
    steps = _steps(path, job)
    wait = _index(steps, lambda s: "Report runner IPv4" in s.get("name", ""), "firewall wait")
    logins = [i for i, s in enumerate(steps) if _is_login(s)]
    assert len(logins) == 2, f"{job}: expected exactly two azure/login steps (initial + refresh), found {len(logins)}"
    first_login, refresh_login = logins
    assert first_login < wait, f"{job}: the initial login must still precede the wait (needed to report the runner IP)"
    assert refresh_login > wait, f"{job}: the refreshed login must come after the bounded firewall wait, not before it"


@pytest.mark.parametrize("path,job", JOBS)
def test_fresh_login_precedes_postgres_token_acquisition(path, job):
    steps = _steps(path, job)
    refresh_login = _index(
        steps,
        lambda s: _is_login(s) and s.get("name", "") != "Login to Azure (OIDC - no stored client secret)",
        "refreshed login")
    token_step = _token_step_index(steps)
    assert refresh_login < token_step, f"{job}: the refreshed login must run before Postgres token acquisition"


@pytest.mark.parametrize("path,job", JOBS)
def test_refresh_login_matches_the_waits_own_condition(path, job):
    """The refresh must be gated the same way as the wait it follows: the
    preflight job's wait (and refresh) only run on apply=true; gov-verify's
    wait (and refresh) are unconditional, since that job only exists on the
    post-deploy path in the first place."""
    steps = _steps(path, job)
    wait = next(s for s in steps if "Report runner IPv4" in s.get("name", ""))
    refresh = next(s for s in steps if _is_login(s) and s.get("name") != "Login to Azure (OIDC - no stored client secret)")
    assert refresh.get("if") == wait.get("if")


@pytest.mark.parametrize("path,job", JOBS)
def test_refresh_login_uses_the_same_federated_identity_no_new_secret(path, job):
    steps = _steps(path, job)
    initial = next(s for s in steps if s.get("name") == "Login to Azure (OIDC - no stored client secret)")
    refresh = next(s for s in steps if _is_login(s) and s is not initial)
    assert refresh["with"] == initial["with"], (
        f"{job}: the refresh must reuse the exact same OIDC client/tenant/subscription, not a new credential")
    assert "client-secret" not in refresh["with"]
    assert "continue-on-error" not in refresh


@pytest.mark.parametrize("path,job", JOBS)
def test_id_token_write_permission_is_preserved(path, job):
    doc = _doc(path)
    assert doc["jobs"][job]["permissions"]["id-token"] == "write"


@pytest.mark.parametrize("path,job", JOBS)
def test_the_bounded_wait_is_still_twenty_minutes(path, job):
    wait = next(s for s in _steps(path, job) if "Report runner IPv4" in s.get("name", ""))
    run = wait["run"]
    assert "seq 1 80" in run and "sleep 15" in run, f"{job}: 80 * 15s = 20 minutes must be unchanged"


@pytest.mark.parametrize("path,job", JOBS)
def test_no_firewall_management_command_exists(path, job):
    text = "\n".join(s.get("run") or "" for s in _steps(path, job))
    assert "firewall-rule" not in text, f"{job}: must not manage firewall rules"


def test_both_pre_and_post_deploy_jobs_refresh_oidc_after_their_own_wait():
    """The regression this whole file exists for: each job's wait is bounded
    independently (two different runners, two different /32s), so each job
    needs its own refresh - fixing one must not be mistaken for fixing both."""
    for path, job in JOBS:
        steps = _steps(path, job)
        logins = [s for s in steps if _is_login(s)]
        assert len(logins) == 2, f"{job} does not have a refresh login"


def test_government_data_comparison_step_is_unchanged():
    """The fix touches only login ordering; the comparison itself - what it
    reads, what script it runs, what it compares against - must be identical
    to the pre-fix job."""
    compare = next(s for s in _steps(DEV_RELEASE, "gov-verify") if s.get("name") == "Compare against the pre-deploy baseline")
    assert compare["env"]["BASELINE_JSON"] == "${{ needs.migration-check.outputs.baseline_json }}"
    assert "gov_integrity_verify.py" in compare["run"]
    assert "psycopg2-binary" in compare["run"]
