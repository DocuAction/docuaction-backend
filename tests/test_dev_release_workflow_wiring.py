"""Static guarantees about how the DEV release workflows use the sequential applier and firewall window.

These read the workflow YAML (nothing is executed). They pin the properties that make the automation safe:
the new behaviour is opt-in, cleanup is the last and unconditional step, the open/close pair names the same
rule, the deployment gate is the one that existed before, and no job gained write access to code.
"""
from __future__ import annotations

import os

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wf(name):
    with open(os.path.join(REPO, ".github", "workflows", name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _steps(job):
    return job["steps"]


def _find(steps, fragment):
    hits = [i for i, s in enumerate(steps) if fragment in (s.get("name") or "")]
    assert len(hits) == 1, f"expected exactly one step containing {fragment!r}, found {len(hits)}"
    return hits[0]


def _on(wf):
    # PyYAML reads the bare key `on` as boolean True.
    return wf.get("on") or wf.get(True)


def test_new_inputs_are_opt_in_in_both_workflows():
    pre = _on(_wf("migration-preflight.yml"))["workflow_call"]["inputs"]
    rel = _on(_wf("dev-release.yml"))["workflow_dispatch"]["inputs"]
    for inputs in (pre, rel):
        for name in ("sequential_to_head", "manage_firewall"):
            assert inputs[name]["default"] is False and inputs[name]["type"] == "boolean"
    # a push has no inputs, so it can never migrate or touch the firewall
    assert "push" in _on(_wf("dev-release.yml"))


def test_cleanup_is_the_last_step_unconditional_and_names_the_rule_the_open_step_created():
    steps = _steps(_wf("migration-preflight.yml")["jobs"]["preflight"])
    open_i = _find(steps, "Open the temporary /32")
    close_i = _find(steps, "Close the temporary /32")
    assert close_i == len(steps) - 1, "the close step must be the very last step"
    assert steps[close_i - 1]["name"].startswith("Refresh Azure OIDC login for cleanup")
    assert steps[close_i]["if"].startswith("always()") and steps[close_i - 1]["if"].startswith("always()")
    assert steps[open_i]["env"]["FIREWALL_RULE_NAME"] == steps[close_i]["env"]["FIREWALL_RULE_NAME"] \
        == "temp-run-${{ github.run_id }}"
    assert "dev_firewall.sh open" in steps[open_i]["run"] and "dev_firewall.sh close" in steps[close_i]["run"]
    # every database-touching step sits between open and close
    for fragment in ("Token, connection", "Apply ALL pending", "Capture Government-integrity", "Final gate inputs"):
        assert open_i < _find(steps, fragment) < close_i


def test_gov_verify_has_its_own_window_with_a_distinct_rule_name():
    job = _wf("dev-release.yml")["jobs"]["gov-verify"]
    steps = _steps(job)
    open_i, close_i = _find(steps, "Open the temporary /32"), _find(steps, "Close the temporary /32")
    assert steps[open_i]["env"]["FIREWALL_RULE_NAME"] == steps[close_i]["env"]["FIREWALL_RULE_NAME"] \
        == "temp-run-${{ github.run_id }}-gv"
    assert close_i == len(steps) - 1 and steps[close_i]["if"].startswith("always()")
    assert _find(steps, "Compare against the pre-deploy baseline") < close_i
    assert job["environment"] == "development", "the human approval stays in front of this job"


def test_manual_handshake_still_works_when_manage_firewall_is_off():
    pre = _steps(_wf("migration-preflight.yml")["jobs"]["preflight"])
    manual = pre[_find(pre, "wait for the operator's temporary /32")]
    assert "manage_firewall != true" in manual["if"]
    assert "gh issue comment" in manual["run"]
    gv = _steps(_wf("dev-release.yml")["jobs"]["gov-verify"])
    assert gv[_find(gv, "wait for the operator's temporary /32")]["if"] == "inputs.manage_firewall != true"


def test_single_target_apply_is_unchanged_and_sequential_is_a_separate_step():
    pre = _steps(_wf("migration-preflight.yml")["jobs"]["preflight"])
    single, seq = pre[_find(pre, "Apply the approved migration")], pre[_find(pre, "Apply ALL pending")]
    assert "sequential_to_head != true" in single["if"] and "sequential_to_head == true" in seq["if"]
    for step in (single, seq):
        assert "inputs.apply == true" in step["if"] and "migration_needed == 'true'" in step["if"]
    assert "apply_migrations_sequentially.sh" in seq["run"]
    assert seq["env"]["EXPECTED_CURRENT"] == "${{ inputs.expected_current }}"
    assert "target_revision" not in seq["run"] and "target_revision" not in str(seq["env"]), \
        "sequential mode never takes a target: it always ends at the repository head"


def test_the_gate_reports_migration_needed_false_only_at_head_after_any_apply():
    pre = _steps(_wf("migration-preflight.yml")["jobs"]["preflight"])
    final = pre[_find(pre, "Final gate inputs")]["run"]
    assert 'steps.apply_seq.outputs.applied' in final and 'steps.apply.outputs.applied' in final
    assert '"$AFTER" = "$HEAD"' in final
    assert final.index("migration_needed=false") < final.index("migration_needed=true")


def test_deployment_gate_and_deploy_job_are_exactly_what_they_were():
    jobs = _wf("dev-release.yml")["jobs"]
    assert jobs["deploy"]["needs"] == ["build", "migration-gate", "migration-check"]
    assert jobs["deploy"]["if"] == ("needs.migration-gate.outputs.proceed == 'true' && "
                                    "needs.migration-check.outputs.baseline_readable == 'true'")
    assert jobs["migration-gate"]["needs"] == "migration-check"
    # deploy runs migrations never: the database phase owns them
    assert jobs["deploy"]["with"]["run_migrations"] == "${{ needs.migration-gate.outputs.run_migrations }}"


def test_no_job_gained_write_access_to_the_repository_or_azure_beyond_what_it_had():
    for name, jobname in (("migration-preflight.yml", "preflight"), ("dev-release.yml", "gov-verify"),
                          ("dev-release.yml", "migration-check")):
        perms = _wf(name)["jobs"][jobname]["permissions"]
        assert perms["contents"] == "read"
        assert perms.get("actions") == "read"
        assert set(perms) <= {"contents", "id-token", "issues", "actions"}


def test_the_firewall_scripts_are_what_the_workflows_call():
    for rel in ("scripts/release/dev_firewall.sh", "scripts/release/apply_migrations_sequentially.sh"):
        path = os.path.join(REPO, rel)
        assert os.path.isfile(path)
        head = open(path, encoding="utf-8").read(200)
        assert head.startswith("#!/usr/bin/env bash")
    text = open(os.path.join(REPO, ".github", "workflows", "migration-preflight.yml"), encoding="utf-8").read()
    assert "scripts/release/dev_firewall.sh" in text and "scripts/release/apply_migrations_sequentially.sh" in text
