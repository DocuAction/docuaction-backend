"""OIDC token freshness across the bounded firewall wait.

Run 35391488253: the handshake wait (bounded ~20 min) and the Postgres
connection both succeeded - the operator's /32 was correct and on time - but
"Token, connection and role assumption" failed with AADSTS700024, because the
first `azure/login@v2`'s federated client assertion (valid ~5 min) had expired
by the time the wait finished. The fix re-authenticates immediately after the
wait and immediately before the token is minted. These pin that ordering and
that nothing else about the workflow moved.
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


def test_firewall_wait_precedes_the_fresh_login():
    steps = _steps(MIGRATION, "preflight")
    wait = _index(steps, lambda s: "Report runner IPv4" in s.get("name", ""), "firewall wait")
    logins = [i for i, s in enumerate(steps) if _is_login(s)]
    assert len(logins) == 2, f"expected exactly two azure/login steps (initial + refresh), found {len(logins)}"
    first_login, refresh_login = logins
    assert first_login < wait, "the initial login must still precede the wait (needed to report the runner IP)"
    assert refresh_login > wait, "the refreshed login must come after the bounded firewall wait, not before it"


def test_fresh_login_precedes_postgres_token_acquisition():
    steps = _steps(MIGRATION, "preflight")
    refresh_login = _index(steps, lambda s: _is_login(s) and s.get("name", "") != "Login to Azure (OIDC - no stored client secret)",
                            "refreshed login")
    token_step = _index(steps, lambda s: s.get("id") == "read", "token/connection step")
    assert refresh_login < token_step, "the refreshed login must run before the step that mints the Postgres access token"


def test_refresh_login_only_runs_when_the_wait_actually_ran():
    """apply=false preflight runs never hit the 20-minute wait, so they must
    not pay for or depend on a second login either."""
    steps = _steps(MIGRATION, "preflight")
    refresh = next(s for s in steps if _is_login(s) and s.get("name") != "Login to Azure (OIDC - no stored client secret)")
    assert refresh.get("if") == "inputs.apply == true"


def test_refresh_login_uses_the_same_federated_identity_no_new_secret():
    steps = _steps(MIGRATION, "preflight")
    initial = next(s for s in steps if s.get("name") == "Login to Azure (OIDC - no stored client secret)")
    refresh = next(s for s in steps if _is_login(s) and s is not initial)
    assert refresh["with"] == initial["with"], "the refresh must reuse the exact same OIDC client/tenant/subscription, not a new credential"
    assert "client-secret" not in refresh["with"]
    assert "continue-on-error" not in refresh


def test_id_token_write_permission_is_preserved():
    doc = _doc(MIGRATION)
    assert doc["jobs"]["preflight"]["permissions"]["id-token"] == "write"


def test_the_bounded_wait_is_still_twenty_minutes():
    wait = next(s for s in _steps(MIGRATION, "preflight") if "Report runner IPv4" in s.get("name", ""))
    run = wait["run"]
    assert "seq 1 80" in run and "sleep 15" in run, "80 * 15s = 20 minutes must be unchanged"


def test_workflow_still_holds_no_firewall_authority():
    steps = _steps(MIGRATION, "preflight")
    text = "\n".join(s.get("run") or "" for s in steps)
    assert "firewall-rule" not in text, "this fix must not add firewall-rule authority"
