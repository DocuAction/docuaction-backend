#!/usr/bin/env python
"""Fail CI if a database-backed test file is wired into neither CI database job.

DEF-2026-10-26-05 shipped because tests/test_cases_route_level_2026_10_26.py used the
`rolled_back_db` fixture (which needs a real, reachable Postgres -- see its `db_required`
dependency in each defining file) but was never added to pr-tests.yml's hand-maintained
isolation-postgres file list. The plain `pytest` job has no database, so `db_required`
skipped it there with "No database reachable" on every run; CI stayed green while this
path was never actually exercised.

This script is static (no database, no network) and belongs ahead of that job, not inside
it, so the feedback is immediate. It does not run the tests themselves.

Checking it against the real file list on 2026-10-26 turned up 40 more DB-backed test
files in the same blind spot, beyond the 4 this PR already wired in -- see EXEMPT below.
Those are a pre-existing gap this PR did not introduce and is not attempting to close
(wiring them into isolation-postgres is a separate, reviewed change: several call out to
screening/import paths this script has no way to confirm are sandboxed under a live
database). Exempting them here makes the gap visible and machine-checked instead of
silent, and -- the actual point of this script -- stops it from growing: any new
`rolled_back_db` file from now on must be either wired in or added to EXEMPT with a dated
reason, a conscious choice instead of a silent omission.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pr-tests.yml"
TESTS_DIR = REPO_ROOT / "tests"

# filename -> (dated reason, tracking reference)
# Found 2026-10-26 while fixing DEF-2026-10-26-05: all 40 already existed before this PR.
# None are newly introduced; none are triaged for live-source safety yet. Remove an entry
# only by adding the file to one of the file lists below instead.
EXEMPT: dict[str, str] = {
    name: "2026-10-26: pre-existing DB-backed test, not yet wired into a CI database job "
          "or triaged for live-source safety -- see 27_DEFECT_FIXES_2026-10-26.md"
    for name in [
        "test_ap013_large_report_metadata.py",
        "test_automated_verification.py",
        "test_automated_verification_cross_delivery_isolation.py",
        "test_automated_verification_load_protection.py",
        "test_case_assignment.py",
        "test_case_update_import_2026_10_25.py",
        "test_curated_text_columns.py",
        "test_curation_rerun_invariant.py",
        "test_current_quality_run_selection.py",
        "test_delivery_delta.py",
        "test_delivery_processing_report.py",
        "test_delivery_report_review_rows_2026_10_10.py",
        "test_disposition_events.py",
        "test_download_security.py",
        "test_dq_issue_code_namespace.py",
        "test_exclusion_name_screening_2026_10_04.py",
        "test_export_jobs.py",
        "test_gov_governance_anchor_fixture.py",
        "test_held_queue.py",
        "test_human_review_workflow.py",
        "test_identifier_conflict.py",
        "test_iqvia_import.py",
        "test_iqvia_routes.py",
        "test_onc_review_workbook.py",
        "test_post_promotion_verification.py",
        "test_preflight_enforcement_2026_10_04.py",
        "test_premerge_review_corrections.py",
        "test_priority_review_operational.py",
        "test_promotion_identifier_guard.py",
        "test_promotion_one_pass.py",
        "test_reconciliation_equation.py",
        "test_reference_preflight_2026_10_04.py",
        "test_report_html_artifact_consistency.py",
        "test_report_scope_normalization_and_audit.py",
        "test_review_cycle_lifecycle_required_behaviors.py",
        "test_review_id_concurrency.py",
        "test_sam_coverage_legs_2026_10_12.py",
        "test_sampling_plan_completion_batched_2026_10_10.py",
        "test_seed_rules_v3_governance.py",
        "test_september_snapshot.py",
        "test_snapshot_governance.py",
        "test_stage_events.py",
        "test_supervisor_operations.py",
        "test_verification_findings.py",
    ]
}

DB_FIXTURE_MARKER = "rolled_back_db"


def _file_list_between(workflow_text: str, start_marker: str, end_marker: str) -> set[str]:
    m = re.search(re.escape(start_marker) + r".*?run: \|\n(.*?)\n\s+- name: " + re.escape(end_marker),
                  workflow_text, re.S)
    if not m:
        raise RuntimeError(f"could not find a file list between {start_marker!r} and {end_marker!r} "
                           "-- pr-tests.yml's structure changed; update this script's markers")
    return set(re.findall(r"tests/([\w.]+\.py)", m.group(1)))


def main() -> int:
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    wired = _file_list_between(workflow_text, "Run the database-backed isolation suites",
                                "Fail if any isolation")
    wired |= _file_list_between(workflow_text, "Run the live-journey and route-level suites",
                                 "Stop the live server")

    db_backed = {
        f.name for f in sorted(TESTS_DIR.glob("test_*.py"))
        if DB_FIXTURE_MARKER in f.read_text(encoding="utf-8", errors="ignore")
    }

    unaccounted = sorted(db_backed - wired - EXEMPT.keys())
    stale_exemptions = sorted((EXEMPT.keys() & wired) | (EXEMPT.keys() - db_backed))

    ok = True
    if unaccounted:
        ok = False
        print("::error::database-backed test file(s) wired into no CI database job and not "
              "exempt -- add to pr-tests.yml's isolation-postgres/journey-live list, or to "
              "EXEMPT in this script with a dated reason:")
        for name in unaccounted:
            print(f"  - tests/{name}")
    if stale_exemptions:
        ok = False
        print("::error::EXEMPT entries no longer need an exemption (now wired in, or no "
              f"longer {DB_FIXTURE_MARKER}-based) -- remove from EXEMPT in "
              "scripts/ci/check_db_test_coverage.py:")
        for name in stale_exemptions:
            print(f"  - tests/{name}")

    if ok:
        print(f"OK: {len(db_backed)} database-backed test file(s) accounted for "
              f"({len(wired)} wired in, {len(EXEMPT)} exempt).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
