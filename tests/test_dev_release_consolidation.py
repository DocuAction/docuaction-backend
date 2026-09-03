"""The consolidated DEV release: one database phase, one runner, fail closed.

Run 33697060119 failed because the Government-integrity jobs executed a
repository script without ever checking the repository out (exit 2, "can't
open file"). The repair folded the whole database phase - read, controlled
apply, baseline - into the reusable migration workflow on ONE runner, so one
operator-added temporary /32 covers all of it. These pin that shape.
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
DEV_RELEASE = os.path.join(WF, "dev-release.yml")
MIGRATION = os.path.join(WF, "migration-preflight.yml")

pytestmark = pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")


def _doc(path):
    return yaml.safe_load(io.open(path, encoding="utf-8").read())


def _trigger(doc):
    return doc.get("on", doc.get(True)) or {}   # PyYAML 1.1: bare `on` -> True


def _steps(path, job):
    return _doc(path)["jobs"][job].get("steps", [])


def _runs_text(steps):
    return "\n".join(s.get("run") or "" for s in steps)


@pytest.mark.parametrize("path,job", [(DEV_RELEASE, "gov-verify"), (MIGRATION, "preflight")])
def test_checkout_precedes_any_repository_script_execution(path, job):
    """The exact defect: `python $GITHUB_WORKSPACE/.github/scripts/...` with
    no checkout. Every job that runs a repository file must check out first."""
    steps = _steps(path, job)
    first_script = next((i for i, s in enumerate(steps)
                         if "GITHUB_WORKSPACE/.github/scripts" in (s.get("run") or "")), None)
    assert first_script is not None, f"{job} no longer runs a repository script - update this test"
    checkout = next((i for i, s in enumerate(steps)
                     if str(s.get("uses", "")).startswith("actions/checkout")), None)
    assert checkout is not None and checkout < first_script, (
        f"{job} executes a repository script at step {first_script} without a prior checkout")


def test_a_push_can_never_execute_a_migration():
    """apply is wired only from a workflow_dispatch input; a push has no inputs."""
    dev = _doc(DEV_RELEASE)
    inputs = _trigger(dev)["workflow_dispatch"]["inputs"]
    assert inputs["apply_migrations"]["type"] == "boolean"
    assert inputs["apply_migrations"]["default"] is False
    with_ = dev["jobs"]["migration-check"]["with"]
    assert "inputs.apply_migrations" in with_["apply"]
    apply_step = next(s for s in _steps(MIGRATION, "preflight") if s.get("id") == "apply")
    assert "inputs.apply == true" in apply_step["if"]
    assert "migration_needed == 'true'" in apply_step["if"]


def test_the_whole_database_window_lives_in_one_job():
    """Handshake, read, apply and baseline share a runner - one /32 per window."""
    ids = [s.get("id") for s in _steps(MIGRATION, "preflight")]
    names = [s.get("name", "") for s in _steps(MIGRATION, "preflight")]
    assert "read" in ids and "apply" in ids and "baseline" in ids
    assert any("Report runner IPv4" in n for n in names)
    assert "gov-baseline" not in _doc(DEV_RELEASE)["jobs"], "baseline must not be a separate job/runner"


def test_apply_runs_as_the_dedicated_identity_with_the_owner_role():
    apply = next(s for s in _steps(MIGRATION, "preflight") if s.get("id") == "apply")["run"]
    env = _doc(MIGRATION)["env"]
    assert env["DB_MIGRATION_ROLE"] == "docuaction_owner"
    assert env["DB_APP_ROLE"] == "docuaction_app"
    assert env["PG_PRINCIPAL"] == "github-actions-docuaction-backend-dev"
    assert 'assert r[0] == os.environ["PG_PRINCIPAL"]' in apply, "session_user must be the dedicated identity"
    assert 'assert r[1] == os.environ["DB_MIGRATION_ROLE"]' in apply, "current_user must be docuaction_owner before DDL"
    assert 'test "$PENDING" -eq 1' in apply, "exactly one pending migration, or nothing runs"
    assert "Running upgrade" in apply, "second-run no-op must be proven"
    assert "imran@agtbi.com" not in apply


def test_the_migration_identity_holds_no_firewall_authority():
    """Separation of duties: the workflow reports an IP and waits; it never
    edits PostgreSQL firewall rules."""
    for path, job in ((MIGRATION, "preflight"), (DEV_RELEASE, "gov-verify")):
        text = _runs_text(_steps(path, job))
        assert "firewall-rule" not in text, f"{job} must not manage firewall rules"
        assert "Failing closed" in text or "FAILS" in text or "exit 1" in text


def test_deploy_requires_a_real_baseline_and_verify_compares_against_it():
    dev = _doc(DEV_RELEASE)
    assert "needs.migration-check.outputs.baseline_readable == 'true'" in dev["jobs"]["deploy"]["if"]
    assert "needs.migration-gate.outputs.proceed == 'true'" in dev["jobs"]["deploy"]["if"]
    verify = next(s for s in _steps(DEV_RELEASE, "gov-verify") if "Compare against" in s.get("name", ""))
    assert "needs.migration-check.outputs.baseline_json" in verify["env"]["BASELINE_JSON"]
    needs = dev["jobs"]["gov-verify"]["needs"]
    assert "deploy" in needs and "migration-check" in needs


def test_unreachable_baseline_is_not_manufactured_into_a_pass():
    baseline = next(s for s in _steps(MIGRATION, "preflight") if s.get("id") == "baseline")
    assert "readable == 'true'" in baseline["if"], "baseline only runs when the DB was actually read"


def test_no_parallel_migration_workflow_exists():
    assert not os.path.exists(os.path.join(WF, "migration-apply.yml"))


def test_release_summary_reports_the_database_phase_truthfully():
    text = _runs_text(_steps(DEV_RELEASE, "summary"))
    for key in ("applied", "baseline_readable", "migration-gate.result", "gov-verify.result"):
        assert key in text
