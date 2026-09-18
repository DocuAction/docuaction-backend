"""Structural validation of the automated migration firewall authorization
in .github/workflows/migration-preflight.yml and dev-release.yml
(2026-09-18). These assert properties of the workflow YAML/text itself -
the things a unit test on extracted Python logic cannot reach, because
they ARE the workflow wiring: which step runs under which `if:` condition,
what a create/delete call's arguments are, and which job depends on which.

No Azure or GitHub Actions run is exercised here - this is static analysis
of the committed YAML, run the same way in CI as any other test.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_PREFLIGHT_TEXT = (_WORKFLOWS / "migration-preflight.yml").read_text(encoding="utf-8")
_PREFLIGHT_YAML = yaml.safe_load(_PREFLIGHT_TEXT)
_DEV_RELEASE_TEXT = (_WORKFLOWS / "dev-release.yml").read_text(encoding="utf-8")
_DEV_RELEASE_YAML = yaml.safe_load(_DEV_RELEASE_TEXT)


def _steps():
    return _PREFLIGHT_YAML["jobs"]["preflight"]["steps"]


def _step(name_substring):
    matches = [s for s in _steps() if name_substring.lower() in s.get("name", "").lower()]
    assert matches, f"no step found matching {name_substring!r}"
    assert len(matches) == 1, f"multiple steps match {name_substring!r}: {[s['name'] for s in matches]}"
    return matches[0]


# ── create uses identical start/end addresses ────────────────────────────────

def test_create_rule_uses_identical_start_and_end_ip():
    step = _step("Create the temporary firewall rule")
    run = step["run"]
    assert '--start-ip-address "$IP"' in run
    assert '--end-ip-address "$IP"' in run
    # Both reference the SAME shell variable - not two independently
    # computed values that could ever diverge into a range.
    assert run.count('"$IP"') >= 2


def test_create_rule_never_uses_allow_all_azure_services_pseudo_range():
    step = _step("Create the temporary firewall rule")
    # Comment lines may legitimately explain what this step refuses to do
    # (and do) - only actual command lines matter for this assertion.
    code_lines = [ln for ln in step["run"].splitlines() if not ln.strip().startswith("#")]
    assert not any("0.0.0.0" in ln for ln in code_lines), (
        "must never construct the 'Allow all Azure services' pseudo-rule (0.0.0.0-0.0.0.0) "
        "in an actual command line")


# ── unique rule name ──────────────────────────────────────────────────────────

def test_rule_name_includes_run_id_run_attempt_and_environment():
    step = _step("Generate a unique firewall-rule name")
    run = step["run"]
    assert "github.run_id" in run
    assert "github.run_attempt" in run
    assert "development" in run


# ── cleanup runs under if: always() for every required scenario ─────────────

def test_cleanup_delete_step_runs_under_if_always():
    step = _step("Delete the temporary firewall rule")
    condition = step.get("if", "")
    assert "always()" in condition, (
        f"delete step's `if:` must include always() so it runs after success, "
        f"migration failure, auth failure, timeout, or cancellation - got: {condition!r}")


def test_cleanup_verification_step_runs_under_if_always():
    step = _step("Verify the temporary rule no longer exists")
    condition = step.get("if", "")
    assert "always()" in condition, f"got: {condition!r}"


def test_cleanup_verification_queries_azure_rather_than_trusting_delete_exit_code():
    step = _step("Verify the temporary rule no longer exists")
    run = step["run"]
    assert "firewall-rule show" in run, "must re-query Azure, not just trust the delete call's own exit code"


def test_cleanup_failure_reports_the_exact_residual_rule_and_fails_the_job():
    step = _step("Verify the temporary rule no longer exists")
    run = step["run"]
    assert "::error::" in run
    assert "$NAME" in run  # the exact residual rule name is in the failure message
    assert "exit 1" in run  # a leftover rule is NOT swallowed - this fails the run


def test_cleanup_only_attempted_when_a_rule_was_actually_created():
    # Both cleanup steps must be gated on steps.create_rule.outputs.created
    # == 'true' - if creation itself failed (e.g. the authorization-missing
    # case), there is nothing to clean up, and attempting a delete for a
    # rule name that was never created would itself be a spurious failure.
    for name in ("Delete the temporary firewall rule", "Verify the temporary rule no longer exists"):
        step = _step(name)
        assert "steps.create_rule.outputs.created" in step.get("if", "")


# ── deployment cannot start before migration verification ───────────────────

def test_deploy_job_depends_on_migration_gate_and_migration_check():
    deploy = _DEV_RELEASE_YAML["jobs"]["deploy"]
    needs = deploy["needs"]
    assert "migration-gate" in needs
    assert "migration-check" in needs
    assert "build" in needs


def test_deploy_job_only_proceeds_when_migration_gate_confirms_safe():
    deploy = _DEV_RELEASE_YAML["jobs"]["deploy"]
    condition = deploy.get("if", "")
    assert "migration-gate.outputs.proceed" in condition
    assert "baseline_readable" in condition, (
        "deploy must also require a real (not empty-because-unreadable) "
        "pre-deploy Government-integrity baseline")


def test_migration_gate_job_runs_before_deploy_in_the_dependency_graph():
    gate = _DEV_RELEASE_YAML["jobs"]["migration-gate"]
    assert gate["needs"] == "migration-check"
    deploy = _DEV_RELEASE_YAML["jobs"]["deploy"]
    assert "migration-gate" in deploy["needs"]


# ── production is never targeted ─────────────────────────────────────────────

def _non_comment_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if not ln.strip().startswith("#")]


def test_dev_release_never_selects_environment_production():
    # Comments are allowed to explain that production is never targeted (one
    # does, deliberately); only actual `environment:` directives matter here.
    code_lines = _non_comment_lines(_DEV_RELEASE_TEXT)
    assert not any("environment: production" in ln or "environment: prod" in ln for ln in code_lines)
    # Also check the parsed job graph directly: no job/step's `environment:`
    # or `with.environment` value is ever "production"/"prod".
    for job in _DEV_RELEASE_YAML["jobs"].values():
        assert job.get("environment") not in ("production", "prod")
        with_block = job.get("with") or {}
        assert with_block.get("environment") not in ("production", "prod")


def test_migration_preflight_never_selects_environment_production():
    code_lines = _non_comment_lines(_PREFLIGHT_TEXT)
    assert not any("environment: production" in ln or "environment: prod" in ln for ln in code_lines)
    for job in _PREFLIGHT_YAML["jobs"].values():
        assert job.get("environment") not in ("production", "prod")


def test_dev_release_deploy_call_pins_environment_dev():
    deploy = _DEV_RELEASE_YAML["jobs"]["deploy"]
    assert deploy["with"]["environment"] == "dev"


# ── no continue-on-error, no silently swallowed migration/cleanup failures ──

def test_no_continue_on_error_anywhere_in_either_workflow():
    assert "continue-on-error" not in _PREFLIGHT_TEXT
    assert "continue-on-error" not in _DEV_RELEASE_TEXT


def test_firewall_creation_failure_is_not_swallowed():
    step = _step("Create the temporary firewall rule")
    run = step["run"]
    assert "exit 1" in run
    assert "|| true" not in run


def test_connectivity_polling_failure_is_not_swallowed():
    step = _step("Poll for connectivity")
    run = step["run"]
    assert "exit 1" in run
    assert "|| true" not in run
